#!/usr/bin/env python3
import argparse,csv,gzip,json,math
from pathlib import Path
from datetime import datetime,timezone
import pandas as pd, numpy as np

def load(p):
    d=pd.read_csv(p,compression="gzip")
    d["dt"]=pd.to_datetime(d.open_time,unit="ms",utc=True); d=d.set_index("dt")
    for c in ["open","high","low","close","volume"]: d[c]=pd.to_numeric(d[c],errors="coerce")
    return d[["open","high","low","close","volume"]].dropna().sort_index()

def h1_from_15m(d):
    return d.resample("1h").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()

def signals(h,thr):
    x=h.copy()
    h4=x.resample("4h").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
    h4["sma20"]=h4.close.rolling(20).mean()
    h4["bias"]=np.where(h4.close>h4.sma20,1,-1)
    x["bias"]=h4.bias.reindex(x.index,method="ffill")
    dy=x.resample("1D").agg({"high":"max","low":"min"})
    dy["prev_high"]=dy.high.shift(1);dy["prev_low"]=dy.low.shift(1)
    x["prev_high"]=dy.prev_high.reindex(x.index,method="ffill")
    x["prev_low"]=dy.prev_low.reindex(x.index,method="ffill")
    atr=(x.high-x.low).rolling(14).mean().bfill()
    bull=(x.high>x.prev_high+.25*atr)&(x.close<x.prev_high)
    bear=(x.low<x.prev_low-.25*atr)&(x.close>x.prev_low)
    # faithfully reproduce published code's FVG implementation
    fvg_bull=x.low>x.low.shift(2)
    fvg_bear=x.high<x.high.shift(2)
    ls=(x.bias.eq(1).astype(int)*4+bull.astype(int)*4+fvg_bull.astype(int)*4)
    ss=(x.bias.eq(-1).astype(int)*4+bear.astype(int)*4+fvg_bear.astype(int)*4)
    sig=np.where((x.bias==1)&(ls>=thr),1,np.where((x.bias==-1)&(ss>=thr),-1,0))
    return x,pd.Series(sig,index=x.index)

def bt(x,sig,fee=.0001,slip=.0002):
    # opposite-signal exit; one position; return normalized to 1x equity for interpretable replay
    pos=0; entry=0.; trades=[]; rets=[]
    for i,(t,s) in enumerate(sig.items()):
        px=float(x.close.iloc[i])
        if pos and s==-pos:
            exitpx=px*(1-slip if pos==1 else 1+slip)
            gross=pos*(exitpx/entry-1)
            net=gross-2*fee
            trades.append((t,pos,net)); rets.append(net); pos=0
        if pos==0 and s:
            pos=int(s); entry=px*(1+slip if pos==1 else 1-slip)
    if pos:
        px=float(x.close.iloc[-1]); exitpx=px*(1-slip if pos==1 else 1+slip)
        net=pos*(exitpx/entry-1)-2*fee; trades.append((x.index[-1],pos,net));rets.append(net)
    if not rets:return {"trades":0}
    a=np.array(rets); wins=a[a>0]; losses=a[a<0]
    eq=np.cumprod(1+a); peak=np.maximum.accumulate(eq); dd=eq/peak-1
    return {"trades":len(a),"win_rate":float((a>0).mean()),"pf":float(wins.sum()/abs(losses.sum())) if len(losses) else None,
      "return_pct":float((eq[-1]-1)*100),"mdd_pct":float(dd.min()*100),"long":sum(z[1]==1 for z in trades),"short":sum(z[1]==-1 for z in trades)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--data",required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
    out={}; Path(a.out).parent.mkdir(parents=True,exist_ok=True)
    for sym in ["BTCUSDT","ETHUSDT"]:
        ps=list(Path(a.data).rglob(sym+".csv.gz"))
        if not ps: out[sym]={"error":"missing"};continue
        h=h1_from_15m(load(ps[0])); out[sym]={"range":[str(h.index.min()),str(h.index.max())]}
        for th in range(4,13):
            x,s=signals(h,th);out[sym][str(th)]=bt(x,s)
    Path(a.out).write_text(json.dumps(out,indent=2),encoding="utf-8");print(json.dumps(out,indent=2))
if __name__=="__main__":main()
