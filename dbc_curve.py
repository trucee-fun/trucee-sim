"""Faithful integer port of Meteora DBC bonding-curve swap math.

Source of truth: MeteoraAg/dynamic-bonding-curve
  - programs/dynamic-bonding-curve/src/curve.rs
  - programs/dynamic-bonding-curve/src/state/virtual_pool.rs
    (calculate_quote_to_base_from_amount_in, calculate_base_to_quote_from_amount_in,
     get_swap_result_from_partial_input, apply_swap_result)
  - programs/dynamic-bonding-curve/src/state/config.rs (get_fee_on_amount)

Rounding directions are copied exactly, because the solvency question we
are testing lives in the last few lamports. Fee mode modelled: QuoteToken
(buy = fee on quote input, sell = fee on quote output). Static base fee only
(no fee scheduler / rate limiter / dynamic fee).
"""

from dataclasses import dataclass, replace

RESOLUTION = 64
FEE_DENOMINATOR = 1_000_000_000
U64_MAX = (1 << 64) - 1
MIN_SQRT_PRICE = 4295048016
MAX_SQRT_PRICE = 79226673521066979257578248091


class InsufficientLiquidity(Exception):
    pass


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def mul_div(x: int, y: int, denominator: int, round_up: bool) -> int:
    prod = x * y
    return ceil_div(prod, denominator) if round_up else prod // denominator


def _check_u64(value: int) -> int:
    if value > U64_MAX:
        raise OverflowError("MathOverflow: result exceeds u64")
    return value


def delta_base(lower: int, upper: int, liquidity: int, round_up: bool) -> int:
    """Δbase = L * (√P_upper - √P_lower) / (√P_upper * √P_lower)"""
    return mul_div(liquidity, upper - lower, lower * upper, round_up)


def delta_quote(lower: int, upper: int, liquidity: int, round_up: bool) -> int:
    """Δquote = L * (√P_upper - √P_lower) >> 128"""
    prod = liquidity * (upper - lower)
    return ceil_div(prod, 1 << (RESOLUTION * 2)) if round_up else prod >> (RESOLUTION * 2)


def next_sqrt_from_base_in(sqrt_price: int, liquidity: int, amount: int) -> int:
    """√P' = √P * L / (L + Δx * √P), rounded up"""
    if amount == 0:
        return sqrt_price
    return mul_div(liquidity, sqrt_price, liquidity + amount * sqrt_price, True)


def next_sqrt_from_quote_in(sqrt_price: int, liquidity: int, amount: int) -> int:
    """√P' = √P + Δy / L, rounded down"""
    return sqrt_price + (amount << (RESOLUTION * 2)) // liquidity


@dataclass(frozen=True)
class CurvePoint:
    sqrt_price: int  # upper bound of this segment
    liquidity: int


@dataclass(frozen=True)
class CurveConfig:
    sqrt_start_price: int
    curve: tuple  # tuple[CurvePoint, ...], strictly increasing sqrt_price
    fee_numerator: int  # out of FEE_DENOMINATOR
    # On-chain configs derive this from migration_quote_threshold; default = end of curve.
    migration_sqrt_override: int | None = None
    # On-chain completion rule (process_swap.rs): quote_reserve >= migration_quote_threshold.
    migration_quote_threshold: int | None = None

    @property
    def migration_sqrt_price(self) -> int:
        if self.migration_sqrt_override is not None:
            return self.migration_sqrt_override
        return self.curve[-1].sqrt_price


@dataclass(frozen=True)
class PoolState:
    sqrt_price: int
    quote_reserve: int = 0
    base_sold: int = 0
    fees_collected: int = 0

    def is_graduated(self, config: CurveConfig) -> bool:
        if config.migration_quote_threshold is not None:
            return self.quote_reserve >= config.migration_quote_threshold
        return self.sqrt_price >= config.migration_sqrt_price


@dataclass(frozen=True)
class SwapOutcome:
    pool: PoolState
    amount_in: int  # included-fee amount actually consumed from the trader
    amount_out: int  # amount actually delivered to the trader
    reserve_delta: int  # signed change of pool.quote_reserve
    fee: int


