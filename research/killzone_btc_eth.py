#!/usr/bin/env python3
import argparse, glob
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd, numpy as np

NY=ZoneInfo("America/New_York")
SESSIONS={"ALL":None,"LONDON":(2,5),"NEWYORK":(7,10),"LONDON_NY":None}

def load(sym,years):
 fs=sorted(glob.glob(f"market_data_store/bitget/15m/{sym}/*.csv")); assert fs
 d=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True); cols={c.lower():c for c in d.columns}
 t=next(cols[x] for x in ("timestamp","timestamp_ms","ts","open_time","datetime_utc","datetime","date") if x in cols); s=d[t]
 if np.issubdtype(s.dtype,np.number): d["dt"]=pd.to_datetime(s,unit=("ms" if float(s.dropna().iloc[0])>1e11 else "s"),utc=True)
 else:d["dt"]=pd.to_datetime(s,utc=True)
 for x in ("open","high","low","close"):d[x]=pd.to_numeric(d[cols[x]],errors="coerce")
 d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt")
 end=d.dt.max(); d=d[d.dt>=end-pd.Timedelta(days=365.25*years)].reset_index(drop=True)
 gaps=(d.dt.diff().dropna()!=pd.Timedelta(minutes=15)).sum()
 assert d.dt.is_monotonic_increasing and d.dt.duplicated().sum()==0
 return d,int(gaps)

def insession(dt,name):
 if name=="ALL":return True
 h=dt.astimezone(NY).hour
 if name=="LONDON_NY":return 2<=h<5 or 7<=h<10
 a,b=SESSIONS[name];return a<=h<b

def candidates(d,session,lb=20):
 hi=d.high.shift(1).rolling(lb).max();lo=d.low.shift(1).rolling(lb).min();o=[]
 for i in range(lb,len(d)-8):
  if not insession(d.dt.iloc[i].to_pydatetime(),session):continue
  if d.low.iloc[i]<lo.iloc[i] and d.close.iloc[i]>lo.iloc[i]:o.append((i,"LONG"))
  if d.high.iloc[i]>hi.iloc[i] and d.close.iloc[i]<hi.iloc[i]:o.append((i,"SHORT"))
 return o

def confirm(d,i,side,need_mss,need_fvg):
 # Strict chronology: all confirmations must occur AFTER sweep and entry only AFTER they are known.
 # MSS = close beyond pre-sweep 4-bar opposing extreme; FVG = standard 3-candle gap completed after sweep.
 ref_hi=d.high.iloc[max(0,i-4):i].max(); ref_lo=d.low.iloc[max(0,i-4):i].min()
 mss_at=fvg_at=None
 for k in range(i+1,min(i+7,len(d))):
  if mss_at is None:
   if side=="LONG" and d.close.iloc[k]>ref_hi:mss_at=k
   if side=="SHORT" and d.close.iloc[k]<ref_lo:mss_at=k
  if k>=i+2 and fvg_at is None:
   if side=="LONG" and d.low.iloc[k]>d.high.iloc[k-2]:fvg_at=k
   if side=="SHORT" and d.high.iloc[k]<d.low.iloc[k-2]:fvg_at=k
 if need_mss and mss_at is None:return None
 if need_fvg and fvg_at is None:return None
 known=max([i]+([mss_at] if need_mss else [])+([fvg_at] if need_fvg else []))
 return known

def trade(d,i,side,m=False,f=False,rr=2,maxbars=32,cost_bps=8):
 known=confirm(d,i,side,m,f)
 if known is None:return None
 e=known+1
 if e>=len(d):return None
 entry=d.open.iloc[e];stop=d.low.iloc[i] if side=="LONG" else d.high.iloc[i]
 risk=(entry-stop) if side=="LONG" else (stop-entry)
 if risk<=0:return None
 tp=entry+rr*risk if side=="LONG" else entry-rr*risk
 amb=0;res=None
 for k in range(e,min(e+maxbars,len(d))):
  sl=(d.low.iloc[k]<=stop) if side=="LONG" else (d.high.iloc[k]>=stop)
  hit=(d.high.iloc[k]>=tp) if side=="LONG" else (d.low.iloc[k]<=tp)
  if sl and hit:amb=1;res=-1;break
  if sl:res=-1;break
  if hit:res=rr;break
 if res is None:
  q=d.close.iloc[min(e+maxbars-1,len(d)-1)];res=((q-entry)/risk)*(1 if side=="LONG" else -1)
 res-=(cost_bps/10000)*entry/risk
 return res,amb,d.dt.iloc[e],e-known

def stats(rs):
 if not rs:return {"trades":0,"wr":None,"pf":None,"expectancy_r":None,"net_r":0,"mdd_r":0}
 a=np.array(rs);gp=a[a>0].sum();gl=-a[a<0].sum();eq=np.cumsum(a);peak=np.maximum.accumulate(np.r_[0,eq])
 return {"trades":len(a),"wr":float((a>0).mean()),"pf":float(gp/gl) if gl else None,"expectancy_r":float(a.mean()),"net_r":float(a.sum()),"mdd_r":float(-(np.r_[0,eq]-peak).min())}

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--years",type=float,default=5);ap.add_argument("--out",default="killzone_audit");z=ap.parse_args()
 Path(z.out).mkdir(parents=True,exist_ok=True);rows=[];yearly=[];audit=[]
 variants=[("SWEEP",0,0),("SWEEP_MSS",1,0),("SWEEP_FVG",0,1),("FULL",1,1)]
 for sym in ("BTCUSDT","ETHUSDT"):
  d,gaps=load(sym,z.years);audit.append({"symbol":sym,"rows":len(d),"start":d.dt.min(),"end":d.dt.max(),"gaps":gaps})
  for sess in SESSIONS:
   sig=candidates(d,sess)
   for vn,m,f in variants:
    by={};amb=0
    for i,side in sig:
     q=trade(d,i,side,m,f)
     if q:
      r,a,dt,_=q;amb+=a;by.setdefault((side,int(dt.year)),[]).append(r)
    rs=[r for v in by.values() for r in v];rows.append({"symbol":sym,"session":sess,"variant":vn,**stats(rs),"ambiguous_sl_first":amb})
    for (side,y),v in by.items():yearly.append({"symbol":sym,"session":sess,"variant":vn,"side":side,"year":y,**stats(v)})
 pd.DataFrame(rows).to_csv(f"{z.out}/summary.csv",index=False);pd.DataFrame(yearly).to_csv(f"{z.out}/yearly.csv",index=False);pd.DataFrame(audit).to_csv(f"{z.out}/data_audit.csv",index=False)
 print(pd.DataFrame(audit).to_string(index=False));print(pd.DataFrame(rows).to_string(index=False))
if __name__=="__main__":main()
