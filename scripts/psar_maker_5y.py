import argparse,glob,json
import pandas as pd,numpy as np
ATR_MULTS=(.25,.5,.75,1.,1.5); TFS={"1h":4,"4h":16}; RS=(1.,1.5,2.,3.)

def psar(h,l,af0=.02,step=.02,afmax=.2):
 n=len(h); s=np.full(n,np.nan); b=np.ones(n,bool)
 if n<3:return s,b
 s[1]=l[0];ep=h[1];af=af0
 for i in range(2,n):
  z=s[i-1]+af*(ep-s[i-1])
  if b[i-1]:
   z=min(z,l[i-1],l[i-2])
   if l[i]<z:b[i]=False;z=ep;ep=l[i];af=af0
   else:
    if h[i]>ep:ep=h[i];af=min(af+step,afmax)
  else:
   z=max(z,h[i-1],h[i-2])
   if h[i]>z:b[i]=True;z=ep;ep=h[i];af=af0
   else:
    b[i]=False
    if l[i]<ep:ep=l[i];af=min(af+step,afmax)
  s[i]=z
 return s,b

def load(p):
 d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"])
 d=d.sort_values("open_time"); return (d.open_time.to_numpy(np.int64),d.open.to_numpy(float),d.high.to_numpy(float),d.low.to_numpy(float),d.close.to_numpy(float))

def resample(t,o,h,l,c,m):
 # Binance 15m timestamps -> aligned m-child bars; discard incomplete tail groups
 bucket=t//(900000*m); cut=np.r_[0,np.flatnonzero(bucket[1:]!=bucket[:-1])+1,len(t)]
 st=cut[:-1]; en=cut[1:]; good=(en-st)==m; st=st[good];en=en[good]
 return t[st],o[st],np.maximum.reduceat(h,st),np.minimum.reduceat(l,st),c[en-1]

def test(t,o,h,l,c,m,atr_mult,horizon=12):
 rt,ro,rh,rl,rc=resample(t,o,h,l,c,m); sar,bull=psar(rh,rl); n=len(rt)
 prev=np.r_[np.nan,rc[:-1]]; tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prev),np.abs(rl-prev)))
 atr=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()
 out={s:{"signals":0,"fills":0,**{f"{r:g}R_{v}":0 for r in RS for v in ("win","loss","amb")}} for s in ("LONG","SHORT")}
 # map resampled start timestamp to 15m array position once
 pos=np.searchsorted(t,rt)
 for i in range(3,n-horizon-1):
  s=sar[i]
  if not np.isfinite(s) or not np.isfinite(atr[i]):continue
  b=bull[i]; side="LONG" if b else "SHORT";q=out[side];q["signals"]+=1;e=s+(atr_mult*atr[i] if b else -atr_mult*atr[i])
  a=pos[i+1]; first_end=min(a+m,len(t)); hits=np.flatnonzero((l[a:first_end]<=e)&(h[a:first_end]>=e))
  if hits.size==0:continue
  q["fills"]+=1; fs=a+hits[0]; end=min(a+m*horizon,len(t)); ph=h[fs:end];pl=l[fs:end];risk=abs(e-s)
  for r in RS:
   tp=e+r*risk if b else e-r*risk
   th=(ph>=tp) if b else (pl<=tp); sh=(pl<=s) if b else (ph>=s)
   ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 10**9;is_=si[0] if si.size else 10**9
   key=f"{r:g}R_"
   if it==is_ and it<10**9:q[key+"amb"]+=1
   elif it<is_:q[key+"win"]+=1
   elif is_<it:q[key+"loss"]+=1
 return out

ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--out",default="psar_replay_results.json");a=ap.parse_args()
files=glob.glob(a.data+"/**/*.csv.gz",recursive=True);assert files
agg={};errors=[]
for z,p in enumerate(files,1):
 try:t,o,h,l,c=load(p)
 except Exception as e:errors.append([p,str(e)]);continue
 for tf,m in TFS.items():
  for atr_mult in ATR_MULTS:
   rr=test(t,o,h,l,c,m,atr_mult);k=f"{tf}|ATR{atr_mult:g}"
   for side,v in rr.items():
    q=agg.setdefault(k,{}).setdefault(side,{kk:0 for kk in v})
    for kk,vv in v.items():q[kk]+=vv
 if z%20==0:print(f"progress {z}/{len(files)}",flush=True)
for k in agg:
 for side,q in agg[k].items():
  q["fill_pct"]=round(100*q["fills"]/q["signals"],3) if q["signals"] else 0
  for r in RS:
   pre=f"{r:g}R_";den=q[pre+"win"]+q[pre+"loss"];q[pre+"win_pct_ex_amb"]=round(100*q[pre+"win"]/den,3) if den else None
   den_cons=q[pre+"win"]+q[pre+"loss"]+q[pre+"amb"];q[pre+"win_pct_amb_as_loss"]=round(100*q[pre+"win"]/den_cons,3) if den_cons else None
res={"files":len(files),"load_errors":errors,"note":"ATR14-normalized PSAR entry distance; NumPy 15m chronological replay; reports ambiguous both excluded and conservatively as loss","summary":agg}
open(a.out,"w").write(json.dumps(res,indent=2));print(json.dumps(res,indent=2))
