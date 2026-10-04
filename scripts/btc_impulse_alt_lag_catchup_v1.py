import argparse,glob,json,os,sys,time,re
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd

import scripts.external_breakout_replay as ex
import scripts.sweep_reclaim_research as base

BAR=900000
HOUR=3600000
TRAIN_START=pd.Timestamp("2021-01-01T00:00:00Z").value//10**6
TRAIN_END=pd.Timestamp("2025-01-01T00:00:00Z").value//10**6

BTC_RANGE_ATR_MIN=1.5
BTC_BODY_RANGE_MIN=.60
ALT_NORM_FLOOR=-.25
MIN_ALT_HISTORY=672
LAG_MAX=(.25,.50,.75)
SL_ATR=(1.0,1.5)
TP_R=(1.5,2.5)
HOLD_HOURS=(4,8)
MIN_RISK=.004
MAX_RISK=.05

CONFIGS=[]
for lag in LAG_MAX:
 for sl in SL_ATR:
  for rr in TP_R:
   for hh in HOLD_HOURS:
    CONFIGS.append({
      "name":f"L{int(lag*100):02d}_SL{int(sl*10):02d}_R{int(rr*10):02d}_H{hh:02d}",
      "lag_max":lag,"sl_atr1h":sl,"tp_r":rr,"hold_hours":hh
    })

def rs1h(t,o,h,l,c):
 b=t//HOUR
 q=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(t)]
 st=q[:-1];en=q[1:]
 g=(en-st)==4
 st=st[g];en=en[g]
 if len(st):
  ok=np.array([t[a]%HOUR==0 and np.all(np.diff(t[a:z])==BAR) for a,z in zip(st,en)],bool)
  st=st[ok];en=en[ok]
 return (t[st],o[st],
         np.array([h[a:z].max() for a,z in zip(st,en)],float),
         np.array([l[a:z].min() for a,z in zip(st,en)],float),
         c[en-1],st)

def atr_prior(h,l,c,n=14):
 prev=np.r_[np.nan,c[:-1]]
 tr=np.maximum(h-l,np.maximum(np.abs(h-prev),np.abs(l-prev)))
 return pd.Series(tr).rolling(n,min_periods=n).mean().shift(1).to_numpy(float)

def btc_events(data):
 t,o,h,l,c=data
 rt,ro,rh,rl,rc,st=rs1h(t,o,h,l,c)
 at=atr_prior(rh,rl,rc,14)
 out={}
 for i in range(14,len(rt)):
  entry_ts=int(rt[i]+HOUR)
  if entry_ts<TRAIN_START or entry_ts>=TRAIN_END:continue
  A=float(at[i])
  if not np.isfinite(A) or A<=0:continue
  rng=float(rh[i]-rl[i])
  if rng<=0 or rng/A<BTC_RANGE_ATR_MIN:continue
  body=float(rc[i]-ro[i])
  if body==0 or abs(body)/rng<BTC_BODY_RANGE_MIN:continue
  direction=1 if body>0 else -1
  out[int(rt[i])]={
    "signal_hour":int(rt[i]),"entry_time":entry_ts,
    "direction":direction,"side":"long" if direction>0 else "short",
    "btc_open":float(ro[i]),"btc_close":float(rc[i]),
    "btc_range_atr":float(rng/A),"btc_body_ratio":float(abs(body)/rng),
    "btc_norm_body":float(abs(body)/A)
  }
 return out

def resolved_or_status(symbol,t,o,h,l,c,start,side,sl,tp,maxbars):
 z=base.trade(symbol,t,o,h,l,c,start,side,sl,tp,maxbars)
 if z is None:return {"status":"canonical_excluded"}
 z=dict(z);z["status"]="resolved";return z

