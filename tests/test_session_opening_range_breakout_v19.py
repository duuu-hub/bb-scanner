import unittest
import numpy as np
from scripts import day_edge_lab as base
from scripts import session_opening_range_breakout_v19 as v19

class TestSessionOpeningRangeBreakoutV19(unittest.TestCase):
    def fixture(self, side=1):
        n=20; t=base.START+np.arange(n,dtype=np.int64)*base.BAR
        o=np.full(n,100.0); h=np.full(n,100.4); l=np.full(n,99.6); c=np.full(n,100.0)
        h[0:2]=[100.8,101.0]; l[0:2]=[99.0,99.2]; c[0:2]=[100.2,99.8]
        c[2:6]=[100.2,99.8,100.1,99.9]
        if side==1:
            o[6]=100.9;h[6]=101.4;l[6]=100.8;c[6]=101.3;o[7]=101.35
        else:
            o[6]=99.1;h[6]=99.2;l[6]=98.6;c[6]=98.7;o[7]=98.65
        raw=(t,o,h,l,c)
        f=dict(eligible=np.ones(n,bool),prior_atr=np.full(n,2.0),
            session_anchor=np.full(n,int(t[0]),dtype=np.int64),session_bar=np.arange(n),
            session_vwap=np.full(n,100.0),clv=np.full(n,.5),
            volume_multiple=np.ones(n),buy_share=np.full(n,.5),r1=np.zeros(n))
        f['clv'][6]=.85 if side==1 else .15
        f['volume_multiple'][6]=2.0
        f['buy_share'][6]=.60 if side==1 else .40
        btc=dict(close=np.full(n,50000.0),r1=np.zeros(n))
        cfg=next(x for x in v19.configurations() if x['side']==side and x['width_cap']==1.0 and x['balance_bars']==4 and x['volume']==1.25)
        return raw,f,btc,cfg

    def test_grid_has_16_entries_and_96_policies(self):
        self.assertEqual(len(v19.configurations()),16);self.assertEqual(len(v19.policies()),96)
        self.assertEqual(len({x['key'] for x in v19.configurations()}),16)

    def test_long_breakout(self):
        raw,f,btc,cfg=self.fixture(1);events,counts=v19.opening_range_events(cfg,raw,f,btc)
        self.assertEqual(len(events),1);self.assertEqual(events[0]['index'],6);self.assertEqual(counts['QUALIFIED_BREAKOUT'],1)

    def test_short_breakout_is_mirrored(self):
        raw,f,btc,cfg=self.fixture(-1);events,_=v19.opening_range_events(cfg,raw,f,btc)
        self.assertEqual(len(events),1);self.assertAlmostEqual(events[0]['breakout_extension_atr'],.15)

    def test_minimum_width_is_enforced(self):
        raw,f,btc,cfg=self.fixture(1);raw[2][0:2]=100.1;raw[3][0:2]=99.9
        events,counts=v19.opening_range_events(cfg,raw,f,btc)
        self.assertFalse(events);self.assertGreater(counts['OPENING_RANGE_TOO_NARROW'],0)

    def test_width_cap_is_enforced(self):
        raw,f,btc,cfg=self.fixture(1);raw[2][0]=102.1
        events,counts=v19.opening_range_events(cfg,raw,f,btc)
        self.assertFalse(events);self.assertGreater(counts['OPENING_RANGE_TOO_WIDE'],0)

    def test_balance_close_must_stay_inside(self):
        raw,f,btc,cfg=self.fixture(1);raw[4][3]=101.01
        events,counts=v19.opening_range_events(cfg,raw,f,btc)
        self.assertFalse(events);self.assertGreater(counts['BALANCE_CLOSE_OUTSIDE_RANGE'],0)

    def test_balance_must_cross_both_sides_of_vwap(self):
        raw,f,btc,cfg=self.fixture(1);raw[4][2:6]=100.2
        events,counts=v19.opening_range_events(cfg,raw,f,btc)
        self.assertFalse(events);self.assertGreater(counts['BALANCE_NOT_TWO_SIDED'],0)

    def test_shifted_volume_gate(self):
        raw,f,btc,cfg=self.fixture(1);f['volume_multiple'][6]=1.24
        events,_=v19.opening_range_events(cfg,raw,f,btc);self.assertFalse(events)

    def test_taker_flow_gate(self):
        raw,f,btc,cfg=self.fixture(1);f['buy_share'][6]=.549
        events,_=v19.opening_range_events(cfg,raw,f,btc);self.assertFalse(events)

    def test_breakout_expires_after_four_bars(self):
        raw,f,btc,cfg=self.fixture(1)
        raw[2][6]=100.4;raw[3][6]=99.6;raw[4][6]=100.0;f['clv'][6]=.5
        raw[1][10]=100.9;raw[2][10]=101.4;raw[3][10]=100.8;raw[4][10]=101.3
        f['clv'][10]=.85;f['volume_multiple'][10]=2;f['buy_share'][10]=.6
        events,_=v19.opening_range_events(cfg,raw,f,btc);self.assertFalse(events)

    def test_entry_is_next_contiguous_open(self):
        raw,f,btc,cfg=self.fixture(1);rows,_=v19.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['entry_time'],raw[0][7]);self.assertEqual(rows[0]['decision_time'],raw[0][7])

    def test_favorable_gap_over_half_percent_is_excluded(self):
        raw,f,btc,cfg=self.fixture(1);raw[1][7]=102.0
        rows,counts=v19.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertFalse(rows);self.assertEqual(counts['ENTRY_CATCHUP_GAP'],1)

    def test_adverse_gap_is_retained(self):
        raw,f,btc,cfg=self.fixture(1);raw[1][7]=100.9
        rows,_=v19.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertEqual(len(rows),1);self.assertLess(rows[0]['known_entry_gap'],0)

    def test_stop_floor_and_measured_move(self):
        raw,f,btc,cfg=self.fixture(1);raw[1][7]=100.9
        rows,_=v19.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        self.assertAlmostEqual(rows[0]['risk_pct'],.005);self.assertAlmostEqual(rows[0]['opening_target'],103.0)
        self.assertGreater(rows[0]['opening_target'],rows[0]['entry'])

    def test_future_perturbation_does_not_change_intent(self):
        raw,f,btc,cfg=self.fixture(1);a,_=v19.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        raw[1][12]=500;raw[2][12]=600;raw[3][12]=1;raw[4][12]=400
        f['session_vwap'][12]=999;f['volume_multiple'][12]=999;f['buy_share'][12]=0
        b,_=v19.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+base.BAR)
        keys=['entry_time','entry','sl','opening_target','breakout_time']
        self.assertEqual([{k:r[k] for k in keys} for r in a],[{k:r[k] for k in keys} for r in b])

if __name__=='__main__':unittest.main()
