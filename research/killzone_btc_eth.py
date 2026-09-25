#!/usr/bin/env python3
import argparse, glob, json, math, os
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

NY=ZoneInfo("America/New_York")
SESSIONS={"ALL":None,"LONDON":(2,5),"NEWYORK":(7,10),"LONDON_NY":None}

def load(sym, years):
    fs=sorted(glob.glob(f"market_data_store/bitget/15m/{sym}/*.csv"))
    if not fs: raise SystemExit(f"no files for {sym}")
    d=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
    cols={c.lower():c for c in d.columns}
    t=next((cols[x] for x in ("timestamp","ts","open_time","datetime","date") if x in cols),None)
    if t is None: raise SystemExit(f"timestamp column missing: {d.columns.tolist()}")
    s=d[t]
    if np.issubdtype(s.dtype,np.number):
        unit="ms" if float(s.dropna().iloc[0])>1e11 else "s"; d["dt"]=pd.to_datetime(s,unit=unit,utc=True)
    else: d["dt"]=pd.to_datetime(s,utc=True)
    for x in ("open","high","low","close"): d[x]=pd.to_numeric(d[cols[x]],errors="coerce")
    d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt")
    end=d.dt.max(); start=end-pd.Timedelta(days=365.25*years)
    return d[d.dt>=start].reset_index(drop=True)

def insession(dt,name):
    if name=="ALL": return True
    h=dt.astimezone(NY).hour
    if name=="LONDON_NY": return (2<=h<5) or (7<=h<10)
    a,b=SESSIONS[name]; return a<=h<b

def signals(d,session,lookback=20):
    hi=d.high.shift(1).rolling(lookback).max(); lo=d.low.shift(1).rolling(lookback).min()
    out=[]
    for i in range(lookback,len(d)-1):
        if not insession(d.dt.iloc[i].to_pydatetime(),session): continue
        bull=d.low.iloc[i] < lo.iloc[i] and d.close.iloc[i] > lo.iloc[i]
        bear=d.high.iloc[i] > hi.iloc[i] and d.close.iloc[i] < hi.iloc[i]
        if bull: out.append((i,"LONG"))
        if bear: out.append((i,"SHORT"))
    return out

def fvg_ok(d,i,side):
    if i+2>=len(d): return False
    return d.low.iloc[i+2]>d.high.iloc[i] if side=="LONG" else d.high.iloc[i+2]<d.low.iloc[i]

def mss_ok(d,i,side,n=4):
    j=min(i+4,len(d)-1)
    if side=="LONG": return d.high.iloc[i+1:j+1].max()>d.high.iloc[max(0,i-n):i].max()
    return d.low.iloc[i+1:j+1].min()<d.low.iloc[max(0,i-n):i].min()

def trade(d,i,side,need_mss=False,need_fvg=False,rr=2.0,maxbars=32,cost_bps=8):
    if need_mss and not mss_ok(d,i,side): return None
    if need_fvg and not any(fvg_ok(d,k,side) for k in range(i,min(i+4,len(d)-2))): return None
    e=i+1; entry=d.open.iloc[e]
    if side=="LONG":
        stop=d.low.iloc[i]; risk=entry-stop
        if risk<=0:return None
        tp=entry+rr*risk
    else:
        stop=d.high.iloc[i]; risk=stop-entry
        if risk<=0:return None
        tp=entry-rr*risk
    exit_r=None; amb=0
    for k in range(e,min(e+maxbars,len(d))):
        if side=="LONG": hit_sl=d.low.iloc[k]<=stop; hit_tp=d.high.iloc[k]>=tp
        else: hit_sl=d.high.iloc[k]>=stop; hit_tp=d.low.iloc[k]<=tp
        if hit_sl and hit_tp: amb+=1; exit_r=-1; break
        if hit_sl: exit_r=-1; break
        if hit_tp: exit_r=rr; break
    if exit_r is None: exit_r=((d.close.iloc[min(e+maxbars-1,len(d)-1)]-entry)/risk)*(1 if side=="LONG" else -1)
    cost=(cost_bps/10000.0)*entry/risk
    return exit_r-cost,amb,d.dt.iloc[e]

def stats(rs):
    if not rs:return {"trades":0,"wr":None,"pf":None,"expectancy_r":None,"net_r":0,"mdd_r":0}
    a=np.array(rs); wins=a[a>0].sum(); losses=-a[a<0].sum(); eq=np.cumsum(a); peak=np.maximum.accumulate(np.r_[0,eq]); dd=np.r_[0,eq]-peak
    return {"trades":len(a),"wr":float((a>0).mean()),"pf":float(wins/losses) if losses>0 else None,"expectancy_r":float(a.mean()),"net_r":float(a.sum()),"mdd_r":float(-dd.min())}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--years",type=float,default=5); ap.add_argument("--out",default="killzone_results"); z=ap.parse_args()
    Path(z.out).mkdir(parents=True,exist_ok=True); rows=[]; yearly=[]
    variants=[("SWEEP",False,False),("SWEEP_MSS",True,False),("SWEEP_FVG",False,True),("FULL",True,True)]
    for sym in ("BTCUSDT","ETHUSDT"):
      d=load(sym,z.years)
      for sess in ("ALL","LONDON","NEWYORK","LONDON_NY"):
       sig=signals(d,sess)
       for vn,m,f in variants:
        by={}; amb=0
        for i,side in sig:
          q=trade(d,i,side,m,f)
          if q:
           r,a,dt=q; by.setdefault((side,int(dt.year)),[]).append(r); amb+=a
        allrs=[r for v in by.values() for r in v]
        st=stats(allrs); rows.append({"symbol":sym,"session":sess,"variant":vn,**st,"ambiguous_sl_first":amb,"start":str(d.dt.min()),"end":str(d.dt.max())})
        for (side,y),rs in by.items(): yearly.append({"symbol":sym,"session":sess,"variant":vn,"side":side,"year":y,**stats(rs)})
    pd.DataFrame(rows).to_csv(f"{z.out}/summary.csv",index=False); pd.DataFrame(yearly).to_csv(f"{z.out}/yearly.csv",index=False)
    print(pd.DataFrame(rows).to_string(index=False)); print("Saved",z.out)
if __name__=="__main__": main()
