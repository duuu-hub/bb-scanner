import unittest
from unittest.mock import patch

from market_data.contract_filters import (
    active_symbols_from_contracts,
    is_active_usdt_perpetual,
    is_rwa_contract,
)


class ContractFilterTests(unittest.TestCase):
    def setUp(self):
        self.crypto = {
            "symbol": "BTCUSDT",
            "symbolType": "perpetual",
            "symbolStatus": "normal",
            "quoteCoin": "USDT",
            "isRwa": "NO",
        }
        self.rwa = {
            "symbol": "AAPLUSDT",
            "symbolType": "perpetual",
            "symbolStatus": "normal",
            "quoteCoin": "USDT",
            "isRwa": "YES",
        }

    def test_crypto_is_included_by_default(self):
        self.assertTrue(is_active_usdt_perpetual(self.crypto))

    def test_rwa_is_excluded_by_default(self):
        self.assertTrue(is_rwa_contract(self.rwa))
        self.assertFalse(is_active_usdt_perpetual(self.rwa))

    def test_rwa_can_be_explicitly_included(self):
        self.assertTrue(is_active_usdt_perpetual(self.rwa, include_rwa=True))

    def test_non_normal_non_usdt_and_non_perpetual_are_excluded(self):
        for patch in (
            {"symbolStatus": "offline"},
            {"quoteCoin": "USDC"},
            {"symbolType": "delivery"},
        ):
            row = dict(self.crypto)
            row.update(patch)
            self.assertFalse(is_active_usdt_perpetual(row))

    def test_symbol_list_is_sorted_deduplicated_and_crypto_only(self):
        rows = [
            self.rwa,
            self.crypto,
            dict(self.crypto),
            {
                **self.crypto,
                "symbol": "ETHUSDT",
            },
        ]
        self.assertEqual(
            active_symbols_from_contracts(rows),
            ["BTCUSDT", "ETHUSDT"],
        )

    def test_scanner_and_auto_backtest_use_same_crypto_only_filter(self):
        import scanner
        import backtest

        rows = [
            self.rwa,
            self.crypto,
            {**self.crypto, "symbol": "ETHUSDT"},
        ]

        with patch("scanner.api_get", return_value=rows):
            self.assertEqual(scanner.get_symbols(), ["BTCUSDT", "ETHUSDT"])

        with patch("backtest.api_get", return_value=rows):
            self.assertEqual(backtest.get_active_symbols(), ["BTCUSDT", "ETHUSDT"])

        with patch("backtest.get_active_symbols", return_value=["BTCUSDT", "ETHUSDT"]):
            chosen = backtest.resolve_symbols("AUTO100")
            self.assertEqual(chosen, ["BTCUSDT", "ETHUSDT"])


    def test_market_regime_excludes_rwa_and_major_bases(self):
        import market_regime

        rows = [
            self.rwa,
            self.crypto,
            {**self.crypto, "symbol": "ETHUSDT", "baseCoin": "ETH"},
            {**self.crypto, "symbol": "ALLOUSDT", "baseCoin": "ALLO"},
        ]
        rows[0]["baseCoin"] = "AAPL"
        rows[1]["baseCoin"] = "BTC"

        with patch("market_regime.api_get", return_value=rows):
            symbols, meta = market_regime.contract_universe()

        self.assertEqual(symbols, ["ALLOUSDT"])
        self.assertEqual(meta["rwa_excluded_count"], 1)
        self.assertEqual(meta["active_usdt_perpetual_count"], 4)
        self.assertEqual(meta["universe_scope"], "crypto_alt_usdt_perpetual")


if __name__ == "__main__":
    unittest.main()
