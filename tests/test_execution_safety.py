import unittest
from datetime import datetime, timedelta, timezone

from execution_safety import evaluate_signal


NOW = datetime(2026, 9, 23, 1, 30, tzinfo=timezone.utc)


def base_config():
    return {
        "active_regime": "BULL",
        "signal_ttl_minutes": 3,
        "position_fraction": 0.30,
        "max_total_exposure_fraction": 2.0,
        "max_spread_pct": 0.20,
        "tp_touch_policy": "reject",
        "sl_recovery_policy": "reject",
        "ambiguous_bar_policy": "reject",
        "require_position_snapshot": True,
        "demo_only": True,
    }


def base_signal():
    return {
        "signal_id": "sig-001",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "signal_time": (NOW - timedelta(minutes=1)).isoformat(),
        "entry_low": 99.0,
        "entry_high": 101.0,
        "tp": 110.0,
        "sl": 95.0,
        "max_hold_minutes": 720,
        "strategy_name": "BULL_TEST",
        "regime": "BULL",
    }


def base_market():
    return {
        "current_price": 100.0,
        "bid": 99.98,
        "ask": 100.02,
        "candles_1m_since_signal": [
            {"high": 101.0, "low": 99.5},
        ],
    }


def base_account():
    return {
        "equity_usdt": 1000.0,
        "gross_exposure_usdt": 900.0,
        "positions_verified": True,
        "positions": [],
    }


class SafetyEngineTests(unittest.TestCase):
    def evaluate(self, signal=None, market=None, account=None, state=None, config=None):
        return evaluate_signal(
            signal or base_signal(),
            market or base_market(),
            account or base_account(),
            state or {"seen_signal_ids": []},
            config or base_config(),
            now=NOW,
        )

    def test_valid_signal_allowed(self):
        d = self.evaluate()
        self.assertTrue(d.allowed)
        self.assertEqual(d.code, "ALLOW")
        self.assertEqual(d.planned_notional_usdt, 300.0)

    def test_off_blocks_new_entry(self):
        cfg = base_config()
        cfg["active_regime"] = "OFF"
        self.assertEqual(self.evaluate(config=cfg).code, "TRADING_OFF")

    def test_regime_mismatch_blocks(self):
        cfg = base_config()
        cfg["active_regime"] = "RANGE"
        self.assertEqual(self.evaluate(config=cfg).code, "REGIME_MISMATCH")

    def test_stale_signal_blocks(self):
        sig = base_signal()
        sig["signal_time"] = (NOW - timedelta(minutes=4)).isoformat()
        self.assertEqual(self.evaluate(signal=sig).code, "STALE_SIGNAL")

    def test_outside_entry_range_blocks(self):
        market = base_market()
        market["current_price"] = 102.0
        market["bid"] = 101.98
        market["ask"] = 102.02
        self.assertEqual(self.evaluate(market=market).code, "OUTSIDE_ENTRY_RANGE")

    def test_tp_already_touched_blocks(self):
        market = base_market()
        market["candles_1m_since_signal"] = [{"high": 111.0, "low": 99.0}]
        self.assertEqual(self.evaluate(market=market).code, "TP_ALREADY_TOUCHED")

    def test_sl_recovery_blocks_by_default(self):
        market = base_market()
        market["candles_1m_since_signal"] = [{"high": 101.0, "low": 94.0}]
        self.assertEqual(self.evaluate(market=market).code, "SL_ALREADY_TOUCHED")

    def test_ambiguous_bar_blocks(self):
        market = base_market()
        market["candles_1m_since_signal"] = [{"high": 111.0, "low": 94.0}]
        self.assertEqual(self.evaluate(market=market).code, "AMBIGUOUS_PATH")

    def test_duplicate_blocks(self):
        self.assertEqual(
            self.evaluate(state={"seen_signal_ids": ["sig-001"]}).code,
            "DUPLICATE_SIGNAL",
        )

    def test_existing_symbol_position_blocks(self):
        account = base_account()
        account["positions"] = [{"symbol": "BTCUSDT", "size": 0.01}]
        self.assertEqual(self.evaluate(account=account).code, "POSITION_EXISTS")

    def test_unverified_position_snapshot_blocks(self):
        account = base_account()
        account["positions_verified"] = False
        self.assertEqual(
            self.evaluate(account=account).code,
            "POSITION_SNAPSHOT_UNVERIFIED",
        )

    def test_exposure_cap_blocks(self):
        account = base_account()
        account["gross_exposure_usdt"] = 1800.0
        self.assertEqual(self.evaluate(account=account).code, "EXPOSURE_LIMIT")

    def test_wide_spread_blocks(self):
        market = base_market()
        market["bid"] = 99.0
        market["ask"] = 101.0
        self.assertEqual(self.evaluate(market=market).code, "SPREAD_TOO_WIDE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
