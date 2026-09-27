#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
T=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"]);T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy();T["year"]=T.entry_time.dt.year;T["signals"]=T.groupby("entry_time").symbol.transform("nunique");T["risk_frac"]=T.net_return/T.r_net
W=pd.read_csv(a.wf)[["test_year","chosen_threshold"]].drop_duplicates();W=W[(W.test_year>=2023)&(W.test_year<=2026)]
Z=pd.concat([T[(T.year==y)&(T.signals>=th)] for y,th in W.itertuples(index=False)]).sort_values(["entry_time","symbol"])
schemes={"fixed2":(9999,9999,.02,.02,.02),"fixed3":(9999,9999,.03,.03,.03),"fixed4":(9999,9999,.04,.04,.04),
"dyn_81_101":(81,101,.02,.03,.04),"dyn_91_111":(91,111,.02,.03,.04),"dyn_81_121":(81,121,.02,.03,.04),
"dyn_81_101_aggr":(81,101,.02,.035,.05),"dyn_91_111_aggr":(91,111,.02,.035,.05)}
def rrisk(n,s):
 a,b,r0,r1,r2=s
 return r0 if n<a else (r1 if n<b else r2)
def sim(s,bp):
 eq=1.;peak=1.;mdd=0.;active=[];acc=[];ev=[]
 for ts,g in Z.groupby("entry_time",sort=True):
  done=[p for p in active if p[0]<=ts]
  for ex,pnl in sorted(done):eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
  active=[p for p in active if p[0]>ts]; free=5-len(active);base=eq;n=int(g.signals.iloc[0]);rk=rrisk(n,s);taken=0
  for x in g.head(max(0,free)).itertuples():
   r=x.r_net-((bp-8)/10000)/x.risk_frac;active.append((x.exit_time,base*rk*r));acc.append(r);taken+=1
  if taken:ev.append((ts,n,rk,taken))
 for ex,pnl in sorted(active):eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
 return eq,mdd,len(acc),len(ev),max([x[2] for x in ev],default=0)
rows=[]
for bp in [8,24]:
 for name,s in schemes.items():
  eq,dd,n,e,mr=sim(s,bp);rows.append(dict(cost_bp=bp,scheme=name,final_equity=eq,total_return=eq-1,mdd=dd,return_mdd=(eq-1)/dd,accepted=n,events=e,max_risk=mr))
R=pd.DataFrame(rows);R.to_csv(O/"dynamic_risk_compare.csv",index=False);print(R.sort_values(["cost_bp","return_mdd"],ascending=[True,False]).to_string(index=False))
