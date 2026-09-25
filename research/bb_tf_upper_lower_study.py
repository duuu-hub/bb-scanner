#!/usr/bin/env python3
from pathlib import Path
import itertools, numpy as np, pandas as pd
import os
ROOT=Path(os.environ.get('CANONICAL_5Y_DIR','canonical'))
TF={"15m":"15min","1H":"1h","4H":"4h","1D":"1D","3D":"3D"}
FWD={"15m":1,"1H":4,"4H":16,"12H":48,"24H":96}
def features(df):
 df=df.sort_values("open_time").drop_duplicates("open_time")
 b=df.assign(dt=pd.to_datetime(df.open_time,unit="ms",utc=True)).set_index("dt")[["open","high","low","close"]].astype(float)
 out=pd.DataFrame(index=b.index)
 for n,r in TF.items():
  x=b.close.resample(r,origin="epoch",label="left",closed="left").last().dropna()
  ma=x.rolling(20).mean().shift(1); sd=x.rolling(20).std(ddof=0).shift(1)
  bucket=b.index.floor(r)
  m=pd.Series(ma.reindex(bucket).to_numpy(),index=b.index); s=pd.Series(sd.reindex(bucket).to_numpy(),index=b.index)
  # causal live-bucket test using current 15m close against bands formed only from prior completed HTF bars
  out[n+"_UP"]=b.close > m+2*s; out[n+"_DN"]=b.close < m-2*s
 for n,k in FWD.items(): out["f_"+n]=(b.close.shift(-k)/b.close-1)*100
 return out
def stats(x,mask):
 z=x.loc[mask]
 d={"n":int(mask.sum())}
 for h in FWD:
  a=z["f_"+h].dropna(); d["mean_"+h]=a.mean(); d["median_"+h]=a.median(); d["up_"+h]=(a>0).mean()
 return d
def main():
 singles=[]; pairs=[]
 for p in sorted(ROOT.rglob("*.parquet")):
  try:x=features(pd.read_parquet(p))
  except Exception as e: print("ERR",p,e); continue
  cols=[t+s for t in TF for s in ("_UP","_DN")]
  for c in cols:
   m=x[c]&~x[c].shift(1,fill_value=False); singles.append({"feature":c,**stats(x,m)})
  for a,b in itertools.combinations(cols,2):
   m=(x[a]&x[b]) & ~(x[a]&x[b]).shift(1,fill_value=False); pairs.append({"a":a,"b":b,**stats(x,m)})
 print("FILES_DONE",len(list(ROOT.rglob("*.parquet"))))
 def agg(rows,keys):
  d=pd.DataFrame(rows); num=[c for c in d if c not in keys+["n"]]
  # weighted aggregate of per-symbol event statistics
  for c in num:d[c]=d[c]*d.n
  g=d.groupby(keys,as_index=False).sum(numeric_only=True)
  for c in num:g[c]=g[c]/g.n
  return g
 s=agg(singles,["feature"]); q=agg(pairs,["a","b"])
 Path("artifacts").mkdir(exist_ok=True)
 s.to_csv("artifacts/bb_tf_upper_lower_single.csv",index=False); q.to_csv("artifacts/bb_tf_upper_lower_pairs.csv",index=False)
 print("SINGLE_24H");print(s.sort_values("mean_24H",ascending=False).to_string(index=False))
 print("PAIR_TOP_24H");print(q[q.n>=300].sort_values("mean_24H",ascending=False).head(30).to_string(index=False))
if __name__=="__main__":main()
