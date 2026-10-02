import argparse,glob,json,sys,time
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
HORIZONS=(8,12,16,24,36,48)
MIN_RISK=.004; MAX_RISK=.05

def ema(c,n): return pd.Series(c).ewm(span=n,adjust=False,min_periods=n).mean().to_numpy()
def atr(h,l,c,n=14):
 prev=np.r_[np.nan,c[:-1]]
 tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
 return pd.Series(tr).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()

def evaluate(symbol,t,o,h,l,c):
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
  if h4i<50 or not all(np.isfinite(x) for x in (hf[h4i],hs[h4i],ha[h4i])):continue
  if not (hf[h4i]<hs[h4i] and hc[h4i]<hf[h4i]):continue
  start=int(ren[q])
  if start>=len(t):continue
  fill=float(o[start]);risk=SL_ATR*A;sl=fill+risk;tp=fill-TP_R*risk;rp=risk/fill
  if sl<=0 or tp<=0 or rp<MIN_RISK or rp>MAX_RISK:continue
  for hrs in HORIZONS:
   z=base.trade(symbol,t,o,h,l,c,start,"short",sl,tp,int(hrs*4))
   if z is None:continue
   rows.append({
    "config":f"SCH_H{hrs}","hours":hrs,"symbol":symbol,"impulse_time":int(rt[q]),
    "entry_time":int(t[start]),"exit_time":int(t[z["exit_bar"]]),"side":"short",
    "impulse_atr":float(rng/A),"entry":fill,"sl":float(sl),"tp":float(tp),
    "exit":z["exit"],"reason":z["reason"],"hold_min":int((t[z["exit_bar"]]-t[start])//60000),
    "risk_pct":z["risk_pct"],"gross_return":z["gross_return"],"gross_r":z["gross_r"],
    "net20_return":z["net20_return"],"net20_r":z["net20_r"],
    "net40_return":z["net40_return"],"net40_r":z["net40_r"]})
 return rows

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
    rows.extend(evaluate(s,*tuple(x[aa:bb] for x in d)))
  finally:ex.CACHE.clear()
  print(f"PROGRESS {n}/{len(fs)} {s} rows={len(rows)} elapsed_min={(time.time()-t0)/60:.1f}",flush=True)
 pd.DataFrame(rows).to_csv(f"short_continuation_horizon_{a.shard}.csv.gz",index=False,compression="gzip")
 Path(f"short_continuation_horizon_meta_{a.shard}.json").write_text(json.dumps({
  "fixed":{"impulse_atr":IMPULSE_ATR,"body_min":BODY_MIN,"sl_atr":SL_ATR,"tp_r":TP_R},
  "horizons_hours":HORIZONS,
  "integrity":"Canonical 1m chronology; corrected short-return formula. Horizon refinement after cost and regime diagnostics."
 },indent=2))
 print("DONE",len(rows))
if __name__=="__main__":main()
