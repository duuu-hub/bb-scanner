import argparse,glob,json,os
import pandas as pd,numpy as np

ENTRY_ATR=(0.,.25,.5,1.,2.,3.,4.,5.,6.,8.,10.)
SL_BUFFER_ATR=(.1,.2,.3,.5,.75,1.,1.5,2.)
RS=(.5,1.,1.5,2.,2.5,3.,4.,5.,6.,7.,8.,9.,10.,11.,12.,13.,14.,15.,16.,17.,18.,19.,20.)
M=16

def load(p):
 d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]).sort_values("open_time")
 return tuple(d[x].to_numpy(np.int64 if x=="open_time" else float) for x in ["open_time","open","high","low","close"])

def resample(t,o,h,l,c,m=16):
 b=t//(900000*m);q=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(t)]
 st=q[:-1];en=q[1:];g=(en-st)==m;st=st[g];en=en[g]
 return t[st],o[st],np.maximum.reduceat(h,st),np.minimum.reduceat(l,st),c[en-1]

def psar(h,l,af0=.02,step=.02,afmax=.2):
 n=len(h);out=np.full(n,np.nan);bull=np.ones(n,bool);sar=l[0];trend=True;ep=h[1];af=af0;out[1]=sar
 for i in range(2,n):
  z=sar+af*(ep-sar);z=min(z,l[i-1],l[i-2]) if trend else max(z,h[i-1],h[i-2])
  out[i]=z;bull[i]=trend
  if trend:
   if l[i]<z:trend=False;sar=ep;ep=l[i];af=af0
   else:sar=z;ep,af=(h[i],min(af+step,afmax)) if h[i]>ep else (ep,af)
  else:
   if h[i]>z:trend=True;sar=ep;ep=h[i];af=af0
   else:sar=z;ep,af=(l[i],min(af+step,afmax)) if l[i]<ep else (ep,af)
 return out,bull

def ev(p):
 t,o,h,l,c=load(p);rt,ro,rh,rl,rc=resample(t,o,h,l,c);sar,bull=psar(rh,rl)
 prev=np.r_[np.nan,rc[:-1]]
 tr=np.maximum(rh-rl,np.maximum(abs(rh-prev),abs(rl-prev)))
 ac=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()
 ao=np.r_[np.nan,ac[:-1]]
 pos=np.searchsorted(t,rt)
 out={}
 for i in range(15,len(rt)-1):
  if not np.isfinite(sar[i]) or not np.isfinite(ao[i]) or ao[i]<=0: continue
  b=bool(bull[i])
  # 4H bar i must fully close before the order exists. First eligible 15m bar is next 4H bar.
  order_start=pos[i]+M
  if order_start>=len(t): continue
  for em in ENTRY_ATR:
   trigger=sar[i]+(em*ao[i] if b else -em*ao[i])
   # Pure maker-limit model: no retroactive/open-price fill. Wait from order_start until trigger is touched.
   hit=np.flatnonzero((l[order_start:]<=trigger)&(h[order_start:]>=trigger))
   if not hit.size: continue
   fs=order_start+int(hit[0]);fill=trigger
   for sb in SL_BUFFER_ATR:
    sl=sar[i]-(sb*ao[i] if b else -sb*ao[i])
    risk=abs(fill-sl)
    if risk<=1e-12*max(1.,abs(fill),abs(sl)): continue
    ph=h[fs:];pl=l[fs:]
    for r in RS:
     tp=fill+(r*risk if b else -r*risk)
     ti=np.flatnonzero((ph>=tp) if b else (pl<=tp))
     si=np.flatnonzero((pl<=sl) if b else (ph>=sl))
     it=ti[0] if ti.size else 10**18; ss=si[0] if si.size else 10**18
     k=f"E{em:g}|SB{sb:g}|R{r:g}|{'LONG' if b else 'SHORT'}"
     q=out.setdefault(k,dict(fills=0,win=0,loss=0,same_bar_loss=0,unresolved_eod=0,pnl_price=0.))
     q["fills"]+=1
     # Conservative chronology: if TP and SL first occur on same 15m bar, count as SL.
     if ss<=it and ss<10**18:
      q["loss"]+=1
      if ss==it:q["same_bar_loss"]+=1
      q["pnl_price"]+=(sl-fill if b else fill-sl)
     elif it<10**18:
      q["win"]+=1
      q["pnl_price"]+=(tp-fill if b else fill-tp)
     else:
      # Only possible at dataset end; do not pretend it is flat PnL.
      q["unresolved_eod"]+=1
 return out

ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,required=True);ap.add_argument("--shards",type=int,required=True);a=ap.parse_args()
files=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));files=[p for n,p in enumerate(files) if n%a.shards==a.shard];agg={}
for n,p in enumerate(files,1):
 try:x=ev(p)
 except Exception as e:print("FAIL",p,e,flush=True);continue
 for k,v in x.items():
  q=agg.setdefault(k,{kk:0 for kk in v})
  for kk,vv in v.items():q[kk]+=vv
 print(f"shard {a.shard}: {n}/{len(files)} {os.path.basename(p)}",flush=True)
open(f"psar_fixed_shard_{a.shard}.json","w").write(json.dumps({"shard":a.shard,"files":len(files),"summary":agg}))
