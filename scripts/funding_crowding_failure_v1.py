import argparse,glob,json,os,sys,time,math
from collections import Counter,defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd,requests

import scripts.external_breakout_replay as ex
import scripts.sweep_reclaim_research as base

BAR=900000; HOUR=3600000; DAY=86400000
TRAIN_START=pd.Timestamp("2021-01-01T00:00:00Z").value//10**6
TRAIN_END=pd.Timestamp("2025-01-01T00:00:00Z").value//10**6
FETCH_START=pd.Timestamp("2020-10-01T00:00:00Z").value//10**6
FETCH_END=TRAIN_END-1

TAIL_Q=(.975,.99)
ABS_FLOOR=(.0002,.0005)
MODES=("H1_REVERSAL","H4_STALL")
SL_ATR=(1.0,1.5)
TP_R=(1.5,2.5)
HOLD_HOURS=(8,16)
MIN_PRIOR=60
LOOKBACK_MS=90*DAY
MIN_RISK=.004
MAX_RISK=.05

CONFIGS=[]
for q in TAIL_Q:
 for fl in ABS_FLOOR:
  for mode in MODES:
   for sl in SL_ATR:
    for rr in TP_R:
     for hh in HOLD_HOURS:
      CONFIGS.append({
       "name":f"Q{int(q*1000):03d}_F{int(fl*1e6):03d}_{'H1R' if mode=='H1_REVERSAL' else 'H4S'}_SL{int(sl*10):02d}_R{int(rr*10):02d}_H{hh:02d}",
       "tail_q":q,"abs_floor":fl,"mode":mode,"sl_atr1h":sl,"tp_r":rr,"hold_hours":hh
      })

def fetch_funding(symbol,start_ms=FETCH_START,end_ms=FETCH_END):
 rows=[];cursor=int(start_ms);sess=requests.Session()
 while cursor<=end_ms:
  last=None
  for attempt in range(5):
   try:
    r=sess.get("https://fapi.binance.com/fapi/v1/fundingRate",
      params={"symbol":symbol,"startTime":cursor,"endTime":int(end_ms),"limit":1000},timeout=30)
    if r.status_code in (418,429):
     time.sleep(3*(attempt+1));continue
    r.raise_for_status();data=r.json();last=None;break
   except Exception as e:
    last=e;time.sleep(min(8,2**attempt))
  if last is not None:raise RuntimeError(f"funding fetch failed {symbol}: {last}")
  if not data:break
  for x in data:
   try:
    rows.append((symbol,int(x["fundingTime"]),float(x["fundingRate"]),float(x.get("markPrice","nan"))))
   except Exception:continue
  nxt=int(data[-1]["fundingTime"])+1
  if nxt<=cursor:break
  cursor=nxt
  if len(data)<1000:break
  time.sleep(.65)
 if not rows:return pd.DataFrame(columns=["symbol","fundingTime","fundingRate","markPrice"])
 d=pd.DataFrame(rows,columns=["symbol","fundingTime","fundingRate","markPrice"])
 d=d.drop_duplicates(["fundingTime"]).sort_values("fundingTime").reset_index(drop=True)
 return d

def hourly(t,o,h,l,c):
 out=[]
 for aa,bb in ex.segments(t):
  tt=t[aa:bb];oo=o[aa:bb];hh=h[aa:bb];ll=l[aa:bb];cc=c[aa:bb]
  b=tt//HOUR;q=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(tt)]
  st=q[:-1];en=q[1:];g=(en-st)==4;st=st[g];en=en[g]
  if len(st):
   ok=np.array([tt[a]%HOUR==0 and np.all(np.diff(tt[a:z])==BAR) for a,z in zip(st,en)],bool)
   st=st[ok];en=en[ok]
  if not len(st):continue
  rt=tt[st];ro=oo[st]
  rh=np.array([hh[a:z].max() for a,z in zip(st,en)],float)
  rl=np.array([ll[a:z].min() for a,z in zip(st,en)],float)
  rc=cc[en-1]
  prev=np.r_[np.nan,rc[:-1]]
  tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prev),np.abs(rl-prev)))
  atr=pd.Series(tr).rolling(14,min_periods=14).mean().shift(1).to_numpy(float)
  out.append((rt,ro,rh,rl,rc,atr))
 return out

