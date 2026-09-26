#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
T=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"]);T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy();T["year"]=T.entry_time.dt.year;T["signals"]=T.groupby("entry_time").symbol.transform("nunique");T["risk_frac"]=T.net_return/T.r_net
W=pd.read_csv(a.wf)[["test_year","chosen_threshold"]].drop_duplicates();W=W[(W.test_year>=2023)&(W.test_year<=2026)]
Z=pd.concat([T[(T.year==y)&(T.signals>=th)] for y,th in W.itertuples(index=False)])
RISKS=[.0005,.001,.0015,.002,.0025,.0035,.005,.0075,.01,.0125,.015,.02,.025,.03]
def sim(q,b,risk,bp):
 q=q.copy();q["r"]=q.r_net-((bp-8)/10000)/q.risk_frac;q=q.sort_values(["entry_time","symbol"]).groupby("entry_time",group_keys=False).head(b)
 eq=1.;peak=1.;mdd=0.;active=[];accepted=[];ruined=False
 for ts,g in q.groupby("entry_time",sort=True):
  done=[p for p in active if p[0]<=ts]
  for ex,pnl,y in sorted(done):
   eq+=pnl
   if eq<=0: ruined=True;eq=max(eq,0.0)
   peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 1)
  active=[p for p in active if p[0]>ts]
  if ruined: continue
  free=b-len(active);base=eq
  for r in g.head(max(0,free)).itertuples():
   active.append((r.exit_time,base*risk*r.r,r.year));accepted.append((r.entry_time,r.r,r.year))
 for ex,pnl,y in sorted(active):
  eq+=pnl
  if eq<=0: ruined=True;eq=max(eq,0.0)
  peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 1)
 A=pd.DataFrame(accepted,columns=["entry","r","year"])
 return dict(final_equity=eq,total_return=eq-1,mdd=mdd,accepted=len(A),events=A.entry.nunique() if len(A) else 0,ruined=ruined)
rows=[]
for b in [5,10]:
 for bp in [8,24]:
  for risk in RISKS:
   d=sim(Z,b,risk,bp);d.update(basket=b,cost_bp=bp,risk_per_symbol=risk,total_nominal_event_risk=b*risk);rows.append(d)
R=pd.DataFrame(rows);R["return_mdd"]=np.where(R.mdd>0,R.total_return/R.mdd,np.nan);R.to_csv(O/"risk_sweep.csv",index=False)
print(R.to_string(index=False))