def quote_to_base(sqrt_price: int, config: CurveConfig, amount_in: int, stop_sqrt: int):
    """Port of calculate_quote_to_base_from_amount_in. Returns (out, next_sqrt, left)."""
    total_out = 0
    current = sqrt_price
    left = amount_in
    for point in config.curve:
        reference = min(stop_sqrt, point.sqrt_price)
        if reference <= current:
            continue
        max_in = delta_quote(current, reference, point.liquidity, True)
        if left < max_in:
            nxt = next_sqrt_from_quote_in(current, point.liquidity, left)
            total_out += _check_u64(delta_base(current, nxt, point.liquidity, False))
            return total_out, nxt, 0
        total_out += _check_u64(delta_base(current, reference, point.liquidity, False))
        current = reference
        left -= max_in
        if current == stop_sqrt:
            break
    return total_out, current, left


def base_to_quote(sqrt_price: int, config: CurveConfig, amount_in: int):
    """Port of calculate_base_to_quote_from_amount_in. Returns (out, next_sqrt, left)."""
    curve = config.curve
    total_out = 0
    current = sqrt_price
    left = amount_in
    for i in reversed(range(len(curve) - 1)):
        lower = curve[i].sqrt_price
        if lower >= current:
            continue
        liquidity = curve[i + 1].liquidity
        max_in = delta_base(lower, current, liquidity, True)
        if left < max_in:
            nxt = next_sqrt_from_base_in(current, liquidity, left)
            total_out += _check_u64(delta_quote(nxt, current, liquidity, False))
            return total_out, nxt, 0
        total_out += _check_u64(delta_quote(lower, current, liquidity, False))
        current = lower
        left -= max_in
    if left != 0:
        liquidity = curve[0].liquidity
        nxt = next_sqrt_from_base_in(current, liquidity, left)
        if nxt < config.sqrt_start_price:
            nxt = config.sqrt_start_price
            left -= delta_base(nxt, current, liquidity, True)
        else:
            left = 0
        total_out += _check_u64(delta_quote(nxt, current, liquidity, False))
        current = nxt
    return total_out, current, left


def _fee_on(amount: int, fee_numerator: int) -> int:
    return mul_div(amount, fee_numerator, FEE_DENOMINATOR, True)


def buy(pool: PoolState, config: CurveConfig, amount_in: int) -> SwapOutcome:
    """Quote -> base, partial fill at the migration price (swap mode PartialFill)."""
    if pool.is_graduated(config):
        raise InsufficientLiquidity("PoolIsCompleted")
    fee = _fee_on(amount_in, config.fee_numerator)
    excluded = amount_in - fee
    out, nxt, left = quote_to_base(pool.sqrt_price, config, excluded, config.migration_sqrt_price)
    included = amount_in
    if left:
        excluded -= left
        included = mul_div(excluded, FEE_DENOMINATOR, FEE_DENOMINATOR - config.fee_numerator, True)
        fee = included - excluded
    new_pool = replace(
        pool,
        sqrt_price=nxt,
        quote_reserve=pool.quote_reserve + excluded,
        base_sold=pool.base_sold + out,
        fees_collected=pool.fees_collected + fee,
    )
    return SwapOutcome(new_pool, included, out, excluded, fee)


def sell(pool: PoolState, config: CurveConfig, base_in: int) -> SwapOutcome:
    """Base -> quote, exact input. Fee is taken from the quote output."""
    if pool.is_graduated(config):
        raise InsufficientLiquidity("PoolIsCompleted")
    gross, nxt, left = base_to_quote(pool.sqrt_price, config, base_in)
    if left != 0:
        raise InsufficientLiquidity("sell exceeds curve")
    fee = _fee_on(gross, config.fee_numerator)
    net = gross - fee
    new_pool = replace(
        pool,
        sqrt_price=nxt,
        quote_reserve=pool.quote_reserve - gross,
        base_sold=pool.base_sold - base_in,
        fees_collected=pool.fees_collected + fee,
    )
    return SwapOutcome(new_pool, base_in, net, -gross, fee)
