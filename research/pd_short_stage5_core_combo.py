#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd, numpy as np

ap=argparse.ArgumentParser()
ap.add_argument("--core",required=True)
ap.add_argument("--selected",required=True)
ap.add_argument("--out",required=True)
a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-09-01",tz="UTC")

# Stage5 row t uses close[t] to decide position and fwd[t]=close[t+1]/close[t]-1.
# Therefore that state/return belongs to the NEXT calendar day (t+1), not day t.
C=pd.read_csv(a.core,parse_dates=["datetime_utc"]).sort_values("datetime_utc").copy()
C["held_day"]=C.datetime_utc.dt.floor("D")+pd.Timedelta(days=1)
C["core_long"]=(C.position>0).astype(int)
gross=C.core_long*(C.BTCUSDT_fwd.fillna(0)+C.ETHUSDT_fwd.fillna(0))/2.0
chg=C.core_long.diff().abs().fillna(C.core_long.abs())
C["core_gross_pct"]=gross
C["core_long_net_pct"]=gross-chg*(0.25/2.0)
H=C[(C.held_day>=START)&(C.held_day<END)].copy()
assert H.held_day.is_unique
active_map=H.set_index("held_day").core_long.to_dict()
ret_map=H.set_index("held_day").core_long_net_pct.to_dict()
gross_map=H.set_index("held_day").core_gross_pct.to_dict()
_before=C[C.held_day<START]
initial_prev_state=int(_before.core_long.iloc[-1]) if len(_before) else 0

S=pd.read_csv(a.selected,parse_dates=["entry_time","exit_time"])
S=S[(S.entry_time>=START)&(S.entry_time<END)].copy()
assert len(S)>0 and S.entry_time.nunique()>0
assert S.event_rank.max()<=10
assert S.groupby("entry_time").size().max()<=10
S=S.sort_values(["entry_time","event_rank","symbol"]).copy()
S["day"]=S.entry_time.dt.floor("D")
S["core_active"]=S.day.map(active_map).fillna(0).astype(int)
S["risk_pct"]=S.net_return/S.r_net
assert np.isfinite(S.risk_pct).all() and (S.risk_pct>0).all()

EV=S.groupby("entry_time").agg(core_active=("core_active","first"),trades=("symbol","size"),mean_r=("r_net","mean")).reset_index()
EV.to_csv(O/"event_overlap.csv",index=False)

def radj(r,bps):
    return (r.net_return + .0008 - bps/10000.0)/r.risk_pct

def core_only():
    vals=H.core_long_net_pct.to_numpy(float)/100.0
    curve=np.r_[1.,np.cumprod(1+vals)]
    peak=np.maximum.accumulate(curve)
    return float(curve[-1]),float(np.max((peak-curve)/peak))

