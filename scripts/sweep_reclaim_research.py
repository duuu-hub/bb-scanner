import argparse,glob,json,math,os,re,sys,time
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.external_breakout_replay as ex

BAR=900000; HOUR=3600000
UNIVERSE={
"BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","SOLUSDT","LTCUSDT","BCHUSDT",
"LINKUSDT","ETCUSDT","TRXUSDT","XLMUSDT","EOSUSDT","DOTUSDT","UNIUSDT","AAVEUSDT","AVAXUSDT"
}
CONFIGS=tuple(
 {"name":f"SR_LB{lb}_S{int(sw*100):02d}_R{str(r).replace('.','')}",
  "lookback":lb,"sweep_atr":sw,"tp_r":r,
  "wick_min":0.45,"reclaim_atr":0.02,"sl_buffer_atr":0.10,
  "min_risk_pct":0.004,"max_risk_pct":0.030,"max_hold_bars":48}
 for lb in (96,192) for sw in (0.10,0.20) for r in (1.5,2.0)
)

def sym(p):
 b=os.path.basename(p);return b[:-7].upper() if b.endswith(".csv.gz") else ""

def rs4h(t,o,h,l,c):
 m=16;b=t//(BAR*m);q=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(t)]
 st=q[:-1];en=q[1:];g=(en-st)==m;st=st[g];en=en[g]
 if len(st):
  span=BAR*m;ok=np.array([t[a]%span==0 and np.all(np.diff(t[a:z])==BAR) for a,z in zip(st,en)],bool);st=st[ok];en=en[ok]
 return t[st],np.array([h[a:z].max() for a,z in zip(st,en)]),np.array([l[a:z].min() for a,z in zip(st,en)]),c[en-1]

def atr4(h,l,c,n=14):
 prev=np.r_[np.nan,c[:-1]];tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
 return pd.Series(tr).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()

def entrybar_exit(symbol,ts,side,tp,sl):
 d=ex.w1m(symbol,int(ts))
 if len(d)==2 and d[0]=="data_gap":return "data_gap"
 _,_,h,l=d;long=side=="long"
 for j in range(15):
  ht=h[j]>=tp if long else l[j]<=tp;hs=l[j]<=sl if long else h[j]>=sl
  # Canonical repo rule: any exit touch in entry minute is LOSS.
  if j==0 and (ht or hs):return "loss"
  if ht and hs:return "loss"
  if hs:return "loss"
  if ht:return "win"
 return "none"

def trade(symbol,t,o,h,l,c,start,side,sl,tp,maxbars):
 fill=float(o[start]);long=side=="long";end=min(start+maxbars+1,len(t))
 if sl<=0 or tp<=0:return None
 risk_pct=abs(fill-sl)/fill
 ep=h[start]>=tp if long else l[start]<=tp
 es=l[start]<=sl if long else h[start]>=sl
 if ep or es:
  rr=entrybar_exit(symbol,int(t[start]),side,tp,sl)
  if rr=="data_gap":return None
  if rr=="win":xb=start;xp=tp;reason="TP"
  elif rr=="loss":xb=start;xp=sl;reason="SL"
  else:return None
 else:
  xb=xp=reason=None
 for k in range(start+1,end):
  if xb is not None:break
  ht=h[k]>=tp if long else l[k]<=tp;hs=l[k]<=sl if long else h[k]>=sl
  if not (ht or hs):continue
  if ht and hs:
   rr=ex.established(symbol,int(t[k]),side,tp,sl)
   if rr in ("data_gap","exit_mismatch"):return None
   xb=k;xp=tp if rr=="win" else sl;reason="TP" if rr=="win" else "SL"
  elif hs:xb=k;xp=sl;reason="SL"
  else:xb=k;xp=tp;reason="TP"
 if xb is None:
  xb=end-1;xp=float(c[xb]);reason="TIME"
 gross=(xp/fill-1) if long else (fill/xp-1)
 out={"exit_bar":int(xb),"exit":float(xp),"reason":reason,"risk_pct":float(risk_pct),
      "gross_return":float(gross),"gross_r":float(gross/risk_pct)}
 for bp in (20,40):
  out[f"net{bp}_return"]=float(gross-bp/10000)
  out[f"net{bp}_r"]=float((gross-bp/10000)/risk_pct)
 return out

