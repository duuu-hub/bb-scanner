#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);ap.add_argument("--sims",type=int,default=10000);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
T=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"]);T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy();T["year"]=T.entry_time.dt.year;T["signals"]=T.groupby("entry_time").symbol.transform("nunique");T["risk_frac"]=T.net_return/T.r_net
W=pd.read_csv(a.wf)[["test_year","chosen_threshold"]].drop_duplicates();W=W[(W.test_year>=2023)&(W.test_year<=2026)]
Z=pd.concat([T[(T.year==y)&(T.signals>=th)] for y,th in W.itertuples(index=False)]).sort_values(["entry_time","symbol"])
# neutral 5-name basket, same as validated portfolio. Aggregate each event into simultaneous equity return.
E=[]
for ts,g in Z.groupby("entry_time",sort=True):
 g=g.head(5); E.append((ts,g.r_net.to_numpy(float),g.risk_frac.to_numpy(float)))
assert len(E)==42
RISKS=[.02,.03,.04,.05,.06]; COSTS=[8,24]; rng=np.random.default_rng(20260927)
def path(order,risk,bp):
 eq=1.;peak=1.;mdd=0.
 for k in order:
  _,rr,rf=E[k]; adj=rr-((bp-8)/10000)/rf
  eq*=max(0.,1.+risk*adj.sum())
  peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak else 1.)
  if eq<=0:return 0.,1.
 return eq,mdd
rows=[]; detail=[]
for bp in COSTS:
 for risk in RISKS:
  base_eq,base_mdd=path(np.arange(len(E)),risk,bp); eqs=np.empty(a.sims);dds=np.empty(a.sims)
  for i in range(a.sims):
   eqs[i],dds[i]=path(rng.permutation(len(E)),risk,bp)
  q=lambda x,p:float(np.quantile(x,p))
  rows.append(dict(cost_bp=bp,risk=risk,sims=a.sims,actual_final=base_eq,actual_mdd=base_mdd,
   final_p01=q(eqs,.01),final_p05=q(eqs,.05),final_median=q(eqs,.5),final_p95=q(eqs,.95),
   mdd_median=q(dds,.5),mdd_p90=q(dds,.9),mdd_p95=q(dds,.95),mdd_p99=q(dds,.99),mdd_worst=float(dds.max()),
   prob_mdd50=float(np.mean(dds>=.5)),prob_mdd60=float(np.mean(dds>=.6)),prob_mdd70=float(np.mean(dds>=.7)),prob_ruin=float(np.mean(eqs<=0))))
R=pd.DataFrame(rows);R.to_csv(O/"mc_summary.csv",index=False);print(R.to_string(index=False))
