"""Causal feature and fixed-time scout integrity checks."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import day_edge_lab as lab


def market(n=400, start=0):
    t = start + np.arange(n, dtype=np.int64)*lab.BAR
    c = 100 + np.arange(n)*.01 + np.sin(np.arange(n)/10)
    return (t, c.copy(), c+1, c-1, c.copy())


class ScoutTests(unittest.TestCase):
    def test_configurations_are_predeclared_and_distinct(self):
        cfg = lab.configurations()
        self.assertEqual(len(cfg), 36)
        self.assertEqual(len({c['key'] for c in cfg}), 36)
        self.assertEqual(len(cfg)*len(lab.HOLDS), 144)

    def test_features_are_invariant_to_future_changes(self):
        raw = market(); q = np.full(400, 1e6)
        f = lab.features(raw, q)
        altered = tuple(a.copy() for a in raw); aq = q.copy()
        for a in altered[1:]: a[280:] *= 2
        aq[280:] *= 50
        g = lab.features(altered, aq)
        for k in f:
            np.testing.assert_allclose(f[k][:280],g[k][:280],equal_nan=True)

    def test_features_restart_after_gaps(self):
        raw = list(market()); raw[0][200:] += lab.BAR
        f = lab.features(raw,np.ones(400)*1e6)
        self.assertTrue(np.isnan(f['r16'][200:216]).all())
        self.assertTrue(np.isnan(f['q96'][200:295]).all())

    def test_quote_volume_baseline_excludes_latest_hour(self):
        raw = market(); q = np.ones(400)*100
        q[116:120] = 1000
        f = lab.features(raw,q)
        self.assertAlmostEqual(f['qratio'][119],10)

    def test_btc_missing_timestamp_is_not_forward_filled(self):
        raw = market(); q = np.ones(400)
        f = lab.features(raw,q)
        keep = np.ones(400,bool);keep[220]=False
        aligned = lab.align_btc(raw[0],raw[0][keep],{k:v[keep] for k,v in f.items()})
        self.assertTrue(np.isnan(aligned['r4'][220]))

    def test_eligibility_uses_age_and_past_turnover(self):
        raw = market(n=3500)
        f = lab.features(raw,np.ones(3500)*1e6)
        self.assertFalse(f['eligible'][:30*96].any())
        self.assertTrue(f['eligible'][-1])
        low = lab.features(raw,np.ones(3500)*10)
        self.assertFalse(low['eligible'].any())

    def events(self,raw,indices,hold=4,start=0,end=100*lab.BAR,side=1):
        mask = np.zeros(len(raw[0]),bool);mask[indices]=True
        with patch.object(lab,'signal_mask',return_value=mask):
            return lab.fixed_events({'side':side},raw,{}, {},hold,start,end)

    def test_next_open_entry_and_fixed_open_exit(self):
        raw = list(market(n=100));raw[1][2]=100;raw[1][6]=110
        ev=self.events(raw,[1])
        self.assertEqual(ev.time.iloc[0],2*lab.BAR)
        self.assertAlmostEqual(ev.gross.iloc[0],.1)
        self.assertAlmostEqual(ev.net40.iloc[0],.1-.002*2.1-.0002*4/96)

    def test_short_has_linear_entry_denominator(self):
        raw = list(market(n=100));raw[1][2]=100;raw[1][6]=80
        self.assertAlmostEqual(self.events(raw,[1],side=-1).gross.iloc[0],.2)

    def test_scheduled_nonoverlap_does_not_use_outcome(self):
        raw=market(n=100)
        ev=self.events(raw,[1,2,3,5])
        self.assertEqual(ev.time.tolist(),[2*lab.BAR,6*lab.BAR])

    def test_outcome_cannot_cross_split(self):
        self.assertTrue(self.events(market(n=100),[95]).empty)
        self.assertTrue(self.events(market(n=100),[10],end=15*lab.BAR).empty)

    def test_missing_bar_excludes_fixed_time_event(self):
        raw=list(market(n=100));raw[0][4:] += lab.BAR
        self.assertTrue(self.events(raw,[1]).empty)

    def test_signal_onset_uses_only_previous_closed_state(self):
        raw=market(n=150)
        f={'eligible':np.ones(150,bool),'r1':np.zeros(150)}
        f['r1'][110:115]=.05
        cfg={'family':'SHOCK_FOLLOW','move':1,'side':1,'lookback':1,'threshold':.03}
        self.assertEqual(np.flatnonzero(lab.signal_mask(cfg,raw,f,{})).tolist(),[110])

    def test_missing_year_cannot_pass_selection(self):
        with tempfile.TemporaryDirectory() as root:
            part=Path(root)/'parts';part.mkdir();out=Path(root)/'out'
            rows=[]
            for split,year in [('DEV',2023),('GATE',2024)]:
                for i in range(10):
                    r=dict(symbol=f'C{i}',key='K',hold=4,family='F',side=1,split=split,year=year)
                    for col in ['gross','net20','net40']:
                        for k,v in lab.moments(np.full(40,.01)).items():r[f'{col}_{k}']=v
                    rows.append(r)
            pd.DataFrame(rows).to_csv(part/'cell_symbol_year.csv.gz',index=False)
            lab.merge(part,out)
            import json
            self.assertEqual(json.loads((out/'selection.json').read_text())['selected'],[])


if __name__=='__main__':unittest.main()
