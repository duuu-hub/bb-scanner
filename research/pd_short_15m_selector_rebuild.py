#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np

ap=argparse.ArgumentParser()
ap.add_argument("--input",required=True)
ap.add_argument("--trades",required=True)
ap.add_argument("--out",required=True)
a=ap.parse_args()
O=Path(a.out);O.mkdir(parents=True,exist_ok=True)

T=pd.read_csv(a.trades,parse_dates=["signal_time","entry_time","exit_time"])
T=T[T.side=="SHORT"].copy();T["year"]=T.entry_time.dt.year

fs=[];px={}
for fn in sorted(glob.glob(a.input+"/**/*.csv.gz",recursive=True)):
    sym=Path(fn).name.replace(".csv.gz","")
    d=pd.read_csv(fn,compression="gzip")
    tc="open_time" if "open_time" in d else "timestamp_ms"
    d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
    for c in ["high","low","close"]:
        d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d.dropna(subset=["dt","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")
    px[sym]=d.close
    x=d.resample("4h",label="left",closed="left").agg(high=("high","max"),low=("low","min"),close=("close","last"),bars=("close","count"))
    x=x[x.bars==16]
    rl=x.low.shift(1).rolling(320).min()
    atr=(x.high-x.low).shift(1).rolling(14).mean()
    q=pd.DataFrame({"signal_time":x.index,"break_atr":(rl-x.close)/atr,"riskdist":atr})
    q["symbol"]=sym;fs.append(q)

F=pd.concat(fs,ignore_index=True)
T=T.merge(F,on=["symbol","signal_time"],how="left")
assert T.break_atr.notna().mean()>.99 and T.riskdist.notna().mean()>.99
T["signals"]=T.groupby("entry_time").symbol.transform("nunique")

thresholds=[51,61,71,81,91,101];W=[]
for y in sorted(T.year.unique()):
    if y<2023: continue
    train=T[T.year<y]
    ev=train.groupby("entry_time").agg(signals=("symbol","nunique"),event_r=("r_net","mean")).reset_index()
    cand=[]
    for th in thresholds:
        z=ev[ev.signals>=th]
        cand.append((z.event_r.mean() if len(z)>=3 else -np.inf,th,len(z)))
    _,th,n=max(cand);W.append((int(y),int(th),int(n)))

sel=[]
for y,th,n in W:
    q=T[(T.year==y)&(T.signals>=th)].copy();q["wf_threshold"]=th
    q=q.sort_values(["entry_time","break_atr","symbol"],ascending=[True,False,True]).groupby("entry_time",group_keys=False).head(10)
    sel.append(q)
S=pd.concat(sel).sort_values(["entry_time","break_atr","symbol"],ascending=[True,False,True]).copy()
S["event_rank"]=S.groupby("entry_time").cumcount()+1
assert S.event_rank.max()<=10
S.to_csv(O/"selected_candidates.csv",index=False)

ev=S.groupby("entry_time").r_net.mean();gp=ev[ev>0].sum();gl=-ev[ev<0].sum()

def admit_batch(rf):
    cash=1.;peak=1.;mdd=0.;active=[];acc=[];pid=0
    for et,g in S.groupby("entry_time",sort=True):
        done=[p for p in active if p["exit_time"]<=et]
        for p in sorted(done,key=lambda x:(x["exit_time"],x["pid"])):
            cash+=p["stake"]*p["r_net"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
        active=[p for p in active if p["exit_time"]>et]

        free=10-len(active);base=cash;new=[]
        if free>0:
            for r in g.sort_values(["event_rank","symbol"]).head(free).itertuples():
                p=dict(pid=pid,symbol=r.symbol,entry_time=r.entry_time,exit_time=r.exit_time,entry=float(r.entry),
                       r_net=float(r.r_net),stake=base*rf,riskdist=float(r.riskdist),event_rank=int(r.event_rank))
                pid+=1;new.append(p)
            active.extend(new);acc.extend(new)

        # Same-time exits happen only after the whole entry batch is admitted.
        instant=[p for p in new if p["exit_time"]<=et]
        for p in sorted(instant,key=lambda x:x["pid"]):
            cash+=p["stake"]*p["r_net"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak);active.remove(p)

    for p in sorted(active,key=lambda x:(x["exit_time"],x["pid"])):
        cash+=p["stake"]*p["r_net"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
    return acc,cash,mdd

def mtm(acc):
    cash=1.;openp=[];peak=1.;mdd=0.;by_entry={};by_exit={}
    for p in acc:
        by_entry.setdefault(int(pd.Timestamp(p["entry_time"]).value),[]).append(p)
        by_exit.setdefault(int(pd.Timestamp(p["exit_time"]).value),[]).append(p)
    keys=set(by_entry)|set(by_exit)
    for p in acc:
        ser=px[p["symbol"]]
        keys.update(int(t.value) for t in ser.index if p["entry_time"]<=t<=p["exit_time"])
    for k in sorted(keys):
        t=pd.Timestamp(k,tz="UTC");old={p["pid"] for p in openp}
        for p in by_exit.get(k,[]):
            if p["pid"] in old:
                i=next(i for i,x in enumerate(openp) if x["pid"]==p["pid"])
                cash+=p["stake"]*p["r_net"];openp.pop(i)
        entrants=by_entry.get(k,[]);openp.extend(entrants)
        for p in by_exit.get(k,[]):
            if p["pid"] not in old:
                hit=[i for i,x in enumerate(openp) if x["pid"]==p["pid"]]
                if hit:
                    cash+=p["stake"]*p["r_net"];openp.pop(hit[0])
        eq=cash
        for p in openp:
            z=px[p["symbol"]].loc[:t]
            mark=float(z.iloc[-1]) if len(z) else p["entry"]
            rr=max((p["entry"]-mark)/p["riskdist"],-1.0)
            eq+=p["stake"]*rr
        peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak)
    return cash,mdd

rows=[]
for rf in [.0025,.005,.0075,.01,.0125,.015,.02,.025,.03]:
    acc,cash,rm=admit_batch(rf);cash2,mm=mtm(acc);assert abs(cash2-cash)<1e-8
    rows.append(dict(risk=rf,candidate_events=S.entry_time.nunique(),candidate_trades=len(S),
                     accepted=len(acc),accepted_events=pd.Series([p["entry_time"] for p in acc]).nunique(),
                     final_equity=cash,total_return=cash-1,realized_mdd=rm,mtm_mdd=mm))
    if rf==.01: pd.DataFrame(acc).to_csv(O/"accepted_risk1.csv",index=False)

pd.DataFrame(rows).to_csv(O/"portfolio.csv",index=False)
pd.DataFrame(W,columns=["year","threshold","train_events"]).to_csv(O/"wf_thresholds.csv",index=False)
print("WF",W)
print("EVENT",len(ev),gp/gl if gl else np.nan,ev.mean())
print(pd.DataFrame(rows).to_string(index=False))
