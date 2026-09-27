#!/usr/bin/env python3
import argparse,zipfile
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--data",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
T=pd.read_csv(a.trades,parse_dates=["entry_time"]);T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy();T["year"]=T.entry_time.dt.year;T["signals"]=T.groupby("entry_time").symbol.transform("nunique")
W=pd.read_csv(a.wf)[["test_year","chosen_threshold"]].drop_duplicates();W=W[(W.test_year>=2023)&(W.test_year<=2026)]
Z=pd.concat([T[(T.year==y)&(T.signals>=th)] for y,th in W.itertuples(index=False)]).sort_values(["entry_time","symbol"])
# neutral 5 basket event R
E=Z.groupby("entry_time").head(5).groupby("entry_time").agg(event_r=("r_net","mean"),breadth=("signals","first")).reset_index()
# find BTC csv recursively
files=list(Path(a.data).rglob("*BTCUSDT*15m*.csv"))
if not files: files=list(Path(a.data).rglob("*BTCUSDT*.csv"))
assert files, "BTCUSDT csv not found"
p=files[0]; B=pd.read_csv(p,compression="infer")
# normalize Binance kline formats
if "open_time" in B: ts=pd.to_datetime(B.open_time,unit="ms",utc=True,errors="coerce")
elif "timestamp" in B: ts=pd.to_datetime(B.timestamp,unit="ms",utc=True,errors="coerce")
else: ts=pd.to_datetime(B.iloc[:,0],unit="ms",utc=True,errors="coerce")
close=pd.to_numeric(B["close"] if "close" in B else B.iloc[:,4],errors="coerce")
D=pd.DataFrame({"ts":ts,"close":close}).dropna().set_index("ts").resample("1D").last().dropna()
for d in [7,30,90]: D[f"ret{d}"]=D.close/D.close.shift(d)-1
D["ma200"]=D.close.rolling(200).mean();D["ma200_dist"]=D.close/D.ma200-1
D["rv30"]=D.close.pct_change().rolling(30).std()*np.sqrt(365)
D=D.reset_index().sort_values("ts")
E["entry_time"]=pd.to_datetime(E.entry_time,utc=True);E=pd.merge_asof(E.sort_values("entry_time"),D.sort_values("ts"),left_on="entry_time",right_on="ts",direction="backward")
# predeclared descriptive buckets; no tuning
E["trend30"]=pd.cut(E.ret30,[-np.inf,-.10,.10,np.inf],labels=["BTC30_DOWN","BTC30_FLAT","BTC30_UP"])
E["ma200_regime"]=np.where(E.ma200_dist>=0,"ABOVE200","BELOW200")
E["vol_regime"]=pd.qcut(E.rv30,3,labels=["LOWVOL","MIDVOL","HIGHVOL"],duplicates="drop")
E["breadth_regime"]=pd.cut(E.breadth,[0,80,120,np.inf],labels=["B51_80","B81_120","B121P"])
def stat(col):
 q=[]
 for k,g in E.groupby(col,observed=True):
  pos=g.event_r[g.event_r>0].sum();neg=-g.event_r[g.event_r<0].sum()
  q.append({"factor":col,"bucket":str(k),"events":len(g),"avg_r":g.event_r.mean(),"median_r":g.event_r.median(),"win_rate":(g.event_r>0).mean(),"pf":pos/neg if neg else np.inf,"sum_r":g.event_r.sum()})
 return q
rows=[]
for c in ["trend30","ma200_regime","vol_regime","breadth_regime"]:rows+=stat(c)
pd.DataFrame(rows).to_csv(O/"regime_summary.csv",index=False);E.to_csv(O/"event_regimes.csv",index=False)
print(pd.DataFrame(rows).to_string(index=False));print("EVENTS",len(E),"BTCFILE",p)
