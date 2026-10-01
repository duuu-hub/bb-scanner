import argparse,glob,io,json,math,os,re,time,urllib.error,urllib.request,zipfile
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import numpy as np,pandas as pd

SOURCE_DATA_RUN="36095439671"; BAR=900000; MIN=60000; M=4; MAXH=96; CACHE={}
CONFIGS=(
{"name":"FORTUNE_N12","group":"FORTUNE","family":"donchian","n":12,"buf":.1,"sl":1.,"r":3.},
{"name":"FORTUNE_N24","group":"FORTUNE","family":"donchian","n":24,"buf":.1,"sl":1.,"r":3.},
{"name":"FORTUNE_N48","group":"FORTUNE","family":"donchian","n":48,"buf":.1,"sl":1.,"r":3.},
{"name":"BLUE_A_DONCHIAN12","group":"BLUE_PROXY","family":"donchian","n":12,"buf":.1,"sl":1.,"r":2.},
{"name":"BLUE_B_DONCHIAN48","group":"BLUE_PROXY","family":"donchian","n":48,"buf":.1,"sl":1.,"r":2.},
{"name":"BLUE_C_COMPRESS12","group":"BLUE_PROXY","family":"compress","n":12,"buf":.1,"sl":1.,"r":2.,"max_range_atr":2.},
{"name":"BLUE_D_SQUEEZE20","group":"BLUE_PROXY","family":"squeeze","n":20,"buf":.1,"sl":1.,"r":2.,"hist":100,"q":.25},
{"name":"BLUE_E_EMA50_DON24","group":"BLUE_PROXY","family":"ema","n":24,"buf":.1,"sl":1.,"r":2.},
{"name":"BLUE_F_PREVDAY","group":"BLUE_PROXY","family":"prevday","n":24,"buf":.1,"sl":1.,"r":2.},
)

def sym(p):
 b=os.path.basename(p); s=b[:-7].upper() if b.endswith(".csv.gz") else ""
 if not re.fullmatch(r"[A-Z0-9]+USDT",s): raise RuntimeError(f"bad symbol file {b}")
 return s

def load(p):
 d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]).sort_values("open_time")
 t=d.open_time.to_numpy(np.int64); o=d.open.to_numpy(float); h=d.high.to_numpy(float); l=d.low.to_numpy(float); c=d.close.to_numpy(float)
 if not len(t) or np.any(t%BAR) or (len(t)>1 and np.any(np.diff(t)<=0)): raise RuntimeError("bad 15m timestamps")
 if not all(np.all(np.isfinite(x)) for x in (o,h,l,c)) or any(np.any(x<=0) for x in (o,h,l,c)): raise RuntimeError("bad OHLC")
 if np.any(h<np.maximum.reduce([o,l,c])) or np.any(l>np.minimum.reduce([o,h,c])): raise RuntimeError("bad OHLC geometry")
 return t,o,h,l,c

def segments(t):
 q=np.r_[0,np.flatnonzero(np.diff(t)!=BAR)+1,len(t)]
 return [(int(a),int(b)) for a,b in zip(q[:-1],q[1:])]

def resample(t,o,h,l,c):
 b=t//(BAR*M); q=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(t)]; st=q[:-1]; en=q[1:]; g=(en-st)==M; st=st[g]; en=en[g]
 ok=np.array([t[a]%(BAR*M)==0 and np.all(np.diff(t[a:z])==BAR) for a,z in zip(st,en)],bool) if len(st) else np.array([],bool); st=st[ok]; en=en[ok]
 return t[st],o[st],np.array([h[a:z].max() for a,z in zip(st,en)]),np.array([l[a:z].min() for a,z in zip(st,en)]),c[en-1],st

def features(rh,rl,rc):
 prev=np.r_[np.nan,rc[:-1]]; tr=np.maximum(rh-rl,np.maximum(abs(rh-prev),abs(rl-prev)))
 atr=np.r_[np.nan,pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()[:-1]]
 ema=np.r_[np.nan,pd.Series(rc).ewm(span=50,adjust=False,min_periods=50).mean().to_numpy()[:-1]]
 s=pd.Series(rc); w=(4*s.rolling(20,min_periods=20).std(ddof=0)/s.rolling(20,min_periods=20).mean()).to_numpy(); bbw=np.r_[np.nan,w[:-1]]
 return atr,ema,bbw