def evaluate_alt(symbol,data,events,cnt):
 t,o,h,l,c=data
 rt,ro,rh,rl,rc,st=rs1h(t,o,h,l,c)
 if len(rt)<MIN_ALT_HISTORY+20:return []
 at=atr_prior(rh,rl,rc,14)
 pos={int(x):i for i,x in enumerate(rt)}
 rows=[]
 for sig_ts,ev in events.items():
  ai=pos.get(sig_ts)
  if ai is None or ai<MIN_ALT_HISTORY:continue
  A=float(at[ai])
  if not np.isfinite(A) or A<=0:continue
  alt_body=float(rc[ai]-ro[ai])
  alt_norm=float(ev["direction"]*alt_body/A)
  if alt_norm<ALT_NORM_FLOOR:continue
  eligible=[lag for lag in LAG_MAX if alt_norm<=lag*ev["btc_norm_body"]]
  if not eligible:continue
  lag_gap=float(ev["btc_norm_body"]-alt_norm)
  start=int(np.searchsorted(t,ev["entry_time"]))
  if start>=len(t) or int(t[start])!=ev["entry_time"]:
   cnt["entry_bar_missing"]+=1
   continue
  cnt["eligible_symbol_events"]+=1

  outcomes={}
  for slm in SL_ATR:
   risk=float(slm*A);fill=float(o[start]);rp=risk/fill
   for rr in TP_R:
    for hh in HOLD_HOURS:
     key=(slm,rr,hh)
     if rp<MIN_RISK or rp>MAX_RISK:
      outcomes[key]={"status":"risk_filter","risk_pct":rp}
      continue
     sl=fill-risk if ev["direction"]>0 else fill+risk
     tp=fill+rr*risk if ev["direction"]>0 else fill-rr*risk
     if sl<=0 or tp<=0:
      outcomes[key]={"status":"bad_risk","risk_pct":rp}
      continue
     maxbars=int(hh*4)
     # Fail closed at TRAIN boundary without reading validation outcomes.
     if int(t[start])+maxbars*BAR>=TRAIN_END:
      outcomes[key]={"status":"split_boundary_excluded","risk_pct":rp,
                     "sl":sl,"tp":tp}
      continue
     z=resolved_or_status(symbol,t,o,h,l,c,start,ev["side"],sl,tp,maxbars)
     z["risk_pct"]=rp if z["status"]!="resolved" else z["risk_pct"]
     z["sl"]=sl;z["tp"]=tp
     outcomes[key]=z

  for lag in eligible:
   for slm in SL_ATR:
    for rr in TP_R:
     for hh in HOLD_HOURS:
      name=f"L{int(lag*100):02d}_SL{int(slm*10):02d}_R{int(rr*10):02d}_H{hh:02d}"
      z=outcomes[(slm,rr,hh)]
      status=z["status"];cnt[f"{name}:{status}"]+=1
      row={
       "config":name,"symbol":symbol,"signal_time":sig_ts,
       "entry_time":int(ev["entry_time"]),"side":ev["side"],"direction":ev["direction"],
       "btc_norm_body":ev["btc_norm_body"],"btc_range_atr":ev["btc_range_atr"],
       "btc_body_ratio":ev["btc_body_ratio"],"alt_norm_move":alt_norm,
       "lag_gap":lag_gap,"lag_max":lag,
       "entry":float(o[start]),"risk_pct":float(z.get("risk_pct",np.nan)),
       "status":status,"sl":float(z.get("sl",np.nan)),"tp":float(z.get("tp",np.nan)),
       "exit_time":np.nan,"exit":np.nan,"reason":"",
       "gross_return":np.nan,"gross_r":np.nan,
       "net20_return":np.nan,"net20_r":np.nan,"net40_return":np.nan,"net40_r":np.nan
      }
      if status=="resolved":
       xb=int(z["exit_bar"])
       row.update({
        "exit_time":int(t[xb]),"exit":float(z["exit"]),"reason":z["reason"],
        "gross_return":float(z["gross_return"]),"gross_r":float(z["gross_r"]),
        "net20_return":float(z["net20_return"]),"net20_r":float(z["net20_r"]),
        "net40_return":float(z["net40_return"]),"net40_r":float(z["net40_r"])
       })
      rows.append(row)
 return rows

def main():
 ap=argparse.ArgumentParser()
 ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1)
 a=ap.parse_args()
 if a.shards<1 or a.shard<0 or a.shard>=a.shards:raise RuntimeError("bad shard")
 allf=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True))
 if not allf:raise RuntimeError("no data")
 bysym={}
 for p in allf:
  s=base.sym(p)
  if s and s not in bysym:bysym[s]=p
 if "BTCUSDT" not in bysym:raise RuntimeError("BTCUSDT missing")
 btc=ex.load(bysym["BTCUSDT"])
 events=btc_events(btc)
 if not events:raise RuntimeError("zero BTC events")
 syms=sorted(s for s in bysym if s!="BTCUSDT")
 syms=[s for j,s in enumerate(syms) if j%a.shards==a.shard]
 if not syms:raise RuntimeError("empty shard")
 rows=[];cnt=Counter();t0=time.time()
 print(f"RUN_START shard={a.shard}/{a.shards} symbols={len(syms)} btc_events={len(events)} configs={len(CONFIGS)}",flush=True)
 for n,s in enumerate(syms,1):
  try:d=ex.load(bysym[s])
  except Exception as e:
   cnt["load_error"]+=1;print(f"LOAD_ERROR {s} {type(e).__name__}:{e}",flush=True);continue
  try:rows.extend(evaluate_alt(s,d,events,cnt))
  finally:ex.CACHE.clear()
  if n%10==0 or n==len(syms):
   print(f"PROGRESS {n}/{len(syms)} {s} rows={len(rows)} eligible_events={cnt['eligible_symbol_events']} elapsed_min={(time.time()-t0)/60:.1f}",flush=True)
 pd.DataFrame(rows).to_csv(f"btc_alt_lag_v1_{a.shard}.csv.gz",index=False,compression="gzip")
 meta={
  "experiment_id":"btc_impulse_alt_lag_catchup_v1_train","classification":"EXPLORATORY",
  "repo":"duuu-hub/bb-scanner","branch":os.environ.get("GITHUB_REF_NAME","research-rank5-binance-15m-5y"),
  "commit_sha":os.environ.get("GITHUB_SHA","local"),"source_data_run":36095439671,
  "train_start":"2021-01-01T00:00:00Z","train_end_exclusive":"2025-01-01T00:00:00Z",
  "btc_event_count":len(events),"configs":CONFIGS,
  "fixed":{"btc_range_atr_min":BTC_RANGE_ATR_MIN,"btc_body_range_min":BTC_BODY_RANGE_MIN,
           "alt_norm_floor":ALT_NORM_FLOOR,"min_alt_history_1h_bars":MIN_ALT_HISTORY,
           "entry":"next 15m open after completed BTC 1h impulse",
           "costs_round_trip_bp":[20,40],"funding":"excluded",
           "risk_pct_bounds":[MIN_RISK,MAX_RISK]},
  "counters":dict(cnt),"rows":len(rows),"symbols":len(syms)
 }
 Path(f"btc_alt_lag_v1_meta_{a.shard}.json").write_text(json.dumps(meta,indent=2)+"\n")
 print("DONE",len(rows),dict(cnt),flush=True)

if __name__=="__main__":main()
