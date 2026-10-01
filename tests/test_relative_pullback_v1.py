"""Chronology, causal inputs, account arithmetic and loss-guard regressions."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts import relative_pullback_v1 as s
from scripts import relative_pullback_portfolio as p


def raw_prices(prices, start=0):
    c = np.asarray(prices, float)
    t = start + np.arange(len(c), dtype=np.int64) * s.BAR
    return (t, c.copy(), c + .1, c - .1, c.copy())


def trade(symbol="X", entry_time=0, exit_time=2 * s.BAR, side=1, entry=100., exit=101., sl=99.5):
    return {"symbol": symbol, "entry_time": entry_time, "exit_time": exit_time,
            "entry": entry, "exit": exit, "sl": sl, "tp": entry + side * 2 * abs(entry - sl),
            "side": side, "score": 1., "reason": "TP", "risk_pct": abs(entry - sl) / entry}


class ChronologyTests(unittest.TestCase):
    def resolve(self, touches, entry=True, side=1):
        t = np.arange(15, dtype=np.int64) * s.MINUTE
        o, h, l = [np.full(15, 100.) for _ in range(3)]
        for j, high, low in touches:
            h[j], l[j] = high, low
        tp, sl = (102., 98.) if side == 1 else (98., 102.)
        with patch.object(s.chronology, "w1m", return_value=(t, o, h, l)):
            return s.resolve_minutes("X", 0, 100., tp, sl, side, entry)

    def test_entry_minute_tp_is_loss(self):
        self.assertEqual(self.resolve([(0, 103, 100)])[0], "SL")

    def test_entry_minute_sl_is_loss(self):
        self.assertEqual(self.resolve([(0, 100, 97)])[0], "SL")

    def test_entry_minute_both_is_loss(self):
        self.assertEqual(self.resolve([(0, 103, 97)])[0], "SL")

    def test_later_tp_proven_before_sl(self):
        result = self.resolve([(2, 103, 100), (3, 100, 97)])
        self.assertEqual(result, ("TP", 3 * s.MINUTE, 102.))

    def test_later_sl_proven_before_tp(self):
        self.assertEqual(self.resolve([(2, 100, 97), (3, 103, 100)])[0], "SL")

    def test_established_same_minute_both_is_loss(self):
        self.assertEqual(self.resolve([(4, 103, 97)], entry=False)[0], "SL")

    def test_established_first_minute_tp_is_win(self):
        self.assertEqual(self.resolve([(0, 103, 100)], entry=False)[0], "TP")

    def test_short_entry_touch_is_loss(self):
        self.assertEqual(self.resolve([(0, 100, 97)], side=-1)[0], "SL")

    def test_short_later_tp_is_win(self):
        self.assertEqual(self.resolve([(3, 100, 97)], side=-1)[0], "TP")

    def test_official_data_gap_is_excluded(self):
        with patch.object(s.chronology, "w1m", return_value=("data_gap", "missing")):
            self.assertEqual(s.resolve_minutes("X", 0, 100, 102, 98, 1, True)[0], "DATA_GAP")

    def test_parent_exit_not_reproduced_is_mismatch(self):
        self.assertEqual(self.resolve([])[0], "EXIT_MISMATCH")

    def test_entry_price_mismatch_is_excluded(self):
        arr = np.full(15, 101.)
        with patch.object(s.chronology, "w1m", return_value=(np.arange(15) * s.MINUTE, arr, arr, arr)):
            self.assertEqual(s.resolve_minutes("X", 0, 100, 102, 98, 1, True)[0], "ENTRY_MISMATCH")


class SignalTests(unittest.TestCase):
    def test_indicators_do_not_use_future_candles(self):
        raw = raw_prices(100 + np.arange(1200) * .01 + np.sin(np.arange(1200) / 20))
        original = s.features(raw)
        changed = tuple(v.copy() for v in raw)
        for arr in changed[1:]:
            arr[1055:] *= 1.5
        new = s.features(changed)
        for key in original:
            np.testing.assert_allclose(original[key][:1055], new[key][:1055], equal_nan=True)

    def test_hour_only_becomes_available_when_closed(self):
        raw = raw_prices(np.arange(12, dtype=float) + 100)
        f = s.features(raw)
        self.assertTrue(np.isnan(f["hour_close"][:3]).all())
        self.assertEqual(f["hour_close"][3], raw[4][3])
        self.assertEqual(f["hour_close"][6], raw[4][3])

    def test_indicators_restart_after_data_gap(self):
        raw = list(raw_prices(np.full(1000, 100.)))
        raw[0][500:] += s.BAR
        f = s.features(raw)
        self.assertTrue(np.isnan(f["return4h"][500:516]).all())
        self.assertTrue(np.isnan(f["ema15"][500:519]).all())

    def test_timeout_is_exactly_twelve_hours(self):
        raw = raw_prices(np.full(60, 100.))
        candidate = {"symbol": "X", "entry_index": 0, "entry_time": 0,
                     "entry": 100., "sl": 98., "tp": 104., "side": 1}
        out = s.resolve_trade(candidate, raw, 60 * s.BAR)
        self.assertEqual(out["exit_time"], 48 * s.BAR)
        self.assertEqual(out["reason"], "TIME")

    def test_gap_stop_fills_at_worse_open(self):
        raw = raw_prices([100., 90., 91., 92.])
        candidate = {"symbol": "X", "entry_index": 0, "entry_time": 0,
                     "entry": 100., "sl": 98., "tp": 104., "side": 1}
        out = s.resolve_trade(candidate, raw, 4 * s.BAR)
        self.assertEqual(out["exit"], 90.)
        self.assertEqual(out["exit_time"], s.BAR)

    def test_missing_timeout_path_is_excluded(self):
        raw = list(raw_prices(np.full(60, 100.)))
        raw[0][10:] += s.BAR
        candidate = {"symbol": "X", "entry_index": 0, "entry_time": 0,
                     "entry": 100., "sl": 98., "tp": 104., "side": 1}
        self.assertEqual(s.resolve_trade(candidate, raw, 60 * s.BAR)["status"], "DATA_GAP")

    def test_short_return_uses_linear_entry_denominator(self):
        raw = raw_prices([100., 95., 90., 91.])
        candidate = {"symbol": "X", "entry_index": 0, "entry_time": 0,
                     "entry": 100., "sl": 105., "tp": 90., "side": -1}
        out = s.resolve_trade(candidate, raw, 4 * s.BAR)
        self.assertAlmostEqual(out["gross_return"], .1)

    def test_signal_confirmed_close_and_next_open_alignment(self):
        raw = list(raw_prices(np.full(30, 100.)))
        raw[1][20], raw[2][20], raw[3][20], raw[4][20] = 100., 101.2, 99., 101.
        raw[2][19], raw[3][19], raw[4][19], raw[1][21] = 100., 99., 99., 101.
        f = {"ema15": np.full(30, 100.5), "return4h": np.full(30, .02),
             "hour_ema20": np.full(30, 102.), "hour_ema50": np.full(30, 100.),
             "hour_close": np.full(30, 105.), "hour_atr": np.ones(30)}
        bf = {"return4h": np.zeros(30), "hour_close": np.full(30, 105.),
              "hour_ema200": np.full(30, 100.), "hour_slope50": np.ones(30)}
        out = s.signals("X", raw, f, raw, bf, 0, 30 * s.BAR)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["signal_time"] + s.BAR, out[0]["entry_time"])
        self.assertEqual(out[0]["decision_time"], out[0]["entry_time"])


class PortfolioTests(unittest.TestCase):
    def run_sim(self, rows, prices=None, start=0, end=8 * s.BAR, guarded=True):
        symbols = {r["symbol"] for r in rows}
        count = end // s.BAR + 1
        raw = {sym: raw_prices((prices or {}).get(sym, np.full(count, 100.))) for sym in symbols}
        return p.simulate(pd.DataFrame(rows), p.Market(raw), start, end, 20, guarded)

    def test_cash_equals_sum_of_net_trade_pnls(self):
        summary, trades, _, _ = self.run_sim([trade()])
        self.assertAlmostEqual(summary["net_return_pct"] / 100, trades.net_pnl.sum())
        self.assertLess(trades.notional.max(), .30000001)
        self.assertLessEqual((trades.reserved_risk / trades.entry_equity).max(), .00500001)

    def test_short_position_accounting(self):
        row = trade(side=-1, exit=99., sl=100.5)
        summary, trades, _, _ = self.run_sim([row])
        self.assertGreater(trades.net_pnl.iloc[0], 0)
        self.assertGreater(summary["net_return_pct"], 0)

    def test_simultaneous_risk_and_six_slot_caps(self):
        rows = [trade(symbol=f"X{i}") for i in range(7)]
        summary, trades, _, _ = self.run_sim(rows)
        self.assertEqual(len(trades), 6)
        self.assertEqual(summary["max_concurrent"], 6)
        self.assertLessEqual(summary["max_reserved_risk_at_entry_pct"], 2.000001)
        self.assertEqual(summary["rejections"]["slots"], 1)

    def test_same_symbol_opposite_signal_is_rejected(self):
        rows = [trade(exit_time=3 * s.BAR), trade(entry_time=s.BAR, side=-1, sl=100.5)]
        summary, trades, _, _ = self.run_sim(rows)
        self.assertEqual(len(trades), 1)
        self.assertEqual(summary["rejections"]["same_symbol"], 1)

    def test_day_loss_blocks_later_entry(self):
        rows = [trade(symbol=f"X{i}", exit_time=s.BAR, exit=90.) for i in range(2)]
        for row in rows:
            row["reason"] = "SL"
        rows += [trade(symbol="NEXT", entry_time=2 * s.BAR, exit_time=3 * s.BAR)]
        prices = {f"X{i}": [100.] + [90.] * 8 for i in range(2)}
        summary, trades, _, _ = self.run_sim(rows, prices)
        self.assertEqual(len(trades), 2)
        self.assertEqual(summary["rejections"]["day_blocked"], 1)
        self.assertEqual(summary["guard_triggers"][0]["kind"], "DAY_STOP")

    def test_profit_above_two_percent_does_not_block_entry(self):
        rows = [trade(symbol=f"X{i}", exit_time=s.BAR, exit=104., sl=98.) for i in range(4)]
        rows += [trade(symbol="NEXT", entry_time=2 * s.BAR, exit_time=3 * s.BAR)]
        summary, trades, _, _ = self.run_sim(rows)
        self.assertGreater(summary["net_return_pct"], 2.)
        self.assertIn("NEXT", trades.symbol.tolist())

    def test_drawdown_halt_is_permanent_within_split(self):
        rows = [trade(symbol=f"X{i}", exit_time=s.BAR, exit=80.) for i in range(4)]
        for row in rows:
            row["reason"] = "SL"
        rows += [trade(symbol="NEXT", entry_time=s.DAY, exit_time=s.DAY + s.BAR)]
        end = 2 * s.DAY
        prices = {f"X{i}": [100.] + [80.] * (end // s.BAR) for i in range(4)}
        summary, trades, _, _ = self.run_sim(rows, prices, end=end)
        self.assertIsNotNone(summary["halt_time"])
        self.assertEqual(summary["rejections"]["halted"], 1)
        self.assertNotIn("NEXT", trades.symbol.tolist())

    def test_half_risk_latches_into_next_day(self):
        rows = [trade(symbol=f"X{i}", exit_time=s.BAR, exit=90.) for i in range(4)]
        for row in rows:
            row["reason"] = "SL"
        rows += [trade(symbol="NEXT", entry_time=s.DAY, exit_time=s.DAY + s.BAR, sl=97.)]
        end = 2 * s.DAY
        prices = {f"X{i}": [100.] + [90.] * (end // s.BAR) for i in range(4)}
        summary, trades, _, _ = self.run_sim(rows, prices, end=end)
        self.assertIsNotNone(summary["risk_half_time"])
        nxt = trades[trades.symbol == "NEXT"].iloc[0]
        self.assertAlmostEqual(nxt.reserved_risk / nxt.entry_equity, .0025)

    def test_empty_days_are_in_denominator_and_korea_aligned(self):
        summary, trades, daily, _ = self.run_sim([], end=3 * s.DAY)
        self.assertEqual(summary["calendar_days"], 2)
        self.assertEqual(summary["day_ge_0_7_pct"], 0.)
        self.assertEqual(summary["flat_days_pct"], 100.)
        self.assertEqual(p.korea_day(15 * 3_600_000), p.korea_day(0) + 1)

    def test_split_crossing_outcome_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "split leakage"):
            self.run_sim([trade(exit_time=9 * s.BAR)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
