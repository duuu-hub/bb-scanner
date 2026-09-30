#!/usr/bin/env python3
import argparse,gzip,glob,json,os
import numpy as np,pandas as pd
Z=[1,1.5,2,2.5,3]; H=[1,2,4,8,16,24]
def calc(path):
 d=pd.read_csv(path,compression="gzip"); d["t"]=pd.to_datetime(d.open_time,unit="ms",utc=True)
 for c in ["open","high","low","close","volume"]: d[c]=pd.to_numeric(d[c],errors="coerce")
 d=d.set_index("t")[["high","low","close","volume"]].dropna().resample("1h").agg({"high":"max","low":"min","close":"last","volume":"sum"}).dropna()
 typ=(d.high+d.low+d.close)/3; day=d.index.floor("D"); w=d.volume
 cw=w.groupby(day).cumsum(); vwap=(typ*w).groupby(day).cumsum()/cw
 var=(((typ-vwap)**2*w).groupby(day).cumsum()/cw).clip(lower=0); sig=np.sqrt(var)
 z=(d.close-vwap)/sig.replace(0,np.nan)
 out=[]
 for th in Z:
  for side in [-1,1]:
   mask=(z*side>=th)&(z.shift(1)*side<th)
   idx=np.flatnonzero(mask.fillna(False).to_numpy())
   for h in H:
    vals=[]; rev=0; cont=0
    for i in idx:
     if i+h>=len(d): continue
     r=(d.close.iloc[i+h]/d.close.iloc[i]-1)*side
     vals.append(r)
     future=z.iloc[i+1:i+h+1]
     if side==1: rev+=bool((future<=0).any()); cont+=bool((future>=th+0.5).any())
     else: rev+=bool((future>=0).any()); cont+=bool((future<=-(th+0.5)).any())
    if vals: out.append([th,side,h,len(vals),float(np.mean(vals)),rev,cont])
 return out
ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int);ap.add_argument("--shards",type=int,default=8);ap.add_argument("--out",required=True);a=ap.parse_args()
fs=sorted(glob.glob(a.data+"/**/*USDT.csv.gz",recursive=True));fs=[f for i,f in enumerate(fs) if i%a.shards==a.shard]
agg={};errs=[]
for f in fs:
 try:
  for th,s,h,n,sm,rv,ct in calc(f):
   k=f"{th}|{s}|{h}";q=agg.setdefault(k,[0,0.,0,0]);q[0]+=n;q[1]+=sm*n;q[2]+=rv;q[3]+=ct
 except Exception as e: errs.append([os.path.basename(f),str(e)])
out={k:{"n":q[0],"mean_directional_return":q[1]/q[0],"vwap_cross":q[2],"further_0_5sigma":q[3]} for k,q in agg.items()}
json.dump({"files":len(fs),"errors":errs,"stats":out},open(a.out,"w"),indent=2);print("DONE",len(fs),len(errs),sum(x["n"] for x in out.values()))
