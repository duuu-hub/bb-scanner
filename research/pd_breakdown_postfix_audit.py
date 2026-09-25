#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();o=Path(a.out);o.mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"])
# Frozen post-fix candidates; N640 primary, N320 comparator. No parameter selection on tail.
t=t[(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1.0)&(t.rr==3.0)&t.n.isin([320,400,480,560,640])].copy()
def st(g):
 w=g.loc[g.r_net>0,"r_net"].sum();l=-g.loc[g.r_net<0,"r_net"].sum()
 return pd.Series({"trades":len(g),"symbols":g.symbol.nunique(),"wr":(g.r_net>0).mean(),"pf":w/l if l else np.nan,"exp_r":g.r_net.mean(),"net_sum":g.net_return.sum()})
overall=t.groupby("n").apply(st,include_groups=False).reset_index();overall.to_csv(o/"overall.csv",index=False)
year=t.groupby(["n","year"]).apply(st,include_groups=False).reset_index();year.to_csv(o/"yearly.csv",index=False)
# fixed calendar tail check, explicitly post-selection diagnostic, not pristine OOS
cut=pd.Timestamp("2025-01-01",tz="UTC")
tail=t[t.entry_time>=cut].groupby("n").apply(st,include_groups=False).reset_index();tail.to_csv(o/"tail_2025_2026.csv",index=False)
# costs: original 8bp, add extra return cost converted to R via risk fraction = net_return/r_net
rows=[]
for n,g in t.groupby("n"):
 rf=(g.net_return/g.r_net).replace([np.inf,-np.inf],np.nan)
 for cost in (.0008,.0012,.0016,.0024):
  rn=g.r_net-(cost-.0008)/rf
  rn=rn.replace([np.inf,-np.inf],np.nan).dropna();w=rn[rn>0].sum();l=-rn[rn<0].sum()
  rows.append({"n":n,"cost":cost,"trades":len(rn),"pf":w/l,"exp_r":rn.mean()})
pd.DataFrame(rows).to_csv(o/"cost.csv",index=False)
# concentration
rows=[]
for n,g in t.groupby("n"):
 q=g.groupby("symbol").r_net.sum().sort_values(ascending=False);pos=q[q>0];den=pos.sum()
 rows.append({"n":n,"symbols":len(q),"positive_symbols":(q>0).sum(),"top1_share":pos.head(1).sum()/den,"top5_share":pos.head(5).sum()/den,"top20_share":pos.head(20).sum()/den})
pd.DataFrame(rows).to_csv(o/"concentration.csv",index=False)
# chronological slot portfolio in R units; tie-break symbol deterministic
rows=[]
for n,g in t.groupby("n"):
 g=g.sort_values(["entry_time","symbol"])
 for slots in (1,3,5,10,20):
  active=[];vals=[]
  for r in g.itertuples():
   active=[x for x in active if x>r.entry_time]
   if len(active)>=slots: continue
   active.append(r.exit_time);vals.append(r.r_net)
  eq=np.cumsum(vals);peak=np.maximum.accumulate(np.r_[0,eq]);dd=peak[1:]-eq
  rows.append({"n":n,"slots":slots,"accepted":len(vals),"net_r":sum(vals),"avg_r":np.mean(vals),"mdd_r":dd.max() if len(dd) else 0})
pd.DataFrame(rows).to_csv(o/"slots.csv",index=False)
assert set(overall.n)=={320,400,480,560,640} and (overall.trades>1000).all() and (overall.symbols>500).all()
assert set(tail.n)==set(overall.n) and (tail.trades>500).all()
print("OVERALL\n",overall.to_string(index=False));print("TAIL\n",tail.to_string(index=False));print("COST24\n",pd.DataFrame(rows).head(0).to_string(index=False))
print("VERIFIED")
