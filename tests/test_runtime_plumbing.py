import json
import tempfile
import unittest
from pathlib import Path

from signal_io import load_signal_jsonl, signal_from_dict
from trade_state import load_trading_state, save_trading_state


class RuntimePlumbingTests(unittest.TestCase):
    def sample(self):
        return {
            "signal_id": "BULL_L1:BTCUSDT:123",
            "strategy": "BULL",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "signal_time_ms": 123,
            "entry_min": 99,
            "entry_max": 101,
            "tp": 110,
            "sl": 95,
            "max_hold_minutes": 720,
        }

    def test_signal_contract(self):
        s = signal_from_dict(self.sample())
        self.assertEqual(s.strategy, "BULL")
        self.assertEqual(s.side, "LONG")

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
