"""Causal session-opening impulse acceptance V18 contracts."""
import unittest
from unittest.mock import patch

import numpy as np

from scripts import session_opening_breadth_v18 as breadth
from scripts import session_opening_impulse_v18 as s


def fixture(side=1, retracement=.382, n=64):
    anchor=(s.base.START//(8*60*60*1000)+1)*(8*60*60*1000)
    t=anchor+np.arange(n,dtype=np.int64)*s.BAR
    o=np.full(n,100.);h=np.full(n,100.2);l=np.full(n,99.8);c=np.full(n,100.)
    if side==1:
        o[:5]=[100.,100.6,101.8,101.7,102.3]
        h[:5]=[100.8,102.2,102.0,102.5,102.5]
        l[:5]=[99.9,100.5,101.5,101.6,102.1]
        c[:5]=[100.6,102.,101.7,102.3,102.4]
        vwap=np.array([100.1,100.5,100.7,100.8,101.]+[101.]*(n-5))
    else:
        o[:5]=[100.,99.4,98.2,98.3,97.7]
        h[:5]=[100.1,99.5,98.5,98.4,97.9]
        l[:5]=[99.2,97.8,98.0,97.5,97.5]
        c[:5]=[99.4,98.,98.3,97.7,97.6]
        vwap=np.array([99.9,99.5,99.3,99.2,99.]+[99.]*(n-5))
    raw=[t,o,h,l,c];q=np.full(n,1e6);buy=q*.5
    session_bar=np.arange(n)%32
    session_anchor=np.where(np.arange(n)<32,anchor,anchor+32*s.BAR).astype(np.int64)
    f=dict(eligible=np.ones(n,bool),session_bar=session_bar,session_anchor=session_anchor,
        session_vwap=vwap,prior_atr=np.ones(n),volume_multiple=np.ones(n),buy_share=np.full(n,.5),
        r1=np.zeros(n),clv=(c-l)/(h-l),n=np.full(n,100.),up_fraction=np.full(n,.7),down_fraction=np.full(n,.7))
    btc=dict(close=np.full(n,20000.),r1=np.zeros(n))
    cfg=next(x for x in s.configurations() if x['side']==side and x['displacement']==1.
        and x['breadth']==.55 and x['retracement']==retracement)
    return raw,q,buy,f,btc,cfg


class GridFeatureBreadthTests(unittest.TestCase):
    def test_exact_frozen_grid(self):
        self.assertEqual(len(s.configurations()),16)
        self.assertEqual(len(s.policies()),96)
        self.assertEqual({p['hold'] for p in s.policies()},{16,32})
        self.assertEqual({p['exit_type'] for p in s.policies()},{'OR1','R15','R25'})

    def test_exact_preregistered_development_gates(self):
        row=dict(n=300,symbols=60,top_symbol_share_pct=30,net40_mean_bp=.1,net40_R_mean=.01)
        for year in (2021,2022,2023):
            row.update({f'year_{year}_n':30,f'year_{year}_days':20,
                f'year_{year}_net40_R':.01,f'year_{year}_net40_mean_bp':.1,f'year_{year}_day_R':.01})
        self.assertEqual(s.development_rejections(row),[])
        for key,value,reason in [('symbols',59,'SYMBOLS_LT_60'),('n',299,'N_LT_300'),
                ('year_2022_n',29,'2022_N_LT_30'),('year_2023_days',19,'2023_DATES_LT_20')]:
            bad=dict(row);bad[key]=value
            self.assertIn(reason,s.development_rejections(bad))

    def test_session_vwap_is_causal_and_fixed_anchor(self):
        raw,q,buy,_,_,_=fixture();f=s.features(raw,q,buy)
        typical=(raw[2]+raw[3]+raw[4])/3
        expected=np.cumsum(q[:32])/np.cumsum(q[:32]/typical[:32])
        np.testing.assert_allclose(f['session_vwap'][:32],expected)
        old=f['session_vwap'].copy();raw[4][20:]*=3;q[20:]*=7
        new=s.features(raw,q,buy)['session_vwap']
        np.testing.assert_allclose(old[:20],new[:20],equal_nan=True)

    def test_breadth_counts_only_second_opening_bar(self):
        raw,_,_,_,_,_=fixture();times=breadth.grid('DEV')
        f=dict(eligible=np.ones(len(raw[0]),bool),session_bar=np.arange(len(raw[0]))%32,
            prior_atr=np.ones(len(raw[0])))
        result=breadth.counts(raw,f,times)
        loc=np.searchsorted(times,raw[0][1])
        self.assertEqual(result[:,0].sum(),2)
        self.assertEqual(result[loc,0],1)
        self.assertEqual(result[loc,2],1)
        self.assertEqual(result[loc,1],0)

    def test_breadth_alignment_rejects_low_universe(self):
        t=np.array([100,200],dtype=np.int64)
        import pandas as pd
        frame=pd.DataFrame({'open_time':t,'n':[29,30],'down':[1,3],'up':[20,18]})
        aligned=breadth.align(t,frame)
        self.assertTrue(np.isnan(aligned['up_fraction'][0]))
        self.assertAlmostEqual(aligned['up_fraction'][1],.6)


class SignalEntryExitTests(unittest.TestCase):
    def test_mirrored_opening_signal_pullback_and_later_confirmation(self):
        for side in (1,-1):
            raw,_,_,f,btc,cfg=fixture(side)
            self.assertEqual(np.flatnonzero(s.opening_mask(cfg,raw,f,btc)).tolist(),[1])
            rows,bad=s.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)
            self.assertEqual(len(rows),1,bad)
            self.assertEqual(rows[0]['pullback_time'],raw[0][2])
            self.assertEqual(rows[0]['confirmation_time'],raw[0][3])
            self.assertEqual(rows[0]['entry_time'],raw[0][4])

    def test_breadth_and_two_vwap_closes_are_required(self):
        raw,_,_,f,btc,cfg=fixture();f['up_fraction'][1]=.54
        self.assertFalse(s.opening_mask(cfg,raw,f,btc).any())
        raw,_,_,f,btc,cfg=fixture();raw[4][0]=99.
        self.assertFalse(s.opening_mask(cfg,raw,f,btc).any())

    def test_pullback_must_hold_vwap_and_remain_shallow_through_confirmation(self):
        raw,_,_,f,btc,cfg=fixture();raw[4][2]=100.4
        rows,bad=s.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)
        self.assertFalse(rows);self.assertEqual(bad['VWAP_ACCEPTANCE_FAILED'],1)
        raw,_,_,f,btc,cfg=fixture();raw[3][3]=101.0
        rows,bad=s.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)
        self.assertFalse(rows);self.assertEqual(bad['PULLBACK_TOO_DEEP'],1)

    def test_favourable_gap_excluded_adverse_gap_retained(self):
        raw,_,_,f,btc,cfg=fixture();raw[1][4]=raw[4][3]*1.006
        rows,bad=s.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)
        self.assertFalse(rows);self.assertEqual(bad['ENTRY_CATCHUP_GAP'],1)
        raw,_,_,f,btc,cfg=fixture();raw[1][4]=raw[4][3]*.995
        self.assertTrue(s.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)[0])

    def test_projection_and_r_targets(self):
        raw,_,_,f,btc,cfg=fixture();seeds,_=s.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)
        result=dict(status='RESOLVED',exit_time=raw[0][5],exit=103.,reason='TP',gross_return=.01)
        targets={}
        for kind in ('OR1','R15','R25'):
            p=dict(cfg,hold=16,exit_type=kind,policy='P')
            with patch.object(s.canonical,'resolve',return_value=result) as resolver:
                s.policy_rows('X',[p],raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)
            targets[kind]=resolver.call_args.args[0]['tp']
        risk=abs(seeds[0]['entry']-seeds[0]['sl'])
        self.assertEqual(targets['OR1'],seeds[0]['projection_target'])
        self.assertAlmostEqual(targets['R15'],seeds[0]['entry']+1.5*risk)
        self.assertAlmostEqual(targets['R25'],seeds[0]['entry']+2.5*risk)


if __name__=='__main__':
    unittest.main()
