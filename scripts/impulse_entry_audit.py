import argparse,glob,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.external_breakout_replay as ex
import scripts.sweep_reclaim_research as base
import scripts.impulse_pullback_v1 as old

BAR=old.BAR;HOUR=old.HOUR;UNIVERSE=old.UNIVERSE
IMPULSE_ATR=2.0;BODY_MIN=.60;TP_R=2.0
EMA_FAST=20;EMA_SLOW=50;MAX_HOLD_BARS=24
PULL_MIN=.30;PULL_MAX=.65;MAX_PULL_BARS=8;MIN_CONFIRM_BODY_ATR=.12
COMMON_SL_ATR=1.0
STRUCT_BUFFER_ATR=.10
MIN_RISK=.004;MAX_RISK=.030
MODES=("IMMEDIATE_ATR","CONFIRM_ATR","PULLBACK_ATR","PULLBACK_STRUCT")

def ema(c,n):return pd.Series(c).ewm(span=n,adjust=False,min_periods=n).mean().to_numpy()
def atr(h,l,c,n=14):
 prev=np.r_[np.nan,c[:-1]]
 tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
 return pd.Series(tr).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()

def find_pullback_trigger(side,s0,end,imp_high,imp_low,imp_range,A,o,h,l,c):
 pull_ext=None;pull_depth=np.nan
 for k in range(s0,end):
  if side=="long":
   depth=(imp_high-float(l[k]))/imp_range
   if depth>PULL_MAX:return None,None,np.nan,True
   if depth<PULL_MIN:continue
   pull_ext=float(l[k]) if pull_ext is None else min(pull_ext,float(l[k]))
   ok=(c[k]>o[k] and (c[k]-o[k])>=MIN_CONFIRM_BODY_ATR*A and c[k]>h[k-1])
  else:
   depth=(float(h[k])-imp_low)/imp_range
   if depth>PULL_MAX:return None,None,np.nan,True
   if depth<PULL_MIN:continue
   pull_ext=float(h[k]) if pull_ext is None else max(pull_ext,float(h[k]))
   ok=(c[k]<o[k] and (o[k]-c[k])>=MIN_CONFIRM_BODY_ATR*A and c[k]<l[k-1])
  if ok:return k,pull_ext,float(depth),False
 return None,pull_ext,pull_depth,False

def enter_trade(symbol,t,o,h,l,c,start,side,A,sl_mode,pull_ext=None):
 fill=float(o[start])
 if sl_mode=="atr":
  sl=fill-COMMON_SL_ATR*A if side=="long" else fill+COMMON_SL_ATR*A
 else:
  if pull_ext is None:return None
  sl=pull_ext-STRUCT_BUFFER_ATR*A if side=="long" else pull_ext+STRUCT_BUFFER_ATR*A
 rp=abs(fill-sl)/fill
 if sl<=0 or rp<MIN_RISK or rp>MAX_RISK:return None
 tp=fill+TP_R*abs(fill-sl) if side=="long" else fill-TP_R*abs(fill-sl)
 if tp<=0:return None
 return base.trade(symbol,t,o,h,l,c,start,side,sl,tp,MAX_HOLD_BARS),sl,tp

