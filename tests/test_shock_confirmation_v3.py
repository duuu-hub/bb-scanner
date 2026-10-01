"""Causal shock confirmation, flow filtering and structural stops."""
import unittest
from unittest.mock import patch
import numpy as np
from scripts import shock_confirmation_v3 as s

def raw(n=3500):
    t=np.arange(n,dtype=np.int64)*s.BAR
    c=100+np.sin(np.arange(n)/15)
    return [t,c.copy(),c+1,c-1,c.copy()]

class ConfirmTests(unittest.TestCase):
    def test_predeclared_grid(self):
        self.assertEqual(len(s.configurations()),16)
        self.assertEqual(len(s.policies()),96)
        self.assertEqual(len({p['policy'] for p in s.policies()}),96)

    def test_features_ignore_future_prices_and_flow(self):
        p=raw();q=np.ones(3500)*1e6;b=q*.5
        f=s.features(p,q,b)
        changed=[x.copy() for x in p];cq=q.copy();cb=b.copy()
        for x in changed[1:]:x[3300:]*=2
        cq[3300:]*=3;cb[3300:]*=4
        g=s.features(changed,cq,cb)
        for k in f:np.testing.assert_allclose(f[k][:3300],g[k][:3300],equal_nan=True)

    def test_shock_window_excludes_current_confirming_bar(self):
        p=raw(n=400);q=np.ones(400)*1e6
        p[4][200]=80;p[3][200]=79
        f=s.features(p,q,q*.5)
        self.assertLess(f['recent_down'][200],.03)
        self.assertGreater(f['recent_down'][201],.15)

    def test_flow_share_uses_actual_quote_ratio(self):
        p=raw(n=400);q=np.ones(400)*1e6
        f=s.features(p,q,q*.6)
        np.testing.assert_allclose(f['buy_share'],.6)

    def test_gap_restarts_recent_shock_and_stop_window(self):
        p=raw(n=400);p[0][200:]+=s.BAR;q=np.ones(400)*1e6
        f=s.features(p,q,q*.5)
        self.assertTrue(np.isnan(f['recent_down'][200:205]).all())
        self.assertTrue(np.isnan(f['low5'][200:204]).all())

    def test_structural_stop_and_actual_fill_risk(self):
        p=raw(n=400);f={'atr':np.full(400,.01),'low5':np.full(400,95.),
                       'high5':np.full(400,105.),'recent_down':np.full(400,.06),
                       'recent_up':np.full(400,.06),'buy_share':np.full(400,.6)}
        cfg=dict(key='X',side=1)
        p[1][201]=100.
        mask=np.zeros(400,bool);mask[200]=True
        with patch.object(s,'mask',return_value=mask):
            entries,exc=s.intents('X',cfg,p,f,{'r1':np.zeros(400)},0,400*s.BAR)
        self.assertEqual(len(entries),1)
        self.assertAlmostEqual(entries[0]['sl'],95.-.25*f['atr'][200]*p[4][200])
        self.assertEqual(entries[0]['entry_time'],201*s.BAR)

    def test_cooldown_is_fixed_not_outcome_conditioned(self):
        p=raw(n=400);q=np.ones(400)*1e6;f=s.features(p,q,q*.6)
        f['low5'][:]=97.;f['recent_down'][:]=.06
        mask=np.zeros(400,bool);mask[[200,201,202,204]]=True
        with patch.object(s,'mask',return_value=mask):
            e,_=s.intents('X',{'key':'X','side':1},p,f,{'r1':np.zeros(400)},0,400*s.BAR)
        self.assertEqual([x['entry_time'] for x in e],[201*s.BAR,205*s.BAR])

    def test_future_confirming_bar_cannot_move_old_stop(self):
        p=raw(n=400);q=np.ones(400)*1e6;f=s.features(p,q,q*.6)
        reference=f['low5'][200]
        p[3][201]=1.
        changed=s.features(p,q,q*.6)
        self.assertEqual(changed['low5'][200],reference)

if __name__=='__main__':unittest.main()
