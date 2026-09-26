#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
T=pd.read_csv(a.trades,parse_dates=["signal_time","entry_time","exit_time"]);T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy();T["year"]=T.entry_time.dt.year
W=pd.read_csv(a.wf)[["test_year","chosen_threshold"]].drop_duplicates();W=W[(W.test_year>=2023)&(W.test_year<=2026)]
fs=[];rs=[]
for fn in sorted(glob.glob(a.input+"/**/*.csv.gz",recursive=True)):
 sym=Path(fn).name.replace(".csv.gz","");d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms";d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
 for c in ["high","low","close"]:d[c]=pd.to_numeric(d[c],errors="coerce")
 d=d.dropna(subset=["dt","high","low","close"]).sort_values("dt").drop_duplicates("dt");x=d.set_index("dt").resample("4h",label="left",closed="left").agg(high=("high","max"),low=("low","min"),close=("close","last"),bars=("close","count"));x=x[x.bars==16]
 r=x.close.pct_change();rs.append(r.rename(sym));rl=x.low.shift(1).rolling(320).min();atr=(x.high-x.low).shift(1).rolling(14).mean();q=pd.DataFrame({"signal_time":x.index,"break_atr":(rl-x.close)/atr});q["symbol"]=sym;fs.append(q)
F=pd.concat(fs,ignore_index=True);T=T.merge(F,on=["symbol","signal_time"],how="left");assert T.break_atr.notna().mean()>.99
R=pd.concat(rs,axis=1).sort_index();bd=(R<0).mean(axis=1);bd2=(R<-.02).mean(axis=1);cssd=R.std(axis=1);M=pd.DataFrame({"breadth2":bd2,"bacc":bd-bd.shift(1),"dacc":cssd-cssd.shift(1)})
T["signals"]=T.groupby("entry_time").symbol.transform("nunique");T["ft"]=T.entry_time-pd.Timedelta(hours=4);T=T.merge(M,left_on="ft",right_index=True,how="left")
# walk-forward event threshold is preselected from prior years; rank only with signal-time break_atr
Z=[]
for y,th in W.itertuples(index=False):
 q=T[(T.year==y)&(T.signals>=th)].copy();q["wf_threshold"]=th;Z.append(q)
Z=pd.concat(Z);assert len(Z)>0 and Z[["breadth2","bacc","dacc"]].notna().all(axis=1).mean()>.98
# Risk state thresholds are learned from PRIOR years only; no current-year fitting.
selected=[]
for y in sorted(Z.year.unique()):
 train=T[T.year<y];test=Z[Z.year==y]
 if len(train)==0:continue
 caps={c:train[c].dropna().quantile(.80) for c in ["breadth2","bacc","dacc"]}
 for b in [5,10]:
  q=test.sort_values(["entry_time","break_atr","symbol"],ascending=[True,False,True]).groupby("entry_time",group_keys=False).head(b).copy()
  extreme=(q[["breadth2","bacc","dacc"]]>pd.Series(caps)).sum(axis=1);q["risk_mult"]=np.where(extreme>=2,.25,np.where(extreme>=1,.5,1.0));q["basket"]=b;selected.append(q)
S=pd.concat(selected).sort_values(["entry_time","symbol"])
def sim(q,use_scale):
 eq=1.;peak=1.;mdd=0.;active=[];n=0
 for r in q.itertuples():
  done=[p for p in active if p[0]<=r.entry_time]
  for ex,pnl in sorted(done):eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
  active=[p for p in active if p[0]>r.entry_time]
  if len(active)>=10:continue
  mult=r.risk_mult if use_scale else 1.;risk=.0025*mult
  active.append((r.exit_time,eq*risk*r.r_net));n+=1
 for ex,pnl in sorted(active):eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
 return n,eq,eq-1,mdd
rows=[]
for b in [5,10]:
 q=S[S.basket==b]
 for scaled in [False,True]:
  n,eq,ret,mdd=sim(q,scaled);ev=q.groupby("entry_time").r_net.mean();gp=ev[ev>0].sum();gl=-ev[ev<0].sum()
  rows.append(dict(basket=b,risk_mode="4f_scaled" if scaled else "fixed",events=q.entry_time.nunique(),candidate_trades=len(q),accepted=n,event_pf=gp/gl,avg_event_r=ev.mean(),final_equity=eq,total_return=ret,mdd_pct=mdd,return_over_mdd=ret/mdd if mdd else np.nan))
# fixed-risk sensitivity using identical selector/order logic
def sim_rf(q,rf):
 eq=1.;peak=1.;mdd=0.;active=[];n=0
 for r in q.itertuples():
  done=[p for p in active if p[0]<=r.entry_time]
  for ex,pnl in sorted(done):eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
  active=[p for p in active if p[0]>r.entry_time]
  if len(active)>=10:continue
  active.append((r.exit_time,eq*rf*r.r_net));n+=1
 for ex,pnl in sorted(active):eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
 return n,eq,eq-1,mdd
for rf in [.005,.0075,.01,.0125,.015,.02,.025,.03]:
 n,eq,ret,mdd=sim_rf(q,rf)
 rows.append(dict(basket=b,risk_mode=f"fixed_{rf:.4f}",events=q.entry_time.nunique(),candidate_trades=len(q),accepted=n,event_pf=gp/gl,avg_event_r=ev.mean(),final_equity=eq,total_return=ret,mdd_pct=mdd,return_over_mdd=ret/mdd if mdd else np.nan))
pd.DataFrame(rows).to_csv(O/"portfolio_compare.csv",index=False)
ann=S.groupby(["year","basket"]).agg(events=("entry_time","nunique"),trades=("symbol","size"),avg_r=("r_net","mean"),avg_risk_mult=("risk_mult","mean")).reset_index();ann.to_csv(O/"annual.csv",index=False)
pd.DataFrame([dict(core_rows=len(T),feature_match=T.break_atr.notna().mean(),selected_events=S.entry_time.nunique(),years=",".join(map(str,sorted(S.year.unique()))))]).to_csv(O/"sanity.csv",index=False)
print(pd.read_csv(O/"sanity.csv").to_string(index=False));print(pd.read_csv(O/"portfolio_compare.csv").to_string(index=False));print(ann.to_string(index=False))