def one_min(symbol,ts):
 ym=datetime.fromtimestamp(ts/1000,tz=timezone.utc).strftime("%Y-%m"); k=(symbol,ym)
 if k in CACHE:return CACHE[k]
 u=f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/{symbol}-1m-{ym}.zip"; last=None
 for a in range(4):
  try:
   raw=urllib.request.urlopen(u,timeout=60).read()
   with zipfile.ZipFile(io.BytesIO(raw)) as z:
    ms=[n for n in z.namelist() if n.lower().endswith(".csv")]
    if len(ms)!=1: raise RuntimeError(f"bad zip members {ms}")
    d=pd.read_csv(z.open(ms[0]),header=None,dtype=str)
   first=pd.to_numeric(pd.Series([d.iat[0,0]]),errors="coerce").iat[0] if len(d) else np.nan
   if len(d) and not np.isfinite(first):d=d.iloc[1:].reset_index(drop=True)
   if not len(d):return ("data_gap","empty")
   t=pd.to_numeric(d.iloc[:,0],errors="raise").to_numpy(np.int64); o=pd.to_numeric(d.iloc[:,1],errors="raise").to_numpy(float); h=pd.to_numeric(d.iloc[:,2],errors="raise").to_numpy(float); l=pd.to_numeric(d.iloc[:,3],errors="raise").to_numpy(float)
   if np.any(t%MIN) or (len(t)>1 and np.any(np.diff(t)!=MIN)):return ("data_gap","1m gap")
   if not all(np.all(np.isfinite(x)) for x in (o,h,l)) or np.any(h<np.maximum(o,l)):return ("data_gap","1m bad ohlc")
   CACHE[k]=(t,o,h,l);return CACHE[k]
  except urllib.error.HTTPError as e:
   last=e
   if e.code==404:CACHE[k]=("data_gap","404");return CACHE[k]
  except Exception as e:last=e
  if a<3:time.sleep(2**a)
 raise RuntimeError(f"1m download failed {symbol} {ym}: {last}")

def w1m(symbol,ts):
 d=one_min(symbol,ts)
 if len(d)==2 and d[0]=="data_gap":return d
 t,o,h,l=d;a=np.searchsorted(t,ts);z=np.searchsorted(t,ts+BAR)
 if z-a!=15 or a>=len(t) or t[a]!=ts or t[z-1]!=ts+14*MIN:return ("data_gap","incomplete")
 return t[a:z],o[a:z],h[a:z],l[a:z]

def dual_side(symbol,ts,buy,sell):
 d=w1m(symbol,ts)
 if len(d)==2 and d[0]=="data_gap":return "data_gap"
 t,o,h,l=d
 for j in range(15):
  a=h[j]>=buy;b=l[j]<=sell
  if a and b:return "side_ambiguous"
  if a:return "long"
  if b:return "short"
 return "entry_mismatch"

def stop_entry_bar(symbol,ts,side,e,tp,sl):
 d=w1m(symbol,ts)
 if len(d)==2 and d[0]=="data_gap":return "data_gap"
 t,o,h,l=d; long=side=="long"; entered=False
 for j in range(15):
  if not entered:
   hit=h[j]>=e if long else l[j]<=e
   if not hit:continue
   if (h[j]>=tp if long else l[j]<=tp) or (l[j]<=sl if long else h[j]>=sl):return "loss"
   entered=True;continue
  ht=h[j]>=tp if long else l[j]<=tp; hs=l[j]<=sl if long else h[j]>=sl
  if hs or (ht and hs):return "loss"
  if ht:return "win"
 return "continue" if entered else "entry_mismatch"

def established(symbol,ts,side,tp,sl):
 d=w1m(symbol,ts)
 if len(d)==2 and d[0]=="data_gap":return "data_gap"
 _,_,h,l=d;long=side=="long"
 for j in range(15):
  ht=h[j]>=tp if long else l[j]<=tp;hs=l[j]<=sl if long else h[j]>=sl
  if hs or (ht and hs):return "loss"
  if ht:return "win"
 return "exit_mismatch"

