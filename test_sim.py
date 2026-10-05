import random
import unittest

import dbc_curve as dbc
import zero_pvp as zp
from curve_factory import LAMPORTS_PER_SOL, build_curve, random_curve


def fresh(config):
    return zp.RouterState(pool=dbc.PoolState(sqrt_price=config.sqrt_start_price))


class DbcPortTest(unittest.TestCase):
    def setUp(self):
        self.config = build_curve(
            random.Random(1), segments=16, start_mcap_sol=28,
            price_multiplier=17, migration_quote_sol=85,
        )
        self.pool = dbc.PoolState(sqrt_price=self.config.sqrt_start_price)

    def test_round_trip_never_returns_more_than_paid(self):
        rng = random.Random(7)
        for _ in range(500):
            amount = rng.randint(1, 5 * LAMPORTS_PER_SOL)
            bought = dbc.buy(self.pool, self.config, amount)
            sold = dbc.sell(bought.pool, self.config, bought.amount_out)
            self.assertLessEqual(sold.amount_out, amount)
            self.assertGreaterEqual(sold.pool.quote_reserve, 0)

    def test_price_increases_with_buys(self):
        first = dbc.buy(self.pool, self.config, LAMPORTS_PER_SOL)
        second = dbc.buy(first.pool, self.config, LAMPORTS_PER_SOL)
        self.assertGreater(first.amount_out, second.amount_out)

    def test_partial_fill_stops_at_migration_price(self):
        whale = dbc.buy(self.pool, self.config, 10_000 * LAMPORTS_PER_SOL)
        self.assertTrue(whale.pool.is_graduated(self.config))
        self.assertLess(whale.amount_in, 10_000 * LAMPORTS_PER_SOL)
        self.assertAlmostEqual(whale.pool.quote_reserve / LAMPORTS_PER_SOL, 85, delta=0.01)

    def test_full_unwind_drains_reserve_to_dust(self):
        rng = random.Random(3)
        pool, holdings = self.pool, []
        for _ in range(50):
            outcome = dbc.buy(pool, self.config, rng.randint(10**6, 2 * LAMPORTS_PER_SOL))
            pool, holdings = outcome.pool, holdings + [outcome.amount_out]
        for tokens in reversed(holdings):
            pool = dbc.sell(pool, self.config, tokens).pool
        self.assertEqual(pool.base_sold, 0)
        self.assertLess(pool.quote_reserve, 1_000)  # lamports of rounding dust


class RouterTest(unittest.TestCase):
    def test_identity_holds_for_every_lot_mode(self):
        for mode in zp.LOT_MODES:
            rng = random.Random(11)
            config = random_curve(rng)
            state = fresh(config)
            for step in range(300):
                user = f"u{rng.randint(0, 9)}"
                held = state.tokens_of(user)
                if held and rng.random() < 0.4:
                    state, _ = zp.router_exit(state, config, user, rng.randint(1, held), mode)
                elif not state.pool.is_graduated(config):
                    state, _ = zp.router_buy(state, config, user, rng.randint(10**5, 3 * 10**9), mode)
                self.assertEqual(zp.check_identity(state), 0, f"{mode} step {step}")

    def test_early_exit_never_profits(self):
        config = build_curve(random.Random(2), segments=8, start_mcap_sol=28,
                             price_multiplier=17, migration_quote_sol=85)
        state = fresh(config)
        state, sniper_buy = zp.router_buy(state, config, "sniper", 2 * LAMPORTS_PER_SOL, "avg")
        for i in range(40):
            state, _ = zp.router_buy(state, config, f"retail{i}", LAMPORTS_PER_SOL, "avg")
        state, receipt = zp.router_exit(state, config, "sniper", state.tokens_of("sniper"), "avg")
        self.assertGreater(receipt["gross"], sniper_buy.amount_in)  # curve would have paid a profit
        self.assertLessEqual(receipt["refund"], sniper_buy.amount_in)  # router pays cost only


DUST_PER_BUY = 16
HAIRCUT_PER_EXIT = 2


class SolvencyTest(unittest.TestCase):
    def test_tiny_slice_exit_attack_cannot_drain_buffer(self):
        for seed in range(60):
            rng = random.Random(seed)
            config = random_curve(rng)
            state = fresh(config)
            for user in ["u0", "u1", "u2", "atk"]:
                if state.pool.is_graduated(config):
                    break
                amount = int(10 ** rng.uniform(-1, 0.5) * LAMPORTS_PER_SOL)
                state, _ = zp.router_buy(state, config, user, amount, "avg", DUST_PER_BUY)
            for _ in range(1500):
                held = state.tokens_of("atk")
                if not held:
                    break
                slice_size = min(held, rng.randint(1, 10**6))
                state, _ = zp.router_exit(state, config, "atk", slice_size, "avg", HAIRCUT_PER_EXIT)
            for user in list(state.positions):
                state, _ = zp.router_exit(
                    state, config, user, state.tokens_of(user), "avg", HAIRCUT_PER_EXIT
                )
            self.assertGreaterEqual(state.min_buffer, 0, f"seed {seed}")

    def test_random_orderings_stay_solvent(self):
        from run_fuzz import simulate

        for seed in range(80):
            for mode in zp.LOT_MODES:
                result = simulate(seed, mode, False, True, HAIRCUT_PER_EXIT, DUST_PER_BUY)
                self.assertGreaterEqual(result.min_buffer, 0, f"{mode} seed {seed}")
                self.assertEqual(result.identity_drift, 0)


if __name__ == "__main__":
    unittest.main()
