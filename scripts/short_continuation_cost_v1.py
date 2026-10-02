import argparse,glob,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.external_breakout_replay as ex
import scripts.sweep_reclaim_research as base
import scripts.impulse_pullback_v1 as ip

BAR=ip.BAR; HOUR=ip.HOUR; UNIVERSE=ip.UNIVERSE
IMPULSE_ATR=2.0
BODY_MIN=.60
EMA_FAST=20
EMA_SLOW=50
MIN_CONFIRM_BODY_ATR=.12
CONFIRM_WINDOW=8
MAX_HOLD_BARS=24
MIN_RISK=.004
MAX_RISK=.050

CONFIGS=tuple(
 {"name":f"SC_{mode}_SL{str(sl).replace('.','')}_R{str(r).replace('.','')}",
  "mode":mode,"sl_atr":sl,"tp_r":r}
 for mode in ("IMM","CONF")
 for sl in (1.0,1.5,2.0)
 for r in (1.5,2.0,2.5,3.0)
)

def ema(c,n): return pd.Series(c).ewm(span=n,adjust=False,min_periods=n).mean().to_numpy()
def atr(h,l,c,n=14):
 prev=np.r_[np.nan,c[:-1]]
 tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
 return pd.Series(tr).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()

def first_confirm(t,o,h,l,c,s0,end,A):
 for k in range(s0,end):
  if c[k] < o[k] and (o[k]-c[k]) >= MIN_CONFIRM_BODY_ATR*A and c[k] < l[k-1]:
   return k
 return None

def evaluate(symbol,t,o,h,l,c):
 rt,ro,rh,rl,rc,rst,ren=ip.rs(t,o,h,l,c,4)
 if len(rt)<80:return []
 ra=atr(rh,rl,rc,14)
 ht,ho,hh,hl,hc,hst,hen=ip.rs(t,o,h,l,c,16)
 if len(ht)<60:return []
 hf=ema(hc,EMA_FAST);hs=ema(hc,EMA_SLOW)
 hclose=ht+4*HOUR
 rows=[]
 for q in range(60,len(rt)-3):
  if not np.isfinite(ra[q]) or ra[q]<=0:continue
  A=float(ra[q]);rng=float(rh[q]-rl[q]);body=abs(float(rc[q]-ro[q]))
  if rng<=0 or rng<IMPULSE_ATR*A or body/rng<BODY_MIN:continue
  # short only
  if not (rc[q] < ro[q]):continue
  h4i=np.searchsorted(hclose,rt[q],side="right")-1
  if h4i<50 or not np.isfinite(hf[h4i]) or not np.isfinite(hs[h4i]):continue
  if not (hf[h4i] < hs[h4i] and hc[h4i] < hf[h4i]):continue

  s0=int(ren[q])
  if s0>=len(t)-1:continue
  end=min(s0+CONFIRM_WINDOW,len(t)-1)
  ck=first_confirm(t,o,h,l,c,s0,end,A)
  starts={"IMM":s0}
  if ck is not None and ck+1<len(t):starts["CONF"]=ck+1

  for cfg in CONFIGS:
   mode=cfg["mode"]
   if mode not in starts:continue
   start=starts[mode]
   fill=float(o[start]);risk=cfg["sl_atr"]*A
   sl=fill+risk;tp=fill-cfg["tp_r"]*risk
   rp=risk/fill
   if sl<=0 or tp<=0 or rp<MIN_RISK or rp>MAX_RISK:continue
   z=base.trade(symbol,t,o,h,l,c,start,"short",sl,tp,MAX_HOLD_BARS)
   if z is None:continue
   rows.append({
    "config":cfg["name"],"mode":mode,"symbol":symbol,"impulse_time":int(rt[q]),
    "entry_time":int(t[start]),"exit_time":int(t[z["exit_bar"]]),"side":"short",
    "impulse_atr":float(rng/A),"entry":fill,"sl":float(sl),"tp":float(tp),
    "exit":z["exit"],"reason":z["reason"],"hold_min":int((t[z["exit_bar"]]-t[start])//60000),
    "risk_pct":z["risk_pct"],"gross_return":z["gross_return"],"gross_r":z["gross_r"],
    "net20_return":z["net20_return"],"net20_r":z["net20_r"],
    "net40_return":z["net40_return"],"net40_r":z["net40_r"]
   })
 return rows

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
 pd.DataFrame(rows).to_csv(f"short_continuation_cost_{a.shard}.csv.gz",index=False,compression="gzip")
 Path(f"short_continuation_cost_meta_{a.shard}.json").write_text(json.dumps({
   "configs":CONFIGS,"universe":sorted(UNIVERSE),
   "fixed":{"impulse_atr":IMPULSE_ATR,"body_min":BODY_MIN,"ema_fast":EMA_FAST,"ema_slow":EMA_SLOW,
            "confirm_body_atr":MIN_CONFIRM_BODY_ATR,"confirm_window_bars":CONFIRM_WINDOW,
            "max_hold_bars":MAX_HOLD_BARS},
   "integrity":"Short-only cost-resistance study. Created after entry audit. Uses corrected canonical short-return formula and canonical 1m collision resolver."
 },indent=2))
 print("DONE",len(rows))
if __name__=="__main__":main()
