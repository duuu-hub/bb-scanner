#!/usr/bin/env python3
import argparse, glob
from pathlib import Path
import numpy as np
import pandas as pd
import pd_breakdown_core_mtm as cm

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-09-01",tz="UTC")
FIXED={2023:51,2024:71,2025:71,2026:71}
COST_BP=24

def normalize_ms(s):
    return cm.normalize_ms(s)

def build_top10_candidates(input_dir,canonical_path,core):
    T=pd.read_csv(canonical_path,parse_dates=["signal_time","entry_time","exit_time"])
    T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy()
    T["year"]=T.entry_time.dt.year
    paths={}
    feats=[]
    need=set(T.symbol)
    for fn in sorted(glob.glob(str(Path(input_dir)/"**/*.csv.gz"),recursive=True)):
        sym=Path(fn).name.replace(".csv.gz","")
        if sym not in need:
            continue
        paths[sym]=fn
        d=pd.read_csv(fn,compression="gzip")
        tc="open_time" if "open_time" in d else "timestamp_ms"
        d["dt"]=normalize_ms(d[tc])
        for c in ["open","high","low","close"]:
            d[c]=pd.to_numeric(d[c],errors="coerce")
        d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")
        x=d.resample("4h",label="left",closed="left").agg(
            open=("open","first"),high=("high","max"),low=("low","min"),
            close=("close","last"),bars=("close","count"))
        x=x[x.bars==16]
        rl=x.low.shift(1).rolling(320).min()
        atr=(x.high-x.low).shift(1).rolling(14).mean()
        q=pd.DataFrame({"signal_time":x.index,"break_atr":(rl-x.close)/atr,"risk_dist":atr})
        q["symbol"]=sym
        feats.append(q)
    F=pd.concat(feats,ignore_index=True)
    T=T.merge(F,on=["symbol","signal_time"],how="left")
    assert T.break_atr.notna().mean()>.99
    T["signals"]=T.groupby("entry_time").symbol.transform("nunique")

    z=[]
    for y,th in FIXED.items():
        q=T[(T.year==y)&(T.signals>=th)].copy()
        q["wf_threshold"]=th
        z.append(q)
    U=pd.concat(z).copy()
    assert U.entry_time.nunique()==42 and len(U)==4632,(U.entry_time.nunique(),len(U))
    U=U.sort_values(["entry_time","break_atr","symbol"],ascending=[True,False,True])
    U["selector_rank"]=U.groupby("entry_time").cumcount()+1
    S=U[U.selector_rank<=10].copy()
    assert len(S)==420 and S.entry_time.nunique()==42,(len(S),S.entry_time.nunique())

    refined=[]
    for sym,g in S.groupby("symbol"):
        d=pd.read_csv(paths[sym],compression="gzip")
        tc="open_time" if "open_time" in d else "timestamp_ms"
        d["dt"]=normalize_ms(d[tc])
        for c in ["open","high","low","close"]:
            d[c]=pd.to_numeric(d[c],errors="coerce")
        d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")
        for r in g.itertuples():
            ep=float(r.entry); rd=float(r.risk_dist)
            sl=ep+rd; tp=ep-3*rd
            w=d[(d.index>=r.entry_time)&(d.index<r.entry_time+pd.Timedelta(hours=24))]
            if len(w)!=96:
                raise AssertionError(f"15m window {sym} {r.entry_time} bars={len(w)}")
            xp=float(w.iloc[-1].close); xt=r.entry_time+pd.Timedelta(hours=24); reason="TIME"
            for ot,b in w.iterrows():
                hs=float(b.high)>=sl; ht=float(b.low)<=tp
                if hs and ht:
                    xp=sl; xt=ot+pd.Timedelta(minutes=15); reason="SL_AMBIG_15M"; break
                if hs:
                    xp=sl; xt=ot+pd.Timedelta(minutes=15); reason="SL"; break
                if ht:
                    xp=tp; xt=ot+pd.Timedelta(minutes=15); reason="TP"; break
            riskpx=rd/ep
            r24=((ep-xp)/ep-(COST_BP/10000.0))/riskpx
            refined.append(dict(
                symbol=sym,entry_time=r.entry_time,exit15=xt,entry=ep,risk_dist=rd,
                break_atr=float(r.break_atr),selector_rank=int(r.selector_rank),
                r24=float(r24),reason=reason,year=int(r.year)))
    R=pd.DataFrame(refined).sort_values(["entry_time","selector_rank","symbol"]).reset_index(drop=True)
    cmap=core.set_index("dt").base.to_dict()
    R["core_active"]=R.entry_time.dt.floor("D").map(cmap).fillna(0).astype(int)
    return R

