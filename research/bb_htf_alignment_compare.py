#!/usr/bin/env python3
from pathlib import Path
import os,pandas as pd,numpy as np
ROOT=Path(os.environ.get("CANONICAL_5Y_DIR","canonical"))
def oldfeat(x,r):
 c=x.close.resample(r,origin="epoch",label="left",closed="left").last().dropna()
 ma=c.rolling(20).mean().shift(1); sd=c.rolling(20).std(ddof=0).shift(1); k=x.index.floor(r)
 m=pd.Series(ma.reindex(k).to_numpy(),index=x.index); s=pd.Series(sd.reindex(k).to_numpy(),index=x.index)
 return x.close>m+2*s,x.close<m-2*s
def explicit(x,mins):
 idx=x.index; ns=idx.astype("int64"); step=mins*60*10**9
 bucket=pd.to_datetime((ns//step)*step,utc=True)
 h=pd.DataFrame({"bucket":bucket,"close":x.close.to_numpy()}).groupby("bucket",sort=True).close.last()
 ma=h.rolling(20).mean().shift(1); sd=h.rolling(20).std(ddof=0).shift(1)
 m=pd.Series(ma.reindex(bucket).to_numpy(),index=idx); s=pd.Series(sd.reindex(bucket).to_numpy(),index=idx)
 return x.close>m+2*s,x.close<m-2*s
def main():
 files=sorted(ROOT.rglob("*.parquet")); assert len(files)==666
 sums=np.zeros(8,dtype=np.int64)
 examples=[]
 for p in files[:50]:
  d=pd.read_parquet(p).sort_values("open_time").drop_duplicates("open_time")
  idx=pd.to_datetime(d.open_time,unit="ms",utc=True);x=d.assign(dt=idx).set_index("dt")[["close"]].astype(float)
  o1u,o1d=oldfeat(x,"1h"); odu,odd=oldfeat(x,"1D")
  n1u,n1d=explicit(x,60); ndu,ndd=explicit(x,1440)
  a=(o1d&odu).to_numpy(); b=(n1d&ndu).to_numpy()
  sums += [a.sum(),b.sum(),(a!=b).sum(),(o1d.to_numpy()!=n1d.to_numpy()).sum(),(odu.to_numpy()!=ndu.to_numpy()).sum(),o1d.sum(),odu.sum(),len(x)]
  if len(examples)<10:
   q=np.flatnonzero(a!=b)
   for i in q[:2]: examples.append((p.stem,str(x.index[i]),bool(a[i]),bool(b[i]),bool(o1d.iloc[i]),bool(odu.iloc[i]),bool(n1d.iloc[i]),bool(ndu.iloc[i])))
 print("COMPARE old_pair,new_pair,pair_diff,1h_diff,1d_diff,old1hdn,old1dup,rows",*sums,flush=True)
 print("EXAMPLES",examples[:10],flush=True)
if __name__=="__main__":main()
