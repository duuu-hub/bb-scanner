#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd, numpy as np

ap=argparse.ArgumentParser()
ap.add_argument("--core",required=True)
ap.add_argument("--selected",required=True)
ap.add_argument("--out",required=True)
a=ap.parse_args(); O=Path(a.out); O.mkdir(parents=True,exist_ok=True)

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-09-01",tz="UTC")

# Stage5 frozen first strategy, reconstructed LONG-only exactly from its saved daily panel.
C=pd.read_csv(a.core,parse_dates=["datetime_utc"])
C=C[(C.datetime_utc>=START)&(C.datetime_utc<END)].copy().sort_values("datetime_utc")
C["core_long"]=(C["position"]>0).astype(int)
gross=C["core_long"]*(C["BTCUSDT_fwd"].fillna(0)+C["ETHUSDT_fwd"].fillna(0))/2.0
chg=C["core_long"].diff().abs().fillna(C["core_long"].abs())
C["core_long_net_pct"]=gross-chg*(0.25/2.0)
C["day"]=C.datetime_utc.dt.floor("D")
active_map=C.set_index("day").core_long.to_dict()
ret_map=C.set_index("day").core_long_net_pct.to_dict()

S=pd.read_csv(a.selected,parse_dates=["entry_time","exit_time"])
S=S[(S.entry_time>=START)&(S.entry_time<END)].copy()
assert len(S)==420 and S.entry_time.nunique()==42 and S.event_rank.max()<=10
S=S.sort_values(["entry_time","event_rank","symbol"])
S["day"]=S.entry_time.dt.floor("D")
S["core_active"]=S.day.map(active_map).fillna(0).astype(int)

# event overlap diagnostics
EV=S.groupby("entry_time").agg(core_active=("core_active","first"),trades=("symbol","size"),mean_r=("r_net","mean")).reset_index()
EV.to_csv(O/"event_overlap.csv",index=False)
print("OVERLAP",dict(events=len(EV),core_off=int((EV.core_active==0).sum()),core_on=int((EV.core_active==1).sum()),
                     off_mean_r=float(EV.loc[EV.core_active==0,"mean_r"].mean()),on_mean_r=float(EV.loc[EV.core_active==1,"mean_r"].mean())))

# exact fee adjustment from original 8bp trade economics
risk_pct=S.net_return/S.r_net
assert np.isfinite(risk_pct).all() and (risk_pct>0).all()
S["risk_pct"]=risk_pct

def radj(r,bps):
    return (r.net_return + .0008 - bps/10000.0)/r.risk_pct

def core_only():
    vals=C.core_long_net_pct.to_numpy(float)/100.0
    curve=np.r_[1.0,np.cumprod(1+vals)]
    peak=np.maximum.accumulate(curve)
    return float(curve[-1]),float(np.max((peak-curve)/peak))

def short_only(rf,bps,gated):
    cash=1.;peak=1.;mdd=0.;active=[];n=0
    for r in S.itertuples():
        done=[p for p in active if p["exit_time"]<=r.entry_time]
        for p in sorted(done,key=lambda x:x["exit_time"]):
            cash+=p["stake"]*p["r"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
        active=[p for p in active if p["exit_time"]>r.entry_time]
        if gated and r.core_active: continue
        if len(active)>=10: continue
        active.append(dict(exit_time=r.exit_time,stake=cash*rf,r=radj(r,bps)));n+=1
    for p in sorted(active,key=lambda x:x["exit_time"]):
        cash+=p["stake"]*p["r"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
    return cash,mdd,n

def combo(rf,bps):
    cash=1.;peak=1.;mdd=0.;active=[];n=0
    # event timeline includes day boundaries and PD entry/exit times.
    days=list(pd.date_range(START,END,freq="D",inclusive="left"))
    entries={k:g for k,g in S.groupby("entry_time",sort=True)}
    alltimes=set(days)|set(entries.keys())|set(S.exit_time.tolist())
    for t in sorted(x for x in alltimes if START<=x<END+pd.Timedelta(days=1)):
        # Realize PD exits first.
        done=[p for p in active if p["exit_time"]<=t]
        for p in sorted(done,key=lambda x:x["exit_time"]):
            cash+=p["stake"]*p["r"]
            active.remove(p)
            peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
        # At UTC day boundary, book prior day's Core LONG return.
        if t.floor("D")==t and t>START:
            prev=t-pd.Timedelta(days=1)
            rr=float(ret_map.get(prev,0.0))/100.0
            cash+=core_stakes.pop(prev,0.0)*rr
            peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
        # Snapshot core stake for this day after prior booking.
        if t.floor("D")==t and t<END:
            if int(active_map.get(t,0)):
                core_stakes[t]=cash
        # Admit PD only when Core LONG is OFF on entry day, preserving break-strength rank.
        if t in entries and int(active_map.get(t.floor("D"),0))==0:
            for r in entries[t].sort_values(["event_rank","symbol"]).itertuples():
                # same-time exits from newly admitted 15m trades are realized before next rank, matching selector admission semantics
                done2=[p for p in active if p["exit_time"]<=t]
                for p in sorted(done2,key=lambda x:x["exit_time"]):
                    cash+=p["stake"]*p["r"];active.remove(p);peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
                if len(active)>=10: continue
                active.append(dict(exit_time=r.exit_time,stake=cash*rf,r=radj(r,bps)));n+=1
    # settle any remaining
    for p in sorted(active,key=lambda x:x["exit_time"]):
        cash+=p["stake"]*p["r"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
    return cash,mdd,n

core_stakes={}
ce,cm=core_only()
rows=[]
for bps in [8,24]:
    for rf in [.005,.0075,.01,.0125,.015]:
        ue,um,un=short_only(rf,bps,False)
        ge,gm,gn=short_only(rf,bps,True)
        core_stakes={}
        xe,xm,xn=combo(rf,bps)
        rows.append(dict(cost_bps=bps,risk=rf,core_equity=ce,core_mdd=cm,
                         pd_ungated_equity=ue,pd_ungated_mdd=um,pd_ungated_trades=un,
                         pd_coreoff_equity=ge,pd_coreoff_mdd=gm,pd_coreoff_trades=gn,
                         combo_equity=xe,combo_booked_mdd=xm,combo_pd_trades=xn))
R=pd.DataFrame(rows);R.to_csv(O/"combo.csv",index=False)
C[["datetime_utc","core_long","core_long_net_pct"]].to_csv(O/"core_long_daily.csv",index=False)
print(R.to_string(index=False))
