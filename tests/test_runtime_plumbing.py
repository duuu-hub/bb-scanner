import json
import tempfile
import unittest
from pathlib import Path

from signal_io import load_signal_jsonl, signal_from_dict
from trade_state import load_trading_state, save_trading_state


class RuntimePlumbingTests(unittest.TestCase):
    def sample(self):
        return {
            "signal_id": "LONG3:L1:BTCUSDT:123",
            "portfolio": "LONG3",
            "strategy": "L1",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "signal_time_ms": 123,
            "detected_price": 100,
            "entry_min": 99,
            "entry_max": 101,
            "tp": 110,
            "sl": 95,
            "max_hold_minutes": 720,
            "scan_started_at": "2026-09-23T00:00:00+00:00",
            "signal_created_at": "2026-09-23T00:01:00+00:00",
            "matched_strategies": ["L1", "L2"],
            "selected_strategy": "L1",
            "market_snapshot": {"market_regime": "BULL"},
        }

    def test_signal_contract(self):
        s = signal_from_dict(self.sample())
        self.assertEqual((s.portfolio, s.strategy, s.side), ("LONG3", "L1", "LONG"))
        self.assertEqual(s.matched_strategies, ("L1", "L2"))

    def test_missing_signal_field_fails(self):
        row = self.sample()
        del row["tp"]
        with self.assertRaises(ValueError):
            signal_from_dict(row)

    def test_jsonl_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "signals.jsonl"
            p.write_text(json.dumps(self.sample()) + "\n", encoding="utf-8")
            rows = load_signal_jsonl(p)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].symbol, "BTCUSDT")

    def test_state_round_trip_and_bounds(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            st = load_trading_state(p)
            st["processed_signal_ids"] = [str(i) for i in range(5100)]
            save_trading_state(p, st)
            reread = load_trading_state(p)
            self.assertEqual(len(reread["processed_signal_ids"]), 5000)
            self.assertEqual(reread["processed_signal_ids"][-1], "5099")


if __name__ == "__main__":
    unittest.main()
