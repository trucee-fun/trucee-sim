# Trucee simulator

A Python integer port of Meteora DBC swap math, a Zero-PvP refund-accounting model, and fuzz/replay tools. Everything runs with Python 3 and its standard library; no package installation or RPC access is needed for the recorded replays.

The router's floor holds until graduation: recorded basis minus the fee at cost (at most 1%) minus a 2-lamport haircut. Exits happen before graduation. The model reserves 16 lamports of rounding dust per buy when run with the commands below.

## Workspace

Clone the repositories side by side with these folder names so replay paths and companion commands resolve:

```sh
mkdir trucee && cd trucee
git clone https://github.com/trucee-fun/trucee-programs zpvp
git clone https://github.com/trucee-fun/trucee-sdk sdk
git clone https://github.com/trucee-fun/trucee-app app
git clone https://github.com/trucee-fun/trucee-sim sim
git clone https://github.com/trucee-fun/trucee-worker worker
git clone https://github.com/trucee-fun/trucee-litesvm-diff litesvm-diff
git clone https://github.com/trucee-fun/trucee-docs gitbook
cd sim
python3 -m unittest
```

The simulator itself works alone. Clone the companion `litesvm-diff` repository to use the recordings below or generate new on-program comparisons.

## Fuzz and replay

```sh
python3 run_fuzz.py --random 2000 --adversarial 300 --seed 90000 --dust-per-buy 16 --haircut 2
python3 replay_diff.py ../litesvm-diff/out/results.json
python3 replay_diff.py ../litesvm-diff/out/results_b.json
```

The recorded fuzz result is **13,800 runs, minimum buffer 0 lamports, no negative buffers**. The command runs 2,000 random and 300 adversarial sequences for each of three accounting modes (`avg`, `fifo`, `lifo`), both to graduation and with a full unwind: `(2000 + 300) × 3 × 2 = 13800`. The on-program router uses average-basis accounting; FIFO and LIFO are research comparisons.

The committed DBC recordings replay as:

```text
results.json:   scenarios: 60   swaps: 9000   exact matches: 9000
               mismatches: 0
results_b.json: scenarios: 100  swaps: 12000  exact matches: 12000
               mismatches: 0
```

That is **21,000 swaps matched**, including matching failures. Successful swaps must agree on trader quote/base deltas, resulting square-root price and quote reserve. These are deterministic comparisons against recorded LiteSVM executions of the DBC program, rather than a replay of real market order flow. Fuzzing checks the model's solvency claim; it is not a proof for all possible transactions.

## Files and additional commands

| File | Purpose |
| --- | --- |
| `dbc_curve.py` | DBC integer swap and rounding math, with quote-collected static fees. |
| `curve_factory.py` | Builds random piecewise-liquidity curves and computes total curve supply. |
| `zero_pvp.py` | Models basis, before-graduation refunds, the rounding reserve and buffer accounting. |
| `run_fuzz.py` | Searches random and adversarial sequences and reports minimum buffer and accounting drift. |
| `export_configs.py` | Exports curve configurations for the LiteSVM DBC harness. |
| `replay_diff.py` | Compares the DBC port with recorded DBC swaps. |
| `replay_router.py` | Compares recorded buys, before-graduation refunds and basis; reports the modeled buffer. |
| `sniper_report.py` | Compares plain DBC and Zero-PvP under the same synthetic sniper/retail order flow. |
| `test_sim.py` | Unit tests for DBC math, refunds and rounding reserves. |

Generate a small fresh comparison using the companion harness after building its program binaries as described in its [README](https://github.com/trucee-fun/trucee-litesvm-diff/blob/main/README.md):

```sh
python3 export_configs.py ../litesvm-diff/out/scenarios_local.json --count 3 --seed 90000
cd ../litesvm-diff
npx tsx src/harness.ts out/scenarios_local.json out/results_local.json 20
cd ../sim
python3 replay_diff.py ../litesvm-diff/out/results_local.json
python3 sniper_report.py
```

To compare a router run, provide its E2E JSON and a DBC harness result containing the same scenario seed:

```sh
python3 replay_router.py ../litesvm-diff/out/e2e_local.json ../litesvm-diff/out/results_tiny_local.json
```

The DBC port models static base fees collected in quote tokens; it does not model fee schedulers, rate limiters or volatility-based fees. The accounting model does not execute transfer hooks, account-ownership checks, launch caps or the full graduation/migration lifecycle. Its generated DBC configurations can include fees above the router's 1% cap. The [router v5 security review](https://github.com/trucee-fun/trucee-docs/blob/main/security/router-v5-review.md) covers the on-program checks and remaining limitations.

## License

Trucee-authored files are MIT under [LICENSE](https://github.com/trucee-fun/trucee-sim/blob/main/LICENSE). `dbc_curve.py` and `curve_factory.py` are DBC ports covered by Meteora's noncommercial terms and excluded from that MIT grant; see [NOTICE](https://github.com/trucee-fun/trucee-sim/blob/main/NOTICE) for the required upstream notice and restrictions.
