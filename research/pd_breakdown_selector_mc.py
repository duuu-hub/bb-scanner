#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);ap.add_argument("--sims",type=int,default=3000);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
T=pd.read_csv(a.trades,parse_dates=["signal_time","entry_time"]);T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy();T["year"]=T.entry_time.dt.year
W=pd.read_csv(a.wf)[["test_year","chosen_threshold"]].drop_duplicates();W=W[(W.test_year>=2023)&(W.test_year<=2026)]
fs=[]
for fn in sorted(glob.glob(a.input+"/*.csv.gz")):
 sym=Path(fn).name.replace(".csv.gz","");d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms";d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
 for c in ["open","high","low","close"]:d[c]=pd.to_numeric(d[c],errors="coerce")
 d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt")
 x=d.set_index("dt").resample("4h",label="left",closed="left").agg(high=("high","max"),low=("low","min"),close=("close","last"),bars=("close","count"));x=x[x.bars==16]
 x["rl"]=x.low.shift(1).rolling(320).min();x["atr"]=(x.high-x.low).shift(1).rolling(14).mean();x["break_atr"]=(x.rl-x.close)/x.atr
 y=x.reset_index()[["dt","break_atr"]];y["symbol"]=sym;fs.append(y)
F=pd.concat(fs,ignore_index=True).rename(columns={"dt":"signal_time"});T=T.merge(F,on=["symbol","signal_time"],how="left");assert T.break_atr.notna().mean()>.99
T["signals"]=T.groupby("entry_time").symbol.transform("nunique")
Z=[]
for y,th in W.itertuples(index=False):Z.append(T[(T.year==y)&(T.signals>=th)])
Z=pd.concat(Z)
def stats(q):
 ev=q.groupby("entry_time").r_net.mean();gp=ev[ev>0].sum();gl=-ev[ev<0].sum();return len(ev),gp/gl if gl else np.nan,ev.mean(),(ev>0).mean()
annual=[]
for y,qy in Z.groupby("year"):
 for b in [5,10]:
  for m in ["symbol_control","break_strong"]:
   col="symbol" if m=="symbol_control" else "break_atr";asc=m=="symbol_control"
   q=qy.sort_values(["entry_time",col,"symbol"],ascending=[True,asc,True]).groupby("entry_time",group_keys=False).head(b)
   e,pf,av,wr=stats(q);annual.append(dict(year=y,basket=b,method=m,events=e,event_pf=pf,avg_event_r=av,event_wr=wr))
pd.DataFrame(annual).to_csv(O/"annual.csv",index=False)
rng=np.random.default_rng(20260926);mc=[]
groups=[g for _,g in Z.groupby("entry_time")]
for b in [5,10]:
 bs=Z.sort_values(["entry_time","break_atr","symbol"],ascending=[True,False,True]).groupby("entry_time",group_keys=False).head(b);_,bpf,bavg,_=stats(bs)
 vals=[]
 for s in range(a.sims):
  chunks=[]
  for g in groups:
   k=min(b,len(g));idx=rng.choice(len(g),size=k,replace=False);chunks.append(g.iloc[idx])
  q=pd.concat(chunks);_,pf,av,_=stats(q);vals.append((pf,av))
 arr=np.array(vals);mc.append(dict(basket=b,sims=a.sims,break_pf=bpf,break_avg_r=bavg,random_pf_mean=arr[:,0].mean(),random_pf_p05=np.quantile(arr[:,0],.05),random_pf_p50=np.quantile(arr[:,0],.5),random_pf_p95=np.quantile(arr[:,0],.95),break_pf_percentile=(arr[:,0]<bpf).mean()*100,random_avg_r_mean=arr[:,1].mean(),break_avg_percentile=(arr[:,1]<bavg).mean()*100))
pd.DataFrame(mc).to_csv(O/"monte_carlo.csv",index=False);print(pd.DataFrame(annual).to_string(index=False));print("MONTE");print(pd.DataFrame(mc).to_string(index=False))
