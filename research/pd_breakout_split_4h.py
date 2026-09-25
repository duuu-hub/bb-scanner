#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
Path(a.out).mkdir(parents=True,exist_ok=True); rows=[]; audit=[]
for fn in sorted(glob.glob(a.input+"/*.csv.gz")):
 sym=Path(fn).name.replace(".csv.gz","")
 try:
  d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms"
  d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
  for c in ("open","high","low","close"): d[c]=pd.to_numeric(d[c],errors="coerce")
  d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
  x=d.set_index("dt").resample("4h",label="left",closed="left").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),bars=("close","count"))
  x=x[x.bars==16].drop(columns="bars").reset_index()
  audit.append(dict(symbol=sym,rows15=len(d),rows4=len(x),gaps15=int((d.dt.diff().dropna()!=pd.Timedelta(minutes=15)).sum()),gaps4=int((x.dt.diff().dropna()!=pd.Timedelta(hours=4)).sum())))
  if len(x)<400: continue
  op=x.open.to_numpy(); cl=x.close.to_numpy(); high=x.high.to_numpy(); low=x.low.to_numpy()
  for n in (20,40,80,160,320):
   hi=x.high.shift(1).rolling(n).max().to_numpy(); lo=x.low.shift(1).rolling(n).min().to_numpy()
   atr=(x.high-x.low).shift(1).rolling(14).mean().to_numpy()
   for q in (.10,.20,.25):
    for hz in (2,6,18):
     buckets={k:[] for k in ("UPPER_APPROACH","UPPER_BREAKOUT","UPPER_FIRST_BREAKOUT","UPPER_BREAKOUT_0.1ATR","LOWER_APPROACH","LOWER_BREAKDOWN","LOWER_FIRST_BREAKDOWN","LOWER_BREAKDOWN_0.1ATR")}
     nxt={k:n for k in buckets}
     for i in range(n,len(x)-hz-1):
      w=hi[i]-lo[i]
      if not np.isfinite(w) or w<=0: continue
      p=(cl[i]-lo[i])/w
      candidates=[]
      if p>=1-q and cl[i]<=hi[i]: candidates.append(("UPPER_APPROACH",1))
      if cl[i]>hi[i]:
       candidates.append(("UPPER_BREAKOUT",1))
       prev_hi=hi[i-1] if i>0 else np.nan
       if np.isfinite(prev_hi) and cl[i-1]<=prev_hi: candidates.append(("UPPER_FIRST_BREAKOUT",1))
       if np.isfinite(atr[i]) and cl[i]>hi[i]+0.1*atr[i]: candidates.append(("UPPER_BREAKOUT_0.1ATR",1))
      if p<=q and cl[i]>=lo[i]: candidates.append(("LOWER_APPROACH",-1))
      if cl[i]<lo[i]:
       candidates.append(("LOWER_BREAKDOWN",-1))
       prev_lo=lo[i-1] if i>0 else np.nan
       if np.isfinite(prev_lo) and cl[i-1]>=prev_lo: candidates.append(("LOWER_FIRST_BREAKDOWN",-1))
       if np.isfinite(atr[i]) and cl[i]<lo[i]-0.1*atr[i]: candidates.append(("LOWER_BREAKDOWN_0.1ATR",-1))
      e=i+1; z=e+hz; raw=cl[z]/op[e]-1
      for b,sgn in candidates:
       if i<nxt[b]: continue
       buckets[b].append(sgn*raw); nxt[b]=z
     for b,v in buckets.items():
      v=np.asarray(v,float)
      if not len(v): continue
      se=v.std(ddof=1)/np.sqrt(len(v)) if len(v)>1 else np.nan
      rows.append(dict(symbol=sym,n=n,q=q,horizon_bars=hz,bucket=b,events=len(v),mean_cont_return=v.mean(),median_cont_return=np.median(v),continuation_rate=(v>0).mean(),t_stat=v.mean()/se if np.isfinite(se) and se>0 else np.nan))
 except Exception as e: audit.append(dict(symbol=sym,error=repr(e)))
pd.DataFrame(rows).to_csv(Path(a.out)/"breakout_split_4h.csv",index=False);pd.DataFrame(audit).to_csv(Path(a.out)/"audit.csv",index=False)
print("SYMBOLS",len(set(r["symbol"] for r in rows)),"RESULT_ROWS",len(rows))
