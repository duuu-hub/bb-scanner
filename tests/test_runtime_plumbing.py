import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scanner
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
            st["pending_entries"] = [{"signal_id": "maker-1"}]
            save_trading_state(p, st)
            reread = load_trading_state(p)
            self.assertEqual(len(reread["processed_signal_ids"]), 5000)
            self.assertEqual(reread["processed_signal_ids"][-1], "5099")
            self.assertEqual(reread["pending_entries"][0]["signal_id"], "maker-1")


    def test_forward_scanner_default_uses_live_public_universe(self):
        live_contracts = [
            {
                "symbol": "BTCUSDT",
                "symbolType": "perpetual",
                "symbolStatus": "normal",
                "quoteCoin": "USDT",
                "isRwa": "NO",
            },
            {
                "symbol": "PLUMEUSDT",
                "symbolType": "perpetual",
                "symbolStatus": "normal",
                "quoteCoin": "USDT",
                "isRwa": "NO",
            },
        ]
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LONG3_DEMO_UNIVERSE_ONLY", None)
            with patch("scanner.api_get", return_value=live_contracts) as api_get:
                self.assertEqual(scanner.get_symbols(), ["BTCUSDT", "PLUMEUSDT"])
                api_get.assert_called_once_with(
                    "/api/v2/mix/market/contracts",
                    {"productType": "usdt-futures"},
                )

    def test_demo_scanner_universe_uses_authenticated_demo_catalog(self):
        demo_contracts = [
            {
                "symbol": "BTCUSDT",
                "symbolType": "perpetual",
                "symbolStatus": "normal",
                "quoteCoin": "USDT",
                "isRwa": "NO",
            },
            {
                "symbol": "rAAPLUSDT",
                "symbolType": "perpetual",
                "symbolStatus": "normal",
                "quoteCoin": "USDT",
                "isRwa": "YES",
            },
        ]
        with patch.dict(os.environ, {"LONG3_DEMO_UNIVERSE_ONLY": "1"}, clear=False):
            with patch("bitget_demo_lifecycle_test.BitgetDemoClassic") as client_cls:
                client_cls.return_value.private_get.return_value = demo_contracts
                self.assertEqual(scanner.get_symbols(), ["BTCUSDT"])
                client_cls.return_value.private_get.assert_called_once_with(
                    "/api/v2/mix/market/contracts",
                    {"productType": "usdt-futures"},
                )

    def test_demo_scanner_universe_fails_closed_when_empty(self):
        with patch.dict(os.environ, {"LONG3_DEMO_UNIVERSE_ONLY": "1"}, clear=False):
            with patch("bitget_demo_lifecycle_test.BitgetDemoClassic") as client_cls:
                client_cls.return_value.private_get.return_value = []
                with self.assertRaises(RuntimeError):
                    scanner.get_symbols()


if __name__ == "__main__":
    unittest.main()
