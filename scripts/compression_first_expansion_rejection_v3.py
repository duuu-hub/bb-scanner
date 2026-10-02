import argparse,glob,json,os,sys,time
from collections import Counter,defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd

import scripts.external_breakout_replay as ex
import scripts.sweep_reclaim_research as base
import scripts.compression_first_expansion_v1 as v1

TRAIN_START=pd.Timestamp("2021-01-01T00:00:00Z").value//10**6
TRAIN_END=pd.Timestamp("2025-01-01T00:00:00Z").value//10**6

BOX_N=16
ATR_FAST=16
ATR_SLOW=96
COMP_MAX=.65
EXP_MIN=2.0
BODY_MIN=.60

RETRACE=(.25,.50,1.00)
RETEST_TTL=(4,8)
CONFIRM_MODES=("SIGNAL_CLOSE","RETEST_LOW")
CONFIRM_TTL=(2,4)
SL_ATR=(1.0,1.5)
TP_R=(1.5,2.5)
HOLD_BARS=32
MIN_RISK=.004
MAX_RISK=.05

CONFIGS=[]
for rf in RETRACE:
 for rttl in RETEST_TTL:
  for mode in CONFIRM_MODES:
   for cttl in CONFIRM_TTL:
    for sl in SL_ATR:
     for rr in TP_R:
      CONFIGS.append({
       "name":f"RT{int(rf*100):03d}_R{rttl:02d}_{'SC' if mode=='SIGNAL_CLOSE' else 'RL'}_C{cttl:02d}_SL{int(sl*10):02d}_R{int(rr*10):02d}",
       "retrace_fraction":rf,"retest_ttl_bars":rttl,"confirm_mode":mode,
       "confirm_ttl_bars":cttl,"sl_atr96":sl,"tp_r":rr
      })

def first_retest_bar(h,start,level,max_bars):
 end=min(start+max_bars,len(h))
 for j in range(start,end):
  if h[j]>=level:return j
 return None

def first_confirm_bar(c,l,retest_bar,signal_close,mode,max_bars):
 threshold=float(signal_close) if mode=="SIGNAL_CLOSE" else float(l[retest_bar])
 end=min(retest_bar+max_bars,len(c))
 for j in range(retest_bar,end):
  if c[j]<threshold:return j
 return None

def resolve_entry_trade(symbol,t,o,h,l,c,entry_bar,a96,sl_mult,tp_r):
 fill=float(o[entry_bar]);risk=float(sl_mult*a96)
 if not np.isfinite(risk) or risk<=0:return {"status":"bad_risk"}
 sl=fill+risk;tp=fill-tp_r*risk
 if tp<=0 or sl<=0:return {"status":"bad_risk"}
 rp=risk/fill
 if rp<MIN_RISK or rp>MAX_RISK:return {"status":"risk_filter"}
 z=base.trade(symbol,t,o,h,l,c,entry_bar,"short",sl,tp,HOLD_BARS)
 if z is None:return {"status":"canonical_excluded"}
 z=dict(z);z["status"]="resolved";z["sl"]=sl;z["tp"]=tp
 return z

