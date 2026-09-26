#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd, numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
Path(a.out).mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"])
c=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1.0)&(t.rr==3.0)].copy()
# One market event per entry timestamp. Equal-weight event return avoids pretending simultaneous alts are independent observations.
e=c.groupby("entry_time").agg(signals=("symbol","nunique"),event_r=("r_net","mean"),sum_r=("r_net","sum"),wins=("r_net",lambda x:(x>0).mean())).reset_index()
e["year"]=e.entry_time.dt.year
thresholds=[51,61,81,101]
rows=[]; yrs=[]
for th in thresholds:
 z=e[e.signals>=th].sort_values("entry_time").copy()
 pos=(z.event_r>0)
 gross=z.loc[z.event_r>0,"event_r"].sum(); loss=-z.loc[z.event_r<0,"event_r"].sum()
 rows.append({"min_signals":th,"events":len(z),"trades":int(z.signals.sum()),"event_wr":pos.mean() if len(z) else np.nan,"event_pf":gross/loss if loss>0 else np.nan,"avg_event_r":z.event_r.mean() if len(z) else np.nan,"median_event_r":z.event_r.median() if len(z) else np.nan,"sum_event_r":z.event_r.sum()})
 for y,g in z.groupby("year"):
  gp=g.loc[g.event_r>0,"event_r"].sum();gl=-g.loc[g.event_r<0,"event_r"].sum()
  yrs.append({"min_signals":th,"year":y,"events":len(g),"trades":int(g.signals.sum()),"event_wr":(g.event_r>0).mean(),"event_pf":gp/gl if gl>0 else np.nan,"avg_event_r":g.event_r.mean(),"sum_event_r":g.event_r.sum()})
pd.DataFrame(rows).to_csv(Path(a.out)/"threshold_summary.csv",index=False)
pd.DataFrame(yrs).to_csv(Path(a.out)/"threshold_yearly.csv",index=False)
e.to_csv(Path(a.out)/"all_events.csv",index=False)
print(pd.DataFrame(rows).to_string(index=False));print(pd.DataFrame(yrs).to_string(index=False))