def levels(cfg,i,rt,rh,rl,rc,atr,ema,bbw):
 a=atr[i];n=cfg["n"]
 if not np.isfinite(a) or a<=0 or i<n:return None
 hi=float(rh[i-n:i].max());lo=float(rl[i-n:i].min());al=ash=True;f=cfg["family"]
 if f=="compress" and hi-lo>cfg["max_range_atr"]*a:return None
 if f=="squeeze":
  hist=cfg["hist"];old=bbw[i-hist:i] if i>=hist else np.array([])
  old=old[np.isfinite(old)]
  if not np.isfinite(bbw[i]) or len(old)<hist//2 or bbw[i]>np.quantile(old,cfg["q"]):return None
 elif f=="ema":
  if not np.isfinite(ema[i]):return None
  al=bool(rc[i-1]>ema[i]);ash=bool(rc[i-1]<ema[i])
 elif f=="prevday":
  day=rt[i]//86400000;ix=np.flatnonzero((rt[:i]//86400000)==day-1)
  if len(ix)!=24:return None
  hi=float(rh[ix].max());lo=float(rl[ix].min())
 elif f not in ("donchian","compress","squeeze"):raise RuntimeError(f"unknown family {f}")
 b=cfg["buf"]*a;buy=hi+b if al else None;sell=lo-b if ash else None
 return buy,sell,a

def entry(symbol,t,o,h,l,start,buy,sell):
 for j in range(start,min(start+M,len(t))):
  lh=buy is not None and h[j]>=buy;sh=sell is not None and l[j]<=sell
  if not (lh or sh):continue
  if lh and sh:
   side=dual_side(symbol,int(t[j]),float(buy),float(sell))
   if side not in ("long","short"):return {"status":side,"bar":j}
  else:side="long" if lh else "short"
  trig=float(buy if side=="long" else sell);fill=float(max(trig,o[j]) if side=="long" else min(trig,o[j]))
  return {"status":"fill","side":side,"bar":j,"trigger":trig,"fill":fill}
 return None

def trade(symbol,t,o,h,l,c,j,side,fill,atr,slatr,r):
 long=side=="long";risk=slatr*atr
 if not np.isfinite(risk) or risk<=0:return {"status":"bad_risk"}
 sl=fill-risk if long else fill+risk;tp=fill+r*risk if long else fill-r*risk
 if sl<=0 or tp<=0:return {"status":"bad_risk"}
 ep=h[j]>=tp if long else l[j]<=tp;es=l[j]<=sl if long else h[j]>=sl;xp=xb=reason=None
 if ep or es:
  rr=stop_entry_bar(symbol,int(t[j]),side,fill,tp,sl)
  if rr in ("data_gap","entry_mismatch"):return {"status":rr}
  if rr=="win":xp,xb,reason=tp,j,"TP"
  elif rr=="loss":xp,xb,reason=sl,j,"SL"
 end=min(j+MAXH+1,len(t))
 if xp is None:
  for k in range(j+1,end):
   ht=h[k]>=tp if long else l[k]<=tp;hs=l[k]<=sl if long else h[k]>=sl
   if not (ht or hs):continue
   if ht and hs:
    rr=established(symbol,int(t[k]),side,tp,sl)
    if rr in ("data_gap","exit_mismatch"):return {"status":rr}
    xp,xb,reason=(tp,k,"TP") if rr=="win" else (sl,k,"SL")
   elif ht:xp,xb,reason=tp,k,"TP"
   else:xp,xb,reason=sl,k,"SL"
   break
 if xp is None:xb=end-1;xp=float(c[xb]);reason="TIME"
 gr=(xp/fill-1) if long else (fill/xp-1);rp=risk/fill
 out={"status":"resolved","exit_bar":int(xb),"exit":float(xp),"reason":reason,"risk_pct":float(rp),"gross_return":float(gr),"gross_r":float(gr/rp)}
 for bp in (20,40):out[f"net{bp}_return"]=float(gr-bp/10000);out[f"net{bp}_r"]=float((gr-bp/10000)/rp)
 return out

