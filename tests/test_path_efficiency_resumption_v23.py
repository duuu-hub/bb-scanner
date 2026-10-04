import unittest
from unittest import mock
import numpy as np
from scripts import day_edge_lab as base
from scripts import path_efficiency_resumption_v23 as v23

def fixture(side=1, flow=.60):
    n,i,w=140,110,8;t=base.START+np.arange(n,dtype=np.int64)*base.BAR;c=np.full(n,100.)
    formation=np.exp(np.linspace(np.log(100 if side==1 else 102),np.log(102 if side==1 else 100),w+1))
    c[i-w-2:i-1]=formation;move=abs(np.log(formation[-1]/formation[0]))
    c[i-1]=formation[-1]*np.exp(-side*.20*move);c[i]=c[i-1]*np.exp(side*.006);c[i+1:]=c[i]
    o=c.copy();o[i-1]=formation[-1];o[i]=c[i-1]
    h,l=np.maximum(o,c)*1.0005,np.minimum(o,c)*.9995
    q=np.full(n,1_000_000.);q[i-1],q[i]=900_000.,1_500_000.
    f={"_quote":q,"eligible":np.ones(n,dtype=bool),"prior_atr":np.full(n,.50),
       "prior_quote_mean":np.full(n,1_000_000.),"buy_share":np.full(n,flow if side==1 else 1-flow),
       "volume_multiple":q/1_000_000.,"r1":np.zeros(n),"clv":np.full(n,.5)}
    cfg=next(x for x in v23.configurations() if x["side"]==side and x["window"]==8
             and x["efficiency"]==.55 and x["confirmation"]=="FLOW55")
    return i,cfg,(t,o,h,l,c),f

class PathEfficiencyResumptionV23Tests(unittest.TestCase):
    def test_frozen_grid(self):
        self.assertEqual((len(v23.configurations()),len(v23.policies())),(16,96))
        self.assertEqual(len({x["key"] for x in v23.configurations()}),16)
        self.assertEqual({x["exit_type"] for x in v23.policies()},{"R15","R25","TRAIL"})
    def test_smooth_long_qualifies(self):
        i,c,r,f=fixture();x=v23.setup_at(i,c,r,f);self.assertIsNotNone(x)
        self.assertGreater(x["formation_efficiency"],.99);self.assertTrue(.05<=x["pause_retrace"]<=.35)
    def test_symmetric_short_qualifies(self):
        i,c,r,f=fixture(-1);self.assertIsNotNone(v23.setup_at(i,c,r,f))
    def test_flow55_and_price_only(self):
        i,c,r,f=fixture(flow=.40);self.assertIsNone(v23.setup_at(i,c,r,f))
        self.assertIsNotNone(v23.setup_at(i,dict(c,confirmation="PRICE_ONLY"),r,f))
    def test_one_bar_dominance_rejected(self):
        i,c,r,f=fixture();close=r[4].copy();start=i-c["window"]-2;close[start+1:i-1]=close[start+1]
        bad=(r[0],r[1],np.maximum(r[1],close)*1.0005,np.minimum(r[1],close)*.9995,close)
        self.assertIsNone(v23.setup_at(i,c,bad,f))
    def test_deep_pause_rejected(self):
        i,c,r,f=fixture();close=r[4].copy();op=r[1].copy();finish=i-2
        move=abs(np.log(close[finish]/close[i-c["window"]-2]));close[i-1]=close[finish]*np.exp(-c["side"]*.50*move);op[i]=close[i-1]
        bad=(r[0],op,np.maximum(op,close)*1.0005,np.minimum(op,close)*.9995,close)
        self.assertIsNone(v23.setup_at(i,c,bad,f))
    def test_future_prices_do_not_change_seed(self):
        i,c,r,f=fixture();btc={"r1":np.zeros(len(r[0]))};end=int(r[0][-1]+base.BAR)
        before,_=v23.intents("X",c,r,f,btc,base.START,end);changed=tuple(x.copy() for x in r)
        changed[1][i+2:]*=4;changed[2][i+2:]*=4;changed[3][i+2:]*=.25;changed[4][i+2:]*=2
        after,_=v23.intents("X",c,changed,f,btc,base.START,end);self.assertEqual(before,after);self.assertEqual(len(before),1)
    def test_scan_forwards_context_and_restores_engine(self):
        names=('CONTEXT','COLUMNS','LOG_PREFIX','configurations','policies','load','features','intents','policy_rows','BTC_LOAD','BTC_FEATURES')
        original={n:getattr(v23.engine,n) for n in names}
        try:
            with mock.patch.object(v23.engine,"scan") as scan:v23.scan(1,2,3,4,5,6)
            self.assertEqual(scan.call_args.kwargs["context_path"],v23.CONTEXT)
        finally:
            for n,x in original.items():setattr(v23.engine,n,x)

if __name__=="__main__":unittest.main()
