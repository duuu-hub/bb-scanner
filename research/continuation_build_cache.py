from pathlib import Path
import sys, numpy as np, pandas as pd
sys.path.insert(0,"research")
import continuation_mining as cm, continuation_validation as cv

OUT=Path("continuation_cache"); OUT.mkdir(exist_ok=True)
print("CACHE: loading raw",flush=True)
raw=cm.load("market_data_store/bitget/research_auto100_15m")
raw.to_pickle(OUT/"raw.pkl")
print("CACHE: features",flush=True)
parts=[]
for n,(sym,g) in enumerate(raw.groupby("symbol",sort=False),1):
    parts.append(cm.add_features(g))
    if n%20==0: print("symbols",n,flush=True)
d=pd.concat(parts,ignore_index=True)
agg=d[["timestamp_ms","ret_1h","ret_4h"]].groupby("timestamp_ms").agg(
 breadth_1h=("ret_1h",lambda s:(s>0).mean()*100),breadth_4h=("ret_4h",lambda s:(s>0).mean()*100),
 market_med_1h=("ret_1h","median"),market_med_4h=("ret_4h","median")).reset_index()
d=d.merge(agg,on="timestamp_ms",how="left")
d["rel_vs_market_1h"]=d.ret_1h-d.market_med_1h; d["rel_vs_market_4h"]=d.ret_4h-d.market_med_4h
long=(d.ret_1h>=1)&(d.ret_4h>0); short=(d.ret_1h<=-1)&(d.ret_4h<0)
cand=d[long|short].copy(); cand["direction"]=np.where(long.loc[cand.index],"LONG","SHORT")
groups={s:g.reset_index(drop=True) for s,g in d.groupby("symbol")}
recs=[]
for n,r in enumerate(cand.itertuples(),1):
    g=groups[r.symbol]; i=int(np.searchsorted(g.timestamp_ms.to_numpy(),int(r.timestamp_ms))); z={}
    for h,bars in cm.HORIZONS.items():
      for t in cm.TARGETS:
       for st in cm.STOPS:z[f"y_t{t:g}_s{st:g}_h{h}"]=cm.barrier_label(g,i,r.direction,t,st,bars)
    prev=g.close.shift(1); tr=np.maximum(g.high-g.low,np.maximum((g.high-prev).abs(),(g.low-prev).abs()))
    atr=(tr.rolling(56,min_periods=20).mean()/g.close*100).iloc[i]
    z["atr14h_pct"]=float(atr) if pd.notna(atr) else np.nan
    recs.append(z)
    if n%5000==0: print("labels",n,"/",len(cand),flush=True)
x=pd.concat([cand.reset_index(drop=True),pd.DataFrame(recs)],axis=1)
x.to_pickle(OUT/"prep.pkl")
print("CACHE_DONE",len(raw),len(x),flush=True)
