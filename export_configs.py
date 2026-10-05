"""Exports random DBC curve configs for the LiteSVM differential test.

Usage: python3 export_configs.py OUT.json [--count N] [--seed S]
"""

import argparse
import json
import random

from curve_factory import random_curve
from dbc_curve import CurveConfig, delta_quote

U128_MAX = (1 << 128) - 1
THRESHOLD_PERCENT_OF_CURVE = 97  # keep migration price strictly inside the last segment


def total_curve_quote(config: CurveConfig) -> int:
    lower, total = config.sqrt_start_price, 0
    for point in config.curve:
        total += delta_quote(lower, point.sqrt_price, point.liquidity, False)
        lower = point.sqrt_price
    return total


def to_scenario(seed: int, config: CurveConfig) -> dict:
    if any(p.liquidity > U128_MAX for p in config.curve):
        raise ValueError("liquidity exceeds u128")
    return {
        "seed": seed,
        "sqrt_start_price": str(config.sqrt_start_price),
        "fee_numerator": config.fee_numerator,
        "migration_quote_threshold": str(
            total_curve_quote(config) * THRESHOLD_PERCENT_OF_CURVE // 100
        ),
        "curve": [
            {"sqrt_price": str(p.sqrt_price), "liquidity": str(p.liquidity)} for p in config.curve
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    scenarios = []
    for i in range(args.count):
        seed = args.seed + i
        config = random_curve(random.Random(seed))
        if config.fee_numerator == 0:  # DBC enforces MIN_FEE_NUMERATOR (25 bps)
            config = CurveConfig(config.sqrt_start_price, config.curve, 2_500_000)
        scenarios.append(to_scenario(seed, config))

    with open(args.out, "w") as handle:
        json.dump(scenarios, handle, indent=1)
    print(f"wrote {len(scenarios)} scenarios to {args.out}")


if __name__ == "__main__":
    main()
