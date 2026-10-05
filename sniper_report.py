"""Sniper economics: plain DBC vs Zero-PvP router, on the same order flow.

Scenario per run: sniper buys first, N retail buyers follow, sniper exits
before graduation. Then retail exits (worst case: the hype died) so we see
who absorbed the loss.
"""

import random

import dbc_curve as dbc
import zero_pvp as zp
from curve_factory import LAMPORTS_PER_SOL, build_curve

DUST_PER_BUY = 16
HAIRCUT_PER_EXIT = 2
RUNS = 300


def plain_dbc(config, sniper_sol, retail_buys):
    pool = dbc.PoolState(sqrt_price=config.sqrt_start_price)
    sniper = dbc.buy(pool, config, sniper_sol)
    pool = sniper.pool
    retail = []
    for amount in retail_buys:
        outcome = dbc.buy(pool, config, amount)
        if outcome.pool.is_graduated(config):
            continue  # keep the curve open so the sniper can still exit pre-graduation
        pool, retail = outcome.pool, retail + [(outcome.amount_in, outcome.amount_out)]
    sniper_exit = dbc.sell(pool, config, sniper.amount_out)
    pool = sniper_exit.pool
    retail_pnl = 0
    for paid, tokens in retail:
        sold = dbc.sell(pool, config, tokens)
        pool, retail_pnl = sold.pool, retail_pnl + sold.amount_out - paid
    return sniper_exit.amount_out - sniper.amount_in, retail_pnl


def zero_pvp(config, sniper_sol, retail_buys):
    state = zp.RouterState(pool=dbc.PoolState(sqrt_price=config.sqrt_start_price))
    state, sniper = zp.router_buy(state, config, "sniper", sniper_sol, "avg", DUST_PER_BUY)
    paid = {}
    for i, amount in enumerate(retail_buys):
        if dbc.buy(state.pool, config, amount).pool.is_graduated(config):
            continue  # same order flow as plain_dbc
        state, outcome = zp.router_buy(state, config, f"r{i}", amount, "avg", DUST_PER_BUY)
        paid[f"r{i}"] = outcome.amount_in
    tokens = state.tokens_of("sniper")
    state, receipt = zp.router_exit(state, config, "sniper", tokens, "avg", HAIRCUT_PER_EXIT)
    retail_pnl = 0
    for user, amount_paid in paid.items():
        tokens = state.tokens_of(user)
        state, r = zp.router_exit(state, config, user, tokens, "avg", HAIRCUT_PER_EXIT)
        retail_pnl += r["refund"] - amount_paid
    return receipt["refund"] - sniper.amount_in, retail_pnl, state.min_buffer


def main() -> None:
    rng = random.Random(42)
    rows = []
    for _ in range(RUNS):
        config = build_curve(
            rng, segments=16, start_mcap_sol=28, price_multiplier=17, migration_quote_sol=85,
        )
        sniper_sol = int(rng.uniform(1, 5) * LAMPORTS_PER_SOL)
        retail = [int(10 ** rng.uniform(-1.5, 0.5) * LAMPORTS_PER_SOL) for _ in range(60)]
        plain = plain_dbc(config, sniper_sol, retail)
        zero = zero_pvp(config, sniper_sol, retail)
        rows.append((plain, zero))

    def avg(values):
        return sum(values) / len(values) / LAMPORTS_PER_SOL

    print(f"{RUNS} runs, 1% fee, 85 SOL threshold, sniper buys first, 60 retail buys")
    print(f"  plain DBC  sniper PnL avg : {avg([p[0] for p, _ in rows]):+.4f} SOL")
    print(f"  plain DBC  retail PnL avg : {avg([p[1] for p, _ in rows]):+.4f} SOL")
    print(f"  Zero-PvP   sniper PnL avg : {avg([z[0] for _, z in rows]):+.4f} SOL")
    print(f"  Zero-PvP   retail PnL avg : {avg([z[1] for _, z in rows]):+.4f} SOL")
    print(f"  Zero-PvP   sniper max PnL : {max(z[0] for _, z in rows) / LAMPORTS_PER_SOL:+.6f} SOL")
    print(f"  Zero-PvP   min buffer     : {min(z[2] for _, z in rows)} lamports")


if __name__ == "__main__":
    main()
