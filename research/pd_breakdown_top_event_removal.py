#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
T=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"]);T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy();T["year"]=T.entry_time.dt.year;T["signals"]=T.groupby("entry_time").symbol.transform("nunique");T["risk_frac"]=T.net_return/T.r_net
W=pd.read_csv(a.wf)[["test_year","chosen_threshold"]].drop_duplicates();W=W[(W.test_year>=2023)&(W.test_year<=2026)]
Z=pd.concat([T[(T.year==y)&(T.signals>=th)] for y,th in W.itertuples(index=False)]).sort_values(["entry_time","symbol"])
# neutral 5-name basket; rank events by baseline 8bp mean R
ev=[]
for ts,g in Z.groupby("entry_time",sort=True):
 g=g.head(5);ev.append(dict(ts=ts,score=float(g.r_net.mean())))
rank=[x["ts"] for x in sorted(ev,key=lambda x:x["score"],reverse=True)]
def sim(dropn,bp,risk):
 drop=set(rank[:dropn]);eq=peak=1.;mdd=0.;active=[];rs=[];events=0
 for ts,g in Z.groupby("entry_time",sort=True):
  done=[p for p in active if p[0]<=ts]
  for ex,pnl in sorted(done):eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
  active=[p for p in active if p[0]>ts]
  if ts in drop:continue
  free=5-len(active);base=eq;taken=0
  for x in g.head(max(0,free)).itertuples():
   r=x.r_net-((bp-8)/10000)/x.risk_frac;active.append((x.exit_time,base*risk*r));rs.append(r);taken+=1
  if taken:events+=1
 for ex,pnl in sorted(active):eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
 pos=sum(r for r in rs if r>0);neg=-sum(r for r in rs if r<0);pf=pos/neg if neg else np.inf
 return eq,mdd,len(rs),events,pf,float(np.mean(rs))
rows=[]
for d in [0,1,3,5]:
 for bp in [8,24]:
  for risk in [.02,.03,.04]:
   eq,dd,n,e,pf,av=sim(d,bp,risk);rows.append(dict(drop_top=d,cost_bp=bp,risk=risk,final_equity=eq,total_return=eq-1,mdd=dd,return_mdd=(eq-1)/dd,accepted=n,events=e,pf=pf,avg_r=av))
R=pd.DataFrame(rows);R.to_csv(O/"top_event_removal.csv",index=False);pd.DataFrame(ev).sort_values("score",ascending=False).to_csv(O/"event_rank.csv",index=False);print(R.to_string(index=False))
