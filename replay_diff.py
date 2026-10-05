"""Differential test: replays LiteSVM-recorded DBC swaps through the Python port.

Usage: python3 replay_diff.py RESULTS.json

Every successful on-chain swap must match the port exactly on: amount taken
from the trader, amount delivered, resulting sqrt_price and quote_reserve.
Every failed on-chain swap must also fail (or be a no-op) in the port.
"""

import json
import sys

import dbc_curve as dbc


def config_from_onchain(onchain: dict) -> dbc.CurveConfig:
    return dbc.CurveConfig(
        sqrt_start_price=int(onchain["sqrt_start_price"]),
        curve=tuple(
            dbc.CurvePoint(int(p["sqrt_price"]), int(p["liquidity"])) for p in onchain["curve"]
        ),
        fee_numerator=int(onchain["cliff_fee_numerator"]),
        migration_sqrt_override=int(onchain["migration_sqrt_price"]),
        migration_quote_threshold=int(onchain["migration_quote_threshold"]),
    )


def replay_op(pool: dbc.PoolState, config: dbc.CurveConfig, op: dict):
    """Returns (new_pool, mismatches list)."""
    amount = int(op["amount"])
    try:
        outcome = (dbc.buy if op["kind"] == "buy" else dbc.sell)(pool, config, amount)
    except (dbc.InsufficientLiquidity, OverflowError) as err:
        if op["ok"]:
            return pool, [f"port failed but chain succeeded: {err}"]
        return pool, []
    if not op["ok"]:
        return outcome.pool, [f"chain failed but port succeeded: {op.get('error', '')[:120]}"]

    if op["kind"] == "buy":
        port = {"quote_delta": -outcome.amount_in, "base_delta": outcome.amount_out}
    else:
        port = {"quote_delta": outcome.amount_out, "base_delta": -outcome.amount_in}
    port["sqrt_price"] = outcome.pool.sqrt_price
    port["quote_reserve"] = outcome.pool.quote_reserve

    mismatches = [
        f"{key}: chain={op[key]} port={value}"
        for key, value in port.items()
        if int(op[key]) != value
    ]
    return outcome.pool, mismatches


def main() -> None:
    results = json.load(open(sys.argv[1]))
    total_ops = matched = 0
    failures = []
    for scenario in results:
        if "setup_error" in scenario:
            failures.append((scenario["seed"], -1, scenario["setup_error"][:200]))
            continue
        config = config_from_onchain(scenario["onchain"])
        pool = dbc.PoolState(sqrt_price=config.sqrt_start_price)
        for index, op in enumerate(scenario["ops"]):
            total_ops += 1
            pool, mismatches = replay_op(pool, config, op)
            if mismatches:
                failures.append((scenario["seed"], index, "; ".join(mismatches)))
                # resync from chain so one divergence does not cascade
                if op["ok"]:
                    pool = dbc.PoolState(int(op["sqrt_price"]), int(op["quote_reserve"]))
            else:
                matched += 1

    print(f"scenarios: {len(results)}  swaps: {total_ops}  exact matches: {matched}")
    print(f"mismatches: {len(failures)}")
    for seed, index, detail in failures[:15]:
        print(f"  seed {seed} op {index}: {detail}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
