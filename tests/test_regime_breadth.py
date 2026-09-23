import unittest

import pandas as pd

from regime_breadth_research import build_breadth


class RegimeBreadthTests(unittest.TestCase):
    def test_simple_regime_classification_after_warmup(self):
        rows = []
        for i in range(100):
            ts = i * 900_000
            for sym in ("AUSDT", "BUSDT"):
                rows.append({
                    "symbol": sym,
                    "ts": ts,
                    "1W_above": 1,
                    "1W_dist": 1.0,
                    "1W_above_basis": 1,
                    "1W_below_lower": 0,
                    "1D_above": 1,
                    "1D_dist": 1.0,
                    "1D_above_basis": 1,
                    "1D_below_lower": 0,
                    "4H_above": 1,
                    "4H_dist": 1.0,
                    "4H_above_basis": 1,
                    "4H_below_lower": 0,
                })
        out = build_breadth(pd.DataFrame(rows))
        self.assertEqual(out.iloc[-1]["regime_60_40"], "BULL")
        self.assertEqual(out.iloc[-1]["weekly_mid_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
