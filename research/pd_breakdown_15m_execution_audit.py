#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--selected",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
S=pd.read_csv(a.selected,parse_dates=["entry_time","exit_time"]);S=S[S.basket==5].copy()
need=set(S.symbol);px={}
for fn in glob.glob(a.input+"/**/*.csv.gz",recursive=True):
 sym=Path(fn).name.replace(".csv.gz","")
 if sym not in need: continue
 d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms";d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
 for c in ["open","high","low","close"]: d[c]=pd.to_numeric(d[c],errors="coerce")
 d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")
 px[sym]=d
S["entry"]=pd.to_numeric(S["entry"]);S["r_net"]=pd.to_numeric(S["r_net"])
# infer canonical stop/TP from canonical R outcome and ATR definition; risk distance = prior 4H mean(H-L), TP=entry-3*risk, SL=entry+risk
def atr15(sym,et):
 d=px[sym];x=d.resample("4h",label="left",closed="left").agg(high=("high","max"),low=("low","min"),bars=("close","count"));x=x[x.bars==16]
 atr=(x.high-x.low).shift(1).rolling(14).mean();return float(atr.loc[:et-pd.Timedelta(hours=4)].iloc[-1])
S["risk_dist"]=[atr15(r.symbol,r.entry_time) for r in S.itertuples()]
# reconstruct true first 15m touch over canonical max-hold window; same 15m bar both => SL
def refine(r):
 d=px[r.symbol]; ep=float(r.entry); rd=float(r.risk_dist); sl=ep+rd; tp=ep-3*rd
 w=d[(d.index>=r.entry_time)&(d.index<=r.entry_time+pd.Timedelta(hours=24))]
 for t,b in w.iterrows():
  hs=b.high>=sl; ht=b.low<=tp
  if hs and ht:return t,sl,"SL_AMBIG_15M"
  if hs:return t,sl,"SL"
  if ht:return t,tp,"TP"
 # canonical timeout is 6 4H bars; preserve canonical exit price/R if no barrier touch
 return r.exit_time,float(r.exit),"TIME"
rr=[refine(r) for r in S.itertuples()];S["exit15"]=[x[0] for x in rr];S["exitpx15"]=[x[1] for x in rr];S["reason15"]=[x[2] for x in rr]
S.to_csv(O/"candidates_15m_b5.csv",index=False)
# Keep canonical net R economics for TP/SL and recompute TIME from canonical r_net; only timing changes. This isolates timing/admission effect.
# Re-run admission using refined exits, candidate order unchanged.
out=[]
for rf in [.01,.0125,.015,.02,.025,.03]:
 cash=1.;peak=1.;mdd=0.;active=[];acc=[]
 for r in S.sort_values(["entry_time","symbol"]).itertuples():
  done=[p for p in active if p["exit15"]<=r.entry_time]
  for p in sorted(done,key=lambda x:x["exit15"]):
   cash+=p["stake"]*p["r_net"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
  active=[p for p in active if p["exit15"]>r.entry_time]
  if len(active)>=5:continue
  p=dict(symbol=r.symbol,entry_time=r.entry_time,exit15=r.exit15,r_net=r.r_net,stake=cash*rf,entry=r.entry,exitpx15=r.exitpx15,reason15=r.reason15,risk_dist=r.risk_dist)
  active.append(p);acc.append(p)
 for p in sorted(active,key=lambda x:x["exit15"]):
  cash+=p["stake"]*p["r_net"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
 # strict 15m MTM replay on accepted set, stop/TP mark frozen at canonical R once refined exit occurs via cash realization
 cash2=1.;openp=[];pm=1.;mm=0.;pt=None;tr=None
 be={};bx={}
 for i,p in enumerate(acc):
  p["pid"]=i;be.setdefault(int(p["entry_time"].value),[]).append(p);bx.setdefault(int(p["exit15"].value),[]).append(p)
 keys=set(be)|set(bx)
 for p in acc:
  keys.update(int(t.value) for t in px[p["symbol"]].index if p["entry_time"]<=t<=p["exit15"])
 for k in sorted(keys):
  t=pd.Timestamp(k,tz="UTC");old={p["pid"] for p in openp}
  for p in bx.get(k,[]):
   if p["pid"] in old:
    q=next(i for i,x in enumerate(openp) if x["pid"]==p["pid"]);cash2+=p["stake"]*p["r_net"];openp.pop(q)
  openp.extend(be.get(k,[]))
  for p in bx.get(k,[]):
   if p["pid"] not in old:
    q=next(i for i,x in enumerate(openp) if x["pid"]==p["pid"]);cash2+=p["stake"]*p["r_net"];openp.pop(q)
  eq=cash2
  for p in openp:
   d=px[p["symbol"]];z=d.loc[:t]
   mark=float(z.close.iloc[-1]) if len(z) else p["entry"];mr=max((p["entry"]-mark)/p["risk_dist"],-1.0);eq+=p["stake"]*mr
  if eq>pm:pm=eq
  dd=(pm-eq)/pm
  if dd>mm:mm=dd;tr=t
 assert abs(cash2-cash)<1e-9
 out.append(dict(risk=rf,accepted=len(acc),final_equity=cash,realized_mdd=mdd,mtm_mdd=mm))
 if rf==.01:pd.DataFrame(acc).to_csv(O/"accepted_15m_b5.csv",index=False)
pd.DataFrame(out).to_csv(O/"summary_15m_b5.csv",index=False)
pd.DataFrame({"canonical_reason":S.reason,"reason15":S.reason15}).value_counts().rename("n").reset_index().to_csv(O/"reason_compare_b5.csv",index=False)
print(pd.DataFrame(out).to_string(index=False));print("candidate timing changed",int((S.exit15!=S.exit_time).sum()),"of",len(S));print(pd.Series(S.reason15).value_counts().to_string())
