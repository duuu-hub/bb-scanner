#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--trades",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
T=pd.read_csv(a.trades,parse_dates=["signal_time","entry_time","exit_time"]);T=T[T.side=="SHORT"].copy();T["year"]=T.entry_time.dt.year
# reconstruct pre-entry break strength and 4h market dispersion exactly from 15m source
fs=[];rs=[];px={}
for fn in sorted(glob.glob(a.input+"/**/*.csv.gz",recursive=True)):
 sym=Path(fn).name.replace(".csv.gz","");d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms";d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
 for c in ["high","low","close"]:d[c]=pd.to_numeric(d[c],errors="coerce")
 d=d.dropna(subset=["dt","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt");px[sym]=d.close
 x=d.resample("4h",label="left",closed="left").agg(high=("high","max"),low=("low","min"),close=("close","last"),bars=("close","count"));x=x[x.bars==16]
 r=x.close.pct_change();rs.append(r.rename(sym));rl=x.low.shift(1).rolling(320).min();atr=(x.high-x.low).shift(1).rolling(14).mean()
 q=pd.DataFrame({"signal_time":x.index,"break_atr":(rl-x.close)/atr});q["symbol"]=sym;fs.append(q)
F=pd.concat(fs,ignore_index=True);T=T.merge(F,on=["symbol","signal_time"],how="left");assert T.break_atr.notna().mean()>.99
R=pd.concat(rs,axis=1).sort_index();bd=(R<0).mean(axis=1);bd2=(R<-.02).mean(axis=1);cssd=R.std(axis=1);M=pd.DataFrame({"breadth2":bd2,"bacc":bd-bd.shift(1),"dacc":cssd-cssd.shift(1)})
T["signals"]=T.groupby("entry_time").symbol.transform("nunique");T["ft"]=T.entry_time-pd.Timedelta(hours=4);T=T.merge(M,left_on="ft",right_index=True,how="left")
# Freeze old selector family: expanding prior-year threshold grid [51..101], chosen by prior-year event mean R.
thresholds=[51,61,71,81,91,101]; W=[]
for y in sorted(T.year.unique()):
 if y<2023:continue
 train=T[T.year<y];ev=train.groupby("entry_time").agg(signals=("symbol","nunique"),event_r=("r_net","mean")).reset_index();cand=[]
 for th in thresholds:
  z=ev[ev.signals>=th];cand.append((z.event_r.mean() if len(z)>=3 else -np.inf,th,len(z)))
 _,th,n=max(cand);W.append((y,th,n))
# Apply frozen break_strong Top10. No refit on test year.
sel=[]
for y,th,n in W:
 q=T[(T.year==y)&(T.signals>=th)].copy();q["wf_threshold"]=th
 q=q.sort_values(["entry_time","break_atr","symbol"],ascending=[True,False,True]).groupby("entry_time",group_keys=False).head(10);sel.append(q)
S=pd.concat(sel).sort_values(["entry_time","symbol"]);S.to_csv(O/"selected_candidates.csv",index=False)
# event stats before slot admission
ev=S.groupby("entry_time").r_net.mean();gp=ev[ev>0].sum();gl=-ev[ev<0].sum()
# exact actual-exit slot admission + realized and 15m MTM
def sim(rf):
 cash=1.;active=[];acc=[];peak=1.;rm=0.
 for r in S.itertuples():
  done=[p for p in active if p["exit_time"]<=r.entry_time]
  for p in sorted(done,key=lambda x:x["exit_time"]):
   cash+=p["stake"]*p["r_net"];peak=max(peak,cash);rm=max(rm,(peak-cash)/peak)
  active=[p for p in active if p["exit_time"]>r.entry_time]
  if len(active)>=10:continue
  p=dict(pid=len(acc),symbol=r.symbol,entry_time=r.entry_time,exit_time=r.exit_time,entry=r.entry,r_net=r.r_net,stake=cash*rf)
  active.append(p);acc.append(p)
 for p in sorted(active,key=lambda x:x["exit_time"]):
  cash+=p["stake"]*p["r_net"];peak=max(peak,cash);rm=max(rm,(peak-cash)/peak)
 # chronological MTM on accepted only
 cash2=1.;op=[];pm=1.;mm=0.;be={};bx={}
 for p in acc:be.setdefault(int(p["entry_time"].value),[]).append(p);bx.setdefault(int(p["exit_time"].value),[]).append(p)
 keys=set(be)|set(bx)
 for p in acc:
  ser=px[p["symbol"]];keys.update(int(t.value) for t in ser.index if p["entry_time"]<=t<=p["exit_time"])
 for k in sorted(keys):
  t=pd.Timestamp(k,tz="UTC");old={p["pid"] for p in op}
  for p in bx.get(k,[]):
   if p["pid"] in old:
    i=next(i for i,x in enumerate(op) if x["pid"]==p["pid"]);cash2+=p["stake"]*p["r_net"];op.pop(i)
  op.extend(be.get(k,[]))
  for p in bx.get(k,[]):
   if p["pid"] not in old:
    i=next(i for i,x in enumerate(op) if x["pid"]==p["pid"]);cash2+=p["stake"]*p["r_net"];op.pop(i)
  eq=cash2
  for p in op:
   ser=px[p["symbol"]];z=ser.loc[:t];mark=float(z.iloc[-1]) if len(z) else p["entry"]
   # infer R-distance from realized TP/SL economics: raw engine stop distance is ATR, but reconstruct from fee-adjusted terminal is unsafe.
   # Recompute exact ATR at signal from feature source below via stored riskdist.
   rd=p["riskdist"];mr=max((p["entry"]-mark)/rd,-1.0);eq+=p["stake"]*mr
  pm=max(pm,eq);mm=max(mm,(pm-eq)/pm)
 assert abs(cash2-cash)<1e-8
 return acc,cash,rm,mm
# add exact risk distance from raw trade economics/source: recompute per symbol signal-time prior14 4H range
rdmap={}
for fn in sorted(glob.glob(a.input+"/**/*.csv.gz",recursive=True)):
 sym=Path(fn).name.replace(".csv.gz","");need=S[S.symbol==sym]
 if need.empty:continue
 d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms";d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
 for c in ["high","low"]:d[c]=pd.to_numeric(d[c],errors="coerce")
 x=d.dropna(subset=["dt","high","low"]).set_index("dt").sort_index().resample("4h",label="left",closed="left").agg(high=("high","max"),low=("low","min"),bars=("high","count"));x=x[x.bars==16];atr=(x.high-x.low).shift(1).rolling(14).mean()
 for r in need.itertuples():rdmap[(sym,r.entry_time)]=float(atr.loc[r.signal_time])
# monkey enrich candidates before sim
S["riskdist"]=[rdmap[(r.symbol,r.entry_time)] for r in S.itertuples()]
# redefine itertuples payload riskdist copied into p by local wrapper via temporary patch: inline simulation below
rows=[]
for rf in [.0025,.005,.0075,.01,.0125,.015,.02,.025,.03]:
 cash=1.;active=[];acc=[];peak=1.;rm=0.
 for r in S.itertuples():
  done=[p for p in active if p["exit_time"]<=r.entry_time]
  for p in sorted(done,key=lambda x:x["exit_time"]):cash+=p["stake"]*p["r_net"];peak=max(peak,cash);rm=max(rm,(peak-cash)/peak)
  active=[p for p in active if p["exit_time"]>r.entry_time]
  if len(active)>=10:continue
  p=dict(pid=len(acc),symbol=r.symbol,entry_time=r.entry_time,exit_time=r.exit_time,entry=r.entry,r_net=r.r_net,stake=cash*rf,riskdist=r.riskdist);active.append(p);acc.append(p)
 for p in sorted(active,key=lambda x:x["exit_time"]):cash+=p["stake"]*p["r_net"];peak=max(peak,cash);rm=max(rm,(peak-cash)/peak)
 cash2=1.;op=[];pm=1.;mm=0.;be={};bx={}
 for p in acc:be.setdefault(int(p["entry_time"].value),[]).append(p);bx.setdefault(int(p["exit_time"].value),[]).append(p)
 keys=set(be)|set(bx)
 for p in acc:
  ser=px[p["symbol"]];keys.update(int(t.value) for t in ser.index if p["entry_time"]<=t<=p["exit_time"])
 for k in sorted(keys):
  t=pd.Timestamp(k,tz="UTC");old={p["pid"] for p in op}
  for p in bx.get(k,[]):
   if p["pid"] in old:i=next(i for i,x in enumerate(op) if x["pid"]==p["pid"]);cash2+=p["stake"]*p["r_net"];op.pop(i)
  op.extend(be.get(k,[]))
  for p in bx.get(k,[]):
   if p["pid"] not in old:i=next(i for i,x in enumerate(op) if x["pid"]==p["pid"]);cash2+=p["stake"]*p["r_net"];op.pop(i)
  eq=cash2
  for p in op:
   z=px[p["symbol"]].loc[:t];mark=float(z.iloc[-1]) if len(z) else p["entry"];mr=max((p["entry"]-mark)/p["riskdist"],-1.0);eq+=p["stake"]*mr
  pm=max(pm,eq);mm=max(mm,(pm-eq)/pm)
 assert abs(cash2-cash)<1e-8
 rows.append(dict(risk=rf,candidate_events=S.entry_time.nunique(),candidate_trades=len(S),accepted=len(acc),final_equity=cash,total_return=cash-1,realized_mdd=rm,mtm_mdd=mm))
 if rf==.01:pd.DataFrame(acc).to_csv(O/"accepted_risk1.csv",index=False)
pd.DataFrame(rows).to_csv(O/"portfolio.csv",index=False);pd.DataFrame(W,columns=["year","threshold","train_events"]).to_csv(O/"wf_thresholds.csv",index=False)
print("WF",W);print("EVENT",len(ev),gp/gl if gl else np.nan,ev.mean());print(pd.DataFrame(rows).to_string(index=False))
