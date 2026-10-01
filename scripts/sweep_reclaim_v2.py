import argparse,glob,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.external_breakout_replay as ex
import scripts.sweep_reclaim_research as v1

BAR=v1.BAR; HOUR=v1.HOUR; UNIVERSE=v1.UNIVERSE
CONFIGS=tuple(
 {"name":f"SR2_LB{lb}_X{str(x).replace('.','')}_R{str(r).replace('.','')}",
  "lookback":lb,"ext_atr":x,"tp_r":r}
 for lb in (96,192) for x in (0.75,1.25) for r in (1.5,2.0)
)
SWEEP_ATR=.10
RECLAIM_ATR=.03
CONFIRM_ATR=.08
WICK_MIN=.50
BODY_ATR=.15
LEVEL_ZONE_ATR=.12
MIN_TOUCHES=2
MIN_TOUCH_GAP=4
MIN_LEVEL_AGE=16
AWAY_BARS=8
SL_BUFFER_ATR=.10
MIN_RISK=.004
MAX_RISK=.030
MAX_HOLD_BARS=32

def ema(c,n):
 return pd.Series(c).ewm(span=n,adjust=False,min_periods=n).mean().to_numpy()

def separated_touch_count(ix,gap):
 if not len(ix):return 0
 n=1;last=int(ix[0])
 for x in ix[1:]:
  if int(x)-last>=gap:n+=1;last=int(x)
 return n

