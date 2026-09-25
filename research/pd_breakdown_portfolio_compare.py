#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd, numpy as np
ap=argparse.ArgumentParser(); ap.add_argument("--trades",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
Path(a.out).mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"])
c=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1.0)&(t.rr==3.0)].sort_values(["entry_time","symbol"]).copy()
assert len(c)>10000 and c.symbol.nunique()>500
cut=c.entry_time.quantile(.60)
def sim(g,slots,rf,cap):
 eq=1.; peak=1.; mdd=0.; active=[]; accepted=0; sumr=0.
 for r in g.itertuples():
  done=[p for p in active if p[0]<=r.entry_time]
  for ex,pnl in sorted(done,key=lambda z:z[0]):
   eq+=pnl; peak=max(peak,eq); mdd=max(mdd,(peak-eq)/peak)
  active=[p for p in active if p[0]>r.entry_time]
  if len(active)>=slots: continue
  # cap total initial risk across simultaneously open positions
  per=min(rf, max(0.,cap-len(active)*rf))
  if per<=0: continue
  active.append((r.exit_time,eq*per*r.r_net)); accepted+=1; sumr+=r.r_net
 for ex,pnl in sorted(active,key=lambda z:z[0]):
  eq+=pnl; peak=max(peak,eq); mdd=max(mdd,(peak-eq)/peak)
 return dict(accepted=accepted,accept_rate=accepted/len(g),avg_r=sumr/accepted if accepted else np.nan,final_equity=eq,total_return=eq-1,mdd_pct=mdd,return_over_mdd=(eq-1)/mdd if mdd else np.nan)
rows=[]
# baseline plus slot caps, all at 0.25% risk/trade
for slots in (3,5,10):
 d=sim(c,slots,.0025,99); d.update(case=f"slots_{slots}",slots=slots,risk_per_trade=.0025,risk_cap=np.nan); rows.append(d)
# 10 slots with portfolio open-risk caps
for cap in (.01,.015,.02):
 d=sim(c,10,.0025,cap); d.update(case=f"riskcap_{cap:.3f}",slots=10,risk_per_trade=.0025,risk_cap=cap); rows.append(d)
pd.DataFrame(rows).to_csv(Path(a.out)/"comparison.csv",index=False)
# same cases on chronological OOS 40%
o=c[c.entry_time>cut]
rows=[]
for slots in (3,5,10):
 d=sim(o,slots,.0025,99); d.update(case=f"slots_{slots}"); rows.append(d)
for cap in (.01,.015,.02):
 d=sim(o,10,.0025,cap); d.update(case=f"riskcap_{cap:.3f}"); rows.append(d)
pd.DataFrame(rows).to_csv(Path(a.out)/"comparison_oos.csv",index=False)
print("ALL"); print(pd.read_csv(Path(a.out)/"comparison.csv").to_string(index=False))
print("OOS"); print(pd.read_csv(Path(a.out)/"comparison_oos.csv").to_string(index=False))
