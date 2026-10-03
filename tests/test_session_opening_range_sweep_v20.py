import unittest
import numpy as np
from scripts import day_edge_lab as base
from scripts import session_opening_range_sweep_v20 as v20


class TestSessionOpeningRangeSweepV20(unittest.TestCase):
    def fixture(self, side=1):
        n=20; t=base.START+np.arange(n,dtype=np.int64)*base.BAR
        o=np.full(n,100.0); h=np.full(n,100.4); l=np.full(n,99.6); c=np.full(n,100.0)
        h[0:2]=[100.8,101.0]; l[0:2]=[99.0,99.2]; c[0:2]=[100.2,99.8]
        c[2:6]=[100.2,99.8,100.1,99.9]
        if side==1:
            o[6]=99.2;h[6]=99.5;l[6]=98.6;c[6]=99.4;o[7]=99.35
        else:
            o[6]=100.8;h[6]=101.4;l[6]=100.5;c[6]=100.6;o[7]=100.65
        raw=(t,o,h,l,c)
        f=dict(eligible=np.ones(n,bool),prior_atr=np.full(n,2.0),
            session_anchor=np.full(n,int(t[0]),dtype=np.int64),session_bar=np.arange(n),
            session_vwap=np.full(n,100.0),clv=np.full(n,.5),
            volume_multiple=np.ones(n),buy_share=np.full(n,.5),r1=np.zeros(n))
        f['volume_multiple'][6]=2.0
        f['buy_share'][6]=.40 if side==1 else .60
        btc=dict(close=np.full(n,50000.0),r1=np.zeros(n))
        cfg=next(x for x in v20.configurations() if x['side']==side and x['width_cap']==1.0 and x['balance_bars']==4 and x['volume']==1.25)
        return raw,f,btc,cfg

    def test_grid_has_16_entries_and_96_policies(self):
        self.assertEqual(len(v20.configurations()),16);self.assertEqual(len(v20.policies()),96)
        self.assertEqual(len({x['key'] for x in v20.configurations()}),16)

    def test_lower_sweep_creates_long_reversal(self):
        raw,f,btc,cfg=self.fixture(1);events,counts=v20.opening_range_sweeps(cfg,raw,f,btc)
        self.assertEqual(len(events),1);self.assertEqual(events[0]['index'],6)
        self.assertAlmostEqual(events[0]['sweep_penetration_atr'],.2);self.assertEqual(counts['QUALIFIED_SWEEP'],1)

    def test_upper_sweep_creates_short_reversal(self):
        raw,f,btc,cfg=self.fixture(-1);events,_=v20.opening_range_sweeps(cfg,raw,f,btc)
        self.assertEqual(len(events),1);self.assertAlmostEqual(events[0]['sweep_reentry_atr'],.2)

    def test_minimum_width_is_enforced(self):
        raw,f,btc,cfg=self.fixture(1);raw[2][0:2]=100.1;raw[3][0:2]=99.9
        events,counts=v20.opening_range_sweeps(cfg,raw,f,btc)
        self.assertFalse(events);self.assertGreater(counts['OPENING_RANGE_TOO_NARROW'],0)

    def test_width_cap_is_enforced(self):
        raw,f,btc,cfg=self.fixture(1);raw[2][0]=102.1
        events,counts=v20.opening_range_sweeps(cfg,raw,f,btc)
        self.assertFalse(events);self.assertGreater(counts['OPENING_RANGE_TOO_WIDE'],0)

    def test_balance_close_must_stay_inside(self):
        raw,f,btc,cfg=self.fixture(1);raw[4][3]=101.01
        events,counts=v20.opening_range_sweeps(cfg,raw,f,btc)
        self.assertFalse(events);self.assertGreater(counts['BALANCE_CLOSE_OUTSIDE_RANGE'],0)

    def test_balance_must_cross_both_sides_of_vwap(self):
        raw,f,btc,cfg=self.fixture(1);raw[4][2:6]=100.2
        events,counts=v20.opening_range_sweeps(cfg,raw,f,btc)
        self.assertFalse(events);self.assertGreater(counts['BALANCE_NOT_TWO_SIDED'],0)

    def test_wick_penetration_gate(self):
        raw,f,btc,cfg=self.fixture(1);raw[3][6]=98.81
        events,_=v20.opening_range_sweeps(cfg,raw,f,btc);self.assertFalse(events)

    def test_decisive_reentry_gate(self):
        raw,f,btc,cfg=self.fixture(1);raw[4][6]=99.09
        events,_=v20.opening_range_sweeps(cfg,raw,f,btc);self.assertFalse(events)

    def test_close_outside_is_not_a_sweep(self):
        raw,f,btc,cfg=self.fixture(1);raw[4][6]=98.9
        events,_=v20.opening_range_sweeps(cfg,raw,f,btc);self.assertFalse(events)

    def test_shifted_volume_gate(self):
        raw,f,btc,cfg=self.fixture(1);f['volume_multiple'][6]=1.24
        events,_=v20.opening_range_sweeps(cfg,raw,f,btc);self.assertFalse(events)

    def test_swept_direction_taker_flow_gate(self):
        raw,f,btc,cfg=self.fixture(1);f['buy_share'][6]=.451
        events,_=v20.opening_range_sweeps(cfg,raw,f,btc);self.assertFalse(events)

    def test_sweep_expires_after_four_bars(self):
        raw,f,btc,cfg=self.fixture(1)
        raw[3][6]=99.6;raw[4][6]=100.0
        raw[1][10]=99.2;raw[2][10]=99.5;raw[3][10]=98.6;raw[4][10]=99.4
        f['volume_multiple'][10]=2;f['buy_share'][10]=.4
        events,_=v20.opening_range_sweeps(cfg,raw,f,btc);self.assertFalse(events)

    def test_entry_is_next_contiguous_open(self):
        raw,f,btc,cfg=self.fixture(1);rows,_=v20.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['entry_time'],raw[0][7]);self.assertEqual(rows[0]['decision_time'],raw[0][7])

    def test_favorable_gap_over_half_percent_is_excluded(self):
        raw,f,btc,cfg=self.fixture(1);raw[1][7]=100.0
        rows,counts=v20.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertFalse(rows);self.assertEqual(counts['ENTRY_CATCHUP_GAP'],1)

    def test_adverse_gap_is_retained(self):
        raw,f,btc,cfg=self.fixture(1);raw[1][7]=99.2
        rows,_=v20.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertEqual(len(rows),1);self.assertLess(rows[0]['known_entry_gap'],0)

    def test_stop_floor_and_frozen_targets(self):
        raw,f,btc,cfg=self.fixture(1);rows,_=v20.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertAlmostEqual(rows[0]['risk_pct'],(99.35-98.4)/99.35)
        self.assertAlmostEqual(rows[0]['midpoint_target'],100.0);self.assertAlmostEqual(rows[0]['opposite_target'],101.0)
        self.assertLess(rows[0]['structural_stop'],raw[3][6])

    def test_future_perturbation_does_not_change_intent(self):
        raw,f,btc,cfg=self.fixture(1);a,_=v20.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        raw[1][12]=500;raw[2][12]=600;raw[3][12]=1;raw[4][12]=400
        f['session_vwap'][12]=999;f['volume_multiple'][12]=999;f['buy_share'][12]=0
        b,_=v20.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        keys=['entry_time','entry','sl','midpoint_target','opposite_target','sweep_time']
        self.assertEqual([{k:r[k] for k in keys} for r in a],[{k:r[k] for k in keys} for r in b])


if __name__=='__main__':unittest.main()