def eval_symbol(symbol,t,o,h,l,c):
 rt,ro,rh,rl,rc,rst,ren=old.rs(t,o,h,l,c,4)
 if len(rt)<80:return []
 ra=atr(rh,rl,rc,14)
 ht,ho,hh,hl,hc,hst,hen=old.rs(t,o,h,l,c,16)
 if len(ht)<60:return []
 hf=ema(hc,EMA_FAST);hs=ema(hc,EMA_SLOW)
 hclose=ht+4*HOUR
 out=[]
 for q in range(60,len(rt)-3):
  if not np.isfinite(ra[q]) or ra[q]<=0:continue
  A=float(ra[q]);rng=float(rh[q]-rl[q]);body=abs(float(rc[q]-ro[q]))
  if rng<=0 or rng<IMPULSE_ATR*A or body/rng<BODY_MIN:continue
  side="long" if rc[q]>ro[q] else ("short" if rc[q]<ro[q] else None)
  if side is None:continue
  h4i=np.searchsorted(hclose,rt[q],side="right")-1
  if h4i<50 or not np.isfinite(hf[h4i]) or not np.isfinite(hs[h4i]):continue
  if side=="long":
   if not (hf[h4i]>hs[h4i] and hc[h4i]>hf[h4i]):continue
  else:
   if not (hf[h4i]<hs[h4i] and hc[h4i]<hf[h4i]):continue
  s0=int(ren[q])
  if s0>=len(t)-1:continue
  imp_low=float(rl[q]);imp_high=float(rh[q]);imp_range=imp_high-imp_low
  if imp_range<=0:continue

  candidates=[]
  # A. Immediate: next 15m open after the H1 impulse closes.
  candidates.append(("IMMEDIATE_ATR",s0,"atr",None,s0-1,np.nan))

  # B. No pullback requirement: wait only for first same-direction 15m continuation confirmation.
  end=min(s0+MAX_PULL_BARS,len(t)-1)
  for k in range(s0,end):
   if side=="long":
    ok=(c[k]>o[k] and (c[k]-o[k])>=MIN_CONFIRM_BODY_ATR*A and c[k]>h[k-1])
   else:
    ok=(c[k]<o[k] and (o[k]-c[k])>=MIN_CONFIRM_BODY_ATR*A and c[k]<l[k-1])
   if ok:
    candidates.append(("CONFIRM_ATR",k+1,"atr",None,k,np.nan));break

  # C/D. Corrected pullback: once retracement exceeds 65%, this impulse is INVALID forever.
  trigger,pull_ext,pull_depth,invalid=find_pullback_trigger(
   side,s0,end,imp_high,imp_low,imp_range,A,o,h,l,c)
  if not invalid and trigger is not None:
   candidates.append(("PULLBACK_ATR",trigger+1,"atr",pull_ext,trigger,pull_depth))
   candidates.append(("PULLBACK_STRUCT",trigger+1,"struct",pull_ext,trigger,pull_depth))

  for mode,start,slmode,pext,confirm,pdepth in candidates:
   if start>=len(t):continue
   ret=enter_trade(symbol,t,o,h,l,c,start,side,A,slmode,pext)
   if ret is None:continue
   z,sl,tp=ret
   if z is None:continue
   out.append({"mode":mode,"config":f"IPC2_{mode}","symbol":symbol,"impulse_time":int(rt[q]),
    "entry_time":int(t[start]),"exit_time":int(t[z["exit_bar"]]),"side":side,
    "impulse_atr":float(rng/A),"pull_depth":pdepth,"entry":float(o[start]),"sl":float(sl),"tp":float(tp),
    "exit":z["exit"],"reason":z["reason"],"hold_min":int((t[z["exit_bar"]]-t[start])//60000),
    "risk_pct":z["risk_pct"],"gross_return":z["gross_return"],"gross_r":z["gross_r"],
    "net20_return":z["net20_return"],"net20_r":z["net20_r"],"net40_return":z["net40_return"],"net40_r":z["net40_r"]})
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);a=ap.parse_args()
 fs=[p for p in sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True)) if base.sym(p) in UNIVERSE]
 fs=[p for i,p in enumerate(fs) if i%a.shards==a.shard]
 if not fs:raise RuntimeError("no universe files")
 rows=[];t0=time.time()
 for n,p in enumerate(fs,1):
  s=base.sym(p);d=ex.load(p)
  try:
   for aa,bb in ex.segments(d[0]):
    if bb-aa<1200:continue
    rows.extend(eval_symbol(s,*tuple(x[aa:bb] for x in d)))
  finally:ex.CACHE.clear()
  print(f"PROGRESS {n}/{len(fs)} {s} rows={len(rows)} elapsed_min={(time.time()-t0)/60:.1f}",flush=True)
 pd.DataFrame(rows).to_csv(f"impulse_entry_audit_{a.shard}.csv.gz",index=False,compression="gzip")
 Path(f"impulse_entry_audit_meta_{a.shard}.json").write_text(json.dumps({
  "fixed":{"impulse_atr":IMPULSE_ATR,"body_min":BODY_MIN,"tp_r":TP_R,"common_sl_atr":COMMON_SL_ATR,
  "pull_min":PULL_MIN,"pull_max":PULL_MAX,"max_pull_bars":MAX_PULL_BARS,"max_hold_bars":MAX_HOLD_BARS},
  "modes":MODES,
  "audit":"Corrects V1 bug: >65% retracement now invalidates pullback setup permanently. Immediate/confirm/pullback ATR use same 1ATR stop to isolate entry selection. Pullback struct preserves original structural-stop concept."
 },indent=2))
 print("DONE",len(rows))
if __name__=="__main__":main()
