#!/usr/bin/env python3
import glob
from pathlib import Path
import pandas as pd, numpy as np

OUT=Path("pd_direction_4h_results"); OUT.mkdir(exist_ok=True)
def load(sym):
 fs=sorted(glob.glob(f"market_data_store/bitget/15m/{sym}/*.csv")); assert fs
 d=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 d["dt"]=pd.to_datetime(pd.to_numeric(d["timestamp_ms"]),unit="ms",utc=True)
 for c in ["open","high","low","close"]: d[c]=pd.to_numeric(d[c],errors="coerce")
 d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
 end=d.dt.max(); d=d[d.dt>=end-pd.Timedelta(days=365.25*5)].reset_index(drop=True)
 assert d.dt.is_monotonic_increasing and not d.dt.duplicated().any()
 assert (d.dt.diff().dropna()==pd.Timedelta(minutes=15)).all()
 d=d.set_index("dt").resample("4h",label="left",closed="left").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna().reset_index()\n assert (d.dt.diff().dropna()==pd.Timedelta(hours=4)).all()\n return d\n\nrows=[]
for sym in ["BTCUSDT","ETHUSDT"]:
 d=load(sym); op=d.open.to_numpy(); cl=d.close.to_numpy()
 for n in [20,40,80,160,320]:
  hi=d.high.shift(1).rolling(n).max().to_numpy(); lo=d.low.shift(1).rolling(n).min().to_numpy()
  for q in [.10,.20,.25]:
   for horizon in [2,6,18]:
    vals={"UPPER":[],"LOWER":[]}
    # non-overlapping events per bucket: after an event, wait horizon bars before same bucket can fire again
    next_ok={"UPPER":n,"LOWER":n}
    for i in range(n,len(d)-horizon-1):
     w=hi[i]-lo[i]
     if not np.isfinite(w) or w<=0: continue
     pos=(cl[i]-lo[i])/w
     bucket="UPPER" if pos>=1-q else ("LOWER" if pos<=q else None)
     if bucket is None or i<next_ok[bucket]: continue
     e=i+1; x=e+horizon
     raw=cl[x]/op[e]-1
     continuation=raw if bucket=="UPPER" else -raw
     vals[bucket].append(continuation)
     next_ok[bucket]=x
    for bucket,a in vals.items():
     a=np.array(a,float); se=a.std(ddof=1)/np.sqrt(len(a)) if len(a)>1 else np.nan
     rows.append(dict(symbol=sym,n=n,q=q,horizon_bars=horizon,zone=bucket,events=len(a),
      mean_cont_return=a.mean(),median_cont_return=np.median(a),continuation_rate=(a>0).mean(),
      t_stat=a.mean()/se if se and se>0 else np.nan))
r=pd.DataFrame(rows);r.to_csv(OUT/"direction_4h.csv",index=False)
print(r.sort_values("mean_cont_return",ascending=False).groupby("symbol").head(20).to_string(index=False))
print("AUDIT",len(r),"rows; min events",r.events.min(),"max events",r.events.max())
assert len(r)==180 and (r.events>0).all()
