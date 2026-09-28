#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import numpy as np
import pandas as pd
START=pd.Timestamp("2023-01-01",tz="UTC");END=pd.Timestamp("2026-09-01",tz="UTC");THS=[51,61,71,81,91,101]
def pf(v):
 v=np.asarray(v,float);gp=v[v>0].sum();gl=-v[v<0].sum();return gp/gl if gl>0 else np.inf
def sim(S,cmap,sel,rf=.02,rng=None):
 cash=1.;peak=1.;mdd=0.;active=[];acc=[]
 for et,g in S.groupby("entry_time",sort=True):
  done=[p for p in active if p["exit_time"]<=et]
  for xt in sorted({p["exit_time"] for p in done}):
   b=[p for p in done if p["exit_time"]==xt];cash+=sum(p["stake"]*p["r"] for p in b);peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
  active=[p for p in active if p["exit_time"]>et]
  if int(cmap.get(et.floor("D"),0)):continue
  free=5-len(active)
  if free<=0:continue
  if sel=="neutral":q=g.sort_values("symbol")
  elif sel=="break":q=g.sort_values(["break_atr","symbol"],ascending=[False,True])
  elif sel=="liquidity":q=g.sort_values(["liq24","symbol"],ascending=[False,True],na_position="last")
  elif sel=="random":q=g.iloc[rng.permutation(len(g))]
  base=cash
  for r in q.head(free).itertuples():
   p=dict(symbol=r.symbol,entry_time=et,exit_time=r.exit_time,r=float(r.r24),stake=base*rf)
   active.append(p);acc.append(p)
 for xt in sorted({p["exit_time"] for p in active}):
  b=[p for p in active if p["exit_time"]==xt];cash+=sum(p["stake"]*p["r"] for p in b);peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
 A=pd.DataFrame(acc);return cash,mdd,A
