#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();P=Path(a.out);P.mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time","signal_time"])
c=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1.0)&(t.rr==3.0)].sort_values(["entry_time","symbol"]).copy()
cut=c.entry_time.quantile(.60);o=c[c.entry_time>cut].copy()
def pf(x):
 w=x[x>0].sum();l=-x[x<0].sum();return w/l if l else np.nan
# Event = one entry timestamp. Equal-weight event return removes cross-section sample inflation.
e=o.groupby("entry_time").agg(signals=("symbol","size"),symbols=("symbol","nunique"),event_avg_r=("r_net","mean"),event_sum_r=("r_net","sum"),wins=("r_net",lambda x:(x>0).sum())).reset_index()
e["event_winrate"]=e.wins/e.signals;e["month"]=e.entry_time.dt.to_period("M").astype(str)
# thresholds known at entry because number of signals at that timestamp is observable before selecting orders.
rows=[]
for th in [1,2,4,6,11,21,31,41,51,61,81,101,151]:
 z=e[e.signals>=th]; x=o[o.entry_time.isin(z.entry_time)]
 rows.append(dict(min_signals=th,events=len(z),trades=len(x),pf_trade=pf(x.r_net),avg_r_trade=x.r_net.mean(),sum_r=x.r_net.sum(),avg_event_r=z.event_avg_r.mean(),median_event_r=z.event_avg_r.median(),positive_event_rate=(z.event_avg_r>0).mean()))
pd.DataFrame(rows).to_csv(P/"thresholds.csv",index=False)
# 51+ event-level detail and concentration
z=e[e.signals>=51].copy(); z["rank_abs_sum"]=z.event_sum_r.abs().rank(ascending=False,method="first")
z.sort_values("entry_time").to_csv(P/"events_51plus.csv",index=False)
# leave-one-event-out; how fragile is aggregate PF / avg R to any one event?
loo=[]
for tm in z.entry_time:
 x=o[(o.entry_time.isin(z.entry_time))&(o.entry_time!=tm)]
 loo.append(dict(removed_entry_time=tm,trades=len(x),pf=pf(x.r_net),avg_r=x.r_net.mean(),sum_r=x.r_net.sum()))
pd.DataFrame(loo).to_csv(P/"loo_51plus.csv",index=False)
# by month event stats, to reveal regime drift
m=e.groupby("month").agg(events=("entry_time","size"),avg_signals=("signals","mean"),max_signals=("signals","max"),avg_event_r=("event_avg_r","mean"),positive_event_rate=("event_avg_r",lambda x:(x>0).mean())).reset_index()
m.to_csv(P/"event_monthly.csv",index=False)
# 51+ only by month
zm=z.groupby("month").agg(events=("entry_time","size"),trades=("signals","sum"),avg_event_r=("event_avg_r","mean"),sum_r=("event_sum_r","sum"),positive_event_rate=("event_avg_r",lambda x:(x>0).mean())).reset_index();zm.to_csv(P/"events_51plus_monthly.csv",index=False)
summary=pd.DataFrame([dict(oos_events=len(e),oos_trades=len(o),events_51plus=len(z),trades_51plus=int(z.signals.sum()),event51_avg_r=z.event_avg_r.mean(),event51_median_r=z.event_avg_r.median(),event51_positive_rate=(z.event_avg_r>0).mean(),largest_event_share_abs_sum=(z.event_sum_r.abs().max()/z.event_sum_r.abs().sum()),top5_share_abs_sum=(z.nlargest(5,"event_sum_r").event_sum_r.sum()/z.event_sum_r.sum()))])
summary.to_csv(P/"summary.csv",index=False)
print(summary.to_string(index=False));print(pd.DataFrame(rows).to_string(index=False));print("51+ MONTH");print(zm.to_string(index=False))