def simulate(core,S,core_marks,short_paths,rf,max_slots,use_core=True,use_shorts=True,initial_prev_base=0):
    core=core.sort_values("dt").reset_index(drop=True)
    cdict={r.dt:r for r in core.itertuples()}
    cmaps={d:g.set_index("mark_time").gross.to_dict() for d,g in core_marks.groupby("day")}
    sp={(sym,et):g.set_index("mark_time").gross_r.to_dict() for (sym,et),g in short_paths.groupby(["symbol","entry_time"])}
    event_groups={et:g for et,g in S.groupby("entry_time",sort=True)}
    cash=1.0; peak=1.0; mtm_mdd=0.0; booked_peak=1.0; booked_mdd=0.0
    openp=[]; accepted=[]; max_open=0
    core_stake=0.0; current_core_day=None; current_core_gross=0.0
    prev_base=int(initial_prev_base)

    def realize(t):
        nonlocal cash,openp,booked_peak,booked_mdd
        done=[p for p in openp if p["exit15"]<=t]
        if done:
            cash+=sum(p["stake"]*p["r24"] for p in done)
            openp=[p for p in openp if p["exit15"]>t]
            booked_peak=max(booked_peak,cash)
            booked_mdd=max(booked_mdd,(booked_peak-cash)/booked_peak)

    def mark(t):
        nonlocal peak,mtm_mdd
        eq=cash
        if use_core and current_core_day is not None and int(cdict[current_core_day].base):
            eq+=core_stake*current_core_gross
        for p in openp:
            path=sp[(p["symbol"],p["entry_time"])]
            ks=[k for k in path if k<=t]
            gr=path[max(ks)] if ks else 0.0
            eq+=p["stake"]*(gr-p["cost_r"])
        peak=max(peak,eq)
        mtm_mdd=max(mtm_mdd,(peak-eq)/peak)

    grid=pd.date_range(START,END,freq="15min",inclusive="both")
    for t in grid:
        if t.minute==0 and t.hour==0:
            if current_core_day is not None and use_core and int(cdict[current_core_day].base):
                current_core_gross=cmaps[current_core_day][t]
                mark(t)
                cash+=core_stake*current_core_gross
                booked_peak=max(booked_peak,cash)
                booked_mdd=max(booked_mdd,(booked_peak-cash)/booked_peak)
            realize(t)
            if t>=END:
                break
            day=t; row=cdict[day]; state=int(row.base)
            base_before=cash
            if use_core:
                fee=abs(state-prev_base)*cm.CORE_HALF_FEE
                if fee:
                    cash-=base_before*fee
                core_stake=base_before if state else 0.0
            else:
                core_stake=0.0
            current_core_day=day; current_core_gross=0.0; prev_base=state
            booked_peak=max(booked_peak,cash)
            booked_mdd=max(booked_mdd,(booked_peak-cash)/booked_peak)
        else:
            realize(t)
            day=t.floor("D")
            row=cdict[day]
            if use_core and int(row.base):
                current_core_gross=cmaps[day][t]
            else:
                current_core_gross=0.0

        day=t.floor("D")
        row=cdict[day]
        if use_shorts and t in event_groups and int(row.base)==0:
            g=event_groups[t]
            free=max_slots-len(openp)
            if free>0:
                base=cash
                for r in g.head(free).itertuples():
                    riskpx=float(r.risk_dist)/float(r.entry)
                    p=dict(symbol=r.symbol,entry_time=r.entry_time,exit15=r.exit15,
                           stake=base*rf,r24=float(r.r24),cost_r=(COST_BP/10000.0)/riskpx,
                           selector_rank=int(r.selector_rank))
                    openp.append(p); accepted.append(p)
        max_open=max(max_open,len(openp))
        mark(t)

    if openp:
        raise AssertionError(f"open shorts remain {len(openp)}")
    A=pd.DataFrame(accepted)
    return dict(final_equity=cash,booked_mdd=booked_mdd,mtm_mdd=mtm_mdd,
                accepted=len(A),accepted_events=(A.entry_time.nunique() if len(A) else 0),
                max_open=max_open)