def selftest():
 day=START;cmap={day:0};z=[]
 for i in range(8):z.append(dict(symbol=f"S{i}",entry_time=day+pd.Timedelta(hours=4),exit_time=day+pd.Timedelta(hours=8),r24=float(i-2),break_atr=float(i),liq24=float(100-i)))
 S=pd.DataFrame(z);_,_,a=sim(S,cmap,"break",.01);_,_,b=sim(S,cmap,"liquidity",.01);_,_,c=sim(S,cmap,"random",.01,np.random.default_rng(1))
 assert list(a.symbol)==["S7","S6","S5","S4","S3"];assert list(b.symbol)==["S0","S1","S2","S3","S4"];assert len(c)==5 and len(set(c.symbol))==5;print("SELF_TEST_PASS")
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--self-test",action="store_true");ap.add_argument("--input");ap.add_argument("--canonical");ap.add_argument("--core");ap.add_argument("--out",default="out");ap.add_argument("--sims",type=int,default=10000);ap.add_argument("--seed",type=int,default=20260928);a=ap.parse_args()
 if a.self_test:selftest();return
 C=pd.read_csv(a.core,compression="gzip",parse_dates=["dt"]);C["dt"]=pd.to_datetime(C.dt,utc=True);C=C[(C.dt>=START)&(C.dt<END)];cmap=C.set_index("dt").base.to_dict()
 T=pd.read_csv(a.canonical,parse_dates=["signal_time","entry_time","exit_time"]);T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy();T["year"]=T.entry_time.dt.year
 fs=[];need=set(T.symbol)
 for fn in sorted(glob.glob(a.input+"/**/*.csv.gz",recursive=True)):
  sym=Path(fn).name.replace(".csv.gz","")
  if sym not in need:continue
  d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms";d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
  for col in ["open","high","low","close"]:d[col]=pd.to_numeric(d[col],errors="coerce")
  if "quote_volume" in d:d["quote_volume"]=pd.to_numeric(d["quote_volume"],errors="coerce").fillna(0.)
  else:d["quote_volume"]=0.
  d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")
  x=d.resample("4h",label="left",closed="left").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),bars=("close","count"));x=x[x.bars==16]
  rl=x.low.shift(1).rolling(320).min();atr=(x.high-x.low).shift(1).rolling(14).mean();v24=d.quote_volume.rolling(96,min_periods=96).sum()
  q=pd.DataFrame({"signal_time":x.index,"break_atr":(rl-x.close)/atr,"riskdist":atr});q["symbol"]=sym;q["liq24"]=v24.reindex(x.index+pd.Timedelta(hours=3,minutes=45)).to_numpy();fs.append(q)
 F=pd.concat(fs,ignore_index=True);T=T.merge(F,on=["symbol","signal_time"],how="left");assert T.break_atr.notna().mean()>.99
 T["signals"]=T.groupby("entry_time").symbol.transform("nunique")
 fixed={2023:51,2024:71,2025:71,2026:71};sel=[]
 for y,th in fixed.items():
  q=T[(T.year==y)&(T.signals>=th)].copy();q["wf_threshold"]=th;sel.append(q)
 S=pd.concat(sel).sort_values(["entry_time","symbol"]).copy();assert S.entry_time.nunique()==42 and len(S)==4632,(S.entry_time.nunique(),len(S))
 out=[]
 for sym,g in S.groupby("symbol"):
  fn=next((p for p in glob.glob(a.input+"/**/*.csv.gz",recursive=True) if Path(p).name==sym+".csv.gz"),None)
  if not fn:continue
  d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms";d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
  for col in ["open","high","low","close"]:d[col]=pd.to_numeric(d[col],errors="coerce")
  d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")
  for r in g.itertuples():
   rd=float(r.riskdist);ep=float(r.entry);sl=ep+rd;tp=ep-3*rd;w=d[(d.index>=r.entry_time)&(d.index<r.entry_time+pd.Timedelta(hours=24))]
   if len(w)!=96:continue
   xp=float(w.iloc[-1].close);xt=r.entry_time+pd.Timedelta(hours=24);reason="TIME"
   for ot,b in w.iterrows():
    hs=b.high>=sl;ht=b.low<=tp
    if hs and ht:xp=sl;xt=ot+pd.Timedelta(minutes=15);reason="SL_AMBIG_15M";break
    if hs:xp=sl;xt=ot+pd.Timedelta(minutes=15);reason="SL";break
    if ht:xp=tp;xt=ot+pd.Timedelta(minutes=15);reason="TP";break
   riskpx=rd/ep;r24=((ep-xp)/ep-.0024)/riskpx
   out.append(dict(symbol=sym,entry_time=r.entry_time,exit_time=xt,r24=r24,break_atr=r.break_atr,liq24=r.liq24,reason=reason))
 S=pd.DataFrame(out);cov=len(S)/4632
 if cov<.995:raise AssertionError(("execution coverage",cov,len(S)))
 S["core_active"]=S.entry_time.dt.floor("D").map(cmap).fillna(0).astype(int)
 O=Path(a.out);O.mkdir(parents=True,exist_ok=True);pd.DataFrame(W,columns=["year","threshold","train_events"]).to_csv(O/"wf_thresholds.csv",index=False)
 pool=S.groupby("entry_time").agg(candidates=("symbol","nunique"),core_active=("core_active","first")).reset_index();pool.to_csv(O/"event_pool.csv",index=False)
 rows=[];det={}
 for sel in ["neutral","break","liquidity"]:
  eq,mdd,A=sim(S,cmap,sel);rows.append(dict(selector=sel,equity=eq,total_return=eq-1,mdd=mdd,events=A.entry_time.nunique(),trades=len(A),pf=pf(A.r),avg_r=A.r.mean()));det[sel]=(eq,mdd,A);A.to_csv(O/f"accepted_{sel}.csv",index=False)
 D=pd.DataFrame(rows);D.to_csv(O/"selector_summary.csv",index=False)
 rng=np.random.default_rng(a.seed);eqs=np.empty(a.sims);mdds=np.empty(a.sims);pfs=np.empty(a.sims);avgs=np.empty(a.sims)
 for i in range(a.sims):
  eq,mdd,A=sim(S,cmap,"random",rng=rng);eqs[i]=eq;mdds[i]=mdd;pfs[i]=pf(A.r);avgs[i]=A.r.mean()
 R=dict(sims=a.sims,equity_p01=np.quantile(eqs,.01),equity_p05=np.quantile(eqs,.05),equity_p50=np.quantile(eqs,.5),equity_p95=np.quantile(eqs,.95),equity_p99=np.quantile(eqs,.99),mdd_p05=np.quantile(mdds,.05),mdd_p50=np.quantile(mdds,.5),mdd_p95=np.quantile(mdds,.95),mdd_p99=np.quantile(mdds,.99),pf_p05=np.quantile(pfs,.05),pf_p50=np.quantile(pfs,.5),pf_p95=np.quantile(pfs,.95),avg_r_p05=np.quantile(avgs,.05),avg_r_p50=np.quantile(avgs,.5),avg_r_p95=np.quantile(avgs,.95))
 for sel,(eq,mdd,A) in det.items():
  R[sel+"_equity_pct"]=float((eqs<=eq).mean());R[sel+"_mdd_pct"]=float((mdds<=mdd).mean());R[sel+"_pf_pct"]=float((pfs<=pf(A.r)).mean())
 pd.DataFrame([R]).to_csv(O/"random5_distribution.csv",index=False)
 print("WF",[(2023,51),(2024,71),(2025,71),(2026,71)]);print("SELECTORS");print(D.to_string(index=False));print("RANDOM5");print(pd.DataFrame([R]).to_string(index=False));print("NEUTRAL_PARITY_DELTA",float(D.loc[D.selector=="neutral","equity"].iloc[0]-4.085172));print("COVERAGE",cov)
if __name__=="__main__":main()
