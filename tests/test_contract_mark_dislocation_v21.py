"""V21 frozen grid, causal signal, entry risk and policy tests."""
import unittest
from unittest.mock import patch

import numpy as np

from scripts import contract_mark_dislocation_v21 as strategy


def fixture(side=1, n=140):
    t = np.arange(n, dtype=np.int64) * strategy.BAR
    open_ = np.full(n, 100.0)
    high = np.full(n, 100.6)
    low = np.full(n, 99.4)
    close = np.full(n, 100.0)
    i = 110
    if side == 1:
        low[i], high[i], close[i] = 97.5, 100.5, 100.25
    else:
        low[i], high[i], close[i] = 99.5, 102.5, 99.75
    raw = [t, open_, high, low, close]
    features = {
        "eligible": np.ones(n, bool),
        "prior_atr": np.full(n, 10.0),
        "volume_multiple": np.full(n, 2.0),
        "buy_share": np.full(n, 0.40 if side == 1 else 0.60),
        "clv": (close - low) / (high - low),
    }
    mark = {
        "mark_available": np.ones(n, bool),
        "mark_open": np.full(n, 100.0),
        "mark_high": np.full(n, 100.5),
        "mark_low": np.full(n, 99.5),
        "mark_close": np.full(n, 100.0),
    }
    cfg = next(c for c in strategy.configurations() if c["side"] == side and c["excursion"] == 0.10 and c["tolerance"] == 0.10 and c["volume"] == 1.25)
    return i, raw, features, mark, cfg


