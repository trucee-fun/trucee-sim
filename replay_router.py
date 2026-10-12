"""Replays the on-chain Zero-PvP router run (litesvm-diff/src/zpvp_e2e.ts) through sim/zero_pvp.py.

Usage: python3 replay_router.py E2E.json HARNESS_RESULTS.json

Compares, per op: tokens received on buy, refund paid on sell, and the trader's
recorded basis afterwards. Also reports the modeled router buffer.
"""

import json
import sys

import dbc_curve as dbc
import zero_pvp as zp
from replay_diff import config_from_onchain

DUST_PER_BUY = 16
HAIRCUT_PER_EXIT = 2
def initial_buy(threshold: int) -> dict:
    """Mirrors check #2 in zpvp_e2e.ts: min(1 quote, threshold / 20)."""
    return {"kind": "buy", "trader": 0, "amount": str(min(10**9, threshold // 20)), "ok": True}


def main() -> None:
    e2e = json.load(open(sys.argv[1]))
    harness = {r["seed"]: r for r in json.load(open(sys.argv[2]))}
    config = config_from_onchain(harness[e2e["seed"]]["onchain"])
    state = zp.RouterState(pool=dbc.PoolState(sqrt_price=config.sqrt_start_price))

    mismatches, compared = [], 0
    first = initial_buy(int(harness[e2e["seed"]]["onchain"]["migration_quote_threshold"]))
    for index, op in enumerate([first] + e2e["ops"]):
        if not op["ok"]:
            continue
        user, amount = f"t{op['trader']}", int(op["amount"])
        if op["kind"] == "buy":
            state, outcome = zp.router_buy(state, config, user, amount, "avg", DUST_PER_BUY)
            port = {"tokens_out": outcome.amount_out}
        else:
            state, receipt = zp.router_exit(state, config, user, amount, "avg", HAIRCUT_PER_EXIT)
            port = {"refund": receipt["refund"]}
        port["basis_after"] = sum(lot.basis for lot in state.positions.get(user, ()))
        for key, value in port.items():
            if key in op and int(op[key]) != value:
                mismatches.append(f"op {index} {op['kind']} {key}: chain={op[key]} port={value}")
        compared += 1

    print(f"router ops compared: {compared}  mismatches: {len(mismatches)}")
    for line in mismatches[:10]:
        print("  " + line)
    print(f"port buffer after sequence: {state.buffer} lamports  (min ever: {state.min_buffer})")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
