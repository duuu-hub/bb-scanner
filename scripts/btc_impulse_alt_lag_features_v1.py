import argparse,glob,json,os,sys,time
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.external_breakout_replay as ex

BAR=900000; HOUR=3600000
TRAIN_START=pd.Timestamp("2021-01-01T00:00:00Z").value//10**6
TRAIN_END=pd.Timestamp("2025-01-01T00:00:00Z").value//10**6
BTC_IMPULSE_MIN=1.5
BTC_BODY_MIN=.60
ALT_LAG_FLOOR=-.25
ALT_LAG_MAX=.75
MIN_HISTORY_1H=672

def files_by_symbol(root):
 fs=sorted(glob.glob(root+"/**/*.csv.gz",recursive=True));m={}
 for p in fs:
  try:s=ex.sym(p)
  except Exception:continue
  if s not in m:m[s]=p
 return m

def hourly(t,o,h,l,c):
 out=[]
 for aa,bb in ex.segments(t):
  if bb-aa<80:continue
  rt,ro,rh,rl,rc,st=ex.resample(t[aa:bb],o[aa:bb],h[aa:bb],l[aa:bb],c[aa:bb])
  if not len(rt):continue
  atr,_,_=ex.features(rh,rl,rc)
  out.append((rt,ro,rh,rl,rc,atr))
 return out

def btc_events(p):
 t,o,h,l,c=ex.load(p);ev={}
 for rt,ro,rh,rl,rc,atr in hourly(t,o,h,l,c):
  for i in range(14,len(rt)):
   entry_ts=int(rt[i]+HOUR)
   if entry_ts<TRAIN_START or entry_ts>=TRAIN_END:continue
   a=float(atr[i])
   if not np.isfinite(a) or a<=0:continue
   rng=float(rh[i]-rl[i]);body=float(rc[i]-ro[i])
   if rng<=0 or abs(body)/rng<BTC_BODY_MIN or rng/a<BTC_IMPULSE_MIN or body==0:continue
   sign=1 if body>0 else -1
   norm=sign*body/a
   ev[entry_ts]={"signal_time":int(rt[i]),"side":"long" if sign>0 else "short",
                 "sign":sign,"btc_norm_move":float(norm),"btc_range_atr":float(rng/a),
                 "btc_body_ratio":float(abs(body)/rng)}
 return ev

def candidates(symbol,p,events):
 t,o,h,l,c=ex.load(p);rows=[]
 for rt,ro,rh,rl,rc,atr in hourly(t,o,h,l,c):
  for i in range(MIN_HISTORY_1H,len(rt)):
   entry_ts=int(rt[i]+HOUR)
   e=events.get(entry_ts)
   if e is None:continue
   a=float(atr[i])
   if not np.isfinite(a) or a<=0:continue
   body=float(rc[i]-ro[i]);alt_norm=e["sign"]*body/a
   if alt_norm<ALT_LAG_FLOOR or alt_norm>ALT_LAG_MAX:continue
   # The entry must exist exactly as the next 15m open.
   j=np.searchsorted(t,entry_ts)
   if j>=len(t) or int(t[j])!=entry_ts:continue
   rows.append({
    "symbol":symbol,"signal_time":e["signal_time"],"entry_time":entry_ts,"side":e["side"],
    "btc_norm_move":e["btc_norm_move"],"btc_range_atr":e["btc_range_atr"],
    "btc_body_ratio":e["btc_body_ratio"],"alt_norm_move":float(alt_norm),
    "lag_gap":float(e["btc_norm_move"]-alt_norm),"alt_atr1h":a,
    "entry_open":float(o[j])
   })
 return rows

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1)
 a=ap.parse_args()
 if a.shards<1 or a.shard<0 or a.shard>=a.shards:raise RuntimeError("bad shard")
 fm=files_by_symbol(a.data)
 if "BTCUSDT" not in fm:raise RuntimeError("BTCUSDT missing")
 events=btc_events(fm["BTCUSDT"])
 syms=[s for s in sorted(fm) if s!="BTCUSDT"]
 syms=[s for j,s in enumerate(syms) if j%a.shards==a.shard]
 if not syms:raise RuntimeError("empty shard")
 rows=[];cnt=Counter();t0=time.time()
 print(f"RUN_START shard={a.shard}/{a.shards} symbols={len(syms)} btc_events={len(events)}",flush=True)
 for n,s in enumerate(syms,1):
  try:rows.extend(candidates(s,fm[s],events))
  except Exception as e:
   cnt["symbol_error"]+=1;print(f"SYMBOL_ERROR {s} {type(e).__name__}:{e}",flush=True)
  if n%10==0 or n==len(syms):
   print(f"PROGRESS {n}/{len(syms)} {s} candidates={len(rows)} elapsed_min={(time.time()-t0)/60:.1f}",flush=True)
 cols=["symbol","signal_time","entry_time","side","btc_norm_move","btc_range_atr","btc_body_ratio","alt_norm_move","lag_gap","alt_atr1h","entry_open"]
 pd.DataFrame(rows,columns=cols).to_csv(f"btc_lag_features_{a.shard}.csv.gz",index=False,compression="gzip")
 Path(f"btc_lag_features_meta_{a.shard}.json").write_text(json.dumps({
  "experiment_id":"btc_impulse_alt_lag_catchup_v1_train","classification":"EXPLORATORY",
  "source_data_run":36095439671,"commit_sha":os.environ.get("GITHUB_SHA","local"),
  "shard":a.shard,"shards":a.shards,"symbols":len(syms),"btc_events":len(events),
  "rows":len(rows),"counters":dict(cnt),
  "feature_contract":{"btc_impulse_min_atr":BTC_IMPULSE_MIN,"btc_body_min":BTC_BODY_MIN,
    "alt_lag_floor":ALT_LAG_FLOOR,"alt_lag_max":ALT_LAG_MAX,"min_history_1h":MIN_HISTORY_1H}
 },indent=2)+"\n")
 print("DONE",len(rows),dict(cnt),flush=True)

if __name__=="__main__":main()
