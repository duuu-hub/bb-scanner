#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
T=pd.read_csv(a.trades,parse_dates=["signal_time","entry_time"]);T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy();T["year"]=T.entry_time.dt.year
W=pd.read_csv(a.wf)[["test_year","chosen_threshold"]].drop_duplicates();W=W[(W.test_year>=2023)&(W.test_year<=2026)]
# reconstruct only pre-entry features at signal candle
fs=[]
for fn in sorted(glob.glob(a.input+"/*.csv.gz")):
 sym=Path(fn).name.replace(".csv.gz","")
 d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms";d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
 for c in ["open","high","low","close","volume"]:
  if c in d:d[c]=pd.to_numeric(d[c],errors="coerce")
 if "volume" not in d: continue
 d=d.dropna(subset=["dt","open","high","low","close","volume"]).sort_values("dt").drop_duplicates("dt")
 x=d.set_index("dt").resample("4h",label="left",closed="left").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("volume","sum"),bars=("close","count"))
 x=x[x.bars==16].copy();x["rl"]=x.low.shift(1).rolling(320).min();x["atr"]=((x.high-x.low).shift(1).rolling(14).mean());x["break_atr"]=(x.rl-x.close)/x.atr;x["volatility"]=x.atr/x.close;x["quote_proxy"]=x.volume*x.close;x["vol20_ratio"]=x.quote_proxy/x.quote_proxy.shift(1).rolling(20).mean()
 y=x.reset_index()[["dt","break_atr","volatility","quote_proxy","vol20_ratio"]];y["symbol"]=sym;fs.append(y)
F=pd.concat(fs,ignore_index=True).rename(columns={"dt":"signal_time"});T=T.merge(F,on=["symbol","signal_time"],how="left");assert T.break_atr.notna().mean()>.95
T["signals"]=T.groupby("entry_time").symbol.transform("nunique")
sels=[]
methods={"symbol_control":("symbol",True),"break_strong":("break_atr",False),"liquid_high":("quote_proxy",False),"volratio_high":("vol20_ratio",False),"volatility_low":("volatility",True),"volatility_high":("volatility",False)}
for y,th in W.itertuples(index=False):
 z=T[(T.year==y)&(T.signals>=th)].copy()
 for b in [5,10]:
  for m,(col,asc) in methods.items():
   q=z.sort_values(["entry_time",col,"symbol"],ascending=[True,asc,True]).groupby("entry_time",group_keys=False).head(b).copy();q["basket"]=b;q["method"]=m;sels.append(q)
X=pd.concat(sels)
rows=[]
for (m,b),q in X.groupby(["method","basket"]):
 ev=q.groupby("entry_time").r_net.mean().sort_index();gp=ev[ev>0].sum();gl=-ev[ev<0].sum();rows.append(dict(method=m,basket=b,events=len(ev),trades=len(q),event_pf=gp/gl if gl else np.nan,avg_event_r=ev.mean(),event_wr=(ev>0).mean()))
pd.DataFrame(rows).sort_values(["basket","event_pf"],ascending=[True,False]).to_csv(O/"selector_summary.csv",index=False)
pd.DataFrame([{"core_rows":len(T),"feature_match":T.break_atr.notna().mean(),"years":",".join(map(str,W.test_year))}]).to_csv(O/"sanity.csv",index=False)
print("SANITY",len(T),T.break_atr.notna().mean());print(pd.DataFrame(rows).sort_values(["basket","event_pf"],ascending=[True,False]).to_string(index=False))
