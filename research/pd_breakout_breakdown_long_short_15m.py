#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
FEE=.0008; rows=[]
for fn in sorted(glob.glob(a.input+"/**/*.csv.gz",recursive=True)):
 sym=Path(fn).name.replace(".csv.gz","")
 d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms";d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
 for c in ["open","high","low","close"]:d[c]=pd.to_numeric(d[c],errors="coerce")
 d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")
 x=d.resample("4h",label="left",closed="left").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),bars=("close","count"));x=x[x.bars==16]
 if len(x)<400:continue
 rh=x.high.shift(1).rolling(320).max();rl=x.low.shift(1).rolling(320).min();atr=(x.high-x.low).shift(1).rolling(14).mean()
 for side in ["LONG","SHORT"]:
  next_i=320
  for i in range(320,len(x)-2):
   if i<next_i or not np.isfinite(atr.iloc[i]) or atr.iloc[i]<=0:continue
   if side=="SHORT":ok=x.close.iloc[i]<rl.iloc[i] and x.close.iloc[i-1]>=rl.iloc[i-1]
   else:ok=x.close.iloc[i]>rh.iloc[i] and x.close.iloc[i-1]<=rh.iloc[i-1]
   if not ok:continue
   et=x.index[i+1]
   if et-x.index[i]!=pd.Timedelta(hours=4):continue
   ep=float(x.open.iloc[i+1]);rd=float(atr.iloc[i]);sl=ep-rd if side=="LONG" else ep+rd;tp=ep+3*rd if side=="LONG" else ep-3*rd
   w=d[(d.index>=et)&(d.index<et+pd.Timedelta(hours=24))]
   reason="TIME";xt=None;xp=None
   for t,b in w.iterrows():
    hs=(b.low<=sl) if side=="LONG" else (b.high>=sl);ht=(b.high>=tp) if side=="LONG" else (b.low<=tp)
    if hs and ht:reason="SL_AMBIG_15M";xt=t+pd.Timedelta(minutes=15);xp=sl;break
    if hs:reason="SL";xt=t+pd.Timedelta(minutes=15);xp=sl;break
    if ht:reason="TP";xt=t+pd.Timedelta(minutes=15);xp=tp;break
   if xt is None:
    # canonical 24h timeout = end of sixth 4H candle; use last available 15m close before boundary
    z=w.iloc[-1];xt=w.index[-1]+pd.Timedelta(minutes=15);xp=float(z.close)
   gross=((xp-ep)/ep) if side=="LONG" else ((ep-xp)/ep);net=gross-FEE;rnet=net/(rd/ep)
   rows.append(dict(side=side,symbol=sym,signal_time=x.index[i],entry_time=et,exit_time=xt,entry=ep,exit=xp,reason=reason,r_net=rnet,net_return=net))
   # same-symbol non-overlap based on actual 15m exit: next 4H signal allowed only after exit
   next_i=int(x.index.searchsorted(xt,side="right"))
T=pd.DataFrame(rows);T.to_csv(O/"trades_15m.csv",index=False)
def st(g):
 gp=g.loc[g.r_net>0,"r_net"].sum();gl=-g.loc[g.r_net<0,"r_net"].sum();return pd.Series(dict(trades=len(g),wr=(g.r_net>0).mean(),pf=gp/gl if gl else np.nan,expectancy_r=g.r_net.mean(),sum_r=g.r_net.sum(),amb15=(g.reason=="SL_AMBIG_15M").sum()))
A=T.groupby("side").apply(st,include_groups=False).reset_index();Y=T.assign(year=T.entry_time.dt.year).groupby(["side","year"]).apply(st,include_groups=False).reset_index();A.to_csv(O/"summary.csv",index=False);Y.to_csv(O/"yearly.csv",index=False);print(A.to_string(index=False));print(Y.to_string(index=False))
