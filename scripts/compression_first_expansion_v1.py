import argparse,glob,json,math,os,sys,time
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.external_breakout_replay as ex
import scripts.sweep_reclaim_research as base

BAR=900000
TRAIN_START=pd.Timestamp("2021-01-01T00:00:00Z").value//10**6
TRAIN_END=pd.Timestamp("2025-01-01T00:00:00Z").value//10**6
BOX_N=16
ATR_FAST=16
ATR_SLOW=96
BODY_MIN=.60
HOLD_BARS=32
MIN_RISK=.004
MAX_RISK=.05

COMPRESSION=(.65,.80)
EXPANSION=(1.5,2.0)
SL_ATR=(1.0,1.5)
TP_R=(1.5,2.5)

CONFIGS=[]
for cr in COMPRESSION:
 for er in EXPANSION:
  for sl in SL_ATR:
   for rr in TP_R:
    CONFIGS.append({
      "name":f"C{int(cr*100):02d}_E{int(er*10):02d}_SL{int(sl*10):02d}_R{int(rr*10):02d}",
      "compression_max":cr,"expansion_min":er,"sl_atr96":sl,"tp_r":rr
    })

def atr(h,l,c,n):
 prev=np.r_[np.nan,c[:-1]]
 tr=np.maximum(h-l,np.maximum(np.abs(h-prev),np.abs(l-prev)))
 return pd.Series(tr).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()

def evaluate(symbol,t,o,h,l,c,counters):
 if len(t)<ATR_SLOW+BOX_N+5:return []
 a16=atr(h,l,c,ATR_FAST);a96=atr(h,l,c,ATR_SLOW)
 rows=[]
 for i in range(max(ATR_SLOW,BOX_N+1),len(t)-1):
  entry_ts=int(t[i+1])
  if entry_ts<TRAIN_START or entry_ts>=TRAIN_END:continue
  if not (np.isfinite(a16[i-1]) and np.isfinite(a96[i-1])):continue
  af=float(a16[i-1]);aslow=float(a96[i-1])
  if af<=0 or aslow<=0:continue
  comp=af/aslow

  ph=float(np.max(h[i-BOX_N:i]));pl=float(np.min(l[i-BOX_N:i]))
  prev_ph=float(np.max(h[i-1-BOX_N:i-1]));prev_pl=float(np.min(l[i-1-BOX_N:i-1]))
  if not (prev_pl<=float(c[i-1])<=prev_ph):continue

  rng=float(h[i]-l[i]);body=abs(float(c[i]-o[i]))
  if rng<=0 or body/rng<BODY_MIN:continue
  exp_strength=rng/af
  side=None
  if float(c[i])>ph:side="long"
  elif float(c[i])<pl:side="short"
  else:continue

  start=i+1;fill=float(o[start])
  for cfg in CONFIGS:
   if comp>cfg["compression_max"] or exp_strength<cfg["expansion_min"]:continue
   risk=cfg["sl_atr96"]*aslow
   sl=fill-risk if side=="long" else fill+risk
   if sl<=0:continue
   rp=abs(fill-sl)/fill
   if rp<MIN_RISK or rp>MAX_RISK:continue
   tp=fill+cfg["tp_r"]*risk if side=="long" else fill-cfg["tp_r"]*risk
   if tp<=0:continue

   z=base.trade(symbol,t,o,h,l,c,start,side,sl,tp,HOLD_BARS)
   if z is None:
    counters["canonical_excluded"]+=1
    continue
   exit_ts=int(t[z["exit_bar"]])
   if exit_ts>=TRAIN_END:
    counters["split_boundary_excluded"]+=1
    continue
   rows.append({
    "config":cfg["name"],"symbol":symbol,"side":side,
    "signal_time":int(t[i]),"entry_time":entry_ts,"exit_time":exit_ts,
    "compression_ratio":float(comp),"compression_strength":float(1.0-comp),
    "expansion_strength":float(exp_strength),"body_ratio":float(body/rng),
    "box_high":ph,"box_low":pl,"entry":fill,"sl":float(sl),"tp":float(tp),
    "exit":z["exit"],"reason":z["reason"],
    "hold_min":int((exit_ts-entry_ts)//60000),
    "risk_pct":z["risk_pct"],"gross_return":z["gross_return"],"gross_r":z["gross_r"],
    "net20_return":z["net20_return"],"net20_r":z["net20_r"],
    "net40_return":z["net40_return"],"net40_r":z["net40_r"]
   })
 return rows

def main():
 ap=argparse.ArgumentParser()
 ap.add_argument("--data",default="data")
 ap.add_argument("--shard",type=int,default=0)
 ap.add_argument("--shards",type=int,default=1)
 a=ap.parse_args()

 fs=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True))
 # Deduplicate by canonical symbol name if the artifact extraction created nested duplicates.
 seen=set();uniq=[]
 for p in fs:
  s=base.sym(p)
  if not s or s in seen:continue
  seen.add(s);uniq.append(p)
 fs=[p for j,p in enumerate(uniq) if j%a.shards==a.shard]
 if not fs:raise RuntimeError("no data files")

 rows=[];cnt=Counter();t0=time.time()
 print(f"RUN_START shard={a.shard}/{a.shards} symbols={len(fs)} configs={len(CONFIGS)}",flush=True)
 for n,p in enumerate(fs,1):
  s=base.sym(p)
  try:d=ex.load(p)
  except Exception as e:
   cnt["load_error"]+=1
   print(f"LOAD_ERROR {s} {type(e).__name__}:{e}",flush=True);continue
  try:
   for aa,bb in ex.segments(d[0]):
    if bb-aa<ATR_SLOW+BOX_N+5:continue
    rows.extend(evaluate(s,*tuple(x[aa:bb] for x in d),cnt))
  finally:
   ex.CACHE.clear()
  if n%10==0 or n==len(fs):
   print(f"PROGRESS {n}/{len(fs)} {s} rows={len(rows)} excluded={dict(cnt)} elapsed_min={(time.time()-t0)/60:.1f}",flush=True)

 pd.DataFrame(rows).to_csv(f"compression_first_expansion_v1_{a.shard}.csv.gz",index=False,compression="gzip")
 meta={
  "experiment_id":"compression_first_expansion_v1_train",
  "classification":"EXPLORATORY",
  "repo":"duuu-hub/bb-scanner",
  "branch":os.environ.get("GITHUB_REF_NAME","research-rank5-binance-15m-5y"),
  "commit_sha":os.environ.get("GITHUB_SHA","local"),
  "source_data_run":36095439671,
  "train_start":"2021-01-01T00:00:00Z","train_end_exclusive":"2025-01-01T00:00:00Z",
  "configs":CONFIGS,
  "fixed":{"box_n":BOX_N,"atr_fast":ATR_FAST,"atr_slow":ATR_SLOW,"body_min":BODY_MIN,
           "hold_bars":HOLD_BARS,"hold_hours":8,"risk_pct_bounds":[MIN_RISK,MAX_RISK],
           "entry":"next 15m open","funding":"excluded","costs_round_trip_bp":[20,40]},
  "canonical_engine":"scripts/sweep_reclaim_research.py::trade",
  "counters":dict(cnt),"rows":len(rows),"symbols":len(fs)
 }
 Path(f"compression_first_expansion_v1_meta_{a.shard}.json").write_text(json.dumps(meta,indent=2)+"\n")
 print("DONE",len(rows),dict(cnt),flush=True)

if __name__=="__main__":main()
