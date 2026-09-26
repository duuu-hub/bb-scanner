#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
T=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"]);T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy();T["year"]=T.entry_time.dt.year;T["signals"]=T.groupby("entry_time").symbol.transform("nunique");T["risk_frac"]=T.net_return/T.r_net
W=pd.read_csv(a.wf)[["test_year","chosen_threshold"]].drop_duplicates();W=W[(W.test_year>=2023)&(W.test_year<=2026)]
Z=pd.concat([T[(T.year==y)&(T.signals>=th)] for y,th in W.itertuples(index=False)])
def sim(q,b,risk,bp,slots):
 q=q.copy();q["r"]=q.r_net-((bp-8)/10000)/q.risk_frac;q=q.sort_values(["entry_time","symbol"]).groupby("entry_time",group_keys=False).head(b)
 eq=1.;peak=1.;mdd=0.;active=[];acc=[];curve=[]
 for ts,g in q.groupby("entry_time",sort=True):
  done=[p for p in active if p[0]<=ts]
  for ex,pnl,y in sorted(done):
   eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak);curve.append((ex,eq,y))
  active=[p for p in active if p[0]>ts];free=slots-len(active);base=eq
  for r in g.head(max(0,free)).itertuples():
   active.append((r.exit_time,base*risk*r.r,r.year));acc.append((r.entry_time,r.exit_time,r.r,r.year))
 for ex,pnl,y in sorted(active):
  eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak);curve.append((ex,eq,y))
 A=pd.DataFrame(acc,columns=["entry","exit","r","year"]); annual=[]
 for y,g in A.groupby("year"):
  ev=g.groupby("entry").r.mean();gp=ev[ev>0].sum();gl=-ev[ev<0].sum()
  annual.append(dict(year=int(y),events=ev.size,trades=len(g),event_pf=gp/gl if gl else np.nan,avg_event_r=ev.mean(),sum_r=g.r.sum()))
 return dict(final_equity=eq,total_return=eq-1,mdd=mdd,accepted=len(A),events=A.entry.nunique()),annual
rows=[];anns=[]
for b in [5,10]:
 for slots in sorted(set([b,10,20])):
  if slots<b:continue
  for risk in [.001,.0015,.002,.0025]:
   for bp in [8,16,24]:
    d,aa=sim(Z,b,risk,bp,slots);d.update(basket=b,maxslots=slots,risk=risk,cost_bp=bp);rows.append(d)
    for x in aa:x.update(basket=b,maxslots=slots,risk=risk,cost_bp=bp);anns.append(x)
R=pd.DataFrame(rows);A=pd.DataFrame(anns);R.to_csv(O/"grid.csv",index=False);A.to_csv(O/"annual.csv",index=False)
# neighborhood checks: all annual years positive avg event R and PF>1 for baseline slot=b
B=A[A.maxslots==A.basket];chk=B.groupby(["basket","risk","cost_bp"]).agg(years=("year","nunique"),min_pf=("event_pf","min"),min_avg_r=("avg_event_r","min")).reset_index();chk["all_years_positive"]=(chk.years==4)&(chk.min_pf>1)&(chk.min_avg_r>0);chk.to_csv(O/"robustness.csv",index=False)
print("GRID");print(R.to_string(index=False));print("ROBUST");print(chk.to_string(index=False))
