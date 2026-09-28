#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd, numpy as np

ap=argparse.ArgumentParser()
ap.add_argument("--core",required=True)
ap.add_argument("--short",required=True)
ap.add_argument("--out",required=True)
a=ap.parse_args()
O=Path(a.out);O.mkdir(parents=True,exist_ok=True)

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-09-01",tz="UTC")
core=pd.read_csv(a.core,compression="gzip",parse_dates=["dt"])
core=core[(core.dt>=START)&(core.dt<END)].copy()
core["day"]=core.dt.dt.floor("D")
cmap=core.set_index("day").base.to_dict()
cnet=core.set_index("day").base_net.to_dict()

S=pd.read_csv(a.short,parse_dates=["entry_time","exit15"])
S=S[(S.entry_time>=START)&(S.entry_time<END)].sort_values(["entry_time","symbol"]).copy()
S["day"]=S.entry_time.dt.floor("D")
S["core_active"]=S.day.map(cmap).fillna(0).astype(int)

ev=S.groupby("entry_time").agg(core_active=("core_active","first"),n=("symbol","size"),mean_r=("r_net","mean")).reset_index()
ev.to_csv(O/"event_overlap.csv",index=False)
overlap=dict(events=len(ev),flat_events=int((ev.core_active==0).sum()),active_events=int((ev.core_active==1).sum()),
             core_flat_day_rate=float((core.base==0).mean()),
             flat_event_mean_r=float(ev.loc[ev.core_active==0,"mean_r"].mean()),
             active_event_mean_r=float(ev.loc[ev.core_active==1,"mean_r"].mean()))
pd.DataFrame([overlap]).to_csv(O/"overlap_summary.csv",index=False)

def short_sim(gated,rf,costbp):
    cash=1.;peak=1.;mdd=0.;active=[];acc=[]
    for et,g in S.groupby("entry_time",sort=True):
        for xt in sorted({p["exit15"] for p in active if p["exit15"]<=et}):
            b=[p for p in active if p["exit15"]==xt]
            cash+=sum(p["stake"]*p["r"] for p in b)
            active=[p for p in active if p["exit15"]!=xt]
            peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
        if gated and int(cmap.get(et.floor("D"),0)): continue
        free=5-len(active)
        if free<=0: continue
        base=cash
        for r in g.head(free).itertuples():
            riskpx=r.risk_dist/r.entry
            radj=r.r_net-((costbp-8)/10000)/riskpx
            p=dict(symbol=r.symbol,entry_time=et,exit15=r.exit15,r=radj,stake=base*rf)
            active.append(p);acc.append(p)
    for xt in sorted({p["exit15"] for p in active}):
        b=[p for p in active if p["exit15"]==xt]
        cash+=sum(p["stake"]*p["r"] for p in b)
        peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
    return cash,mdd,len(acc)

def combo_sim(rf,costbp):
    cash=1.;peak=1.;mdd=0.;active=[];acc=[]
    for day in pd.date_range(START,END,freq="D",inclusive="left"):
        for xt in sorted({p["exit15"] for p in active if p["exit15"]<=day}):
            b=[p for p in active if p["exit15"]==xt]
            cash+=sum(p["stake"]*p["r"] for p in b)
            active=[p for p in active if p["exit15"]!=xt]
            peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
        core_stake=cash if int(cmap.get(day,0)) else 0.
        gday=S[S.day==day]
        times=sorted(set(gday.entry_time.tolist()+[p["exit15"] for p in active if day<p["exit15"]<day+pd.Timedelta(days=1)]))
        for t in times:
            for xt in sorted({p["exit15"] for p in active if p["exit15"]<=t}):
                b=[p for p in active if p["exit15"]==xt]
                cash+=sum(p["stake"]*p["r"] for p in b)
                active=[p for p in active if p["exit15"]!=xt]
                peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
            eg=gday[gday.entry_time==t]
            if len(eg) and int(cmap.get(day,0))==0:
                free=5-len(active)
                if free>0:
                    base=cash
                    for r in eg.head(free).itertuples():
                        riskpx=r.risk_dist/r.entry
                        radj=r.r_net-((costbp-8)/10000)/riskpx
                        p=dict(symbol=r.symbol,entry_time=t,exit15=r.exit15,r=radj,stake=base*rf)
                        active.append(p);acc.append(p)
        if core_stake:
            cash+=core_stake*(float(cnet[day])/100)
            peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
    for xt in sorted({p["exit15"] for p in active}):
        b=[p for p in active if p["exit15"]==xt]
        cash+=sum(p["stake"]*p["r"] for p in b)
        peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
    return cash,mdd,len(acc)

r=core.base_net.to_numpy()/100
eq=np.cumprod(1+r);curve=np.r_[1.,eq]
core_eq=float(eq[-1]);core_mdd=float(1-np.min(curve/np.maximum.accumulate(curve)))
rows=[]
for cost in [8,24]:
    for rf in [.02,.025,.03]:
        ue,um,un=short_sim(False,rf,cost)
        ge,gm,gn=short_sim(True,rf,cost)
        ce,cm,cn=combo_sim(rf,cost)
        rows.append(dict(cost_bp=cost,risk=rf,core_equity=core_eq,core_mdd=core_mdd,
                         short_ungated_equity=ue,short_ungated_mdd=um,short_ungated_trades=un,
                         short_flat_equity=ge,short_flat_mdd=gm,short_flat_trades=gn,
                         combo_equity=ce,combo_booked_mdd=cm,combo_short_trades=cn))
R=pd.DataFrame(rows);R.to_csv(O/"core_short_overlap.csv",index=False)
print("OVERLAP",overlap)
print(R.to_string(index=False))
