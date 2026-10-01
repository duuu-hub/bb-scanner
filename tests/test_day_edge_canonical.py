"""Canonical chronology, causal trail, sizing and shard checks."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import day_edge_canonical as s
from scripts import relative_pullback_portfolio as a

def raw(n=60):
    t=np.arange(n,dtype=np.int64)*s.BAR
    z=np.full(n,100.)
    return [t,z.copy(),z+1,z-1,z.copy()]

def intent(side=1,hold=48):
    return dict(symbol='X',entry_index=0,entry_time=0,entry=100.,side=side,
                sl=90. if side==1 else 110.,tp=120. if side==1 else 80.,
                max_hold_bars=hold,atr_mult=3.)

class CanonicalTests(unittest.TestCase):
    def test_timeout_is_exact_seed_deadline(self):
        r=s.resolve(intent(),raw(),{'atr':np.full(60,.01)},'TIME',60*s.BAR)
        self.assertEqual(r['exit_time'],48*s.BAR)
        self.assertEqual(r['reason'],'TIME')

    def test_timeout_cannot_cross_split(self):
        r=s.resolve(intent(),raw(),{'atr':np.full(60,.01)},'TIME',30*s.BAR)
        self.assertEqual(r['exit_time'],30*s.BAR)
        self.assertEqual(r['reason'],'SPLIT_END')

    def test_gap_sl_fills_at_worse_open(self):
        p=raw();p[1][1]=80;p[3][1]=79
        r=s.resolve(intent(),p,{},'TIME',60*s.BAR)
        self.assertEqual(r['exit'],80.)
        self.assertEqual(r['exit_time'],s.BAR)

    def test_missing_path_is_excluded(self):
        p=raw();p[0][10:]+=s.BAR
        self.assertEqual(s.resolve(intent(),p,{},'TIME',60*s.BAR)['status'],'DATA_GAP')

    def test_parent_collision_uses_minute_authority(self):
        p=raw();p[2][1]=121;p[3][1]=89
        with patch.object(s.chrono,'resolve_minutes',return_value=('TP',s.BAR+3*60000,120.)) as m:
            r=s.resolve(intent(),p,{},'TP2',60*s.BAR)
        self.assertEqual(r['reason'],'TP')
        self.assertEqual(r['exit'],120.)
        self.assertEqual(m.call_count,1)
        self.assertFalse(m.call_args.args[-1])

    def test_entry_bar_calls_authority_even_single_tp(self):
        p=raw();p[2][0]=121
        with patch.object(s.chrono,'resolve_minutes',return_value=('SL',60000,90.)) as m:
            r=s.resolve(intent(),p,{},'TP2',60*s.BAR)
        self.assertEqual(r['reason'],'SL')
        self.assertTrue(m.call_args.args[-1])

    def test_authoritative_mismatch_is_not_a_fake_loss(self):
        p=raw();p[2][0]=121
        with patch.object(s.chrono,'resolve_minutes',return_value=('ENTRY_MISMATCH',None,None)):
            self.assertEqual(s.resolve(intent(),p,{},'TP2',60*s.BAR)['status'],'ENTRY_MISMATCH')

    def test_trailing_stop_only_activates_after_bar_close(self):
        p=raw();p[2][3]=111;p[3][3]=100;p[4][3]=110
        p[1][4]=110;p[2][4]=111;p[3][4]=106;p[4][4]=110
        r=s.resolve(intent(),p,{'atr':np.full(60,.01)},'TRAIL',60*s.BAR)
        self.assertEqual(r['exit_time'],5*s.BAR)
        self.assertAlmostEqual(r['exit'],106.7)

    def test_short_has_linear_entry_denominator(self):
        p=raw();p[1][48]=80
        r=s.resolve(intent(side=-1),p,{},'TIME',60*s.BAR)
        self.assertAlmostEqual(r['gross_return'],.2)

    def test_union_tie_uses_entry_priority_not_future_outcome(self):
        x=pd.DataFrame([dict(symbol='X',entry_time=0,key='SHOCK_FADE_L4_T6_M-1',
                            atr_mult=3.,exit_type='TIME',gross_return=.5,max_hold_bars=96),
                        dict(symbol='X',entry_time=0,key='SHOCK_FADE_L1_T6_M-1',
                            atr_mult=3.,exit_type='TIME',gross_return=-.1,max_hold_bars=48)])
        r=s.union_ledger(x,3.,'TIME')
        self.assertEqual(len(r),1)
        self.assertEqual(r.max_hold_bars.iloc[0],48)
        self.assertEqual(r.gross_return.iloc[0],-.1)

    def test_actual_max_hold_used_in_stop_risk_budget(self):
        base=dict(entry=100.,sl=98.,side=1)
        short=a.stop_loss_fraction({**base,'max_hold_bars':4},.001)
        long=a.stop_loss_fraction({**base,'max_hold_bars':96},.001)
        self.assertAlmostEqual(long-short,a.FUND_PER_DAY*92/96)
        self.assertAlmostEqual(a.stop_loss_fraction(base,.001),
                               a.stop_loss_fraction({**base,'max_hold_bars':48},.001))

    def test_beyond_one_week_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'seven days'):
            a.stop_loss_fraction(dict(entry=100.,sl=98.,side=1,max_hold_bars=673),.001)

    def test_incomplete_shards_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'incomplete'):
                s.accounts(Path(d),Path(d),Path(d)/'out',Path(d)/'selection.json',
                           ('DEV','GATE'),8)

if __name__=='__main__':unittest.main()
