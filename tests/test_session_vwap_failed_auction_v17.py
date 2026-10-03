"""Causal fixed-session VWAP failed-auction V17 contracts."""
import unittest
from unittest.mock import patch
import numpy as np
from scripts import session_vwap_failed_auction_v17 as s

def fixture(side=1,confirm_age=1,n=500):
    anchor=(s.base.START//(8*60*60*1000)+1)*(8*60*60*1000)
    t=anchor+np.arange(n,dtype=np.int64)*s.BAR
    o=np.full(n,100.);h=np.full(n,100.4);l=np.full(n,99.6);c=np.full(n,100.)
    q=np.full(n,1e6);buy=q*.5
    e=100;j=e+confirm_age
    if side==1:
        o[e]=98.3;h[e]=98.5;l[e]=96.0;c[e]=98.0
        o[j]=97.2;h[j]=99.2;l[j]=97.;c[j]=99.0;o[j+1]=99.0
    else:
        o[e]=101.7;h[e]=104.;l[e]=101.5;c[e]=102.
        o[j]=102.8;h[j]=103.;l[j]=100.8;c[j]=101.;o[j+1]=101.
    q[e]=2e6
    raw=[t,o,h,l,c]
    f=dict(eligible=np.ones(n,bool),session_bar=np.arange(n)%32,session_anchor=np.full(n,anchor,dtype=np.int64),
        session_vwap=np.full(n,100.),prior_atr=np.ones(n),volume_multiple=np.ones(n),
        buy_share=np.full(n,.5),r1=np.zeros(n),clv=(c-l)/(h-l))
    f['volume_multiple'][e]=2.
    btc=dict(close=np.full(n,20000.),r1=np.zeros(n))
    cfg=next(x for x in s.configurations() if x['side']==side and x['displacement']==2.
        and x['volume']==1.5 and x['confirmation']==.5)
    return raw,q,buy,f,btc,cfg,e,j

class GridAndFeatureTests(unittest.TestCase):
    def test_exact_frozen_grid(self):
        self.assertEqual(len(s.configurations()),16)
        self.assertEqual(len(s.policies()),96)
        self.assertEqual({p['hold'] for p in s.policies()},{16,32})
        self.assertEqual({p['exit_type'] for p in s.policies()},{'VWAP','R15','R25'})
    def test_session_anchor_and_causal_vwap(self):
        raw,q,buy,_,_,_,_,_=fixture();f=s.features(raw,q,buy)
        self.assertEqual(f['session_bar'][0],0);self.assertEqual(f['session_bar'][31],31)
        typical=(raw[2]+raw[3]+raw[4])/3
        expected=np.cumsum(q)/np.cumsum(q/typical)
        np.testing.assert_allclose(f['session_vwap'][:32],expected[:32])
    def test_future_perturbation_does_not_change_prefix(self):
        raw,q,buy,_,_,_,_,_=fixture();old=s.features(raw,q,buy)
        for z in raw[1:]:z[250:]*=3
        q[250:]*=7;new=s.features(raw,q,buy)
        for k in ('session_vwap','prior_atr','prior_quote_median','volume_multiple'):
            np.testing.assert_allclose(old[k][:250],new[k][:250],equal_nan=True)
    def test_gap_invalidates_new_session_until_real_anchor(self):
        raw,q,buy,_,_,_,_,_=fixture();raw[0][20:]+=s.BAR;f=s.features(raw,q,buy)
        self.assertTrue((f['session_bar'][20:31]<0).all());self.assertEqual(f['session_bar'][31],0)
    def test_current_volume_not_in_rolling_median(self):
        raw,q,buy,_,_,_,e,_=fixture();q[:e]=1e6;q[e]=99e6;f=s.features(raw,q,buy)
        self.assertEqual(f['prior_quote_median'][e],1e6)
        self.assertEqual(f['volume_multiple'][e],99.)

class EventTests(unittest.TestCase):
    def test_mirrored_sides_and_later_confirmation(self):
        for side in (1,-1):
            raw,_,_,f,btc,cfg,e,j=fixture(side);events,_=s.failed_auction_events(cfg,raw,f,btc)
            self.assertEqual(events[0]['event_index'],e);self.assertEqual(events[0]['index'],j)
    def test_same_bar_confirmation_is_impossible(self):
        raw,_,_,f,btc,cfg,e,_=fixture();events,_=s.failed_auction_events(cfg,raw,f,btc)
        self.assertGreater(events[0]['index'],e)
    def test_four_bar_window_inclusive(self):
        raw,_,_,f,btc,cfg,e,j=fixture(confirm_age=4);self.assertEqual(s.failed_auction_events(cfg,raw,f,btc)[0][0]['index'],j)
        raw,_,_,f,btc,cfg,e,j=fixture(confirm_age=5);self.assertFalse(s.failed_auction_events(cfg,raw,f,btc)[0])
    def test_event_filters(self):
        for key in ('displacement','volume','wick','outer','session'):
            raw,_,_,f,btc,cfg,e,_=fixture()
            if key=='displacement':f['session_vwap'][e]=98.
            elif key=='volume':f['volume_multiple'][e]=1.49
            elif key=='wick':raw[3][e]=97.7
            elif key=='outer':f['clv'][e]=.5
            else:f['session_bar'][e]=25
            self.assertFalse(s.failed_auction_events(cfg,raw,f,btc)[0],key)
    def test_confirmation_body_midpoint_strength_and_path(self):
        for key in ('body','midpoint','strength','gap','session'):
            raw,_,_,f,btc,cfg,e,j=fixture()
            if key=='body':
                raw[1][e+1:e+5]=101.;raw[4][e+1:e+5]=100.
            elif key=='midpoint':
                raw[1][e+1:e+5]=96.5;raw[4][e+1:e+5]=97.
            elif key=='strength':
                raw[1][e+1:e+5]=96.1;raw[4][e+1:e+5]=96.4
            elif key=='gap':raw[0][j:]+=s.BAR
            else:f['session_anchor'][e+1:e+5]+=8*60*60*1000
            self.assertFalse(s.failed_auction_events(cfg,raw,f,btc)[0],key)
    def test_frozen_event_vwap_survives_future_changes(self):
        raw,_,_,f,btc,cfg,e,j=fixture();before=s.failed_auction_events(cfg,raw,f,btc)[0][0]
        f['session_vwap'][j:]=999.;after=s.failed_auction_events(cfg,raw,f,btc)[0][0]
        self.assertEqual(before['event_vwap'],after['event_vwap'])

class EntryExitTests(unittest.TestCase):
    def rows(self,side=1):
        raw,_,_,f,btc,cfg,e,j=fixture(side)
        return raw,f,btc,cfg,s.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)
    def test_next_open_and_structural_stop(self):
        for side in (1,-1):
            raw,f,btc,cfg,(rows,bad)=self.rows(side);row=rows[0]
            self.assertEqual(row['entry_time'],raw[0][102]);self.assertEqual(row['event_time'],raw[0][100])
            self.assertEqual(row['event_vwap'],100.);self.assertLessEqual(row['risk_pct'],.06)
    def test_favourable_gap_excluded_adverse_retained(self):
        raw,_,_,f,btc,cfg,e,j=fixture();raw[1][j+1]=raw[4][j]*1.006
        self.assertEqual(s.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)[1]['ENTRY_CATCHUP_GAP'],1)
        raw,_,_,f,btc,cfg,e,j=fixture();raw[1][j+1]=98.
        self.assertTrue(s.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)[0])
    def test_stop_floor_and_cap(self):
        raw,_,_,f,btc,cfg,e,j=fixture();events,_=s.failed_auction_events(cfg,raw,f,btc)
        events[0]['event_extreme']=raw[1][j+1]-.001;events[0]['event_atr']=.001
        with patch.object(s,'failed_auction_events',return_value=(events,{})):
            row=s.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)[0][0]
        self.assertAlmostEqual(row['risk_pct'],.005)
        events[0]['event_extreme']=80.
        with patch.object(s,'failed_auction_events',return_value=(events,{})):
            rows,bad=s.intents('X',cfg,raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)
        self.assertFalse(rows);self.assertEqual(bad['STOP_ABOVE_6PCT'],1)
    def test_frozen_vwap_and_r_targets(self):
        raw,f,btc,cfg,(seeds,_)=self.rows();result=dict(status='RESOLVED',exit_time=raw[0][104],exit=100.,reason='TP',gross_return=.01)
        expected={}
        for kind in ('VWAP','R15','R25'):
            p=dict(cfg,hold=16,exit_type=kind,policy='P')
            with patch.object(s.canonical,'resolve',return_value=result) as resolver:
                s.policy_rows('X',[p],raw,f,btc,raw[0][0],raw[0][-1]+s.BAR)
            tr=resolver.call_args.args[0];expected[kind]=tr['tp']
        risk=abs(seeds[0]['entry']-seeds[0]['sl'])
        self.assertEqual(expected['VWAP'],seeds[0]['event_vwap'])
        self.assertAlmostEqual(expected['R15'],seeds[0]['entry']+1.5*risk)
        self.assertAlmostEqual(expected['R25'],seeds[0]['entry']+2.5*risk)
        self.assertTrue(all(p['hold'] in (16,32) for p in s.policies()))

if __name__=='__main__':unittest.main()
