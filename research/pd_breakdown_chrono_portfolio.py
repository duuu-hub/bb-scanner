#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
T=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"]);T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy();T["year"]=T.entry_time.dt.year;T["signals"]=T.groupby("entry_time").symbol.transform("nunique")
W=pd.read_csv(a.wf)[["test_year","chosen_threshold"]].drop_duplicates();W=W[(W.test_year>=2023)&(W.test_year<=2026)]
Z=[]
for y,th in W.itertuples(index=False):Z.append(T[(T.year==y)&(T.signals>=th)])
Z=pd.concat(Z)
# exact risk fraction permits exact cost-R adjustment
Z["risk_frac"]=Z.net_return/Z.r_net
assert np.isfinite(Z.risk_frac).all() and (Z.risk_frac>0).all()
def sim(q,b,risk,costbp,maxslots):
 q=q.copy();q["r_adj"]=q.r_net-((costbp-8)/10000)/q.risk_frac
 # event candidates, strongest break unavailable here => neutral symbol control; selector edge handled separately
 q=q.sort_values(["entry_time","symbol"]).groupby("entry_time",group_keys=False).head(b)
 eq=1.;peak=1.;mdd=0.;active=[];accepted=[];skipped=0
 # process by event; release exits at/before event, then allocate only free slots
 for ts,g in q.groupby("entry_time",sort=True):
  realized=[p for p in active if p["exit"]<=ts]
  for p in sorted(realized,key=lambda x:x["exit"]): eq+=p["pnl"];peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
  active=[p for p in active if p["exit"]>ts]
  free=maxslots-len(active);take=g.head(max(0,free));skipped+=len(g)-len(take)
  # all orders at same event use same pre-entry equity; total new risk cannot exceed free*risk
  base=eq
  for r in take.itertuples():
   pnl=base*risk*r.r_adj;active.append(dict(exit=r.exit_time,pnl=pnl));accepted.append((ts,r.symbol,r.r_adj))
 for p in sorted(active,key=lambda x:x["exit"]):eq+=p["pnl"];peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
 A=pd.DataFrame(accepted,columns=["entry_time","symbol","r"]);ev=A.groupby("entry_time").r.mean() if len(A) else pd.Series(dtype=float)
 gp=ev[ev>0].sum();gl=-ev[ev<0].sum()
 return dict(events_candidate=q.entry_time.nunique(),candidate_trades=len(q),accepted_trades=len(A),accepted_events=A.entry_time.nunique(),skipped_trades=skipped,event_pf=gp/gl if gl else np.nan,avg_event_r=ev.mean(),final_equity=eq,total_return=eq-1,mdd=mdd,return_mdd=(eq-1)/mdd if mdd else np.nan)
rows=[]
for b in [5,10]:
 for slots in [b,10,20]:
  if slots<b:continue
  for risk in [.001,.0015,.002,.0025]:
   for bp in [8,16,24]:
    d=sim(Z,b,risk,bp,slots);d.update(basket=b,maxslots=slots,risk_per_symbol=risk,cost_bp=bp);rows.append(d)
R=pd.DataFrame(rows);R.to_csv(O/"chronological_portfolio.csv",index=False)
print(R.to_string(index=False))
# required headline configs
H=R[((R.basket==R.maxslots)&(R.risk_per_symbol.isin([.001,.0025]))&(R.cost_bp.isin([8,24])))]
print("HEADLINE");print(H.to_string(index=False))
