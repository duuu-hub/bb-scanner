import unittest

from trade_guard import Candle, GuardConfig, Signal, validate_signal


class TradeGuardTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000_000
        self.sig = Signal(
            signal_id="LONG3:L1:BTCUSDT:1799999100000",
            portfolio="LONG3",
            strategy="L1",
            symbol="BTCUSDT",
            side="LONG",
            signal_time_ms=self.now - 60_000,
            detected_price=100.0,
            entry_min=99.0,
            entry_max=101.0,
            tp=110.0,
            sl=95.0,
            max_hold_minutes=720,
            scan_started_at="2027-01-15T07:59:00+00:00",
            signal_created_at="2027-01-15T08:00:00+00:00",
            matched_strategies=("L1",),
            selected_strategy="L1",
        )
        self.cfg = GuardConfig(
            active_portfolio="LONG3",
            enabled_strategies=("L1", "L2", "L3"),
            signal_ttl_seconds=300,
            max_spread_pct=0.20,
        )

    def run_guard(self, sig=None, price=100.0, bid=99.99, ask=100.01, candles=None, seen=None):
        return validate_signal(
            sig or self.sig,
            price,
            bid,
            ask,
            candles or [],
            self.cfg,
            seen_signal_ids=seen or set(),
            now_ms=self.now,
        )

    def test_ok(self):
        self.assertEqual(self.run_guard().reason, "OK")
        self.assertTrue(self.run_guard().allowed)

    def test_inactive_portfolio(self):
        s = Signal(**{**self.sig.__dict__, "portfolio": "OTHER"})
        self.assertEqual(self.run_guard(s).reason, "INACTIVE_PORTFOLIO")

    def test_disabled_strategy(self):
        s = Signal(**{**self.sig.__dict__, "strategy": "S1"})
        self.assertEqual(self.run_guard(s).reason, "DISABLED_STRATEGY")

    def test_long_only(self):
        s = Signal(**{**self.sig.__dict__, "side": "SHORT"})
        self.assertEqual(self.run_guard(s).reason, "LONG3_LONG_ONLY")

    def test_stale(self):
        s = Signal(**{**self.sig.__dict__, "signal_time_ms": self.now - 301_000})
        self.assertEqual(self.run_guard(s).reason, "STALE_SIGNAL")

    def test_entry_range(self):
        self.assertEqual(self.run_guard(price=102.0).reason, "PRICE_OUTSIDE_ENTRY_RANGE")

    def test_duplicate(self):
        self.assertEqual(
            self.run_guard(seen={self.sig.signal_id}).reason,
            "DUPLICATE_SIGNAL",
        )

    def test_spread(self):
        self.assertEqual(
            self.run_guard(bid=99.0, ask=101.0).reason,
            "SPREAD_TOO_WIDE",
        )

    def test_maker_allows_current_price_outside_range_and_wide_spread(self):
        cfg = GuardConfig(
            active_portfolio="LONG3",
            enabled_strategies=("L1", "L2", "L3"),
            signal_ttl_seconds=300,
            max_spread_pct=0.20,
            entry_mode="MAKER_LIMIT",
        )
        d = validate_signal(
            self.sig,
            105.0,
            104.0,
            106.0,
            [],
            cfg,
            seen_signal_ids=set(),
            now_ms=self.now,
        )
        self.assertTrue(d.allowed)
        self.assertEqual(d.reason, "OK")

    def test_tp_touched(self):
        bars = [Candle(self.now - 30_000, high=111.0, low=99.0)]
        self.assertEqual(self.run_guard(candles=bars).reason, "TP_ALREADY_TOUCHED")

    def test_sl_touched(self):
        bars = [Candle(self.now - 30_000, high=101.0, low=94.0)]
        self.assertEqual(self.run_guard(candles=bars).reason, "SL_ALREADY_TOUCHED")

    def test_ambiguous(self):
        bars = [Candle(self.now - 30_000, high=111.0, low=94.0)]
        self.assertEqual(
            self.run_guard(candles=bars).reason,
            "AMBIGUOUS_TP_SL_SAME_CANDLE",
        )


if __name__ == "__main__":
    unittest.main()
