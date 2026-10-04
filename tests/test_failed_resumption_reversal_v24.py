import unittest
import numpy as np
from scripts import day_edge_lab as base
from scripts import failed_resumption_reversal_v24 as v24


def fixture(side=1, failure_flow=.40):
    n,i,w=150,115,8;t=base.START+np.arange(n,dtype=np.int64)*base.BAR;c=np.full(n,100.)
    formation=np.exp(np.linspace(np.log(100 if side==1 else 102),np.log(102 if side==1 else 100),w+1))
    start=i-w-3;finish=i-3;pause=i-2;attempt=i-1
    c[start:finish+1]=formation;move=abs(np.log(formation[-1]/formation[0]))
    c[pause]=formation[-1]*np.exp(-side*.20*move)
    c[attempt]=c[pause]*np.exp(side*.006)
    c[i]=formation[-1]*np.exp(-side*.006);c[i+1:]=c[i]
    o=c.copy();o[pause]=formation[-1];o[attempt]=c[pause];o[i]=c[attempt]
    h,l=np.maximum(o,c)*1.0005,np.minimum(o,c)*.9995
    q=np.full(n,1_000_000.);q[pause],q[attempt]=800_000.,1_500_000.
    buy=np.full(n,.5);buy[attempt]=.60 if side==1 else .40;buy[i]=failure_flow
    f={"_quote":q,"eligible":np.ones(n,dtype=bool),"prior_atr":np.full(n,.50),
       "prior_quote_mean":np.full(n,1_000_000.),"buy_share":buy,"volume_multiple":q/1_000_000.,
       "r1":np.zeros(n),"clv":np.full(n,.5)}
    cfg=next(x for x in v24.configurations() if x["side"]==side and x["window"]==8
             and x["efficiency"]==.55 and x["confirmation"]=="OPPOSITE_FLOW55")
    return i,cfg,(t,o,h,l,c),f


class FailedResumptionReversalV24Tests(unittest.TestCase):
    def test_frozen_grid(self):
        self.assertEqual((len(v24.configurations()),len(v24.policies())),(16,96))
        self.assertEqual({x["exit_type"] for x in v24.policies()},{"R10","R15","TRAIL"})
    def test_long_path_failure_qualifies_and_reverses_short(self):
        i,c,r,f=fixture();self.assertIsNotNone(v24.setup_at(i,c,r,f))
        seeds,_=v24.intents("X",c,r,f,{"r1":np.zeros(len(r[0]))},base.START,int(r[0][-1]+base.BAR))
        self.assertEqual(len(seeds),1);self.assertEqual(seeds[0]["side"],-1);self.assertEqual(seeds[0]["atr_mult"],2.0)
    def test_short_path_failure_qualifies_and_reverses_long(self):
        i,c,r,f=fixture(-1,.60);self.assertIsNotNone(v24.setup_at(i,c,r,f))
        seeds,_=v24.intents("X",c,r,f,{"r1":np.zeros(len(r[0]))},base.START,int(r[0][-1]+base.BAR))
        self.assertEqual(seeds[0]["side"],1)
    def test_opposite_flow_required_only_for_flow_variant(self):
        i,c,r,f=fixture(failure_flow=.60);self.assertIsNone(v24.setup_at(i,c,r,f))
        self.assertIsNotNone(v24.setup_at(i,dict(c,confirmation="REENTRY"),r,f))
    def test_attempt_and_failure_must_be_distinct(self):
        i,c,r,f=fixture();close=r[4].copy();close[i]=close[i-1]
        bad=(r[0],r[1],np.maximum(r[1],close)*1.0005,np.minimum(r[1],close)*.9995,close)
        self.assertIsNone(v24.setup_at(i,c,bad,f))
    def test_future_prices_do_not_change_seed(self):
        i,c,r,f=fixture();btc={"r1":np.zeros(len(r[0]))};end=int(r[0][-1]+base.BAR)
        before,_=v24.intents("X",c,r,f,btc,base.START,end);changed=tuple(x.copy() for x in r)
        for k in range(1,5):changed[k][i+2:]*=3
        after,_=v24.intents("X",c,changed,f,btc,base.START,end)
        self.assertEqual(before,after);self.assertEqual(len(before),1)
    def test_gap_through_structural_stop_is_not_moved_past_entry(self):
        i,c,r,f=fixture();t,o,h,l,close=(x.copy() for x in r)
        # Reversal is short.  Gap the next open above its structural stop; the
        # seed must retain that already-breached level instead of inventing a
        # new stop above the adverse entry.
        structural=max(h[i-1],h[i])+.10*f["prior_atr"][i-2]
        o[i+1]=structural+.10
        seeds,_=v24.intents("X",c,(t,o,h,l,close),f,{"r1":np.zeros(len(t))},base.START,int(t[-1]+base.BAR))
        self.assertEqual(len(seeds),1)
        self.assertLess(seeds[0]["sl"],seeds[0]["entry"])
        self.assertAlmostEqual(seeds[0]["sl"],structural)


if __name__=="__main__":unittest.main()
