#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--selected",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
S=pd.read_csv(a.selected,parse_dates=["entry_time","exit_time"]);S=S[S.basket==10].copy()
# load only symbols actually selected; 15m close is used for portfolio MTM, while exit R remains canonical
need=set(S.symbol);px={}
for fn in glob.glob(a.input+"/**/*.csv.gz",recursive=True):
 sym=Path(fn).name.replace(".csv.gz","")
 if sym not in need: continue
 d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms"
 d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
 for c in ["high","low","close"]: d[c]=pd.to_numeric(d[c],errors="coerce")
 d=d.dropna(subset=["dt","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")
 x=d.resample("4h",label="left",closed="left").agg(high=("high","max"),low=("low","min"),close=("close","last"),bars=("close","count"))
 x=x[x.bars==16]
 atr=x.high.sub(x.low).shift(1).rolling(14).mean()
 px[sym]={"close":d.close,"atr":atr}
def entry_px(r):
 z=px[r.symbol]["close"].loc[:r.entry_time]
 return float(z.iloc[-1]) if len(z) else np.nan
S["ep"]=[entry_px(r) for r in S.itertuples()]
vals=[]
for r in S.itertuples():
 st=r.entry_time-pd.Timedelta(hours=4)
 z=px[r.symbol]["atr"].loc[:st]
 vals.append(float(z.iloc[-1]) if len(z) else np.nan)
S["risk_dist"]=vals
assert S.ep.notna().all() and S.risk_dist.notna().all() and (S.risk_dist>0).all()
risks=[.01,.0125,.015,.02,.025,.03]
rows=[]
for rf in risks:
 cash=1.;active=[];peak=1.;mdd=0.;accepted=0
 times=sorted(set(S.entry_time)|set(S.exit_time))
 for t in times:
  # realize exits first at canonical r_net
  done=[p for p in active if p["exit_time"]<=t]
  for p in sorted(done,key=lambda x:x["exit_time"]): cash += p["stake"]*p["r_net"]
  active=[p for p in active if p["exit_time"]>t]
  # entries, same ordering as selector sim
  for r in S[S.entry_time==t].sort_values("symbol").itertuples():
   if len(active)>=10: break
   active.append(dict(symbol=r.symbol,entry_time=r.entry_time,exit_time=r.exit_time,ep=r.ep,risk_dist=r.risk_dist,r_net=r.r_net,stake=cash*rf));accepted+=1
  # evaluate every 15m point until next event boundary using union grid
  nxt=min([p["exit_time"] for p in active]+[S.entry_time[S.entry_time>t].min() if (S.entry_time>t).any() else pd.Timestamp.max.tz_localize("UTC")])
  if active:
   grid=None
   for p in active:
    ser=px[p["symbol"]]["close"]; idx=ser.loc[(ser.index>=t)&(ser.index<nxt)].index
    grid=idx if grid is None else grid.union(idx)
   for tt in grid:
    eq=cash
    for p in active:
     z=px[p["symbol"]]["close"].loc[:tt]
     if len(z):
      rr=(p["ep"]-float(z.iloc[-1]))/p["risk_dist"]
      eq += p["stake"]*rr
    peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else np.inf)
  peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak if peak>0 else np.inf)
 # terminal exits
 for p in sorted(active,key=lambda x:x["exit_time"]): cash += p["stake"]*p["r_net"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
 rows.append(dict(risk=rf,accepted=accepted,final_equity=cash,total_return=cash-1,mtm_mdd_pct=mdd))
pd.DataFrame(rows).to_csv(O/"mtm_mdd.csv",index=False);print(pd.DataFrame(rows).to_string(index=False))

# trigger exact MTM rerun

# rerun after newline normalization

# trigger latest exact ATR build

# verified trigger
