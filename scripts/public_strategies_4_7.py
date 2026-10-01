import argparse,glob,json,math,os,sys,time
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.external_breakout_replay as ex

BAR=900000; HOUR=3600000; DAY=86400000
SOURCE_DATA_RUN="36095439671"

def rs(t,o,h,l,c,m):
 b=t//(BAR*m);q=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(t)]
 st=q[:-1];en=q[1:];g=(en-st)==m;st=st[g];en=en[g]
 if len(st):
  span=BAR*m;ok=np.array([t[a]%span==0 and np.all(np.diff(t[a:z])==BAR) for a,z in zip(st,en)],bool);st=st[ok];en=en[ok]
 return t[st],o[st],np.array([h[a:z].max() for a,z in zip(st,en)]),np.array([l[a:z].min() for a,z in zip(st,en)]),c[en-1],st,en

def atr(h,l,c,n=14):
 prev=np.r_[np.nan,c[:-1]];tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
 return pd.Series(tr).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()

def ema(c,n):return pd.Series(c).ewm(span=n,adjust=False,min_periods=n).mean().to_numpy()

def adx_pack(h,l,c,n=14):
 prev=np.r_[np.nan,c[:-1]]
 tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
 up=np.r_[np.nan,np.diff(h)];dn=np.r_[np.nan,-np.diff(l)]
 pdm=np.where((up>dn)&(up>0),up,0.0);mdm=np.where((dn>up)&(dn>0),dn,0.0)
 atrv=pd.Series(tr).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()
 psm=pd.Series(pdm).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()
 msm=pd.Series(mdm).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()
 pdi=100*psm/atrv;mdi=100*msm/atrv;dx=100*np.abs(pdi-mdi)/(pdi+mdi)
 adx=pd.Series(dx).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()
 return atrv,pdi,mdi,adx

def first_exit(symbol,t,h,l,start,side,tp,sl,end=None):
 long=side=="long";end=len(t) if end is None else min(end,len(t))
 for k in range(start,end):
  ht=h[k]>=tp if long else l[k]<=tp;hs=l[k]<=sl if long else h[k]>=sl
  if not (ht or hs):continue
  if ht and hs:
   rr=ex.established(symbol,int(t[k]),side,tp,sl)
   if rr in ("data_gap","exit_mismatch"):return rr,None,None
   return ("win" if rr=="win" else "loss"),k,(tp if rr=="win" else sl)
  return ("win" if ht else "loss"),k,(tp if ht else sl)
 return "timeout",end-1,None

def fixed_trade(symbol,t,o,h,l,c,start,side,sl,tp,maxbars=384):
 fill=float(o[start]);end=min(start+maxbars,len(t));status,k,x=first_exit(symbol,t,h,l,start,side,tp,sl,end)
 if status in ("data_gap","exit_mismatch"):return {"status":status}
 if status=="timeout":x=float(c[k]);reason="TIME"
 else:reason="TP" if status=="win" else "SL"
 gross=(x/fill-1) if side=="long" else (fill/x-1);risk=abs(fill-sl)/fill
 if risk<=0:return {"status":"bad_risk"}
 z={"status":"resolved","exit_bar":int(k),"exit":float(x),"reason":reason,"gross_return":float(gross),"gross_r":float(gross/risk),"risk_pct":float(risk)}
 for bp in (20,40):z[f"net{bp}_return"]=float(gross-bp/10000);z[f"net{bp}_r"]=float((gross-bp/10000)/risk)
 return z