def evaluate(symbol,t,o,h,l,c):
 ht,hh,hl,hc=rs4h(t,o,h,l,c)
 if len(ht)<20:return []
 ha=atr4(hh,hl,hc,14)
 hclose=ht+4*HOUR
 hidx=np.searchsorted(hclose,t,side="right")-1
 rows=[];free={x["name"]:-1 for x in CONFIGS}
 for i in range(193,len(t)-1):
  hi=int(hidx[i])
  if hi<13 or not np.isfinite(ha[hi]) or ha[hi]<=0:continue
  A=float(ha[hi]);rng=float(h[i]-l[i])
  if rng<=0:continue
  for cfg in CONFIGS:
   name=cfg["name"]
   if i<free[name]:continue
   lb=cfg["lookback"];ph=float(np.max(h[i-lb:i]));pl=float(np.min(l[i-lb:i]))
   side=None;sweep=0.0;reclaim=0.0;wick=0.0;extreme=None
   if h[i]>=ph+cfg["sweep_atr"]*A and c[i]<=ph-cfg["reclaim_atr"]*A:
    side="short";sweep=(h[i]-ph)/A;reclaim=(ph-c[i])/A;wick=(h[i]-max(o[i],c[i]))/rng;extreme=float(h[i])
   if l[i]<=pl-cfg["sweep_atr"]*A and c[i]>=pl+cfg["reclaim_atr"]*A:
    lsweep=(pl-l[i])/A;lreclaim=(c[i]-pl)/A;lwick=(min(o[i],c[i])-l[i])/rng
    if side is None or (lsweep+lreclaim+lwick)>(sweep+reclaim+wick):
     side="long";sweep=lsweep;reclaim=lreclaim;wick=lwick;extreme=float(l[i])
   if side is None or wick<cfg["wick_min"]:continue
   start=i+1;fill=float(o[start])
   sl=extreme+cfg["sl_buffer_atr"]*A if side=="short" else extreme-cfg["sl_buffer_atr"]*A
   rp=abs(fill-sl)/fill
   if rp<cfg["min_risk_pct"] or rp>cfg["max_risk_pct"] or sl<=0:continue
   tp=fill+cfg["tp_r"]*abs(fill-sl) if side=="long" else fill-cfg["tp_r"]*abs(fill-sl)
   if tp<=0:continue
   z=trade(symbol,t,o,h,l,c,start,side,sl,tp,cfg["max_hold_bars"])
   if z is None:continue
   free[name]=z["exit_bar"]+1
   score=float(sweep+reclaim+0.5*wick)
   rows.append({"config":name,"symbol":symbol,"signal_time":int(t[i]),"entry_time":int(t[start]),
    "exit_time":int(t[z["exit_bar"]]),"side":side,"score":score,"sweep_atr":float(sweep),
    "reclaim_atr":float(reclaim),"wick_ratio":float(wick),"entry":fill,"sl":float(sl),"tp":float(tp),
    "exit":z["exit"],"reason":z["reason"],"hold_min":int((t[z["exit_bar"]]-t[start])//60000),
    "risk_pct":z["risk_pct"],"gross_return":z["gross_return"],"gross_r":z["gross_r"],
    "net20_return":z["net20_return"],"net20_r":z["net20_r"],
    "net40_return":z["net40_return"],"net40_r":z["net40_r"]})
 return rows

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);a=ap.parse_args()
 fs=[p for p in sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True)) if sym(p) in UNIVERSE]
 fs=[p for j,p in enumerate(fs) if j%a.shards==a.shard]
 if not fs:raise RuntimeError("no universe files")
 rows=[];t0=time.time()
 print(f"RUN_START shard={a.shard}/{a.shards} files={len(fs)}",flush=True)
 for n,p in enumerate(fs,1):
  s=sym(p);d=ex.load(p)
  try:
   for aa,bb in ex.segments(d[0]):
    if bb-aa<900:continue
    rows.extend(evaluate(s,*tuple(x[aa:bb] for x in d)))
  finally:ex.CACHE.clear()
  print(f"PROGRESS {n}/{len(fs)} {s} rows={len(rows)} elapsed_min={(time.time()-t0)/60:.1f}",flush=True)
 pd.DataFrame(rows).to_csv(f"sweep_reclaim_raw_{a.shard}.csv.gz",index=False,compression="gzip")
 Path(f"sweep_reclaim_meta_{a.shard}.json").write_text(json.dumps({
   "universe":sorted(UNIVERSE),"configs":CONFIGS,"source_data_run":ex.SOURCE_DATA_RUN,
   "notes":"Pre-registered before results. 15m sweep/reclaim signal, prior 24h/48h extremes, closed 4H ATR, next-15m-open entry, canonical Binance 1m collision handling."
 },indent=2))
 print("DONE",len(rows))
if __name__=="__main__":main()