def self_test():
    core=pd.DataFrame({"dt":[START,START+pd.Timedelta(days=1)],"base":[0,0],"base_net":[0.,0.]})
    cmk=pd.DataFrame(columns=["day","mark_time","gross"])
    et=START+pd.Timedelta(hours=4)
    S=pd.DataFrame([
        dict(symbol="A",entry_time=et,exit15=et+pd.Timedelta(hours=4),entry=100.,risk_dist=1.,r24=1.,selector_rank=1),
        dict(symbol="B",entry_time=et,exit15=et+pd.Timedelta(hours=4),entry=100.,risk_dist=1.,r24=-1.,selector_rank=2),
    ])
    sp=pd.DataFrame([
        ("A",et,et,0.),("B",et,et,0.)
    ],columns=["symbol","entry_time","mark_time","gross_r"])
    old_end=globals()["END"]
    globals()["END"]=START+pd.Timedelta(days=2)
    z=simulate(core,S,cmk,sp,.05,2,use_core=False,use_shorts=True)
    globals()["END"]=old_end
    assert z["accepted"]==2 and z["max_open"]==2 and z["final_equity"]>0
    print("SELF_TEST_PASS",z)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--self-test",action="store_true")
    ap.add_argument("--core")
    ap.add_argument("--canonical")
    ap.add_argument("--um")
    ap.add_argument("--out",default="out")
    ap.add_argument("--spot-cache",default="spot15m")
    a=ap.parse_args()
    if a.self_test:
        self_test(); return
    allcore=pd.read_csv(a.core,compression="gzip",parse_dates=["dt"])
    allcore["dt"]=pd.to_datetime(allcore.dt,utc=True)
    prev=int(allcore.loc[allcore.dt<START,"base"].iloc[-1]) if (allcore.dt<START).any() else 0
    core=allcore[(allcore.dt>=START)&(allcore.dt<END)].copy().reset_index(drop=True)

    S=build_top10_candidates(a.um,a.canonical,core)
    btc=cm.download_spot_15m("BTCUSDT",START,END,a.spot_cache)
    eth=cm.download_spot_15m("ETHUSDT",START,END,a.spot_cache)
    core_marks,par=cm.build_core_marks(core,btc,eth,prev)
    px=cm.load_um_needed(a.um,set(S.symbol))
    paths=cm.build_short_paths(S.rename(columns={"exit15":"exit15"}),px)

    O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
    S.to_csv(O/"top10_candidates_15m.csv",index=False)
    par.to_csv(O/"core_spot15m_parity.csv",index=False)

    core_only=simulate(core,S.iloc[0:0],core_marks,paths.iloc[0:0],.02,5,use_core=True,use_shorts=False,initial_prev_base=prev)
    rows=[]
    for name,max_slots,rf,rankmax in [("top5",5,.02,5),("top10",10,.01,10)]:
        q=S[S.selector_rank<=rankmax].copy()
        combo=simulate(core,q,core_marks,cm.build_short_paths(q,px),rf,max_slots,True,True,prev)
        short=simulate(core,q,core_marks,cm.build_short_paths(q,px),rf,max_slots,False,True,prev)
        rows.append(dict(selector=name,max_slots=max_slots,risk_each=rf,total_risk_cap=max_slots*rf,
                         core_only_equity=core_only["final_equity"],core_only_mtm_mdd=core_only["mtm_mdd"],
                         combo_equity=combo["final_equity"],combo_booked_mdd=combo["booked_mdd"],combo_mtm_mdd=combo["mtm_mdd"],
                         combo_trades=combo["accepted"],combo_events=combo["accepted_events"],combo_max_open=combo["max_open"],
                         short_equity=short["final_equity"],short_booked_mdd=short["booked_mdd"],short_mtm_mdd=short["mtm_mdd"],
                         short_trades=short["accepted"],short_events=short["accepted_events"],short_max_open=short["max_open"]))
    R=pd.DataFrame(rows)
    R.to_csv(O/"top5_top10_core_mtm_compare.csv",index=False)
    print("CORE_ONLY",core_only)
    print(R.to_string(index=False))

if __name__=="__main__":
    main()
