import argparse,glob,json,math,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.external_breakout_replay as ex

BAR=900000; HOUR=3600000; DAY=86400000
MAJORS={"BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","LTCUSDT","BCHUSDT","LINKUSDT"}
FORTUNE=(
 {"name":"F_PD_R025","kind":"prevday","risk":.0025},
 {"name":"F_PD_R050","kind":"prevday","risk":.0050},
 {"name":"F_H4_R025","kind":"h4struct","risk":.0025},
 {"name":"F_REJECT_R025","kind":"reject","risk":.0025},
)
BLUE=(
 {"name":"B_PD","kind":"prevday"},
 {"name":"B_H4","kind":"h4struct"},
 {"name":"B_COMP","kind":"compress"},
 {"name":"B_SESSION","kind":"session"},
 {"name":"B_REJECT","kind":"reject"},
 {"name":"B_WEEK","kind":"week"},
)

def rs(t,o,h,l,c,m):
 b=t//(BAR*m);q=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(t)]
 st=q[:-1];en=q[1:];g=(en-st)==m;st=st[g];en=en[g]
 if len(st):
  span=BAR*m;ok=np.array([t[a]%span==0 and np.all(np.diff(t[a:z])==BAR) for a,z in zip(st,en)],bool);st=st[ok];en=en[ok]
 return t[st],o[st],np.array([h[a:z].max() for a,z in zip(st,en)]),np.array([l[a:z].min() for a,z in zip(st,en)]),c[en-1],st,en

def atr(h,l,c,n=14):
 prev=np.r_[np.nan,c[:-1]];tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)))
 return pd.Series(tr).ewm(alpha=1/n,adjust=False,min_periods=n).mean().to_numpy()

def h4_map(ht,hst,t15):
 # last fully CLOSED H4 bar at each 15m timestamp
 close=ht+4*HOUR
 return np.searchsorted(close,t15,side="right")-1

def prior_day_levels(rt,rh,rl,i):
 day=rt[i]//DAY;ix=np.flatnonzero((rt[:i]//DAY)==day-1)
 if len(ix)!=24:return None
 return float(rh[ix].max()),float(rl[ix].min())

def prior_week_levels(rt,rh,rl,i):
 day=rt[i]//DAY;ix=np.flatnonzero(((rt[:i]//DAY)>=day-7)&((rt[:i]//DAY)<day))
 if len(ix)<5*24:return None
 return float(rh[ix].max()),float(rl[ix].min())

def h4_levels(hh,hl,ha,k):
 if k<6 or not np.isfinite(ha[k]) or ha[k]<=0:return None
 return float(hh[k-5:k+1].max()),float(hl[k-5:k+1].min())

def rejection_levels(hh,hl,ha,k):
 if k<10 or not np.isfinite(ha[k]) or ha[k]<=0:return None
 idx=np.arange(k-9,k+1);H=hh[idx];L=hl[idx];a=float(ha[k]);bestH=None;bestL=None;dsH=9;dsL=9
 for x in range(len(idx)):
  for y in range(x+2,len(idx)):
   dh=abs(H[x]-H[y])/a;dl=abs(L[x]-L[y])/a
   if dh<dsH:dsH=dh;bestH=(H[x]+H[y])/2
   if dl<dsL:dsL=dl;bestL=(L[x]+L[y])/2
 up=float(bestH) if bestH is not None and dsH<=.25 else None
 dn=float(bestL) if bestL is not None and dsL<=.25 else None
 if up is None and dn is None:return None
 return up,dn

def session_levels(rt,rh,rl,i):
 # at 06:00 UTC use 00:00-05:59 H1 range
 dt=pd.Timestamp(int(rt[i]),unit="ms",tz="UTC")
 if dt.hour!=6:return None
 day=rt[i]//DAY;ix=np.flatnonzero(((rt[:i]//DAY)==day)&((rt[:i]%DAY)<6*HOUR))
 if len(ix)!=6:return None
 return float(rh[ix].max()),float(rl[ix].min())