class ContractMarkTests(unittest.TestCase):
    def test_frozen_grid_is_16_entries_and_96_unique_policies(self):
        self.assertEqual(len(strategy.configurations()), 16)
        self.assertEqual(len(strategy.policies()), 96)
        self.assertEqual(len({row["policy"] for row in strategy.policies()}), 96)

    def test_mirrored_contract_only_excursion(self):
        for side in (1, -1):
            i, raw, features, mark, cfg = fixture(side)
            self.assertEqual(np.flatnonzero(strategy.signal_mask(cfg, raw, features, mark)).tolist(), [i])
            if side == 1:
                raw[3][i] = mark["mark_low"][i] - 0.9
            else:
                raw[2][i] = mark["mark_high"][i] + 0.9
            self.assertFalse(strategy.signal_mask(cfg, raw, features, mark).any())

    def test_close_tolerance_rejection_and_failed_direction_flow_are_hard(self):
        for side in (1, -1):
            i, raw, features, mark, cfg = fixture(side)
            raw[4][i] = 101.1 if side == 1 else 98.9
            self.assertFalse(strategy.signal_mask(cfg, raw, features, mark).any())
            i, raw, features, mark, cfg = fixture(side)
            features["buy_share"][i] = 0.55 if side == 1 else 0.45
            self.assertFalse(strategy.signal_mask(cfg, raw, features, mark).any())
            i, raw, features, mark, cfg = fixture(side)
            raw[4][i] = 98.0 if side == 1 else 102.0
            self.assertFalse(strategy.signal_mask(cfg, raw, features, mark).any())

    def test_mark_reference_shock_missing_alignment_and_volume_fail(self):
        i, raw, features, mark, cfg = fixture()
        mark["mark_high"][i] = 111
        self.assertFalse(strategy.signal_mask(cfg, raw, features, mark).any())
        i, raw, features, mark, cfg = fixture()
        mark["mark_available"][i] = False
        self.assertFalse(strategy.signal_mask(cfg, raw, features, mark).any())
        i, raw, features, mark, cfg = fixture()
        raw[0][i] += 1
        self.assertFalse(strategy.signal_mask(cfg, raw, features, mark).any())
        i, raw, features, mark, cfg = fixture()
        features["volume_multiple"][i] = 1.24
        self.assertFalse(strategy.signal_mask(cfg, raw, features, mark).any())

    def test_next_open_actual_fill_stop_floor_and_cap(self):
        i, raw, features, mark, cfg = fixture()
        raw[1][i + 1] = 100.1
        rows, excluded = strategy.intents("X", cfg, raw, features, mark, 0, 999 * strategy.BAR)
        self.assertFalse(excluded)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry"], 100.1)
        self.assertEqual(rows[0]["structural_stop"], 96.5)
        self.assertEqual(rows[0]["sl"], 96.5)
        i, raw, features, mark, cfg = fixture()
        features["prior_atr"][:] = 0.1
        mark["mark_high"][i], mark["mark_low"][i] = 100.05, 99.95
        raw[2][i], raw[3][i] = 100.05, 99.93
        raw[4][i] = 100.0
        rows, _ = strategy.intents("X", cfg, raw, features, mark, 0, 999 * strategy.BAR)
        self.assertAlmostEqual(rows[0]["risk_pct"], 0.005)
        i, raw, features, mark, cfg = fixture()
        raw[3][i] = 90
        rows, excluded = strategy.intents("X", cfg, raw, features, mark, 0, 999 * strategy.BAR)
        self.assertFalse(rows)
        self.assertEqual(excluded["STOP_ABOVE_6PCT"], 1)

    def test_favorable_gap_excluded_adverse_gap_retained(self):
        for side in (1, -1):
            i, raw, features, mark, cfg = fixture(side)
            raw[1][i + 1] = raw[4][i] * (1 + side * 0.006)
            rows, excluded = strategy.intents("X", cfg, raw, features, mark, 0, 999 * strategy.BAR)
            self.assertFalse(rows)
            self.assertEqual(excluded["ENTRY_CATCHUP_GAP"], 1)
            i, raw, features, mark, cfg = fixture(side)
            raw[1][i + 1] = raw[4][i] * (1 - side * 0.006)
            rows, _ = strategy.intents("X", cfg, raw, features, mark, 0, 999 * strategy.BAR)
            self.assertEqual(len(rows), 1)

    def test_future_data_does_not_change_existing_intent(self):
        i, raw, features, mark, cfg = fixture()
        old = strategy.intents("X", cfg, raw, features, mark, 0, 999 * strategy.BAR)[0][0]
        raw[2][i + 2 :] *= 20
        raw[3][i + 2 :] *= 0.01
        raw[4][i + 2 :] *= 7
        mark["mark_close"][i + 2 :] *= 9
        new = strategy.intents("X", cfg, raw, features, mark, 0, 999 * strategy.BAR)[0][0]
        self.assertEqual(old, new)

    def test_fixed_intent_cooldown_is_outcome_independent(self):
        i, raw, features, mark, cfg = fixture(n=180)
        for j in (i + 5, i + 15, i + 16):
            raw[3][j], raw[2][j], raw[4][j] = 97.5, 100.5, 100.25
        rows, excluded = strategy.intents("X", cfg, raw, features, mark, 0, 999 * strategy.BAR)
        self.assertEqual([row["signal_time"] // strategy.BAR for row in rows], [i, i + 16])
        self.assertEqual(excluded["INTENT_COOLDOWN"], 2)

    def test_policy_targets_and_shared_resolver(self):
        _, raw, features, mark, cfg = fixture()
        chosen = [
            {**cfg, "hold": 16, "exit_type": "R15", "policy": "R15"},
            {**cfg, "hold": 32, "exit_type": "R25", "policy": "R25"},
        ]
        result = {"status": "RESOLVED", "exit_time": 113 * strategy.BAR, "exit": 105.0, "reason": "TP", "gross_return": 0.05}
        with patch.object(strategy.canonical, "resolve", return_value=result) as resolver:
            rows, _, bad = strategy.policy_rows("X", chosen, raw, features, mark, 0, 999 * strategy.BAR)
        self.assertFalse(bad)
        self.assertEqual(len(rows), 2)
        first, second = resolver.call_args_list
        risk = abs(first.args[0]["entry"] - first.args[0]["sl"])
        self.assertAlmostEqual(first.args[0]["tp"], first.args[0]["entry"] + 1.5 * risk)
        self.assertAlmostEqual(second.args[0]["tp"], second.args[0]["entry"] + 2.5 * risk)

    def test_mark_target_wrong_side_is_excluded_not_repurposed(self):
        _, raw, features, mark, cfg = fixture()
        chosen = [{**cfg, "hold": 16, "exit_type": "MARK", "policy": "MARK"}]
        rows, counts, bad = strategy.policy_rows("X", chosen, raw, features, mark, 0, 999 * strategy.BAR)
        self.assertFalse(rows)
        self.assertFalse(bad)
        self.assertEqual(counts["MARK/TARGET_WRONG_SIDE"], 1)

    def test_three_year_95_percent_coverage_gate(self):
        originals = [{"coverage": [{"symbol": "X", "mark_year_coverage": {str(year): {"eligible": 100, "known": 95} for year in (2021, 2022, 2023)}}]}]
        self.assertTrue(strategy.mark_coverage(originals)["passed"])
        originals[0]["coverage"][0]["mark_year_coverage"]["2022"]["known"] = 94
        self.assertFalse(strategy.mark_coverage(originals)["passed"])


if __name__ == "__main__":
    unittest.main()
