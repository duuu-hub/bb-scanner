import unittest

from trade_guard import Candle, GuardConfig, Signal, validate_signal


class TradeGuardTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000_000
        self.sig = Signal(
            signal_id="BULL_L1:BTCUSDT:1",
            strategy="BULL",
            symbol="BTCUSDT",
            side="LONG",
            signal_time_ms=self.now - 60_000,
            entry_min=99.0,
            entry_max=101.0,
            tp=110.0,
            sl=95.0,
            max_hold_minutes=720,
        )
        self.cfg = GuardConfig(active_strategy="BULL", signal_ttl_seconds=300)

    def test_ok(self):
        d = validate_signal(self.sig, 100.0, [], self.cfg, now_ms=self.now)
        self.assertTrue(d.allowed)
        self.assertEqual(d.reason, "OK")

    def test_off(self):
        d = validate_signal(self.sig, 100.0, [], GuardConfig(active_strategy="OFF"), now_ms=self.now)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "TRADING_OFF")

    def test_inactive_strategy(self):
        d = validate_signal(self.sig, 100.0, [], GuardConfig(active_strategy="RANGE"), now_ms=self.now)
        self.assertEqual(d.reason, "INACTIVE_STRATEGY")

    def test_expired(self):
        old = Signal(**{**self.sig.__dict__, "signal_time_ms": self.now - 301_000})
        d = validate_signal(old, 100.0, [], self.cfg, now_ms=self.now)
        self.assertEqual(d.reason, "SIGNAL_EXPIRED")

    def test_entry_range(self):
        d = validate_signal(self.sig, 102.0, [], self.cfg, now_ms=self.now)
        self.assertEqual(d.reason, "PRICE_OUTSIDE_ENTRY_RANGE")

    def test_duplicate(self):
        d = validate_signal(
            self.sig, 100.0, [], self.cfg,
            seen_signal_ids={self.sig.signal_id},
            now_ms=self.now,
        )
        self.assertEqual(d.reason, "DUPLICATE_SIGNAL")

    def test_tp_already_touched(self):
        candles = [Candle(self.now - 30_000, high=111.0, low=99.5)]
        d = validate_signal(self.sig, 100.0, candles, self.cfg, now_ms=self.now)
        self.assertEqual(d.reason, "TP_ALREADY_TOUCHED")

    def test_sl_already_touched(self):
        candles = [Candle(self.now - 30_000, high=101.0, low=94.0)]
        d = validate_signal(self.sig, 100.0, candles, self.cfg, now_ms=self.now)
        self.assertEqual(d.reason, "SL_ALREADY_TOUCHED")

    def test_ambiguous_same_candle(self):
        candles = [Candle(self.now - 30_000, high=111.0, low=94.0)]
        d = validate_signal(self.sig, 100.0, candles, self.cfg, now_ms=self.now)
        self.assertEqual(d.reason, "AMBIGUOUS_TP_SL_SAME_CANDLE")


if __name__ == "__main__":
    unittest.main()