def row(name,group,family,symbol,t,o,start,side,z):
 return {"config":name,"group":group,"family":family,"symbol":symbol,"entry_time":int(t[start]),"exit_time":int(t[z["exit_bar"]]),"side":side,"entry":float(o[start]),"exit":z["exit"],"reason":z["reason"],"hold_min":int((t[z["exit_bar"]]-t[start])//60000),"risk_pct":z["risk_pct"],"gross_return":z["gross_return"],"gross_r":z["gross_r"],"net20_return":z["net20_return"],"net20_r":z["net20_r"],"net40_return":z["net40_return"],"net40_r":z["net40_r"]}

def run_adx(symbol,t,o,h,l,c):
 rt,ro,rh,rl,rc,st,en=rs(t,o,h,l,c,4);a,pdi,mdi,adx=adx_pack(rh,rl,rc,14);e=ema(rc,20);out=[];free=-1
 for i in range(30,len(rt)):
  start=int(st[i])
  if start<free:continue
  j=i-1;k=i-2
  vals=[a[j],a[k],e[j],e[k],adx[j],adx[k],pdi[j],mdi[j]]
  if not all(np.isfinite(x) for x in vals) or a[j]<=0 or a[k]<=0:continue
  d0=abs(rc[k]-e[k])/a[k];d1=abs(rc[j]-e[j])/a[j]
  if not (adx[j]>25 and adx[j]>adx[k] and d0>=.5 and d1<.5):continue
  if pdi[j]>mdi[j]:side="long"
  elif mdi[j]>pdi[j]:side="short"
  else:continue
  fill=float(o[start]);risk=1.5*a[j];sl=fill-risk if side=="long" else fill+risk;tp=fill+2*risk if side=="long" else fill-2*risk
  if sl<=0 or tp<=0:continue
  z=fixed_trade(symbol,t,o,h,l,c,start,side,sl,tp,maxbars=4*24*30)
  if z["status"]!="resolved":continue
  free=z["exit_bar"]+1;out.append(row("S5_ADX_PULLBACK_EXACT","S5_ADX","adx_pullback",symbol,t,o,start,side,z))
 return out

def run_xau_bar(symbol,t,o,h,l,c):
 rt,ro,rh,rl,rc,st,en=rs(t,o,h,l,c,4);a=atr(rh,rl,rc,14);out=[];free={"ATR2":-1,"BAR2":-1}
 for i in range(16,len(rt)):
  j=i-1;k=i-2
  if not np.isfinite(a[j]) or a[j]<=0:continue
  r1=rh[j]-rl[j];r0=rh[k]-rl[k]
  if r0<=0 or r1<1.1*r0:continue
  side="long" if rc[j]>ro[j] else ("short" if rc[j]<ro[j] else None)
  if side is None:continue
  start=int(st[i]);fill=float(o[start])
  for tag in ("ATR2","BAR2"):
   if start<free[tag]:continue
   if tag=="ATR2":risk=a[j];sl=fill-risk if side=="long" else fill+risk
   else:
    sl=float(rl[j] if side=="long" else rh[j]);risk=abs(fill-sl)
    if (side=="long" and sl>=fill) or (side=="short" and sl<=fill):continue
   if risk<=0:continue
   tp=fill+2*risk if side=="long" else fill-2*risk
   if sl<=0 or tp<=0:continue
   z=fixed_trade(symbol,t,o,h,l,c,start,side,sl,tp,maxbars=4*24*7)
   if z["status"]!="resolved":continue
   free[tag]=z["exit_bar"]+1;out.append(row(f"S6_XAU_BAR_{tag}","S6_XAU_BAR","bar_expansion",symbol,t,o,start,side,z))
 return out

def nexus_trade(symbol,t,o,h,l,c,start,side,sl,tp1,tp2,maxbars=4*24*7):
 fill=float(o[start]);long=side=="long";end=min(start+maxbars,len(t));state=0;real_r=0.0;risk=abs(fill-sl);exitbar=end-1;exitprice=float(c[exitbar]);reason="TIME"
 if risk<=0:return {"status":"bad_risk"}
 for k in range(start,end):
  slev=sl if state==0 else fill
  h1=h[k]>=tp1 if long else l[k]<=tp1;h2=h[k]>=tp2 if long else l[k]<=tp2;hs=l[k]<=slev if long else h[k]>=slev
  if state==0:
   if hs and h1:
    rr=ex.established(symbol,int(t[k]),side,tp1,slev)
    if rr in ("data_gap","exit_mismatch"):return {"status":rr}
    if rr=="loss":exitbar=k;exitprice=slev;reason="SL";real_r=-1.0;break
    state=1;real_r=1.4
    continue
   if hs:exitbar=k;exitprice=slev;reason="SL";real_r=-1.0;break
   if h1:state=1;real_r=1.4;continue
  else:
   if hs and h2:
    rr=ex.established(symbol,int(t[k]),side,tp2,fill)
    if rr in ("data_gap","exit_mismatch"):return {"status":rr}
    if rr=="win":real_r=2.3;exitprice=tp2;reason="TP2"
    else:exitprice=fill;reason="TP1_BE"
    exitbar=k;break
   if h2:real_r=2.3;exitprice=tp2;reason="TP2";exitbar=k;break
   if hs:exitprice=fill;reason="TP1_BE";exitbar=k;break
 else:
  gross=(c[exitbar]/fill-1) if long else (fill/c[exitbar]-1);real_r=(gross/(risk/fill)) if state==0 else 1.4+0.3*max(0.0,gross/(risk/fill))
 if reason in ("SL","TP2","TP1_BE"):gross=real_r*(risk/fill)
 else:gross=real_r*(risk/fill)
 z={"status":"resolved","exit_bar":int(exitbar),"exit":float(exitprice),"reason":reason,"risk_pct":float(risk/fill),"gross_r":float(real_r),"gross_return":float(gross)}
 for bp in (20,40):z[f"net{bp}_return"]=float(gross-bp/10000);z[f"net{bp}_r"]=float((gross-bp/10000)/(risk/fill))
 return z

def run_nexus(symbol,t,o,h,l,c):
 ht,ho,hh,hl,hc,hst,hen=rs(t,o,h,l,c,16);fast=ema(hc,11);slow=ema(hc,47);a=atr(hh,hl,hc,14);out=[];free={"LONG":-1,"BOTH":-1}
 if len(ht)<60:return out
 idx=np.searchsorted(ht,t,side="right")-1
 for b in range(1,len(t)):
  hi=idx[b]
  if hi<48:continue
  # use last CLOSED H4 bar only; if t[b] is inside/open of H4, hi may be current bucket absent from ht; ht contains completed exact buckets from source,
  # so require its close time <= current 15m open.
  while hi>=0 and ht[hi]+4*HOUR>t[b]:hi-=1
  if hi<48 or not all(np.isfinite(x) for x in (fast[hi],slow[hi],a[hi])) or a[hi]<=0:continue
  # 15m bar b-1 is the closed pullback/confirmation bar, enter at b open.
  j=b-1;dist=abs(c[j]-slow[hi])
  if dist>a[hi]:continue
  long_sig=fast[hi]>slow[hi] and c[j]>fast[hi]
  short_sig=fast[hi]<slow[hi] and c[j]<fast[hi]
  for mode in ("LONG","BOTH"):
   if b<free[mode]:continue
   if long_sig:side="long"
   elif mode=="BOTH" and short_sig:side="short"
   else:continue
   fill=float(o[b]);sl=(slow[hi]-a[hi]) if side=="long" else (slow[hi]+a[hi])
   if (side=="long" and sl>=fill) or (side=="short" and sl<=fill) or sl<=0:continue
   risk=abs(fill-sl);tp1=fill+2*risk if side=="long" else fill-2*risk;tp2=fill+3*risk if side=="long" else fill-3*risk
   if tp1<=0 or tp2<=0:continue
   z=nexus_trade(symbol,t,o,h,l,c,b,side,sl,tp1,tp2)
   if z["status"]!="resolved":continue
   free[mode]=z["exit_bar"]+1;out.append(row(f"S7_NEXUS_{mode}_PROXY","S7_NEXUS","h4_ema_atr_pullback",symbol,t,o,b,side,z))
 return out

def run_surf(symbol,t,o,h,l,c):
 rt,ro,rh,rl,rc,st,en=rs(t,o,h,l,c,4);a=atr(rh,rl,rc,14);anchor=ema(rc,50);out=[]
 for mult in (.75,1.0):
  name=f"S4_SURFBOT_ATR{mult:g}";busy=-1;i=55
  while i<len(rt):
   start=int(st[i])
   if start<busy or not np.isfinite(a[i-1]) or not np.isfinite(anchor[i-1]) or a[i-1]<=0:i+=1;continue
   anc=float(anchor[i-1]);av=float(a[i-1]);levels=[mult*av*x for x in (1,2,3)]
   side=None;first=None
   # activate episode if current H1 reaches first layer away from anchor
   if rl[i]<=anc-levels[0]:side="long";first=anc-levels[0]
   elif rh[i]>=anc+levels[0]:side="short";first=anc+levels[0]
   else:i+=1;continue
   entries=[];entrybars=[];end_i=min(i+24*7,len(rt));resolved=False;exit_px=None;exit_b=None;reason=None
   for q in range(i,end_i):
    bs=int(st[q]);be=int(en[q])
    # add equal-size layers when reached; max 3
    for d in levels[len(entries):]:
     lv=anc-d if side=="long" else anc+d
     touched=(l[bs:be].min()<=lv) if side=="long" else (h[bs:be].max()>=lv)
     if touched:
      entries.append(float(lv));entrybars.append(bs)
     else:break
    if not entries:continue
    # target is moving anchor approximation frozen per episode for causal simplicity
    target=anc
    hit=(h[bs:be].max()>=target) if side=="long" else (l[bs:be].min()<=target)
    safety=anc-5*av if side=="long" else anc+5*av
    bad=(l[bs:be].min()<=safety) if side=="long" else (h[bs:be].max()>=safety)
    if hit and bad:
     # conservative: safety loss when parent H1 cannot order target vs safety
     exit_px=safety;reason="SAFETY";exit_b=bs;resolved=True;break
    if bad:exit_px=safety;reason="SAFETY";exit_b=bs;resolved=True;break
    if hit:exit_px=target;reason="ANCHOR";exit_b=bs;resolved=True;break
   if not resolved:
    exit_b=int(en[end_i-1]-1);exit_px=float(c[exit_b]);reason="TIME"
   avg=float(np.mean(entries));gross=(exit_px/avg-1) if side=="long" else (avg/exit_px-1)
   # diagnostic risk unit = 5 ATR from anchor relative to average entry
   risk=max(abs(avg-(anc-5*av if side=="long" else anc+5*av))/avg,1e-12)
   z={"exit_bar":exit_b,"exit":float(exit_px),"reason":reason,"risk_pct":risk,"gross_return":gross,"gross_r":gross/risk}
   for bp in (20,40):z[f"net{bp}_return"]=gross-bp/10000;z[f"net{bp}_r"]=(gross-bp/10000)/risk
   r=row(name,"S4_SURFBOT","grid_mean_reversion",symbol,t,np.where(np.arange(len(o))==entrybars[0],avg,o),entrybars[0],side,z)
   r["layers"]=len(entries);r["anchor"]=anc;r["atr"]=av
   out.append(r);busy=exit_b+1;i=q+1
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);a=ap.parse_args()
 allf=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True))
 if not allf:raise RuntimeError("no data")
 fs=[p for i,p in enumerate(allf) if i%a.shards==a.shard];rows=[];started=time.time();cnt=defaultdict(int)
 for z,p in enumerate(fs,1):
  s=ex.sym(p)
  if s=="BNXUSDT":continue
  d=ex.load(p)
  try:
   for aa,bb in ex.segments(d[0]):
    if bb-aa<800:continue
    seg=tuple(x[aa:bb] for x in d)
    for fn in (run_surf,run_adx,run_xau_bar,run_nexus):
     try:rows.extend(fn(s,*seg))
     except Exception as e:cnt[f"{fn.__name__}_error"]+=1;raise
  finally:ex.CACHE.clear()
  print(f"PROGRESS {z}/{len(fs)} {s} rows={len(rows)} elapsed_min={(time.time()-started)/60:.1f}",flush=True)
 pd.DataFrame(rows).to_csv(f"public_strategies_4_7_shard_{a.shard}.csv.gz",index=False,compression="gzip")
 meta={"source_data_run":SOURCE_DATA_RUN,"workflow_commit_sha":os.environ.get("GITHUB_SHA","local"),"rows":len(rows),"notes":{"S4":"SurfBot crypto structural proxy; FX carry unavailable; EMA50 anchor, ATR grid 3 layers, 5ATR safety, 7d max","S5":"ADX Trend Pullback public source reconstruction: H1 ADX14>25 rising, EMA20 pullback 0.5ATR cross, DI direction, 1.5ATR SL, 2R TP","S6":"XAU Bar Break public signal reconstruction; H1 range expansion 1.1x, candle direction; exits pre-registered proxies ATR or prior-bar SL, 2R","S7":"Nexus proxy: H4 EMA11/47 + ATR14, 15m confirmation proxy for proprietary M1 trigger, 70% 2R + 30% 3R with BE"}}
 Path(f"public_strategies_4_7_meta_{a.shard}.json").write_text(json.dumps(meta,indent=2))
 print(json.dumps({"rows":len(rows),"counters":dict(cnt)},indent=2))
if __name__=="__main__":main()
