"""Builds realistic multi-segment DBC curves for simulation.

Defaults mirror a typical meme launch: 1B supply (6 decimals), SOL quote
(9 decimals), ~85 SOL migration threshold, ~25 SOL starting market cap.
"""

import math
import random

from dbc_curve import CurveConfig, CurvePoint, MIN_SQRT_PRICE, MAX_SQRT_PRICE, delta_base

LAMPORTS_PER_SOL = 10**9
BASE_DECIMALS = 6
TOTAL_SUPPLY_RAW = 1_000_000_000 * 10**BASE_DECIMALS
MAX_SEGMENTS = 16
DEFAULT_FEE_NUMERATOR = 10_000_000  # 1%


def sqrt_price_q64(price_lamports_per_raw_base: float) -> int:
    return int(math.sqrt(price_lamports_per_raw_base) * (1 << 64))


def build_curve(
    rng: random.Random,
    *,
    segments: int,
    start_mcap_sol: float,
    price_multiplier: float,
    migration_quote_sol: float,
    fee_numerator: int = DEFAULT_FEE_NUMERATOR,
) -> CurveConfig:
    """Random segment weights, geometric sqrt-price grid, liquidity solved per segment."""
    if not 1 <= segments <= MAX_SEGMENTS:
        raise ValueError(f"segments must be 1..{MAX_SEGMENTS}")
    start_price = start_mcap_sol * LAMPORTS_PER_SOL / TOTAL_SUPPLY_RAW
    s0 = sqrt_price_q64(start_price)
    s_end = sqrt_price_q64(start_price * price_multiplier)
    if not (MIN_SQRT_PRICE < s0 < s_end < MAX_SQRT_PRICE):
        raise ValueError("sqrt price out of DBC bounds")

    ratio = (s_end / s0) ** (1 / segments)
    grid = [int(s0 * ratio**i) for i in range(segments)] + [s_end]
    # Liquidity multipliers around a constant-L (pump.fun-like) baseline.
    weights = [rng.uniform(0.3, 3.0) for _ in range(segments)]
    widths = [grid[i + 1] - grid[i] for i in range(segments)]
    total_quote = migration_quote_sol * LAMPORTS_PER_SOL
    base_liquidity = total_quote * (1 << 128) / sum(w * d for w, d in zip(weights, widths))

    points = tuple(
        CurvePoint(sqrt_price=grid[i + 1], liquidity=int(base_liquidity * weights[i]))
        for i in range(segments)
    )

    config = CurveConfig(sqrt_start_price=s0, curve=points, fee_numerator=fee_numerator)
    if total_base_on_curve(config) > TOTAL_SUPPLY_RAW:
        raise ValueError("curve sells more than total supply")
    return config


def total_base_on_curve(config: CurveConfig) -> int:
    lower = config.sqrt_start_price
    total = 0
    for point in config.curve:
        total += delta_base(lower, point.sqrt_price, point.liquidity, True)
        lower = point.sqrt_price
    return total


def random_curve(rng: random.Random) -> CurveConfig:
    while True:
        try:
            return build_curve(
                rng,
                segments=rng.randint(1, MAX_SEGMENTS),
                start_mcap_sol=rng.uniform(5, 60),
                price_multiplier=rng.uniform(2, 120),
                migration_quote_sol=rng.uniform(10, 400),
                fee_numerator=rng.choice([0, 2_500_000, 10_000_000, 50_000_000]),
            )
        except ValueError:
            continue
