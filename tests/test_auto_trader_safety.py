import unittest
from decimal import Decimal

from auto_trader import (
    active_positions,
    order_size,
    position_exposure_usdt,
    symbol_has_position,
    spread_pct,
    signal_shadow_stats,
    load_config,
)


class AutoTraderSafetyHelpersTests(unittest.TestCase):
    def test_active_positions_ignore_zero(self):
        rows = [{"symbol": "BTCUSDT", "total": "0"}, {"symbol": "ETHUSDT", "total": "2"}]
        self.assertEqual(len(active_positions(rows)), 1)

    def test_symbol_existing_position(self):
        rows = [{"symbol": "BTCUSDT", "total": "0.01", "markPrice": "100"}]
        self.assertTrue(symbol_has_position(rows, "BTCUSDT"))
        self.assertFalse(symbol_has_position(rows, "ETHUSDT"))

    def test_gross_exposure_uses_exchange_positions(self):
        rows = [
            {"symbol": "BTCUSDT", "total": "2", "markPrice": "100"},
            {"symbol": "ETHUSDT", "total": "3", "markPrice": "50"},
        ]
        self.assertEqual(position_exposure_usdt(rows), Decimal("350"))

    def test_order_size_respects_min_qty(self):
        cfg = {"minTradeNum": "0.01", "sizeMultiplier": "0.001", "minTradeUSDT": "1", "volumePlace": "3"}
        self.assertEqual(order_size(cfg, Decimal("1000"), Decimal("1")), "0.010")

    def test_order_size_rounds_up_step(self):
        cfg = {"minTradeNum": "0.001", "sizeMultiplier": "0.001", "minTradeUSDT": "0", "volumePlace": "3"}
        self.assertEqual(order_size(cfg, Decimal("100"), Decimal("10.01")), "0.101")

    def test_frozen_long3_risk_config(self):
        cfg = load_config()
        self.assertEqual(cfg["active_portfolio"], "LONG3")
        self.assertEqual(cfg["enabled_strategies"], ["L1", "L2", "L3"])
        self.assertEqual(cfg["position_size_pct"], 30.0)
        self.assertEqual(cfg["max_total_exposure_pct"], 200.0)
        self.assertEqual(cfg["max_open_positions"], 6)

    def test_spread_pct_uses_bid_ask_midpoint(self):
        self.assertAlmostEqual(
            spread_pct(Decimal("99"), Decimal("101")),
            2.0,
            places=9,
        )

    def test_signal_shadow_stats_tracks_cumulative(self):
        state = {
            "signal_shadow_closed": [
                {"closed_at_ms": 1, "signal_id": "a", "shadow_return_pct": 10.0},
                {"closed_at_ms": 2, "signal_id": "b", "shadow_return_pct": -5.0},
            ]
        }
        stats = signal_shadow_stats(state, {"position_size_pct": 30.0})
        self.assertEqual(stats["valid"], 2)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["losses"], 1)
        self.assertAlmostEqual(stats["win_rate_pct"], 50.0)
        self.assertAlmostEqual(stats["pf"], 2.0)
        self.assertAlmostEqual(stats["avg_return_pct"], 2.5)
        self.assertAlmostEqual(stats["weighted_compounded_pct"], 1.455, places=6)

    def test_repository_is_demo_and_live_disabled(self):
        cfg = load_config()
        self.assertEqual(cfg["trading_mode"], "DEMO")
        self.assertFalse(cfg["live_trading_enabled"])
        self.assertIsInstance(cfg["demo_auto_execute"], bool)


if __name__ == "__main__":
    unittest.main()