def evaluate(symbol,t,o,h,l,c):
 rt,ro,rh,rl,rc,st=resample(t,o,h,l,c)
 if len(rt)<160:return [],defaultdict(int)
 atr,ema,bbw=features(rh,rl,rc);rows=[];cnt=defaultdict(int);free={x["name"]:-1 for x in CONFIGS}
 for i in range(120,len(rt)):
  start=int(st[i])
  for cfg in CONFIGS:
   name=cfg["name"]
   if start<free[name]:continue
   lv=levels(cfg,i,rt,rh,rl,rc,atr,ema,bbw)
   if lv is None:continue
   en=entry(symbol,t,o,h,l,start,*lv[:2])
   if en is None:continue
   if en["status"]!="fill":cnt[f'{name}:{en["status"]}']+=1;continue
   z=trade(symbol,t,o,h,l,c,en["bar"],en["side"],en["fill"],lv[2],cfg["sl"],cfg["r"])
   if z["status"]!="resolved":cnt[f'{name}:{z["status"]}']+=1;continue
   eb=en["bar"];xb=z["exit_bar"];free[name]=xb+1
   rows.append({"config":name,"group":cfg["group"],"family":cfg["family"],"symbol":symbol,"signal_time":int(rt[i]),"entry_time":int(t[eb]),"exit_time":int(t[xb]),"side":en["side"],"trigger":en["trigger"],"entry":en["fill"],"exit":z["exit"],"reason":z["reason"],"hold_min":int((t[xb]-t[eb])//MIN),"risk_pct":z["risk_pct"],"gross_return":z["gross_return"],"gross_r":z["gross_r"],"net20_return":z["net20_return"],"net20_r":z["net20_r"],"net40_return":z["net40_return"],"net40_r":z["net40_r"]})
 return rows,cnt

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);a=ap.parse_args()
 if a.shards<1 or a.shard<0 or a.shard>=a.shards:raise RuntimeError("bad shard")
 allf=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True))
 if not allf:raise RuntimeError("no data")
 fs=[p for i,p in enumerate(allf) if i%a.shards==a.shard];rows=[];cnt=defaultdict(int);started=time.time()
 print(f"RUN_START shard={a.shard}/{a.shards} files={len(fs)} total={len(allf)}",flush=True)
 for z,p in enumerate(fs,1):
  s=sym(p)
  if s=="BNXUSDT":cnt["excluded_BNXUSDT"]+=1;continue
  d=load(p)
  try:
   for aa,bb in segments(d[0]):
    if bb-aa<640:continue
    rr,cc=evaluate(s,*tuple(x[aa:bb] for x in d));rows.extend(rr)
    for k,v in cc.items():cnt[k]+=v
  finally:CACHE.clear()
  print(f"PROGRESS {z}/{len(fs)} {s} trades={len(rows)} elapsed_min={(time.time()-started)/60:.1f}",flush=True)
 cols=["config","group","family","symbol","signal_time","entry_time","exit_time","side","trigger","entry","exit","reason","hold_min","risk_pct","gross_return","gross_r","net20_return","net20_r","net40_return","net40_r"]
 pd.DataFrame(rows,columns=cols).to_csv(f"external_breakout_trades_shard_{a.shard}.csv.gz",index=False,compression="gzip")
 meta={"definition":{"source_data_run":SOURCE_DATA_RUN,"workflow_commit_sha":os.environ.get("GITHUB_SHA","local"),"tf":"1h","entry_ttl":"1h","max_hold":"24h","costs":"20/40bp round trip","fortune":"fixed 3R core; hidden trailing not guessed","blue":"six pre-registered proxy families; proprietary A-F unknown","chronology":"official Binance 1m for competing stops, entry-bar exits and TP/SL collisions; same-1m ambiguity conservative or excluded","position":"one position per symbol/config"},"configs":CONFIGS,"rows":len(rows),"counters":dict(cnt)}
 Path(f"external_breakout_meta_shard_{a.shard}.json").write_text(json.dumps(meta,indent=2))
 print(json.dumps({"rows":len(rows),"counters":dict(cnt)},indent=2))
if __name__=="__main__":main()
