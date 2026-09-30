#!/usr/bin/env python3
import argparse,gzip,glob,json,os
import numpy as np,pandas as pd
def one(path):
 d=pd.read_csv(path,compression="gzip"); d["t"]=pd.to_datetime(d.open_time,unit="ms",utc=True)
 for c in ["open","high","low","close","volume"]: d[c]=pd.to_numeric(d[c],errors="coerce")
 d=d.set_index("t")[["open","high","low","close","volume"]].dropna().resample("1h").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()
 tp=(d.high+d.low+d.close)/3; day=d.index.floor("D")
 d["vwap"]=(tp*d.volume).groupby(day).cumsum()/d.volume.groupby(day).cumsum()
 d["ema"]=d.close.ewm(span=9,adjust=False).mean()
 pc=d.close.shift(); tr=pd.concat([(d.high-d.low),(d.high-pc).abs(),(d.low-pc).abs()],axis=1).max(axis=1)
 d["atr"]=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
 cross=(d.ema>d.vwap)&(d.ema.shift()<=d.vwap.shift())&(d.close>d.ema)&(d.close>d.vwap)
 out=[]
 for i in np.flatnonzero(cross.to_numpy()):
  if i+1>=len(d): continue
  trigger=d.high.iloc[i]; end=min(i+4,len(d)); ent=None
  for j in range(i+1,end):
   if d.high.iloc[j]>=trigger: ent=j; break
  if ent is None: continue
  entry=float(trigger); atr=float(d.atr.iloc[i])
  if not np.isfinite(atr) or atr<=0: continue
  sl=entry-1.5*atr; tpv=entry+3*atr; ex=min(ent+60,len(d)-1); pnl=None; reason="TIME"
  for k in range(ent,ex+1):
   hitS=d.low.iloc[k]<=sl; hitT=d.high.iloc[k]>=tpv
   if hitS: pnl=sl/entry-1; reason="SL"; ex=k; break
   if hitT: pnl=tpv/entry-1; reason="TP"; ex=k; break
  if pnl is None: pnl=float(d.close.iloc[ex]/entry-1)
  out.append((pnl,reason,d.index[ent],d.index[ex]))
 return out
ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=8);ap.add_argument("--out",required=True);a=ap.parse_args()
fs=sorted(glob.glob(a.data+"/**/*USDT.csv.gz",recursive=True)); fs=[f for i,f in enumerate(fs) if i%a.shards==a.shard]
all=[];errs=[]
for f in fs:
 try: all+=one(f)
 except Exception as e: errs.append([os.path.basename(f),str(e)])
p=np.array([x[0] for x in all],float); gp=p[p>0].sum(); gl=-p[p<0].sum()
res={"definition":{"strategy":"EMA9 x UTC Daily VWAP breakout","tf":"1h","wait_bars":3,"sl_atr":1.5,"tp_atr":3.0,"timeout_h":60,"atr":14,"same_bar_policy":"SL_first","cost_model":"gross plus 0.10% round-trip diagnostic","shard":a.shard,"shards":a.shards},"files":len(fs),"errors":errs,"n":len(all),"wins":int((p>0).sum()),"losses":int((p<=0).sum()),"gross_pf":float(gp/gl) if gl else None,"gross_return_sum_pct":float(p.sum()*100),"net_return_sum_pct_10bp_rt":float((p-0.001).sum()*100),"tp":sum(x[1]=="TP" for x in all),"sl":sum(x[1]=="SL" for x in all),"time":sum(x[1]=="TIME" for x in all)}
json.dump(res,open(a.out,"w"),indent=2,default=str);print(json.dumps(res))
