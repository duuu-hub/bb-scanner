#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np

def load(sym,years):
 fs=sorted(glob.glob(f"market_data_store/bitget/15m/{sym}/*.csv"));assert fs
 d=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True);c={x.lower():x for x in d.columns}
 t=c["timestamp_ms"];d["dt"]=pd.to_datetime(pd.to_numeric(d[t]),unit="ms",utc=True)
 for x in ("open","high","low","close"):d[x]=pd.to_numeric(d[c[x]],errors="coerce")
 d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").reset_index(drop=True)
 end=d.dt.max();d=d[d.dt>=end-pd.Timedelta(days=365.25*years)].reset_index(drop=True)
 assert d.dt.is_monotonic_increasing and not d.dt.duplicated().any()
 gaps=int((d.dt.diff().dropna()!=pd.Timedelta(minutes=15)).sum())
 return d,gaps

def stats(a):
 a=np.asarray(a,float)
 if not len(a):return dict(trades=0,wr=np.nan,pf=np.nan,exp_r=np.nan,net_r=0,mdd_r=0)
 gp=a[a>0].sum();gl=-a[a<0].sum();eq=np.cumsum(a);pk=np.maximum.accumulate(np.r_[0,eq])
 return dict(trades=len(a),wr=(a>0).mean(),pf=gp/gl if gl else np.nan,exp_r=a.mean(),net_r=a.sum(),mdd_r=-(np.r_[0,eq]-pk).min())

def run(d,n,q,exit_mode,hold,cost_bps=8,rr=2):
 # Range at signal t uses ONLY bars t-N..t-1. Signal is close[t], entry=open[t+1].
 hi=d.high.shift(1).rolling(n).max().to_numpy();lo=d.low.shift(1).rolling(n).min().to_numpy()
 op=d.open.to_numpy();h=d.high.to_numpy();l=d.low.to_numpy();cl=d.close.to_numpy();dt=d.dt
 out=[];i=n
 while i<len(d)-1:
  width=hi[i]-lo[i]
  if not np.isfinite(width) or width<=0:i+=1;continue
  pos=(cl[i]-lo[i])/width
  side=1 if pos<=q else (-1 if pos>=1-q else 0)
  if not side:i+=1;continue
  e=i+1;entry=op[e];stop=lo[i] if side==1 else hi[i];risk=(entry-stop)*side
  if risk<=0:i+=1;continue
  target=(lo[i]+0.5*width) if exit_mode=="EQ" else ((lo[i]+(1-q)*width) if side==1 else (lo[i]+q*width)) if exit_mode=="OPPOSITE" else entry+side*rr*risk
  res=None;amb=0;last=min(e+hold-1,len(d)-1)
  for k in range(e,last+1):
   hs=(l[k]<=stop) if side==1 else (h[k]>=stop);ht=(h[k]>=target) if side==1 else (l[k]<=target)
   if hs and ht:res=-1;amb=1;last=k;break
   if hs:res=-1;last=k;break
   if ht:res=((target-entry)/risk)*side;last=k;break
  if res is None:res=((cl[last]-entry)/risk)*side
  res-=(cost_bps/10000)*entry/risk
  out.append((res,side,int(dt.iloc[e].year),amb))
  i=last+1 # no overlapping positions
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--years",type=float,default=5);ap.add_argument("--out",default="pd_results");a=ap.parse_args();Path(a.out).mkdir(exist_ok=True)
 rows=[];yr=[];audit=[]
 for sym in ("BTCUSDT","ETHUSDT"):
  d,g=load(sym,a.years);audit.append(dict(symbol=sym,rows=len(d),start=d.dt.min(),end=d.dt.max(),gaps=g))
  for n in (20,40,80,160,320):
   for q in (.10,.20,.25):
    for ex in ("EQ","OPPOSITE","R2"):
     for hold in (32,96,288):
      z=run(d,n,q,ex,hold);rs=[x[0] for x in z];s=stats(rs)
      rows.append(dict(symbol=sym,n=n,q=q,exit=ex,hold_bars=hold,**s,ambiguous=sum(x[3] for x in z)))
      for side in (1,-1):
       for y in sorted(set(x[2] for x in z)):
        v=[x[0] for x in z if x[1]==side and x[2]==y]
        if v:yr.append(dict(symbol=sym,n=n,q=q,exit=ex,hold_bars=hold,side="LONG" if side==1 else "SHORT",year=y,**stats(v)))
 pd.DataFrame(rows).to_csv(f"{a.out}/summary.csv",index=False);pd.DataFrame(yr).to_csv(f"{a.out}/yearly.csv",index=False);pd.DataFrame(audit).to_csv(f"{a.out}/data_audit.csv",index=False)
 x=pd.DataFrame(rows);print(pd.DataFrame(audit).to_string(index=False));print(x.sort_values(["symbol","pf"],ascending=[True,False]).groupby("symbol").head(15).to_string(index=False))
if __name__=="__main__":main()
