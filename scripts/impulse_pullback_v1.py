import argparse,glob,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.external_breakout_replay as ex
import scripts.sweep_reclaim_research as base

BAR=900000; HOUR=3600000
UNIVERSE=base.UNIVERSE

CONFIGS=tuple(
 {"name":f"IPC_I{str(imp).replace('.','')}_R{str(r).replace('.','')}",
  "impulse_atr":imp,"tp_r":r}
 for imp in (1.5,2.0) for r in (1.5,2.0)
)
PULL_MIN=.30
PULL_MAX=.65
BODY_MIN=.60
EMA_FAST=20
EMA_SLOW=50
MAX_PULL_BARS=8
MAX_HOLD_BARS=24
MIN_RISK=.004
MAX_RISK=.025
SL_BUFFER_ATR=.10
MIN_CONFIRM_BODY_ATR=.12

def rs(t,o,h,l,c,m):
 b=t//(BAR*m);q=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(t)]
 st=q[:-1];en=q[1:];g=(en-st)==m;st=st[g];en=en[g]
 if len(st):
  span=BAR*m;ok=np.array([t[a]%span==0 and np.all(np.diff(t[a:z])==BAR) for a,z in zip(st,en)],bool);st=st[ok];en=en[ok]
 return t[st],o[st],np.array([h[a:z].max() for a,z in zip(st,en)]),np.array([l[a:z].min() for a,z in zip(st,en)]),c[en-1],st,en

def atr(h,l,c,n=14):
 prev=np.r_[np.nan,c[:-1]]
 tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
 return pd.Series(tr).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()

def ema(c,n):
 return pd.Series(c).ewm(span=n,adjust=False,min_periods=n).mean().to_numpy()