def find_hour_context(parts,ft):
 for rt,ro,rh,rl,rc,atr in parts:
  ends=rt+HOUR
  i=int(np.searchsorted(ends,ft,side="right")-1)
  if i<4 or i>=len(rt):continue
  if int(ends[i])!=int(ft):continue
  return rt,ro,rh,rl,rc,atr,i
 return None

def segment_for_time(t,ts):
 for aa,bb in ex.segments(t):
  j=int(np.searchsorted(t[aa:bb],ts))
  if j<bb-aa and int(t[aa+j])==int(ts):return aa,bb,aa+j
 return None

def event_rows(symbol,t,o,h,l,c,fund,cnt):
 if len(fund)<MIN_PRIOR+1:return []
 parts=hourly(t,o,h,l,c);rows=[]
 ft=fund.fundingTime.to_numpy(np.int64);fr=fund.fundingRate.to_numpy(float)
 for j in range(len(fund)):
  ts=int(ft[j]);rate=float(fr[j])
  if ts<TRAIN_START or ts>=TRAIN_END:continue
  lo=np.searchsorted(ft,ts-LOOKBACK_MS,side="left")
  hist=fr[lo:j]
  hist=hist[np.isfinite(hist)]
  if len(hist)<MIN_PRIOR:continue
  if rate==0 or not np.isfinite(rate):continue
  sign=1 if rate>0 else -1
  tail=float(np.mean(hist<=rate)) if sign>0 else float(np.mean(hist>=rate))
  if tail<min(TAIL_Q) or abs(rate)<min(ABS_FLOOR):continue

  hc=find_hour_context(parts,ts)
  if hc is None:
   cnt["hour_context_missing"]+=1;continue
  rt,ro,rh,rl,rc,atr,hi=hc
  A=float(atr[hi])
  if not np.isfinite(A) or A<=0:continue
  h1_fail=sign*float(rc[hi]-ro[hi])<=0
  h4_norm=sign*float(rc[hi]-rc[hi-4])/A
  h4_fail=h4_norm<=.5

  entry_ts=ts+BAR
  sg=segment_for_time(t,entry_ts)
  if sg is None:
   cnt["entry_timestamp_missing"]+=1;continue
  aa,bb,start_abs=sg;local=start_abs-aa
  tt=t[aa:bb];oo=o[aa:bb];hh=h[aa:bb];ll=l[aa:bb];cc=c[aa:bb]
  side="short" if sign>0 else "long";fill=float(o[start_abs])

  for q in TAIL_Q:
   if tail<q:continue
   for fl in ABS_FLOOR:
    if abs(rate)<fl:continue
    for mode,mode_ok in (("H1_REVERSAL",h1_fail),("H4_STALL",h4_fail)):
     if not mode_ok:continue
     for slm in SL_ATR:
      risk=slm*A;sl=fill-risk if side=="long" else fill+risk
      rp=abs(fill-sl)/fill
      if rp<MIN_RISK or rp>MAX_RISK or sl<=0:
       cnt["risk_filter"]+=len(TP_R)*len(HOLD_HOURS);continue
      for rr in TP_R:
       tp=fill+rr*risk if side=="long" else fill-rr*risk
       if tp<=0:
        cnt["bad_tp"]+=len(HOLD_HOURS);continue
       for hold in HOLD_HOURS:
        name=f"Q{int(q*1000):03d}_F{int(fl*1e6):03d}_{'H1R' if mode=='H1_REVERSAL' else 'H4S'}_SL{int(slm*10):02d}_R{int(rr*10):02d}_H{hold:02d}"
        maxbars=hold*4
        if local+maxbars>=len(tt) or entry_ts+maxbars*BAR>=TRAIN_END:
         cnt[f"{name}:data_gap_horizon"]+=1;continue
        z=base.trade(symbol,tt,oo,hh,ll,cc,local,side,sl,tp,maxbars)
        if z is None:
         cnt[f"{name}:canonical_excluded"]+=1;continue
        xb=int(z["exit_bar"]);xt=int(tt[xb])
        if xt>=TRAIN_END:
         cnt[f"{name}:split_boundary_excluded"]+=1;continue
        rows.append({
         "config":name,"symbol":symbol,"funding_time":ts,"entry_time":entry_ts,"exit_time":xt,
         "side":side,"funding_rate":rate,"tail_strength":tail,"mode":mode,
         "h1_signed_body":sign*float(rc[hi]-ro[hi])/A,"h4_signed_move_atr":h4_norm,
         "entry":fill,"sl":float(sl),"tp":float(tp),"exit":z["exit"],"reason":z["reason"],
         "risk_pct":z["risk_pct"],"gross_return":z["gross_return"],"gross_r":z["gross_r"],
         "net20_return":z["net20_return"],"net20_r":z["net20_r"],
         "net40_return":z["net40_return"],"net40_r":z["net40_r"]
        })
 return rows

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1)
 a=ap.parse_args()
 allf=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));m={}
 for p in allf:
  s=base.sym(p)
  if s and s not in m:m[s]=p
 syms=[s for i,s in enumerate(sorted(m)) if i%a.shards==a.shard]
 if not syms:raise RuntimeError("empty shard")
 rows=[];fund_snaps=[];cnt=Counter();t0=time.time()
 print(f"RUN_START shard={a.shard}/{a.shards} symbols={len(syms)} configs={len(CONFIGS)}",flush=True)
 for n,s in enumerate(syms,1):
  try:fund=fetch_funding(s)
  except Exception as e:
   cnt["funding_fetch_error"]+=1;print(f"FUNDING_ERROR {s} {e}",flush=True);continue
  if len(fund):
   fund_snaps.append(fund)
  else:
   cnt["no_funding"]+=1;continue
  try:d=ex.load(m[s])
  except Exception as e:
   cnt["price_load_error"]+=1;print(f"PRICE_ERROR {s} {e}",flush=True);continue
  try:rows.extend(event_rows(s,*d,fund,cnt))
  finally:ex.CACHE.clear()
  if n%5==0 or n==len(syms):
   print(f"PROGRESS {n}/{len(syms)} {s} trades={len(rows)} funding_rows={sum(len(x) for x in fund_snaps)} elapsed_min={(time.time()-t0)/60:.1f}",flush=True)
 pd.DataFrame(rows).to_csv(f"funding_crowding_failure_v1_{a.shard}.csv.gz",index=False,compression="gzip")
 if fund_snaps:pd.concat(fund_snaps,ignore_index=True).to_csv(f"funding_snapshot_v1_{a.shard}.csv.gz",index=False,compression="gzip")
 else:pd.DataFrame(columns=["symbol","fundingTime","fundingRate","markPrice"]).to_csv(f"funding_snapshot_v1_{a.shard}.csv.gz",index=False,compression="gzip")
 Path(f"funding_crowding_failure_v1_meta_{a.shard}.json").write_text(json.dumps({
  "experiment_id":"funding_crowding_failure_v1_train","classification":"EXPLORATORY",
  "commit_sha":os.environ.get("GITHUB_SHA","local"),"price_source_run":36095439671,
  "funding_source":"https://fapi.binance.com/fapi/v1/fundingRate","funding_fetch_start":FETCH_START,"funding_fetch_end":FETCH_END,
  "train_start":TRAIN_START,"train_end_exclusive":TRAIN_END,"configs":CONFIGS,
  "costs_round_trip_bp":[20,40],"funding_pnl":"excluded","rows":len(rows),"symbols":len(syms),"counters":dict(cnt)
 },indent=2)+"\n")
 print("DONE",len(rows),dict(cnt),flush=True)

if __name__=="__main__":main()
