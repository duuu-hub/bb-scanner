import argparse,glob,json,math,os,sys,time
from collections import Counter,defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd

import scripts.external_breakout_replay as ex
import scripts.sweep_reclaim_research as base
import scripts.compression_first_expansion_v1 as v1
import scripts.psar_open_canonical_compare as canon

TRAIN_START=pd.Timestamp("2021-01-01T00:00:00Z").value//10**6
TRAIN_END=pd.Timestamp("2025-01-01T00:00:00Z").value//10**6

BOX_N=16
ATR_FAST=16
ATR_SLOW=96
COMP_MAX=.65
EXP_MIN=2.0
BODY_MIN=.60
RETRACE=(.25,.50,1.00)
TTL_BARS=(2,4,8)
SL_ATR=(1.0,1.5)
TP_R=(1.5,2.5)
HOLD_BARS=32
MIN_RISK=.004
MAX_RISK=.05

CONFIGS=[]
for rf in RETRACE:
 for ttl in TTL_BARS:
  for sl in SL_ATR:
   for rr in TP_R:
    CONFIGS.append({
      "name":f"RT{int(rf*100):03d}_T{ttl:02d}_SL{int(sl*10):02d}_R{int(rr*10):02d}",
      "retrace_fraction":rf,"ttl_bars":ttl,"sl_atr96":sl,"tp_r":rr
    })

def first_retest_fill(t,o,h,start,limit,max_ttl):
 stop=min(start+max_ttl,len(t))
 for j in range(start,stop):
  if o[j]>=limit:
   return {"bar":j,"fill":float(o[j]),"is_taker":True}
  if h[j]>=limit:
   return {"bar":j,"fill":float(limit),"is_taker":False}
 return None

def resolve_trade(symbol,t,o,h,l,c,fill_bar,fill,is_taker,a96,sl_mult,tp_r):
 risk=float(sl_mult*a96)
 if not np.isfinite(risk) or risk<=0:return {"status":"bad_risk"}
 sl=float(fill+risk)
 tp=float(fill-tp_r*risk)
 if tp<=0 or sl<=0:return {"status":"bad_risk"}
 rp=risk/fill
 if rp<MIN_RISK or rp>MAX_RISK:return {"status":"risk_filter"}

 j=int(fill_bar);xp=xb=reason=None
 hit_tp=l[j]<=tp;hit_sl=h[j]>=sl
 if hit_tp or hit_sl:
  rr=canon._resolve_1m(
    symbol,int(t[j]),tp,sl,False,
    None if is_taker else fill,
    float(h[j]),float(l[j])
  )
  if rr in ("data_gap","entry_mismatch","exit_mismatch"):
   return {"status":rr}
  if rr=="win":xp,xb,reason=tp,j,"TP"
  elif rr=="loss":xp,xb,reason=sl,j,"SL"
  elif rr!="continue":
   return {"status":"unknown_1m_"+str(rr)}

 end=min(j+HOLD_BARS+1,len(t))
 if xp is None:
  for k in range(j+1,end):
   ht=l[k]<=tp;hs=h[k]>=sl
   if not (ht or hs):continue
   if ht and hs:
    rr=canon._resolve_1m(symbol,int(t[k]),tp,sl,False,None,float(h[k]),float(l[k]))
    if rr in ("data_gap","exit_mismatch","entry_mismatch"):
     return {"status":rr}
    xp,xb,reason=(tp,k,"TP") if rr=="win" else (sl,k,"SL")
   elif hs:
    xp,xb,reason=sl,k,"SL"
   else:
    xp,xb,reason=tp,k,"TP"
   break

 if xp is None:
  xb=end-1;xp=float(c[xb]);reason="TIME"

 gr=(fill-xp)/fill
 out={
  "status":"resolved","exit_bar":int(xb),"exit":float(xp),"reason":reason,
  "risk_pct":float(rp),"gross_return":float(gr),"gross_r":float(gr/rp),
  "sl":sl,"tp":tp
 }
 for bp in (20,40):
  out[f"net{bp}_return"]=float(gr-bp/10000)
  out[f"net{bp}_r"]=float((gr-bp/10000)/rp)
 return out