def iter_minutes(symbol,start_ts,end_ts):
 ts=(start_ts//BAR)*BAR
 while ts<end_ts:
  d=ex.w1m(symbol,int(ts))
  if len(d)==2 and d[0]=="data_gap":yield ("gap",);return
  mt,mo,mh,ml=d
  for j in range(15):
   q=int(mt[j])
   if q<start_ts:continue
   if q>=end_ts:return
   yield q,float(mo[j]),float(mh[j]),float(ml[j])
  ts+=BAR

def time_close(t,c,end_ts):
 j=np.searchsorted(t,end_ts,side="right")-1
 return float(c[max(0,min(j,len(c)-1))])

def execute(symbol,t,c,start_ts,buy,sell,risk_pct,tpR,max_entry_h,max_hold_h,trail=False):
 entry=None
 for m in iter_minutes(symbol,start_ts,start_ts+max_entry_h*HOUR):
  if m[0]=="gap":return None
  q,oo,hh,ll=m;bh=buy is not None and hh>=buy;sh=sell is not None and ll<=sell
  if not (bh or sh):continue
  if bh and sh:return None
  side="long" if bh else "short";trig=buy if bh else sell
  fill=max(trig,oo) if side=="long" else min(trig,oo);entry=(q,side,float(fill),hh,ll);break
 if entry is None:return None
 ets,side,fill,eh,el=entry;R=fill*risk_pct
 sl=fill-R if side=="long" else fill+R;tp=fill+tpR*R if side=="long" else fill-tpR*R
 if sl<=0 or tp<=0:return None
 # Repo conservative contract: any exit touch in the entry minute is a loss.
 if (eh>=tp if side=="long" else el<=tp) or (el<=sl if side=="long" else eh>=sl):
  exit_ts=ets;exit_px=sl;reason="ENTRY_MIN_LOSS"
 else:
  stop=sl;mfe=0.0;exit_ts=None;exit_px=None;reason=None
  for m in iter_minutes(symbol,ets+60000,ets+max_hold_h*HOUR):
   if m[0]=="gap":return None
   q,oo,hh,ll=m
   hitS=ll<=stop if side=="long" else hh>=stop;hitT=hh>=tp if side=="long" else ll<=tp
   if hitS and hitT:exit_ts=q;exit_px=stop;reason="SL";break
   if hitS:exit_ts=q;exit_px=stop;reason="SL";break
   if hitT:exit_ts=q;exit_px=tp;reason="TP";break
   if trail:
    cur=((hh-fill)/R) if side=="long" else ((fill-ll)/R);mfe=max(mfe,cur)
    if mfe>=.5:
     be=fill+.1*R if side=="long" else fill-.1*R
     stop=max(stop,be) if side=="long" else min(stop,be)
    if mfe>=1.0:
     tr=(fill+(mfe-.5)*R) if side=="long" else (fill-(mfe-.5)*R)
     stop=max(stop,tr) if side=="long" else min(stop,tr)
  if exit_ts is None:
   exit_ts=ets+max_hold_h*HOUR;exit_px=time_close(t,c,exit_ts);reason="TIME"
 gross=(exit_px/fill-1) if side=="long" else (fill/exit_px-1)
 out={"entry_time":ets,"exit_time":int(exit_ts),"side":side,"entry":fill,"exit":float(exit_px),"reason":reason,"hold_min":int((exit_ts-ets)//60000),"risk_pct":risk_pct,"gross_return":gross,"gross_r":gross/risk_pct}
 for bp in (20,40):out[f"net{bp}_return"]=gross-bp/10000;out[f"net{bp}_r"]=(gross-bp/10000)/risk_pct
 return out

def eval_symbol(symbol,t,o,h,l,c):
 rt,ro,rh,rl,rc,st,en=rs(t,o,h,l,c,4);ht,ho,hh,hl,hc,hst,hen=rs(t,o,h,l,c,16);ha=atr(hh,hl,hc,14)
 if len(rt)<200 or len(ht)<60:return []
 mapk=h4_map(ht,hst,rt);rows=[];busy=defaultdict(lambda:-1)
 for i in range(25,len(rt)):
  dt=pd.Timestamp(int(rt[i]),unit="ms",tz="UTC")
  if dt.hour!=6:continue
  k=int(mapk[i]);pdv=prior_day_levels(rt,rh,rl,i);h4=h4_levels(hh,hl,ha,k);rej=rejection_levels(hh,hl,ha,k);ses=session_levels(rt,rh,rl,i);week=prior_week_levels(rt,rh,rl,i)
  # Fortune: one daily bracket, limited hours, tight fixed-percent risk, BE+tight trail.
  for cfg in FORTUNE:
   if rt[i]<busy[cfg["name"]]:continue
   lv={"prevday":pdv,"h4struct":h4,"reject":rej}.get(cfg["kind"])
   if not lv:continue
   up,dn=lv
   z=execute(symbol,t,c,int(rt[i]),up,dn,cfg["risk"],3.0,12,6,True)
   if z:
    busy[cfg["name"]]=z["exit_time"]+60000
    rows.append({"config":cfg["name"],"group":"FORTUNE_INFER","family":cfg["kind"],"symbol":symbol,**z})
  # Blue: six sparse daily breakout archetypes, no post-hoc tuning.
  blue_levels={"prevday":pdv,"h4struct":h4,"compress":h4,"session":ses,"reject":rej,"week":week}
  for cfg in BLUE:
   if rt[i]<busy[cfg["name"]]:continue
   lv=blue_levels[cfg["kind"]]
   if not lv:continue
   up,dn=lv
   if cfg["kind"]=="compress" and h4 and k>=5:
    if (h4[0]-h4[1])>2.0*ha[k]:continue
   z=execute(symbol,t,c,int(rt[i]),up,dn,.0075,2.0,18,12,False)
   if z:
    busy[cfg["name"]]=z["exit_time"]+60000
    rows.append({"config":cfg["name"],"group":"BLUE_INFER","family":cfg["kind"],"symbol":symbol,**z})
 return rows

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);a=ap.parse_args()
 files=[p for p in sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True)) if ex.sym(p) in MAJORS]
 files=[p for j,p in enumerate(files) if j%a.shards==a.shard];rows=[];start=time.time()
 print(f"START shard={a.shard}/{a.shards} majors={len(files)}",flush=True)
 for z,p in enumerate(files,1):
  s=ex.sym(p);d=ex.load(p)
  try:
   for aa,bb in ex.segments(d[0]):
    if bb-aa<1000:continue
    rows.extend(eval_symbol(s,*tuple(x[aa:bb] for x in d)))
  finally:ex.CACHE.clear()
  print(f"PROGRESS {z}/{len(files)} {s} rows={len(rows)} min={(time.time()-start)/60:.1f}",flush=True)
 pd.DataFrame(rows).to_csv(f"fortune_blue_infer_{a.shard}.csv.gz",index=False,compression="gzip")
 Path(f"fortune_blue_infer_meta_{a.shard}.json").write_text(json.dumps({"rows":len(rows),"symbols":sorted(MAJORS),"fortune":FORTUNE,"blue":BLUE,"notes":"Hypothesis-driven reconstruction from public Fortune/Blue descriptions. Uses official Binance 1m for full execution. Not exact vendor code and not a pristine untouched OOS after prior simple-proxy review."},indent=2))
 print("DONE",len(rows))
if __name__=="__main__":main()
