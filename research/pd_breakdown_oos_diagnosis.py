#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
Path(a.out).mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"])
c=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1.0)&(t.rr==3.0)].sort_values(["entry_time","symbol"]).copy()
cut=c.entry_time.quantile(.60); o=c[c.entry_time>cut].copy()
def stats(g):
 w=g.loc[g.r_net>0,"r_net"].sum();l=-g.loc[g.r_net<0,"r_net"].sum()
 return pd.Series(dict(trades=len(g),symbols=g.symbol.nunique(),wr=(g.r_net>0).mean(),pf=w/l if l else np.nan,avg_r=g.r_net.mean(),sum_r=g.r_net.sum()))
o["month"]=o.entry_time.dt.to_period("M").astype(str)
o["quarter"]=o.entry_time.dt.to_period("Q").astype(str)
o["year"]=o.entry_time.dt.year
o.groupby("month").apply(stats,include_groups=False).reset_index().to_csv(Path(a.out)/"oos_monthly.csv",index=False)
o.groupby("quarter").apply(stats,include_groups=False).reset_index().to_csv(Path(a.out)/"oos_quarterly.csv",index=False)
o.groupby("year").apply(stats,include_groups=False).reset_index().to_csv(Path(a.out)/"oos_yearly.csv",index=False)
# signal crowding at identical entry timestamp
crowd=o.groupby("entry_time").size().rename("signals_same_time")
o=o.join(crowd,on="entry_time")
bins=pd.cut(o.signals_same_time,[-1,1,3,5,10,20,50,np.inf],labels=["1","2-3","4-5","6-10","11-20","21-50","51+"])
o.assign(crowd_bin=bins).groupby("crowd_bin",observed=True).apply(stats,include_groups=False).reset_index().to_csv(Path(a.out)/"oos_crowding.csv",index=False)
meta=pd.DataFrame([{"full_start":c.entry_time.min(),"full_end":c.entry_time.max(),"cutoff_60pct":cut,"oos_start":o.entry_time.min(),"oos_end":o.entry_time.max(),"full_trades":len(c),"oos_trades":len(o)}])
meta.to_csv(Path(a.out)/"oos_meta.csv",index=False)
print(meta.to_string(index=False));print("YEAR");print(pd.read_csv(Path(a.out)/"oos_yearly.csv").to_string(index=False));print("QUARTER");print(pd.read_csv(Path(a.out)/"oos_quarterly.csv").to_string(index=False));print("CROWD");print(pd.read_csv(Path(a.out)/"oos_crowding.csv").to_string(index=False))
