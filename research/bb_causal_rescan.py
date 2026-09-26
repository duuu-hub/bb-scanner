#!/usr/bin/env python3
from pathlib import Path
import os,itertools,pandas as pd,numpy as np
ROOT=Path(os.environ.get("CANONICAL_5Y_DIR","canonical"))
TFS={"15m":15,"1H":60,"4H":240,"1D":1440,"3D":4320}; FWD={"15m":1,"1H":4,"4H":16,"12H":48,"24H":96}
def feats(d):
 d=d.sort_values("open_time").drop_duplicates("open_time"); idx=pd.to_datetime(d.open_time,unit="ms",utc=True)
 x=d.assign(dt=idx).set_index("dt")[["close"]].astype(float); ns=idx.astype("int64").to_numpy(); out={}
 for n,mins in TFS.items():
  step=mins*60*10**9; bucket=pd.to_datetime((ns//step)*step,utc=True)
  h=pd.DataFrame({"bucket":bucket,"close":x.close.to_numpy()}).groupby("bucket",sort=True).close.last()
  ma=h.rolling(20).mean().shift(1); sd=h.rolling(20).std(ddof=0).shift(1)
  m=pd.Series(ma.reindex(bucket).to_numpy(),index=idx); s=pd.Series(sd.reindex(bucket).to_numpy(),index=idx)
  out[n+"_UP"]=(x.close>m+2*s).to_numpy(); out[n+"_DN"]=(x.close<m-2*s).to_numpy()
 if len(out) and not hasattr(feats,"_printed"):\n  print("SANITY", "rows",len(x), "close_finite",int(np.isfinite(x.close).sum()), *[(k,int(np.isfinite(v).sum()),int(v.sum())) for k,v in out.items()], flush=True); feats._printed=True\n return x,out
def main():
 fs=sorted(ROOT.rglob("*.parquet")); assert len(fs)==666
 names=[n+s for n in TFS for s in ("_UP","_DN")]; stats={}
 def add(key,r):
  z=stats.setdefault(key,[0,0.,0.,0])
  z[0]+=len(r); z[1]+=np.nansum(r); z[2]+=np.nansum(r>0); z[3]+=np.nansum(np.isfinite(r))
 for fi,p in enumerate(fs):
  x,f=feats(pd.read_parquet(p)); close=x.close.to_numpy()
  for n in names:
   a=f[n]; ev=a & ~np.r_[False,a[:-1]]
   ids=np.flatnonzero(ev)
   for hn,k in FWD.items():
    ids2=ids[ids+k<len(close)]; r=close[ids2+k]/close[ids2]-1
    add(("single",n,"",hn),r)
  for a,b in itertools.combinations(names,2):
   # exclude impossible same-TF UP+DN
   if a.split("_")[0]==b.split("_")[0]: continue
   st=f[a]&f[b]; ids=np.flatnonzero(st & ~np.r_[False,st[:-1]])
   for hn,k in FWD.items():
    ids2=ids[ids+k<len(close)]; r=close[ids2+k]/close[ids2]-1
    add(("pair",a,b,hn),r)
  if fi%100==0: print("DONE",fi,flush=True)
 rows=[]
 for (kind,a,b,h),z in stats.items():
  n,s,pos,valid=z
  rows.append((kind,a,b,h,n,s/n*100 if n else np.nan,pos/valid if valid else np.nan))
 o=pd.DataFrame(rows,columns=["kind","a","b","horizon","n","mean_ret_pct","up_prob"])
 assert (o.n>0).any(), "EMPTY_SIGNAL_OUTPUT: causal BB scan produced zero events"\n Path("artifacts").mkdir(exist_ok=True);o.to_csv("artifacts/bb_causal_rescan.csv",index=False)
 q=o[(o.horizon=="24H")&(o.n>=300)].sort_values("mean_ret_pct",ascending=False)
 print("FILES_DONE",len(fs),flush=True);print("TOP_24H");print(q.head(40).to_string(index=False),flush=True)
 print("BOTTOM_24H");print(q.tail(25).to_string(index=False),flush=True)
if __name__=="__main__":main()
