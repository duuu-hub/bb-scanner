#!/usr/bin/env python3
from pathlib import Path
import os,pandas as pd,numpy as np
ROOT=Path(os.environ.get("CANONICAL_5Y_DIR","canonical"))
COSTS=[.20,.45,.70]
def features(d):
 d=d.sort_values("open_time").drop_duplicates("open_time")
 idx=pd.to_datetime(d.open_time,unit="ms",utc=True)
 x=d.assign(dt=idx).set_index("dt")[["open","high","low","close"]].astype(float)
 out={}
 ns=idx.view("int64")
 for name,mins in [("1H",60),("1D",1440)]:
  step=mins*60*10**9; bucket=pd.to_datetime((ns//step)*step,utc=True)
  h=pd.DataFrame({"bucket":bucket,"close":x.close.to_numpy()}).groupby("bucket",sort=True).close.last()
  ma=h.rolling(20).mean().shift(1); sd=h.rolling(20).std(ddof=0).shift(1)
  m=pd.Series(ma.reindex(bucket).to_numpy(),index=idx); s=pd.Series(sd.reindex(bucket).to_numpy(),index=idx)
  out[name+"_UP"]=x.close>m+2*s; out[name+"_DN"]=x.close<m-2*s
  out[name+"_z"]=(x.close-m)/s
 return x,out
def main():
 fs=sorted(ROOT.rglob("*.parquet")); assert len(fs)==666
 rows=[]
 for fi,p in enumerate(fs):
  x,f=features(pd.read_parquet(p)); state=(f["1H_DN"]&f["1D_UP"]).to_numpy(); ev=np.flatnonzero(state & ~np.r_[False,state[:-1]])
  for i in ev:
   ent=i+1
   if ent>=len(x): continue
   ep=x.open.iloc[ent]; end=min(len(x),ent+96); H=x.high.iloc[ent:end].to_numpy(); L=x.low.iloc[ent:end].to_numpy()
   # frozen TP4/SL4/24h; conservative same-bar SL.
   ti=np.flatnonzero(H>=ep*1.04); si=np.flatnonzero(L<=ep*.96); ti=ti[0] if len(ti) else 10**9; si=si[0] if len(si) else 10**9
   if si<len(H) and si<=ti: gross=-4.
   elif ti<len(H): gross=4.
   else: gross=(x.close.iloc[end-1]/ep-1)*100
   rows.append((x.index[ent],p.stem,float(f["1D_z"].iloc[i]),float(f["1H_z"].iloc[i]),gross))
  if fi%100==0: print("DONE",fi,flush=True)
 d=pd.DataFrame(rows,columns=["time","symbol","z1d","z1h","gross"]); assert len(d)>0
 d["year"]=d.time.dt.year
 # Structural bins fixed a priori, not optimized on OOS.
 d["strength_1d"]=pd.cut(d.z1d,[-np.inf,2.0,2.5,3.0,np.inf],labels=["2-2.5","2.5-3","3-4","4+"])
 d["depth_1h"]=pd.cut(d.z1h,[-np.inf,-4,-3,-2,-0],labels=["<-4","-4--3","-3--2","-2-0"])
 out=[]
 for dims in [["strength_1d"],["depth_1h"],["strength_1d","depth_1h"]]:
  for key,g in d.groupby(dims,observed=True):
   key=(key,) if not isinstance(key,tuple) else key
   for cost in COSTS:
    r=g.gross-cost
    def pf(a):
     neg=-a[a<0].sum(); return a[a>0].sum()/neg if neg>0 else np.inf
    ins=r[g.year<=2023]; oos=r[g.year>=2024]; yrs=g.assign(net=r).groupby("year").net.mean()
    top=g.groupby("symbol").gross.sum().sort_values(ascending=False); conc=(top.head(10).sum()/max(g.gross[g.gross>0].sum(),1e-9))
    out.append(("+".join(dims),"|".join(map(str,key)),cost,len(g),r.mean(),pf(r),pf(ins),pf(oos),int((yrs>0).sum()),float(yrs.min()),float(conc)))
 o=pd.DataFrame(out,columns=["slice","bin","cost","n","avg_net","pf","pf_is","pf_oos","positive_years","worst_year_avg","top10_profit_share"])
 Path("artifacts").mkdir(exist_ok=True);d.to_csv("artifacts/bb_1d_up_1h_dn_trades.csv.gz",index=False,compression="gzip");o.to_csv("artifacts/bb_1d_up_1h_dn_slices.csv",index=False)
 print("TRADES",len(d),flush=True);print("BASE_YEAR");print(d.assign(net=d.gross-.2).groupby("year").net.agg(["count","mean"]).to_string(),flush=True)
 print("TOP_SLICES");print(o[(o.cost==.2)&(o.n>=300)].sort_values(["pf_oos","pf"],ascending=False).head(30).to_string(index=False),flush=True)
 print("COST70");print(o[(o.cost==.7)&(o.n>=300)&(o.pf_oos>1)].sort_values("pf_oos",ascending=False).head(20).to_string(index=False),flush=True)
if __name__=="__main__":main()
