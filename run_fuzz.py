"""Fuzz + adversarial search for the Zero-PvP solvency claim.

Usage: python3 run_fuzz.py [--random N] [--adversarial N] [--seed S]

Reports, per lot-accounting mode:
  - worst (most negative) router buffer seen at any step, in lamports
  - whether any step broke the identity buffer == basis_remaining - reserve
  - surplus left in the buffer at graduation (what "holders who stayed" receive)
"""

import argparse
import random
from dataclasses import dataclass

import dbc_curve as dbc
import zero_pvp as zp
from curve_factory import LAMPORTS_PER_SOL, random_curve

MAX_STEPS = 400
CANDIDATES_PER_STEP = 12


@dataclass(frozen=True)
class RunResult:
    min_buffer: int
    min_theorem_gap: int
    final_buffer: int
    graduated: bool
    surplus_at_graduation: int
    identity_drift: int
    volume: int


def random_buy_amount(rng: random.Random) -> int:
    # log-uniform 0.001 .. 50 SOL
    return int(10 ** rng.uniform(-3, 1.7) * LAMPORTS_PER_SOL)


def candidate_actions(state, config, rng, users):
    if state.pool.is_graduated(config):
        return []  # DBC rejects every swap once the curve is complete (PoolIsCompleted)
    actions = [("buy", rng.choice(users), random_buy_amount(rng)) for _ in range(4)]
    holders = list(state.positions.keys())
    for _ in range(8 if holders else 0):
        user = rng.choice(holders)
        held = state.tokens_of(user)
        size = held if rng.random() < 0.5 else rng.randint(1, held)
        actions.append(("exit", user, size))
    return actions


def apply(state, config, action, mode, haircut=0, dust_per_buy=0):
    kind, user, amount = action
    if kind == "buy":
        new_state, outcome = zp.router_buy(state, config, user, amount, mode, dust_per_buy)
        return new_state, outcome.amount_in
    new_state, receipt = zp.router_exit(state, config, user, amount, mode, haircut)
    return new_state, receipt["gross"]


def simulate(
    seed: int,
    mode: str,
    adversarial: bool,
    full_unwind: bool,
    haircut: int = 0,
    dust_per_buy: int = 0,
) -> RunResult:
    rng = random.Random(seed)
    config = random_curve(rng)
    users = [f"u{i}" for i in range(rng.randint(2, 30))]
    state = zp.RouterState(pool=dbc.PoolState(sqrt_price=config.sqrt_start_price))
    surplus_at_graduation, graduated, volume, drift = 0, False, 0, 0
    min_gap = 0

    for _ in range(MAX_STEPS):
        actions = candidate_actions(state, config, rng, users)
        if not actions:
            break
        if adversarial:
            # greedy adversary: pick the action that pushes the buffer lowest
            scored = [(apply(state, config, a, mode, haircut, dust_per_buy), a) for a in actions]
            (state, traded), _ = min(scored, key=lambda item: item[0][0].buffer)
        else:
            state, traded = apply(state, config, rng.choice(actions), mode, haircut, dust_per_buy)
        volume += traded
        drift = max(drift, abs(zp.check_identity(state)))
        min_gap = min(min_gap, state.theorem_gap)
        if state.pool.is_graduated(config) and not graduated:
            graduated, surplus_at_graduation = True, state.buffer
            if not full_unwind:
                break

    if full_unwind and not graduated:  # stress: dead curve, everyone exits in random order
        for user in rng.sample(list(state.positions), len(state.positions)):
            state, _ = zp.router_exit(state, config, user, state.tokens_of(user), mode, haircut)
            drift = max(drift, abs(zp.check_identity(state)))
            min_gap = min(min_gap, state.theorem_gap)

    return RunResult(state.min_buffer, min_gap, state.buffer, graduated, surplus_at_graduation, drift, volume)


def summarize(label: str, results: list) -> None:
    worst = min(results, key=lambda r: r.min_buffer)
    negatives = [r for r in results if r.min_buffer < 0]
    grads = [r for r in results if r.graduated]
    surplus = sorted(r.surplus_at_graduation for r in grads)
    print(f"\n== {label} ({len(results)} runs) ==")
    print(f"  runs with buffer < 0 at any step : {len(negatives)}")
    print(f"  worst min buffer                 : {worst.min_buffer} lamports")
    print(f"  worst theorem gap sum(gross-basis): {min(r.min_theorem_gap for r in results)} lamports")
    print(f"  max identity drift               : {max(r.identity_drift for r in results)}")
    print(f"  graduated runs                   : {len(grads)}")
    if surplus:
        median = surplus[len(surplus) // 2] / LAMPORTS_PER_SOL
        print(f"  surplus at graduation (median)   : {median:.4f} SOL")
        print(f"  surplus at graduation (max)      : {surplus[-1] / LAMPORTS_PER_SOL:.4f} SOL")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--random", type=int, default=600)
    parser.add_argument("--adversarial", type=int, default=150)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--haircut", type=int, default=0, help="lamports withheld per exit")
    parser.add_argument("--dust-per-buy", type=int, default=0, help="lamports withheld per buy")
    args = parser.parse_args()

    for mode in zp.LOT_MODES:
        for adversarial, count in ((False, args.random), (True, args.adversarial)):
            for full_unwind in (False, True):
                results = [
                    simulate(
                        args.seed + i, mode, adversarial, full_unwind,
                        args.haircut, args.dust_per_buy,
                    ) for i in range(count)
                ]
                kind = "adversarial" if adversarial else "random"
                tail = "+full-unwind" if full_unwind else "to-graduation"
                summarize(f"{mode} / {kind} / {tail}", results)


if __name__ == "__main__":
    main()