def signal_rows(symbol,t,o,h,l,c,cnt,ccfg):
 if len(t)<ATR_SLOW+BOX_N+16:return []
 a16=v1.atr(h,l,c,ATR_FAST);a96=v1.atr(h,l,c,ATR_SLOW)
 rows=[]
 for i in range(max(ATR_SLOW,BOX_N+1),len(t)-2):
  first_live=i+1
  if int(t[first_live])<TRAIN_START or int(t[first_live])>=TRAIN_END:continue
  if not (np.isfinite(a16[i-1]) and np.isfinite(a96[i-1])):continue
  af=float(a16[i-1]);aslow=float(a96[i-1])
  if af<=0 or aslow<=0:continue
  comp=af/aslow
  if comp>COMP_MAX:continue

  ph=float(np.max(h[i-BOX_N:i]));pl=float(np.min(l[i-BOX_N:i]))
  prev_ph=float(np.max(h[i-1-BOX_N:i-1]));prev_pl=float(np.min(l[i-1-BOX_N:i-1]))
  if not (prev_pl<=float(c[i-1])<=prev_ph):continue

  rng=float(h[i]-l[i]);body=abs(float(c[i]-o[i]))
  if rng<=0 or body/rng<BODY_MIN:continue
  exp=float(rng/af)
  if exp<EXP_MIN or not (float(c[i])<pl):continue

  penetration=float(pl-c[i])
  if penetration<=0:continue
  cnt["raw_short_signals"]+=1
  sig_close=float(c[i])

  for rf in RETRACE:
   level=sig_close+rf*penetration
   rb=first_retest_bar(h,first_live,level,max(RETEST_TTL))
   roff=None if rb is None else int(rb-first_live+1)

   for rttl in RETEST_TTL:
    retest_ok=(rb is not None and roff<=rttl)
    for mode in CONFIRM_MODES:
     for cttl in CONFIRM_TTL:
      cb=first_confirm_bar(c,l,rb,sig_close,mode,cttl) if retest_ok else None
      entry_bar=None if cb is None else cb+1

      for slm in SL_ATR:
       for rr in TP_R:
        name=f"RT{int(rf*100):03d}_R{rttl:02d}_{'SC' if mode=='SIGNAL_CLOSE' else 'RL'}_C{cttl:02d}_SL{int(slm*10):02d}_R{int(rr*10):02d}"
        ccfg[name]["signals"]+=1
        if not retest_ok:
         ccfg[name]["no_retest"]+=1;continue
        ccfg[name]["retest_touched"]+=1
        if cb is None or entry_bar>=len(t):
         ccfg[name]["no_confirmation"]+=1;continue
        ccfg[name]["confirmed"]+=1
        if int(t[entry_bar])>=TRAIN_END:
         ccfg[name]["split_boundary_excluded"]+=1;continue

        z=resolve_entry_trade(symbol,t,o,h,l,c,entry_bar,aslow,slm,rr)
        if z["status"]!="resolved":
         ccfg[name][z["status"]]+=1;continue
        exit_ts=int(t[z["exit_bar"]])
        if exit_ts>=TRAIN_END:
         ccfg[name]["split_boundary_excluded"]+=1;continue
        ccfg[name]["resolved"]+=1
        rows.append({
         "config":name,"symbol":symbol,"side":"short",
         "signal_time":int(t[i]),"retest_time":int(t[rb]),"confirm_time":int(t[cb]),
         "entry_time":int(t[entry_bar]),"exit_time":exit_ts,
         "retrace_fraction":rf,"retest_ttl_bars":rttl,"retest_delay_bars":roff,
         "confirm_mode":mode,"confirm_ttl_bars":cttl,"confirm_delay_bars":int(cb-rb+1),
         "retest_level":float(level),"signal_close":sig_close,
         "compression_ratio":comp,"compression_strength":1.0-comp,
         "expansion_strength":exp,"body_ratio":body/rng,
         "box_high":ph,"box_low":pl,
         "entry":float(o[entry_bar]),"sl":z["sl"],"tp":z["tp"],
         "exit":z["exit"],"reason":z["reason"],
         "hold_min":int((exit_ts-int(t[entry_bar]))//60000),
         "risk_pct":z["risk_pct"],"gross_return":z["gross_return"],"gross_r":z["gross_r"],
         "net20_return":z["net20_return"],"net20_r":z["net20_r"],
         "net40_return":z["net40_return"],"net40_r":z["net40_r"]
        })
 return rows

def main():
 ap=argparse.ArgumentParser()
 ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1)
 a=ap.parse_args()
 if a.shards<1 or a.shard<0 or a.shard>=a.shards:raise RuntimeError("bad shard")
 allf=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True))
 if not allf:raise RuntimeError("no data files")
 seen=set();uniq=[]
 for p in allf:
  s=base.sym(p)
  if not s or s in seen:continue
  seen.add(s);uniq.append(p)
 fs=[p for j,p in enumerate(uniq) if j%a.shards==a.shard]
 if not fs:raise RuntimeError("empty shard")

 rows=[];cnt=Counter();ccfg=defaultdict(Counter);t0=time.time()
 print(f"RUN_START shard={a.shard}/{a.shards} symbols={len(fs)} configs={len(CONFIGS)}",flush=True)
 for n,p in enumerate(fs,1):
  s=base.sym(p)
  try:d=ex.load(p)
  except Exception as e:
   cnt["load_error"]+=1;print(f"LOAD_ERROR {s} {type(e).__name__}:{e}",flush=True);continue
  try:
   for aa,bb in ex.segments(d[0]):
    if bb-aa<ATR_SLOW+BOX_N+16:continue
    rows.extend(signal_rows(s,*tuple(x[aa:bb] for x in d),cnt,ccfg))
  finally:ex.CACHE.clear()
  if n%10==0 or n==len(fs):
   print(f"PROGRESS {n}/{len(fs)} {s} rows={len(rows)} raw_signals={cnt['raw_short_signals']} elapsed_min={(time.time()-t0)/60:.1f}",flush=True)

 cols=["config","symbol","side","signal_time","retest_time","confirm_time","entry_time","exit_time",
 "retrace_fraction","retest_ttl_bars","retest_delay_bars","confirm_mode","confirm_ttl_bars","confirm_delay_bars",
 "retest_level","signal_close","compression_ratio","compression_strength","expansion_strength","body_ratio",
 "box_high","box_low","entry","sl","tp","exit","reason","hold_min","risk_pct","gross_return","gross_r",
 "net20_return","net20_r","net40_return","net40_r"]
 pd.DataFrame(rows,columns=cols).to_csv(f"compression_rejection_v3_{a.shard}.csv.gz",index=False,compression="gzip")
 meta={"experiment_id":"compression_first_expansion_rejection_v3_train","classification":"EXPLORATORY",
 "repo":"duuu-hub/bb-scanner","branch":os.environ.get("GITHUB_REF_NAME","research-rank5-binance-15m-5y"),
 "commit_sha":os.environ.get("GITHUB_SHA","local"),"source_data_run":36095439671,
 "train_start":"2021-01-01T00:00:00Z","train_end_exclusive":"2025-01-01T00:00:00Z",
 "signal":{"side":"SHORT","compression_max":COMP_MAX,"expansion_min":EXP_MIN,"box_n":BOX_N,"atr_fast":ATR_FAST,"atr_slow":ATR_SLOW,"body_min":BODY_MIN},
 "retest":{"retrace_fraction_grid":RETRACE,"ttl_bars_grid":RETEST_TTL},
 "confirmation":{"modes":CONFIRM_MODES,"ttl_bars_grid":CONFIRM_TTL,"entry":"next 15m open after confirmation close"},
 "exit":{"sl_atr96_grid":SL_ATR,"tp_r_grid":TP_R,"hold_bars":HOLD_BARS,"risk_pct_bounds":[MIN_RISK,MAX_RISK]},
 "costs_round_trip_bp":[20,40],"funding":"excluded",
 "canonical_engine":"scripts/sweep_reclaim_research.py::trade",
 "global_counters":dict(cnt),"config_counters":{k:dict(v) for k,v in ccfg.items()},
 "rows":len(rows),"symbols":len(fs)}
 Path(f"compression_rejection_v3_meta_{a.shard}.json").write_text(json.dumps(meta,indent=2)+"\n")
 print("DONE",len(rows),dict(cnt),flush=True)

if __name__=="__main__":main()
