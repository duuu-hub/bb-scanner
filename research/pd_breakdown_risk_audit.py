#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
Path(a.out).mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"])
c=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1.0)&(t.rr==3.0)].sort_values(["entry_time","symbol"]).copy()
# streaks in chronological trade stream
loss=(c.r_net<=0).to_numpy();best=cur=0
for v in loss:
 cur=cur+1 if v else 0;best=max(best,cur)
# rolling worst blocks
roll=[]
for k in (20,50,100,250):
 x=c.r_net.rolling(k).sum();roll.append({"window":k,"worst_sum_r":x.min(),"worst_mean_r":x.min()/k})
pd.DataFrame(roll).to_csv(Path(a.out)/"worst_windows.csv",index=False)
pd.DataFrame([{"max_consecutive_losses":best,"trades":len(c)}]).to_csv(Path(a.out)/"streaks.csv",index=False)
# portfolio equity with max 10 slots; risk fraction of current equity per accepted trade, realized on exit.
# deterministic event simulation: accept entry if slots free; size risk at entry equity; realize pnl at exit.
rows=[]
for rf in (.0025,.005,.01):
 eq=1.;peak=1.;mdd=0.;active=[];accepted=0
 for r in c.itertuples():
  # realize positions already exited before this entry
  done=[p for p in active if p[0]<=r.entry_time]
  for ex,pnl in sorted(done,key=lambda z:z[0]): eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
  active=[p for p in active if p[0]>r.entry_time]
  if len(active)>=10: continue
  riskcash=eq*rf;active.append((r.exit_time,riskcash*r.r_net));accepted+=1
 for ex,pnl in sorted(active,key=lambda z:z[0]): eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
 rows.append({"risk_fraction":rf,"slots":10,"accepted":accepted,"final_equity":eq,"total_return":eq-1,"mdd_pct":mdd})
pd.DataFrame(rows).to_csv(Path(a.out)/"capital_curve.csv",index=False)
print("STREAK",best);print(pd.DataFrame(roll).to_string(index=False));print(pd.DataFrame(rows).to_string(index=False))
