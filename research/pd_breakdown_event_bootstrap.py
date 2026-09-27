#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);ap.add_argument("--sims",type=int,default=50000);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
T=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"]);T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy();T["year"]=T.entry_time.dt.year;T["signals"]=T.groupby("entry_time").symbol.transform("nunique");T["risk_frac"]=T.net_return/T.r_net
W=pd.read_csv(a.wf)[["test_year","chosen_threshold"]].drop_duplicates();W=W[(W.test_year>=2023)&(W.test_year<=2026)]
Z=pd.concat([T[(T.year==y)&(T.signals>=th)] for y,th in W.itertuples(index=False)]).sort_values(["entry_time","symbol"])
E=[]
for ts,g in Z.groupby("entry_time",sort=True):
 g=g.head(5);E.append((g.r_net.to_numpy(float),g.risk_frac.to_numpy(float)))
assert len(E)==42
rng=np.random.default_rng(20260927);rows=[]
for bp in [8,24]:
 for risk in [.02,.0225,.025,.0275,.03]:
  finals=np.empty(a.sims);dds=np.empty(a.sims)
  for i in range(a.sims):
   eq=peak=1.;dd=0.
   for k in rng.integers(0,len(E),size=len(E)):
    rr,rf=E[k];adj=rr-((bp-8)/10000)/rf;eq*=max(0.,1+risk*adj.sum());peak=max(peak,eq);dd=max(dd,(peak-eq)/peak)
    if eq<=0:break
   finals[i]=eq;dds[i]=dd
  q=lambda x,p:float(np.quantile(x,p))
  rows.append(dict(cost_bp=bp,risk=risk,sims=a.sims,final_p01=q(finals,.01),final_p05=q(finals,.05),final_median=q(finals,.5),final_p95=q(finals,.95),mdd_median=q(dds,.5),mdd_p95=q(dds,.95),mdd_p99=q(dds,.99),prob_loss=float(np.mean(finals<1)),prob_ruin=float(np.mean(finals<=0)),prob_mdd50=float(np.mean(dds>=.5)),prob_mdd70=float(np.mean(dds>=.7))))
R=pd.DataFrame(rows);R.to_csv(O/"bootstrap_summary.csv",index=False);print(R.to_string(index=False))
