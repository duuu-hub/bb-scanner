import argparse,glob,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.external_breakout_replay as ex
import scripts.sweep_reclaim_research as base
import scripts.impulse_pullback_v1 as ip

BAR=ip.BAR; HOUR=ip.HOUR; UNIVERSE=ip.UNIVERSE
IMPULSE_ATR=2.0; BODY_MIN=.60
EMA_FAST=20; EMA_SLOW=50
SL_ATR=2.0; TP_R=3.0
MAX_HOLD_BARS=24
MIN_RISK=.004; MAX_RISK=.05
MIN_BREADTH_N=8

def ema(c,n): return pd.Series(c).ewm(span=n,adjust=False,min_periods=n).mean().to_numpy()
def atr(h,l,c,n=14):
 prev=np.r_[np.nan,c[:-1]]
 tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
 return pd.Series(tr).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()

def build_regime(all_files):
 states={}
 for p in all_files:
  s=base.sym(p)
  if s not in UNIVERSE: continue
  t,o,h,l,c=ex.load(p)
  # Only full contiguous file-level H4 bars are needed for slow regime state.
  ht,ho,hh,hl,hc,hst,hen=ip.rs(t,o,h,l,c,16)
  if len(ht)<60: continue
  hf=ema(hc,EMA_FAST); hs=ema(hc,EMA_SLOW); ha=atr(hh,hl,hc,14)
  hclose=ht+4*HOUR
  valid=np.isfinite(hf)&np.isfinite(hs)&np.isfinite(ha)&(ha>0)
  bear=valid&(hf<hs)&(hc<hf)
  dist=np.where(valid,(hf-hc)/ha,np.nan)
  gap=np.where(valid,(hs-hf)/ha,np.nan)
  states[s]={"close":hclose,"bear":bear,"dist":dist,"gap":gap}
 return states

def regime_at(states,ts):
 n=0;b=0
 for s,x in states.items():
  j=np.searchsorted(x["close"],ts,side="right")-1
  if j<0: continue
  if not np.isfinite(x["dist"][j]) or not np.isfinite(x["gap"][j]): continue
  n+=1;b+=int(bool(x["bear"][j]))
 btc=False
 x=states.get("BTCUSDT")
 if x is not None:
  j=np.searchsorted(x["close"],ts,side="right")-1
  if j>=0: btc=bool(x["bear"][j])
 return (b/n if n>=MIN_BREADTH_N else np.nan),n,btc

def evaluate(symbol,t,o,h,l,c,states):
 rt,ro,rh,rl,rc,rst,ren=ip.rs(t,o,h,l,c,4)
 if len(rt)<80:return []
 ra=atr(rh,rl,rc,14)
 ht,ho,hh,hl,hc,hst,hen=ip.rs(t,o,h,l,c,16)
 if len(ht)<60:return []
 hf=ema(hc,EMA_FAST);hs=ema(hc,EMA_SLOW);ha=atr(hh,hl,hc,14)
 hclose=ht+4*HOUR
 rows=[]
 for q in range(60,len(rt)-3):
  if not np.isfinite(ra[q]) or ra[q]<=0:continue
  A=float(ra[q]);rng=float(rh[q]-rl[q]);body=abs(float(rc[q]-ro[q]))
  if rng<=0 or rng<IMPULSE_ATR*A or body/rng<BODY_MIN:continue
  if not (rc[q]<ro[q]):continue
  h4i=np.searchsorted(hclose,rt[q],side="right")-1
  if h4i<50 or not all(np.isfinite(x) for x in (hf[h4i],hs[h4i],ha[h4i])) or ha[h4i]<=0:continue
  if not (hf[h4i]<hs[h4i] and hc[h4i]<hf[h4i]):continue
  breadth,bn,btc_bear=regime_at(states,int(rt[q]))
  if not np.isfinite(breadth):continue
  s0=int(ren[q])
  if s0>=len(t):continue
  fill=float(o[s0]);risk=SL_ATR*A;sl=fill+risk;tp=fill-TP_R*risk;rp=risk/fill
  if sl<=0 or tp<=0 or rp<MIN_RISK or rp>MAX_RISK:continue
  z=base.trade(symbol,t,o,h,l,c,s0,"short",sl,tp,MAX_HOLD_BARS)
  if z is None:continue
  h4A=float(ha[h4i])
  ema_dist=float((hf[h4i]-hc[h4i])/h4A)
  trend_gap=float((hs[h4i]-hf[h4i])/h4A)
  rows.append({
   "symbol":symbol,"impulse_time":int(rt[q]),"entry_time":int(t[s0]),"exit_time":int(t[z["exit_bar"]]),
   "impulse_atr":float(rng/A),"body_ratio":float(body/rng),"ema_dist":ema_dist,"trend_gap":trend_gap,
   "breadth":float(breadth),"breadth_n":int(bn),"btc_bear":int(btc_bear),
   "entry":fill,"sl":float(sl),"tp":float(tp),"exit":z["exit"],"reason":z["reason"],
   "hold_min":int((t[z["exit_bar"]]-t[s0])//60000),"risk_pct":z["risk_pct"],
   "gross_return":z["gross_return"],"gross_r":z["gross_r"],
   "net20_return":z["net20_return"],"net20_r":z["net20_r"],
   "net40_return":z["net40_return"],"net40_r":z["net40_r"]
  })
 return rows

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);a=ap.parse_args()
 allf=[p for p in sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True)) if base.sym(p) in UNIVERSE]
 if not allf:raise RuntimeError("no universe files")
 states=build_regime(allf)
 if len(states)<MIN_BREADTH_N:raise RuntimeError(f"insufficient regime symbols {len(states)}")
 fs=[p for i,p in enumerate(allf) if i%a.shards==a.shard]
 rows=[];t0=time.time()
 print(f"RUN_START shard={a.shard}/{a.shards} files={len(fs)} regime_symbols={len(states)}",flush=True)
 for n,p in enumerate(fs,1):
  s=base.sym(p);d=ex.load(p)
  try:
   for aa,bb in ex.segments(d[0]):
    if bb-aa<1200:continue
    rows.extend(evaluate(s,*tuple(x[aa:bb] for x in d),states))
  finally:ex.CACHE.clear()
  print(f"PROGRESS {n}/{len(fs)} {s} rows={len(rows)} elapsed_min={(time.time()-t0)/60:.1f}",flush=True)
 pd.DataFrame(rows).to_csv(f"short_continuation_regime_{a.shard}.csv.gz",index=False,compression="gzip")
 Path(f"short_continuation_regime_meta_{a.shard}.json").write_text(json.dumps({
  "fixed":{"impulse_atr":IMPULSE_ATR,"body_min":BODY_MIN,"sl_atr":SL_ATR,"tp_r":TP_R,
           "max_hold_bars":MAX_HOLD_BARS,"min_breadth_n":MIN_BREADTH_N},
  "features":["18-symbol H4 bearish breadth","BTC H4 bear","impulse ATR","H4 EMA distance/ATR","H4 EMA gap/ATR"],
  "integrity":"Regime/ranking refinement after cost V1. 2025-26 is evaluation, not pristine OOS."
 },indent=2))
 print("DONE",len(rows))
if __name__=="__main__":main()