def short_only(rf,bps,gated):
    cash=1.;peak=1.;mdd=0.;active=[];accepted=0;accepted_events=set()
    for et,g in S.groupby("entry_time",sort=True):
        done=[p for p in active if p["exit_time"]<=et]
        for p in sorted(done,key=lambda x:x["exit_time"]):
            cash+=p["stake"]*p["r"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
        active=[p for p in active if p["exit_time"]>et]
        if gated and int(g.core_active.iloc[0]): continue
        free=10-len(active);base=cash;new=[]
        for r in g.sort_values(["event_rank","symbol"]).head(max(0,free)).itertuples():
            p=dict(exit_time=r.exit_time,stake=base*rf,r=radj(r,bps));new.append(p)
        active.extend(new);accepted+=len(new)
        if new: accepted_events.add(et)
        # same-time exits happen after whole batch admission
        instant=[p for p in new if p["exit_time"]<=et]
        for p in instant:
            cash+=p["stake"]*p["r"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak);active.remove(p)
    for p in sorted(active,key=lambda x:x["exit_time"]):
        cash+=p["stake"]*p["r"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
    return cash,mdd,accepted,len(accepted_events)

def combo(rf,bps):
    cash=1.;peak=1.;mdd=0.;active=[];accepted=0;accepted_events=set()
    core_stake=0.;prev_day=None;prev_state=initial_prev_state;overlap_days=0
    entries={et:g for et,g in S.groupby("entry_time",sort=True)}
    days=list(pd.date_range(START,END,freq="D",inclusive="both"))
    alltimes=set(days)|set(entries.keys())|set(S.exit_time.tolist())

    def realize_exits(t):
        nonlocal cash,peak,mdd,active
        done=[p for p in active if p["exit_time"]<=t]
        for p in sorted(done,key=lambda x:x["exit_time"]):
            cash+=p["stake"]*p["r"];active.remove(p)
            peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)

    for t in sorted(x for x in alltimes if START<=x<=END):
        if t.floor("D")==t:
            # Book prior Core day's gross PnL first, then process PD exits at this boundary.
            if prev_day is not None and core_stake:
                cash+=core_stake*(float(gross_map.get(prev_day,0.0))/100.0)
                peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
            realize_exits(t)
            if t>=END: break

            state=int(active_map.get(t,0))
            base=cash
            fee=abs(state-prev_state)*0.00125
            if fee:
                cash-=base*fee
                peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
            core_stake=base if state else 0.0
            prev_day=t;prev_state=state
            if state and active: overlap_days+=1
        else:
            realize_exits(t)

        if t in entries and int(active_map.get(t.floor("D"),0))==0:
            g=entries[t];free=10-len(active);base=cash;new=[]
            for r in g.sort_values(["event_rank","symbol"]).head(max(0,free)).itertuples():
                p=dict(exit_time=r.exit_time,stake=base*rf,r=radj(r,bps));new.append(p)
            active.extend(new);accepted+=len(new)
            if new: accepted_events.add(t)
            instant=[p for p in new if p["exit_time"]<=t]
            for p in instant:
                cash+=p["stake"]*p["r"];active.remove(p)
                peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)

    for p in sorted(active,key=lambda x:x["exit_time"]):
        cash+=p["stake"]*p["r"];peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
    return cash,mdd,accepted,len(accepted_events),overlap_days


ce,cm=core_only()
rows=[]
for bps in [8,24]:
    for rf in [.005,.0075,.01,.0125,.015]:
        ue,um,un,uve=short_only(rf,bps,False)
        ge,gm,gn,gve=short_only(rf,bps,True)
        xe,xm,xn,xve,od=combo(rf,bps)
        rows.append(dict(cost_bps=bps,risk=rf,core_equity=ce,core_mdd=cm,
                         pd_ungated_equity=ue,pd_ungated_mdd=um,pd_ungated_trades=un,pd_ungated_events=uve,
                         pd_coreoff_equity=ge,pd_coreoff_mdd=gm,pd_coreoff_trades=gn,pd_coreoff_events=gve,
                         combo_equity=xe,combo_booked_mdd=xm,combo_pd_trades=xn,combo_pd_events=xve,
                         core_start_with_open_pd_days=od))
R=pd.DataFrame(rows)
R.to_csv(O/"combo.csv",index=False)
H[["held_day","core_long","core_long_net_pct"]].to_csv(O/"core_long_held_daily.csv",index=False)
EV.to_csv(O/"event_overlap.csv",index=False)

# Final MTM engine run 36377034059 is the independent parity reference.
ref=float(R[(R.cost_bps==8)&(R.risk==0.01)].combo_equity.iloc[0])
assert abs(ref-14.627315)<5e-6, f"booked/MTM final equity parity failed: {ref}"

print("OVERLAP",{"events":len(EV),"core_off":int((EV.core_active==0).sum()),
                 "core_on":int((EV.core_active==1).sum()),
                 "off_mean_r":float(EV.loc[EV.core_active==0,"mean_r"].mean()),
                 "on_mean_r":float(EV.loc[EV.core_active==1,"mean_r"].mean())})
print(R.to_string(index=False))