def evaluate(symbol,t,o,h,l,c):
 ht,hh,hl,hc=v1.rs4h(t,o,h,l,c)
 if len(ht)<24:return []
 ha=v1.atr4(hh,hl,hc,14);he=ema(hc,20)
 hclose=ht+4*HOUR;hidx=np.searchsorted(hclose,t,side="right")-1
 rows=[];free={x["name"]:-1 for x in CONFIGS}
 for i in range(193,len(t)-2):
  j=i+1;start=i+2;hi=int(hidx[i])
  if hi<20 or not np.isfinite(ha[hi]) or not np.isfinite(he[hi]) or ha[hi]<=0:continue
  A=float(ha[hi]);E=float(he[hi]);rng=float(h[i]-l[i])
  if rng<=0:continue
  for cfg in CONFIGS:
   name=cfg["name"]
   if start<free[name]:continue
   lb=cfg["lookback"];lo0=i-lb
   wh=h[lo0:i];wl=l[lo0:i]
   ah=int(np.argmax(wh));al=int(np.argmin(wl))
   ph=float(wh[ah]);pl=float(wl[al]);idxh=lo0+ah;idxl=lo0+al

   cand=[]
   # Mature, repeatedly tested but recently untouched resistance -> sweep -> reclaim -> bearish confirmation.
   if i-idxh>=MIN_LEVEL_AGE:
    touches=np.flatnonzero(wh>=ph-LEVEL_ZONE_ATR*A)
    away=np.all(h[max(lo0,i-AWAY_BARS):i] <= ph-LEVEL_ZONE_ATR*A)
    if separated_touch_count(touches,MIN_TOUCH_GAP)>=MIN_TOUCHES and away:
     wick=(h[i]-max(o[i],c[i]))/rng
     if (h[i]>=ph+SWEEP_ATR*A and c[i]<=ph-RECLAIM_ATR*A and wick>=WICK_MIN and
         h[i]-E>=cfg["ext_atr"]*A):
      crng=max(float(h[j]-l[j]),1e-12);body=abs(float(c[j]-o[j]))
      if (c[j]<=ph-CONFIRM_ATR*A and c[j]<=c[i] and c[j]<o[j] and body>=BODY_ATR*A and
          h[j] < h[i]+.05*A):
       score=(h[i]-ph)/A+(ph-c[i])/A+(ph-c[j])/A+0.5*wick
       cand.append(("short",float(h[i]),float(score),ph))

   # Mature, repeatedly tested but recently untouched support -> sweep -> reclaim -> bullish confirmation.
   if i-idxl>=MIN_LEVEL_AGE:
    touches=np.flatnonzero(wl<=pl+LEVEL_ZONE_ATR*A)
    away=np.all(l[max(lo0,i-AWAY_BARS):i] >= pl+LEVEL_ZONE_ATR*A)
    if separated_touch_count(touches,MIN_TOUCH_GAP)>=MIN_TOUCHES and away:
     wick=(min(o[i],c[i])-l[i])/rng
     if (l[i]<=pl-SWEEP_ATR*A and c[i]>=pl+RECLAIM_ATR*A and wick>=WICK_MIN and
         E-l[i]>=cfg["ext_atr"]*A):
      body=abs(float(c[j]-o[j]))
      if (c[j]>=pl+CONFIRM_ATR*A and c[j]>=c[i] and c[j]>o[j] and body>=BODY_ATR*A and
          l[j] > l[i]-.05*A):
       score=(pl-l[i])/A+(c[i]-pl)/A+(c[j]-pl)/A+0.5*wick
       cand.append(("long",float(l[i]),float(score),pl))

   if not cand:continue
   side,extreme,score,level=max(cand,key=lambda x:x[2])
   fill=float(o[start])
   sl=extreme+SL_BUFFER_ATR*A if side=="short" else extreme-SL_BUFFER_ATR*A
   rp=abs(fill-sl)/fill
   if sl<=0 or rp<MIN_RISK or rp>MAX_RISK:continue
   tp=fill+cfg["tp_r"]*abs(fill-sl) if side=="long" else fill-cfg["tp_r"]*abs(fill-sl)
   if tp<=0:continue
   z=v1.trade(symbol,t,o,h,l,c,start,side,sl,tp,MAX_HOLD_BARS)
   if z is None:continue
   free[name]=z["exit_bar"]+1
   rows.append({"config":name,"symbol":symbol,"signal_time":int(t[i]),"confirm_time":int(t[j]),
    "entry_time":int(t[start]),"exit_time":int(t[z["exit_bar"]]),"side":side,"score":score,
    "level":level,"entry":fill,"sl":float(sl),"tp":float(tp),"exit":z["exit"],"reason":z["reason"],
    "hold_min":int((t[z["exit_bar"]]-t[start])//60000),"risk_pct":z["risk_pct"],
    "gross_return":z["gross_return"],"gross_r":z["gross_r"],"net20_return":z["net20_return"],
    "net20_r":z["net20_r"],"net40_return":z["net40_return"],"net40_r":z["net40_r"]})
 return rows

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);a=ap.parse_args()
 fs=[p for p in sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True)) if v1.sym(p) in UNIVERSE]
 fs=[p for k,p in enumerate(fs) if k%a.shards==a.shard]
 if not fs:raise RuntimeError("no universe files")
 rows=[];t0=time.time()
 print(f"RUN_START shard={a.shard}/{a.shards} files={len(fs)}",flush=True)
 for n,p in enumerate(fs,1):
  s=v1.sym(p);d=ex.load(p)
  try:
   for aa,bb in ex.segments(d[0]):
    if bb-aa<1000:continue
    rows.extend(evaluate(s,*tuple(x[aa:bb] for x in d)))
  finally:ex.CACHE.clear()
  print(f"PROGRESS {n}/{len(fs)} {s} rows={len(rows)} elapsed_min={(time.time()-t0)/60:.1f}",flush=True)
 pd.DataFrame(rows).to_csv(f"sweep_reclaim_v2_raw_{a.shard}.csv.gz",index=False,compression="gzip")
 Path(f"sweep_reclaim_v2_meta_{a.shard}.json").write_text(json.dumps({
  "configs":CONFIGS,"universe":sorted(UNIVERSE),
  "fixed":{"sweep_atr":SWEEP_ATR,"reclaim_atr":RECLAIM_ATR,"confirm_atr":CONFIRM_ATR,"wick_min":WICK_MIN,
   "body_atr":BODY_ATR,"level_zone_atr":LEVEL_ZONE_ATR,"min_touches":MIN_TOUCHES,"min_touch_gap":MIN_TOUCH_GAP,
   "min_level_age_bars":MIN_LEVEL_AGE,"away_bars":AWAY_BARS,"sl_buffer_atr":SL_BUFFER_ATR,"max_hold_bars":MAX_HOLD_BARS},
  "integrity":"V2 conceived after V1 results; 2025-26 is evaluation, not pristine untouched holdout."
 },indent=2))
 print("DONE",len(rows))
if __name__=="__main__":main()
