"""Zero-PvP router: pre-graduation, every exit is refunded at cost basis.

Model of the on-chain design:
  - A transfer hook forces every pre-graduation buy/sell through this router,
    and buys land in per-user escrow (so cost basis is exact and complete).
  - buy: user pays `amount_in`; the net quote that entered the curve is
    recorded as the user's cost basis (the buy fee is the user's cost).
  - exit: router sells the user's tokens into the curve and receives
    `net = gross - sell_fee`; the user is paid `refund_for(basis_part)`.
    `net - refund` goes to (or comes from) the router buffer.

Refund rule: basis - fee_at_cost - haircut (user pays the fee as if selling at cost;
haircut absorbs DBC's pool-favoured rounding dust).

Claim under test: buffer >= 0 at every step, for every curve and ordering.
Exact identity: sum(gross - basis) == sum(remaining basis) - quote_reserve.
"""

from dataclasses import dataclass, field, replace
from types import MappingProxyType

import dbc_curve as dbc

LOT_MODES = ("avg", "fifo", "lifo")


@dataclass(frozen=True)
class Lot:
    tokens: int
    basis: int


@dataclass(frozen=True)
class RouterState:
    pool: dbc.PoolState
    positions: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    buffer: int = 0
    min_buffer: int = 0
    paid_out: int = 0  # total refunds paid to users
    # sum over exits of (gross - basis); the quantity the solvency theorem bounds
    theorem_gap: int = 0
    dust_reserved: int = 0  # basis withheld at buy time to cover rounding

    def total_basis(self) -> int:
        return sum(lot.basis for lots in self.positions.values() for lot in lots)

    def tokens_of(self, user: str) -> int:
        return sum(lot.tokens for lot in self.positions.get(user, ()))


def _with_position(positions, user, lots):
    updated = dict(positions)
    if lots:
        updated[user] = tuple(lots)
    else:
        updated.pop(user, None)
    return MappingProxyType(updated)


def router_buy(
    state: RouterState,
    config: dbc.CurveConfig,
    user: str,
    amount_in: int,
    mode: str,
    dust_per_buy: int = 0,
):
    """Records basis = net quote into the curve minus a fixed rounding-dust reserve."""
    outcome = dbc.buy(state.pool, config, amount_in)
    if outcome.amount_out == 0:
        return state, outcome
    lots = list(state.positions.get(user, ()))
    dust = min(dust_per_buy, outcome.reserve_delta)
    new_lot = Lot(tokens=outcome.amount_out, basis=outcome.reserve_delta - dust)
    if mode == "avg" and lots:
        merged = Lot(lots[0].tokens + new_lot.tokens, lots[0].basis + new_lot.basis)
        lots = [merged]
    else:
        lots.append(new_lot)
    new_state = replace(
        state,
        pool=outcome.pool,
        positions=_with_position(state.positions, user, lots),
        dust_reserved=state.dust_reserved + dust,
    )
    return new_state, outcome


def _consume_lots(lots, tokens: int, mode: str):
    """Returns (basis_consumed, remaining_lots). Partial lots are pro-rated, floor."""
    order = list(reversed(lots)) if mode == "lifo" else list(lots)
    remaining_to_take = tokens
    basis_taken = 0
    kept = []
    for lot in order:
        if remaining_to_take == 0:
            kept.append(lot)
            continue
        take = min(lot.tokens, remaining_to_take)
        if take == lot.tokens:
            basis_part = lot.basis
        else:
            basis_part = lot.basis * take // lot.tokens
            kept.append(Lot(lot.tokens - take, lot.basis - basis_part))
        basis_taken += basis_part
        remaining_to_take -= take
    if remaining_to_take:
        raise ValueError("exit exceeds position")
    if mode == "lifo":
        kept = list(reversed(kept))
    return basis_taken, kept


def refund_for(basis_part: int, fee_numerator: int, haircut: int) -> int:
    """User receives basis minus the fee they would pay selling *at cost*, minus a dust haircut."""
    fee_at_cost = dbc.mul_div(basis_part, fee_numerator, dbc.FEE_DENOMINATOR, True)
    return max(basis_part - fee_at_cost - haircut, 0)


def router_exit(
    state: RouterState,
    config: dbc.CurveConfig,
    user: str,
    tokens: int,
    mode: str,
    haircut: int = 0,
):
    basis_part, kept = _consume_lots(state.positions.get(user, ()), tokens, mode)
    outcome = dbc.sell(state.pool, config, tokens)
    gross = -outcome.reserve_delta
    refund = refund_for(basis_part, config.fee_numerator, haircut)
    buffer = state.buffer + outcome.amount_out - refund
    new_state = replace(
        state,
        pool=outcome.pool,
        positions=_with_position(state.positions, user, kept),
        buffer=buffer,
        min_buffer=min(state.min_buffer, buffer),
        paid_out=state.paid_out + refund,
        theorem_gap=state.theorem_gap + gross - basis_part,
    )
    receipt = {"gross": gross, "refund": refund, "basis": basis_part, "fee": outcome.fee}
    return new_state, receipt


def check_identity(state: RouterState) -> int:
    """Drift of: sum(gross - basis) == total_basis + dust_reserved - quote_reserve."""
    expected = state.total_basis() + state.dust_reserved - state.pool.quote_reserve
    return state.theorem_gap - expected
