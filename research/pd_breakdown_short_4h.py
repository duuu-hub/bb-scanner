#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
Path(a.out).mkdir(parents=True,exist_ok=True); trades=[]; audit=[]
FEE=.0008
for fn in sorted(glob.glob(a.input+"/*.csv.gz")):
 sym=Path(fn).name.replace(".csv.gz","")
 try:
  d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms"
  d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
  for c in ("open","high","low","close"): d[c]=pd.to_numeric(d[c],errors="coerce")
  d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
  x=d.set_index("dt").resample("4h",label="left",closed="left").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),bars=("close","count"))
  x=x[x.bars==16].drop(columns="bars").reset_index(); audit.append(dict(symbol=sym,rows4=len(x)))
  if len(x)<400: continue
  op=x.open.to_numpy();hi4=x.high.to_numpy();lo4=x.low.to_numpy();cl=x.close.to_numpy();dt=x.dt.to_numpy()
  for n in (320,400,480,560,640):
   rh=x.high.shift(1).rolling(n).max().to_numpy(); rl=x.low.shift(1).rolling(n).min().to_numpy()
   atr=(x.high-x.low).shift(1).rolling(14).mean().to_numpy()
   for sig in ("BREAKDOWN","FIRST_BREAKDOWN","BREAKDOWN_0.1ATR"):
    for stop_atr in (0.5,1.0,1.5,2.0):
     for rr in (1.0,1.5,2.0,3.0):
      next_i=n
      for i in range(n,len(x)-2):
       if i<next_i or not np.isfinite(rl[i]) or not np.isfinite(atr[i]) or atr[i]<=0: continue
       ok=cl[i]<rl[i]
       if sig=="FIRST_BREAKDOWN": ok=ok and i>0 and np.isfinite(rl[i-1]) and cl[i-1]>=rl[i-1]
       if sig=="BREAKDOWN_0.1ATR": ok=ok and cl[i]<rl[i]-0.1*atr[i]
       if not ok: continue
       e=i+1; entry=op[e]; stop=entry+stop_atr*atr[i]; risk=stop-entry; tp=entry-rr*risk
       if risk<=0: continue
       exit_px=None; reason=None; z=min(e+5,len(x)-1) # 24h max hold: 6 x 4h candles from entry open
       for j in range(e,z+1):
        hs=hi4[j]>=stop; ht=lo4[j]<=tp
        if hs and ht: exit_px=stop;reason="SL_AMBIG";z=j;break
        if hs: exit_px=stop;reason="SL";z=j;break
        if ht: exit_px=tp;reason="TP";z=j;break
       if exit_px is None: exit_px=cl[z];reason="TIME"
       gross=(entry-exit_px)/entry; net=gross-FEE; rnet=net/(risk/entry)
       trades.append(dict(symbol=sym,n=n,signal=sig,stop_atr=stop_atr,rr=rr,signal_time=str(x.dt.iloc[i]),entry_time=str(x.dt.iloc[e]),exit_time=str(x.dt.iloc[z]),entry=entry,exit=exit_px,net_return=net,r_net=rnet,reason=reason,year=pd.Timestamp(x.dt.iloc[e]).year))
       next_i=z+1
 except Exception as e: audit.append(dict(symbol=sym,error=repr(e)))
T=pd.DataFrame(trades);T.to_csv(Path(a.out)/"trades.csv",index=False);pd.DataFrame(audit).to_csv(Path(a.out)/"audit.csv",index=False)
if len(T):
 def summ(g):
  win=g.r_net[g.r_net>0].sum();loss=-g.r_net[g.r_net<0].sum()
  return pd.Series(dict(trades=len(g),wr=(g.r_net>0).mean(),pf=win/loss if loss>0 else np.nan,expectancy_r=g.r_net.mean(),net_return_sum=g.net_return.sum(),ambiguous=(g.reason=="SL_AMBIG").sum()))
 S=T.groupby(["n","signal","stop_atr","rr"]).apply(summ,include_groups=False).reset_index();S.to_csv(Path(a.out)/"summary.csv",index=False)
 Y=T.groupby(["n","signal","stop_atr","rr","year"]).apply(summ,include_groups=False).reset_index();Y.to_csv(Path(a.out)/"yearly.csv",index=False)
 print(S.sort_values("pf",ascending=False).head(30).to_string(index=False))
