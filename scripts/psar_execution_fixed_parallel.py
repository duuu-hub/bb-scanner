import argparse,glob,json,os
import pandas as pd,numpy as np
ENTRY_ATR=(0.,.25,.5,1.,2.,3.,4.,5.,6.,8.,10.); SL_BUFFER_ATR=(0.,.1,.2,.3,.5,.75,1.,1.5,2.); RS=(.5,1.,1.5,2.,2.5,3.,4.,5.,6.,8.,10.); M=16;HORIZON=12
def load(p):
 d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]).sort_values("open_time"); return tuple(d[x].to_numpy(np.int64 if x=="open_time" else float) for x in ["open_time","open","high","low","close"])
def resample(t,o,h,l,c,m=16):
 b=t//(900000*m);q=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(t)];st=q[:-1];en=q[1:];g=(en-st)==m;st=st[g];en=en[g]; return t[st],o[st],np.maximum.reduceat(h,st),np.minimum.reduceat(l,st),c[en-1]
def psar(h,l,af0=.02,step=.02,afmax=.2):
 n=len(h);out=np.full(n,np.nan);bull=np.ones(n,bool);sar=l[0];trend=True;ep=h[1];af=af0;out[1]=sar
 for i in range(2,n):
  z=sar+af*(ep-sar);z=min(z,l[i-1],l[i-2]) if trend else max(z,h[i-1],h[i-2]);out[i]=z;bull[i]=trend
  if trend:
   if l[i]<z:trend=False;sar=ep;ep=l[i];af=af0
   else:sar=z;ep,af=(h[i],min(af+step,afmax)) if h[i]>ep else (ep,af)
  else:
   if h[i]>z:trend=True;sar=ep;ep=h[i];af=af0
   else:sar=z;ep,af=(l[i],min(af+step,afmax)) if l[i]<ep else (ep,af)
 return out,bull
def ev(p):
 t,o,h,l,c=load(p);rt,ro,rh,rl,rc=resample(t,o,h,l,c);sar,bull=psar(rh,rl);prev=np.r_[np.nan,rc[:-1]]
 tr=np.maximum(rh-rl,np.maximum(abs(rh-prev),abs(rl-prev)));atr=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy();pos=np.searchsorted(t,rt);out={}
 # Signal is known only AFTER 4H bar i closes. Earliest executable time is 4H bar i+1 open.
 for i in range(15,len(rt)-1-HORIZON):
  if not np.isfinite(sar[i]) or not np.isfinite(atr[i]) or atr[i]<=0:continue
  b=bool(bull[i]);a0=atr[i];start=pos[i+1];end=min(start+M*HORIZON,len(t));next_open=ro[i+1]
  for em in ENTRY_ATR:
   trigger=sar[i]+(em*a0 if b else -em*a0)
   crossed=(b and next_open<=trigger) or ((not b) and next_open>=trigger)
   if crossed: fs=start;fill=next_open;mode="TAKER_OPEN"
   else:
    hit=np.flatnonzero((l[start:start+M]<=trigger)&(h[start:start+M]>=trigger))
    if not hit.size:continue
    fs=start+int(hit[0]);fill=trigger;mode="LIMIT_TOUCH"
   for sb in SL_BUFFER_ATR:
    sl=sar[i]-(sb*a0 if b else -sb*a0);risk_actual=abs(fill-sl)
    if risk_actual<=1e-12*max(1.,abs(fill),abs(sl)):continue
    ph=h[fs:end];pl=l[fs:end]
    for r in RS:
     tp=fill+(r*risk_actual if b else -r*risk_actual);ti=np.flatnonzero((ph>=tp) if b else (pl<=tp));si=np.flatnonzero((pl<=sl) if b else (ph>=sl));it=ti[0] if ti.size else 10**9;ss=si[0] if si.size else 10**9
     k=f"E{em:g}|SB{sb:g}|R{r:g}|{'LONG' if b else 'SHORT'}";q=out.setdefault(k,dict(fills=0,taker_open=0,limit_touch=0,win=0,loss=0,amb=0,timeout=0,pnl_price=0.));q["fills"]+=1;q["taker_open" if mode=="TAKER_OPEN" else "limit_touch"]+=1
     if it==ss and it<10**9:q["amb"]+=1
     elif it<ss:q["win"]+=1;q["pnl_price"]+=(tp-fill if b else fill-tp)
     elif ss<it:q["loss"]+=1;q["pnl_price"]+=(sl-fill if b else fill-sl)
     else:q["timeout"]+=1
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
