#!/usr/bin/env python3
from pathlib import Path
import os,itertools,numpy as np,pandas as pd
ROOT=Path(os.environ.get("CANONICAL_5Y_DIR","canonical"))
TF={"15m":"15min","1H":"1h","4H":"4h","1D":"1D","3D":"3D"}
PAT={
"pullback_1Hdn_1Dup":["1H_DN","1D_UP"],
"pullback_1Hdn_3Dup":["1H_DN","3D_UP"],
"rebound_15dn_1Ddn":["15m_DN","1D_DN"],
"rebound_1Hdn_1Ddn":["1H_DN","1D_DN"],
"rebound_15dn_1Hdn":["15m_DN","1H_DN"],
}
TPS=[1,2,3,4,5]; SLS=[1,2,3,4]; HOLDS=[4,12,24] # hours
COSTS=[.20,.45,.70]
def frame(df):
 df=df.sort_values("open_time").drop_duplicates("open_time")
 x=df.assign(dt=pd.to_datetime(df.open_time,unit="ms",utc=True)).set_index("dt")[["open","high","low","close"]].astype(float)
 f=pd.DataFrame(index=x.index)
 for n,r in TF.items():
  c=x.close.resample(r,origin="epoch",label="left",closed="left").last().dropna()
  ma=c.rolling(20).mean().shift(1); sd=c.rolling(20).std(ddof=0).shift(1); k=x.index.floor(r)
  m=pd.Series(ma.reindex(k).to_numpy(),index=x.index); s=pd.Series(sd.reindex(k).to_numpy(),index=x.index)
  f[n+"_UP"]=x.close>m+2*s; f[n+"_DN"]=x.close<m-2*s
 return x,f
def main():
 rows=[]
 files=sorted(ROOT.rglob("*.parquet")); print("UNIVERSE",len(files),flush=True)
 combos=list(itertools.product(TPS,SLS,HOLDS))
 for fi,p in enumerate(files):
  try:x,f=frame(pd.read_parquet(p))
  except Exception as e: print("ERR",p,e,flush=True); continue
  op=x.open.to_numpy(float); hi=x.high.to_numpy(float); lo=x.low.to_numpy(float); cl=x.close.to_numpy(float)
  years=x.index.year.to_numpy()
  for pn,cs in PAT.items():
   state=np.logical_and.reduce([f[z].to_numpy() for z in cs])
   ids=np.flatnonzero(state & ~np.r_[False,state[:-1]])
   for i in ids:
    ent=i+1
    if ent>=len(x): continue
    ep=op[ent]; yr=years[ent]; maxend=min(len(x),ent+96)
    H=hi[ent:maxend]; L=lo[ent:maxend]
    # Precompute first touch bar once for every TP and SL threshold.
    tpfirst={tp:(np.flatnonzero(H>=ep*(1+tp/100))[0] if np.any(H>=ep*(1+tp/100)) else 10**9) for tp in TPS}
    slfirst={sl:(np.flatnonzero(L<=ep*(1-sl/100))[0] if np.any(L<=ep*(1-sl/100)) else 10**9) for sl in SLS}
    for tp,sl,hold in combos:
     lim=min(len(H),hold*4); ti=tpfirst[tp]; si=slfirst[sl]
     if si<lim and si<=ti: ret=-sl
     elif ti<lim: ret=tp
     else:
      e=min(len(x)-1,ent+hold*4-1); ret=(cl[e]/ep-1)*100
     rows.append((pn,yr,tp,sl,hold,ret))
  if fi%50==0: print("DONE",fi,flush=True)
 print("FILES_DONE",len(files),flush=True)
 d=pd.DataFrame(rows,columns=["pattern","year","tp","sl","hold","gross"])
 out=[]
 for (p,tp,sl,h),g in d.groupby(["pattern","tp","sl","hold"]):
  for cost in COSTS:
   r=g.gross-cost; yrs=pd.DataFrame({"year":g.year.to_numpy(),"net":r.to_numpy()}).groupby("year").net.mean()
   ins=r[g.year<=2023]; oos=r[g.year>=2024]
   def pf(a):
    return a[a>0].sum()/(-a[a<0].sum()) if (a<0).any() else np.inf
   out.append((p,tp,sl,h,cost,len(r),r.mean(),pf(r),pf(ins),pf(oos),(yrs>0).sum(),yrs.min()))
 o=pd.DataFrame(out,columns=["pattern","tp","sl","hold_h","cost","n","avg_net","pf","pf_is","pf_oos","positive_years","worst_year_avg"])
 Path("artifacts").mkdir(exist_ok=True);o.to_csv("artifacts/bb_pattern_validation.csv",index=False)
 base=o[(o.cost==.20)&(o.n>=300)].sort_values(["pf_oos","pf"],ascending=False)
 print("TOP_BASE");print(base.head(40).to_string(index=False))
 print("ROBUST_COST");print(o[(o.cost==.70)&(o.n>=300)&(o.pf_oos>1)].sort_values("pf_oos",ascending=False).head(30).to_string(index=False))
if __name__=="__main__":main()
