#!/usr/bin/env python3
import argparse,glob,json,os
import numpy as np,pandas as pd
H=[1,2,4,8,16,24]
def one(path):
 d=pd.read_csv(path,compression="gzip");d["t"]=pd.to_datetime(d.open_time,unit="ms",utc=True)
 for c in ["high","low","close","volume"]:d[c]=pd.to_numeric(d[c],errors="coerce")
 d=d.set_index("t")[["high","low","close","volume"]].dropna().resample("1h").agg({"high":"max","low":"min","close":"last","volume":"sum"}).dropna()
 tp=(d.high+d.low+d.close)/3;day=d.index.floor("D");w=d.volume;cw=w.groupby(day).cumsum();v=(tp*w).groupby(day).cumsum()/cw
 sd=np.sqrt((((tp-v)**2*w).groupby(day).cumsum()/cw).clip(lower=0));z=(d.close-v)/sd.replace(0,np.nan)
 arr=z.to_numpy();cl=d.close.to_numpy();out=[]
 # reclaim: previous bar beyond +/-3, current bar back inside +/-2.5
 for side in [1,-1]:
  idx=np.flatnonzero((np.roll(arr,1)*side>=3)&(arr*side<2.5));idx=idx[idx>0]
  for h in H:
   vals=[]
   for i in idx:
    if i+h<len(cl): vals.append((cl[i+h]/cl[i]-1)*(-side)) # positive = mean-reversion direction
   if vals:out.append((side,h,len(vals),sum(vals),sum(x>0 for x in vals)))
 return out
ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int);ap.add_argument("--shards",type=int,default=8);ap.add_argument("--out",required=True);a=ap.parse_args()
fs=sorted(glob.glob(a.data+"/**/*USDT.csv.gz",recursive=True));fs=[f for i,f in enumerate(fs) if i%a.shards==a.shard];agg={};err=[]
for f in fs:
 try:
  for s,h,n,sm,w in one(f):
   k=f"{s}|{h}";q=agg.setdefault(k,[0,0.,0]);q[0]+=n;q[1]+=sm;q[2]+=w
 except Exception as e:err.append([os.path.basename(f),str(e)])
json.dump({"files":len(fs),"errors":err,"stats":{k:{"n":q[0],"mean_reversion_return":q[1]/q[0],"positive_rate":q[2]/q[0]} for k,q in agg.items()}},open(a.out,"w"),indent=2);print("RESULT",json.dumps({"files":len(fs),"errors":len(err),"stats":{k:{"n":q[0],"mean_reversion_return":q[1]/q[0],"positive_rate":q[2]/q[0]} for k,q in agg.items()}}))