def evaluate(symbol,t,o,h,l,c):
 # H1 impulse features
 rt,ro,rh,rl,rc,rst,ren=rs(t,o,h,l,c,4)
 if len(rt)<80:return []
 ra=atr(rh,rl,rc,14)
 # H4 trend features
 ht,ho,hh,hl,hc,hst,hen=rs(t,o,h,l,c,16)
 if len(ht)<60:return []
 hf=ema(hc,EMA_FAST);hs=ema(hc,EMA_SLOW);ha=atr(hh,hl,hc,14)
 hclose=ht+4*HOUR
 out=[];free={x["name"]:-1 for x in CONFIGS}

 for q in range(60,len(rt)-3):
  # impulse is CLOSED H1 q; search pullback after it
  if not np.isfinite(ra[q]) or ra[q]<=0:continue
  A=float(ra[q]);rng=float(rh[q]-rl[q]);body=abs(float(rc[q]-ro[q]))
  if rng<=0:continue
  side="long" if rc[q]>ro[q] else ("short" if rc[q]<ro[q] else None)
  if side is None or body/rng<BODY_MIN:continue

  imp_start=int(rst[q]);imp_end=int(ren[q]-1)
  h4i=np.searchsorted(hclose,rt[q],side="right")-1
  if h4i<50 or not all(np.isfinite(x) for x in (hf[h4i],hs[h4i],ha[h4i])):continue
  if side=="long":
   if not (hf[h4i]>hs[h4i] and hc[h4i]>hf[h4i]):continue
   imp_low=float(rl[q]);imp_high=float(rh[q]);imp_range=imp_high-imp_low
  else:
   if not (hf[h4i]<hs[h4i] and hc[h4i]<hf[h4i]):continue
   imp_low=float(rl[q]);imp_high=float(rh[q]);imp_range=imp_high-imp_low
  if imp_range<=0:continue

  for cfg in CONFIGS:
   name=cfg["name"]
   if imp_start<free[name]:continue
   if rng < cfg["impulse_atr"]*A:continue

   # search first valid pullback + continuation trigger in next 8 x 15m bars
   s0=int(ren[q]);s1=min(s0+MAX_PULL_BARS,len(t)-1)
   best=None
   pull_ext=None
   for k in range(s0,s1):
    if side=="long":
     depth=(imp_high-float(l[k]))/imp_range
     if depth<PULL_MIN or depth>PULL_MAX:continue
     pull_ext=float(l[k]) if pull_ext is None else min(pull_ext,float(l[k]))
     # continuation: bullish 15m body and close above previous 15m high
     if k<=s0:continue
     cbody=float(c[k]-o[k])
     if cbody < MIN_CONFIRM_BODY_ATR*A:continue
     if c[k] <= h[k-1]:continue
     start=k+1
     if start>=len(t):continue
     best=(start,pull_ext,k,depth)
     break
    else:
     depth=(float(h[k])-imp_low)/imp_range
     if depth<PULL_MIN or depth>PULL_MAX:continue
     pull_ext=float(h[k]) if pull_ext is None else max(pull_ext,float(h[k]))
     if k<=s0:continue
     cbody=float(o[k]-c[k])
     if cbody < MIN_CONFIRM_BODY_ATR*A:continue
     if c[k] >= l[k-1]:continue
     start=k+1
     if start>=len(t):continue
     best=(start,pull_ext,k,depth)
     break
   if best is None:continue
   start,pull_ext,confirm_bar,depth=best
   fill=float(o[start])
   sl=(pull_ext-SL_BUFFER_ATR*A) if side=="long" else (pull_ext+SL_BUFFER_ATR*A)
   rp=abs(fill-sl)/fill
   if sl<=0 or rp<MIN_RISK or rp>MAX_RISK:continue
   tp=fill+cfg["tp_r"]*abs(fill-sl) if side=="long" else fill-cfg["tp_r"]*abs(fill-sl)
   if tp<=0:continue
   z=base.trade(symbol,t,o,h,l,c,start,side,sl,tp,MAX_HOLD_BARS)
   if z is None:continue
   free[name]=z["exit_bar"]+1
   out.append({
    "config":name,"symbol":symbol,"signal_time":int(rt[q]),"confirm_time":int(t[confirm_bar]),
    "entry_time":int(t[start]),"exit_time":int(t[z["exit_bar"]]),"side":side,
    "impulse_atr":float(rng/A),"pull_depth":float(depth),"entry":fill,"sl":float(sl),"tp":float(tp),
    "exit":z["exit"],"reason":z["reason"],"hold_min":int((t[z["exit_bar"]]-t[start])//60000),
    "risk_pct":z["risk_pct"],"gross_return":z["gross_return"],"gross_r":z["gross_r"],
    "net20_return":z["net20_return"],"net20_r":z["net20_r"],
    "net40_return":z["net40_return"],"net40_r":z["net40_r"]
   })
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);a=ap.parse_args()
 fs=[p for p in sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True)) if base.sym(p) in UNIVERSE]
 fs=[p for i,p in enumerate(fs) if i%a.shards==a.shard]
 if not fs:raise RuntimeError("no universe files")
 rows=[];t0=time.time()
 print(f"RUN_START shard={a.shard}/{a.shards} files={len(fs)}",flush=True)
 for n,p in enumerate(fs,1):
  s=base.sym(p);d=ex.load(p)
  try:
   for aa,bb in ex.segments(d[0]):
    if bb-aa<1200:continue
    rows.extend(evaluate(s,*tuple(x[aa:bb] for x in d)))
  finally:ex.CACHE.clear()
  print(f"PROGRESS {n}/{len(fs)} {s} rows={len(rows)} elapsed_min={(time.time()-t0)/60:.1f}",flush=True)
 pd.DataFrame(rows).to_csv(f"impulse_pullback_v1_raw_{a.shard}.csv.gz",index=False,compression="gzip")
 Path(f"impulse_pullback_v1_meta_{a.shard}.json").write_text(json.dumps({
   "configs":CONFIGS,"universe":sorted(UNIVERSE),
   "fixed":{"pull_min":PULL_MIN,"pull_max":PULL_MAX,"body_min":BODY_MIN,"ema_fast":EMA_FAST,"ema_slow":EMA_SLOW,
    "max_pull_bars":MAX_PULL_BARS,"max_hold_bars":MAX_HOLD_BARS,"sl_buffer_atr":SL_BUFFER_ATR,
    "min_confirm_body_atr":MIN_CONFIRM_BODY_ATR},
   "integrity":"New family created after Sweep/Reclaim V1/V2 failure. 2025-26 is evaluation, not pristine untouched holdout."
 },indent=2))
 print("DONE",len(rows))
if __name__=="__main__":main()
