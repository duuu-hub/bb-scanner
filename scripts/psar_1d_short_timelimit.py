import argparse,glob,os,re,json
import pandas as pd,numpy as np
from psar_1d_exec_helpers import load,contiguous_segments,resample,psar_open_projection,_resolve_1m
V=(2.75,1.2,.75); LIMITS=(1,3,7,14); BURN=100; CUT=1735689600000
def sym(p): return os.path.basename(p)[:-7].upper()
def one(t,o,h,l,c,S):
 assert len(t)==len(o)==len(h)==len(l)==len(c) and len(t)>0
 assert np.all(np.diff(t)==900000), "non-contiguous segment"
 rt,ro,rh,rl,rc=resample(t,o,h,l,c,96);sar,bull=psar_open_projection(rh,rl);prev=np.r_[np.nan,rc[:-1]]
 tr=np.maximum(rh-rl,np.maximum(abs(rh-prev),abs(rl-prev)));ac=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy();ao=np.r_[np.nan,ac[:-1]]
 pos=np.searchsorted(t,rt);out=[]
 for i in range(max(BURN,15),len(rt)):
  if bull[i]:continue
  a=float(ao[i]);ps=float(sar[i]);start=int(pos[i])
  if not np.isfinite(a) or a<=0:continue
  target=ps-V[0]*a; crossed=ro[i]>=target
  if crossed: fs=start;fill=float(ro[i]);order="TAKER"
  else:
   z=h[start:min(start+96,len(t))];hit=np.flatnonzero(z>=target)
   if not hit.size:continue
   fs=start+int(hit[0]);fill=target;order="MAKER"
  sl=ps+V[1]*a
  if fill>=sl:continue
  risk=sl-fill;tp=fill-V[2]*risk
  for days in LIMITS:
   deadline=fs+days*96
   if deadline>len(t): continue
   end=deadline; outcome=None;expx=None;exts=None
   for j in range(fs,end):
    if not crossed and j==fs:
     ht=l[j]<=tp;hs=h[j]>=sl
     if ht or hs:
      rr=_resolve_1m(S,int(t[j]),tp,sl,False,fill,float(h[j]),float(l[j]))
      if rr in ("win","loss"): outcome=rr;expx=tp if rr=="win" else sl;exts=int(t[j]+900000);break
      if rr in ("data_gap","entry_mismatch"): outcome="exclude";break
     continue
    ht=l[j]<=tp;hs=h[j]>=sl
    if ht and hs:
     rr=_resolve_1m(S,int(t[j]),tp,sl,False,None,float(h[j]),float(l[j]))
     if rr in ("win","loss"):outcome=rr;expx=tp if rr=="win" else sl;exts=int(t[j]+900000);break
     outcome="exclude";break
    if hs:outcome="loss";expx=sl;exts=int(t[j]+900000);break
    if ht:outcome="win";expx=tp;exts=int(t[j]+900000);break
   if outcome=="exclude":continue
   if outcome is None:
    j=end-1
    assert j>=fs and int(t[j]+900000)==int(t[fs]+days*86400000)
    outcome="time";expx=float(c[j]);exts=int(t[j]+900000)
   pnl=(fill-expx)/fill*100
   out.append(dict(symbol=S,signal_ts=int(rt[i]),fill_ts=int(t[fs]),exit_ts=exts,limit_days=days,outcome=outcome,pnl_pct=pnl,atr_pct=100*a/float(ro[i]),stop_pct=risk/fill*100))
 return out
ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=8);ap.add_argument("--out",default="tl.csv.gz");a=ap.parse_args()
files=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));files=[p for k,p in enumerate(files) if k%a.shards==a.shard];rows=[]
for n,p in enumerate(files,1):
 S=sym(p);D=load(p)
 for aa,bb in contiguous_segments(D[0]):
  if bb-aa<96*(BURN+1):continue
  rows+=one(*(x[aa:bb] for x in D),S)
 print("PROGRESS",a.shard,n,len(files),S,len(rows),flush=True)
z=pd.DataFrame(rows)
assert len(z)>0
assert set(z.limit_days.unique())==set(LIMITS)
assert z[["fill_ts","exit_ts","pnl_pct","atr_pct","stop_pct"]].notna().all().all()
assert (z.exit_ts>z.fill_ts).all()
assert np.isfinite(z[["pnl_pct","atr_pct","stop_pct"]].to_numpy()).all()
assert (z.atr_pct>0).all() and (z.stop_pct>0).all()
for days,g in z.groupby("limit_days"):
 assert (g.exit_ts-g.fill_ts<=days*86400000).all()
 assert (g[g.outcome=="time"].exit_ts-g[g.outcome=="time"].fill_ts==days*86400000).all()
z.to_csv(a.out,index=False,compression="gzip");print("PASS",len(z),z.groupby(["limit_days","outcome"]).size().to_dict())
