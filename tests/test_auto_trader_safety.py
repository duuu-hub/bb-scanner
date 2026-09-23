import unittest
from decimal import Decimal

from auto_trader import (
    active_positions,
    order_size,
    position_exposure_usdt,
    symbol_has_position,
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


if __name__ == "__main__":
    unittest.main()