def signal_rows(symbol,t,o,h,l,c,cnt,ccfg):
 if len(t)<ATR_SLOW+BOX_N+10:return []
 a16=v1.atr(h,l,c,ATR_FAST);a96=v1.atr(h,l,c,ATR_SLOW)
 rows=[]
 for i in range(max(ATR_SLOW,BOX_N+1),len(t)-1):
  order_live=int(t[i+1])
  if order_live<TRAIN_START or order_live>=TRAIN_END:continue
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
  start=i+1

  for rf in RETRACE:
   limit=float(c[i]+rf*penetration)
   fill_info=first_retest_fill(t,o,h,start,limit,max(TTL_BARS))
   fill_offset=None if fill_info is None else int(fill_info["bar"]-start+1)

   for ttl in TTL_BARS:
    eligible=(fill_info is not None and fill_offset<=ttl)
    for slm in SL_ATR:
     for rr in TP_R:
      name=f"RT{int(rf*100):03d}_T{ttl:02d}_SL{int(slm*10):02d}_R{int(rr*10):02d}"
      ccfg[name]["signals"]+=1
      if not eligible:
       ccfg[name]["unfilled"]+=1
       continue

      z=resolve_trade(
        symbol,t,o,h,l,c,int(fill_info["bar"]),float(fill_info["fill"]),
        bool(fill_info["is_taker"]),aslow,slm,rr
      )
      if z["status"]!="resolved":
       ccfg[name][z["status"]]+=1
       continue
      exit_ts=int(t[z["exit_bar"]])
      if exit_ts>=TRAIN_END:
       ccfg[name]["split_boundary_excluded"]+=1
       continue

      ccfg[name]["resolved"]+=1
      ccfg[name]["taker" if fill_info["is_taker"] else "maker"]+=1
      rows.append({
       "config":name,"symbol":symbol,"side":"short",
       "signal_time":int(t[i]),"order_live_time":order_live,
       "entry_time":int(t[fill_info["bar"]]),"exit_time":exit_ts,
       "entry_ttl_bars":ttl,"fill_delay_bars":fill_offset,
       "retrace_fraction":rf,"limit_price":limit,
       "compression_ratio":comp,"compression_strength":1.0-comp,
       "expansion_strength":exp,"body_ratio":body/rng,
       "box_high":ph,"box_low":pl,"signal_close":float(c[i]),
       "entry":float(fill_info["fill"]),"is_taker":bool(fill_info["is_taker"]),
       "sl":z["sl"],"tp":z["tp"],"exit":z["exit"],"reason":z["reason"],
       "hold_min":int((exit_ts-int(t[fill_info["bar"]]))//60000),
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
 if a.shards<1 or a.shard<0 or a.shard>=a.shards:raise RuntimeError("bad shard")

 allf=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True))
 if not allf:raise RuntimeError("no data files")
 # De-duplicate by parsed symbol in case gh artifact extraction nests repeats.
 seen=set();uniq=[]
 for p in allf:
  s=base.sym(p)
  if not s or s in seen:continue
  seen.add(s);uniq.append(p)
 fs=[p for j,p in enumerate(uniq) if j%a.shards==a.shard]
 if not fs:raise RuntimeError("empty shard")

 rows=[];cnt=Counter();ccfg=defaultdict(Counter);started=time.time()
 print(f"RUN_START shard={a.shard}/{a.shards} symbols={len(fs)} configs={len(CONFIGS)}",flush=True)
 for n,p in enumerate(fs,1):
  s=base.sym(p)
  try:
   d=ex.load(p)
  except Exception as e:
   cnt["load_error"]+=1
   print(f"LOAD_ERROR {s} {type(e).__name__}:{e}",flush=True)
   continue
  try:
   for aa,bb in ex.segments(d[0]):
    if bb-aa<ATR_SLOW+BOX_N+10:continue
    rows.extend(signal_rows(s,*tuple(x[aa:bb] for x in d),cnt,ccfg))
  finally:
   ex.CACHE.clear()
   canon._ONE_MIN_CACHE.clear()
  if n%10==0 or n==len(fs):
   print(f"PROGRESS {n}/{len(fs)} {s} rows={len(rows)} raw_signals={cnt['raw_short_signals']} elapsed_min={(time.time()-started)/60:.1f}",flush=True)

 cols=[
  "config","symbol","side","signal_time","order_live_time","entry_time","exit_time",
  "entry_ttl_bars","fill_delay_bars","retrace_fraction","limit_price",
  "compression_ratio","compression_strength","expansion_strength","body_ratio",
  "box_high","box_low","signal_close","entry","is_taker","sl","tp","exit","reason",
  "hold_min","risk_pct","gross_return","gross_r","net20_return","net20_r","net40_return","net40_r"
 ]
 pd.DataFrame(rows,columns=cols).to_csv(f"compression_retest_v2_{a.shard}.csv.gz",index=False,compression="gzip")
 meta={
  "experiment_id":"compression_first_expansion_retest_v2_train",
  "classification":"EXPLORATORY",
  "repo":"duuu-hub/bb-scanner",
  "branch":os.environ.get("GITHUB_REF_NAME","research-rank5-binance-15m-5y"),
  "commit_sha":os.environ.get("GITHUB_SHA","local"),
  "source_data_run":36095439671,
  "train_start":"2021-01-01T00:00:00Z","train_end_exclusive":"2025-01-01T00:00:00Z",
  "signal":{"side":"SHORT","compression_max":COMP_MAX,"expansion_min":EXP_MIN,
            "box_n":BOX_N,"atr_fast":ATR_FAST,"atr_slow":ATR_SLOW,"body_min":BODY_MIN},
  "entry":{"retrace_fraction_grid":RETRACE,"ttl_bars_grid":TTL_BARS},
  "exit":{"sl_atr96_grid":SL_ATR,"tp_r_grid":TP_R,"hold_bars":HOLD_BARS,
          "risk_pct_bounds":[MIN_RISK,MAX_RISK]},
  "costs_round_trip_bp":[20,40],"funding":"excluded",
  "canonical_maker_resolver":"scripts/psar_open_canonical_compare.py::_resolve_1m semantics",
  "global_counters":dict(cnt),
  "config_counters":{k:dict(v) for k,v in ccfg.items()},
  "rows":len(rows),"symbols":len(fs)
 }
 Path(f"compression_retest_v2_meta_{a.shard}.json").write_text(json.dumps(meta,indent=2)+"\n")
 print("DONE",len(rows),json.dumps(dict(cnt)),flush=True)

if __name__=="__main__":main()
