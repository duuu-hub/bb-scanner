import unittest

from long3_signal_adapter import choose_group, floor_15m, make_signal_id


class Long3SignalAdapterTests(unittest.TestCase):
    def row(self, strategy, all_matches):
        return {
            "strategy": strategy,
            "all_matches": all_matches,
            "entry_price": "100",
            "entry_low": "99",
            "entry_high": "101",
            "tp_price": "110",
            "sl_price": "95",
            "timestamp_utc": "2026-09-23T00:00:10+00:00",
        }

    def test_priority_same_boundary(self):
        c = choose_group([
            self.row("L3", "L3"),
            self.row("L2", "L2+L3"),
            self.row("L1", "L1+L2+L3"),
        ])
        self.assertEqual(c["selected"], "L1")
        self.assertEqual(c["new_matches"], ["L1", "L2", "L3"])

    def test_old_l1_does_not_suppress_new_l2(self):
        c = choose_group([self.row("L2", "L1+L2")])
        self.assertEqual(c["selected"], "L2")
        self.assertEqual(c["new_matches"], ["L2"])
        self.assertEqual(c["matches"], ["L1", "L2"])

    def test_boundary_and_deterministic_id(self):
        ts = 1_800_000_123_456
        boundary = floor_15m(ts)
        self.assertEqual(boundary % 900_000, 0)
        self.assertEqual(
            make_signal_id("l1", "btcusdt", boundary),
            f"LONG3:L1:BTCUSDT:{boundary}",
        )

    def test_non_long3_rows_not_selected(self):
        self.assertIsNone(choose_group([self.row("S1", "S1")]))


if __name__ == "__main__":
    unittest.main()
