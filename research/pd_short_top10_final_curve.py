#!/usr/bin/env python3
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import pd_breakdown_core_mtm as cm
import pd_short_top10_cost_slippage_stress as st
import pd_short_top10_1m_ambiguity_resolution as one

START=st.START
END=st.END
COST_BP=24
RISK_EACH=.01
MAX_SLOTS=10

def self_test():
    riskpx=.01
    assert abs((COST_BP/10000.0)/riskpx-.24)<1e-12
    print("SELF_TEST_PASS")

def simulate_curve(core,q,core_marks,short_paths,initial_prev_base=0):
    core=core.sort_values("dt").reset_index(drop=True)
    cdict={r.dt:r for r in core.itertuples()}
    cmaps={d:g.set_index("mark_time").gross.to_dict() for d,g in core_marks.groupby("day")}
    sp={(sym,et):g.set_index("mark_time").gross_r.to_dict()
        for (sym,et),g in short_paths.groupby(["symbol","entry_time"])}
    groups={et:g for et,g in q.groupby("entry_time",sort=True)}

    cash=1.0
    book_peak=1.0;book_mdd=0.0
    mtm_peak=1.0;mtm_mdd=0.0
    openp=[];accepted=[]
    core_day=None;core_stake=0.0;core_gross=0.0
    prev_state=int(initial_prev_base)
    core_realized=0.0;pd_realized=0.0
    curve=[]

    def realize_shorts(t):
        nonlocal cash,openp,book_peak,book_mdd,pd_realized
        done=[p for p in openp if p["exit15"]<=t]
        if done:
            for p in done:
                pnl=p["stake"]*p["r_net"]
                cash+=pnl;pd_realized+=pnl
            openp=[p for p in openp if p["exit15"]>t]
            book_peak=max(book_peak,cash)
            book_mdd=max(book_mdd,(book_peak-cash)/book_peak)

    def mark(t):
        nonlocal mtm_peak,mtm_mdd
        core_unr=0.0
        if core_day is not None and int(cdict[core_day].base):
            core_unr=core_stake*core_gross
        pd_unr=0.0
        for p in openp:
            path=sp[(p["symbol"],p["entry_time"])]
            ks=[k for k in path if k<=t]
            gr=path[max(ks)] if ks else 0.0
            pd_unr+=p["stake"]*(gr-p["cost_r"])
        eq=cash+core_unr+pd_unr
        mtm_peak=max(mtm_peak,eq)
        mtm_mdd=max(mtm_mdd,(mtm_peak-eq)/mtm_peak)
        curve.append(dict(time=t,equity=eq,cash=cash,
                          core_component=core_realized+core_unr,
                          pd_component=pd_realized+pd_unr,
                          core_unrealized=core_unr,pd_unrealized=pd_unr,
                          open_pd=len(openp),
                          core_active=int(cdict[t.floor("D")].base) if t<END else 0))
        return eq

    for t in pd.date_range(START,END,freq="15min",inclusive="both"):
        if t.minute==0 and t.hour==0:
            if core_day is not None and int(cdict[core_day].base):
                core_gross=cmaps[core_day][t]
                mark(t)
                pnl=core_stake*core_gross
                cash+=pnl;core_realized+=pnl
                book_peak=max(book_peak,cash)
                book_mdd=max(book_mdd,(book_peak-cash)/book_peak)
            realize_shorts(t)
            if t>=END:
                break
            row=cdict[t];state=int(row.base);base_before=cash
            fee=abs(state-prev_state)*cm.CORE_HALF_FEE
            if fee:
                cost=base_before*fee
                cash-=cost;core_realized-=cost
                book_peak=max(book_peak,cash)
                book_mdd=max(book_mdd,(book_peak-cash)/book_peak)
            core_day=t;core_stake=base_before if state else 0.0
            core_gross=0.0;prev_state=state
        else:
            realize_shorts(t)
            day=t.floor("D")
            if int(cdict[day].base):
                core_gross=cmaps[day][t]
            else:
                core_gross=0.0

        day=t.floor("D")
        if t in groups and int(cdict[day].base)==0:
            g=groups[t]
            free=MAX_SLOTS-len(openp)
            if free>0:
                base=cash
                for r in g.head(free).itertuples():
                    riskpx=float(r.risk_dist)/float(r.entry)
                    p=dict(symbol=r.symbol,entry_time=r.entry_time,exit15=r.exit15,
                           stake=base*RISK_EACH,r_net=float(r.r_net),
                           cost_r=(COST_BP/10000.0)/riskpx)
                    openp.append(p);accepted.append(p)
        mark(t)

    if openp:
        raise AssertionError(f"open shorts remain {len(openp)}")
    C=pd.DataFrame(curve).drop_duplicates("time",keep="last").sort_values("time").reset_index(drop=True)
    C["seq"]=np.arange(len(C))
    x=C.equity.to_numpy(float);peak=np.maximum.accumulate(x);dd=(peak-x)/peak
    return C,dict(final_equity=float(cash),booked_mdd=float(book_mdd),mtm_mdd=float(dd.max()),
                  accepted=len(accepted),events=len(set(p["entry_time"] for p in accepted)))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--self-test",action="store_true")
    ap.add_argument("--core");ap.add_argument("--canonical");ap.add_argument("--um")
    ap.add_argument("--out",default="out");ap.add_argument("--spot-cache",default="spot15m")
    ap.add_argument("--one-cache",default="um1m")
    a=ap.parse_args()
    if a.self_test:
        self_test();return

    allcore=pd.read_csv(a.core,compression="gzip",parse_dates=["dt"])
    allcore["dt"]=pd.to_datetime(allcore.dt,utc=True)
    prev=int(allcore.loc[allcore.dt<START,"base"].iloc[-1]) if (allcore.dt<START).any() else 0
    core=allcore[(allcore.dt>=START)&(allcore.dt<END)].copy().reset_index(drop=True)

    U,paths=st.build_base(a.um,a.canonical,core)
    S=st.refine(U,paths,COST_BP,0)
    amb=S[(S.core_active==0)&(S.reason=="SL_AMBIG_15M")].copy()
    assert len(amb)==3,len(amb)
    cache={};rr=[]
    for r in amb.itertuples():
        month=pd.Timestamp(r.exit15).to_period("M").start_time.tz_localize("UTC")
        key=(r.symbol,month)
        if key not in cache:
            cache[key]=one.download_um_1m(r.symbol,month,a.one_cache)
        z=one.resolve_one(r,cache[key])
        rr.append(dict(symbol=r.symbol,entry_time=r.entry_time,**z))
    q=S[S.core_active==0].copy()
    q,nchg=one.apply_resolutions(q,pd.DataFrame(rr),COST_BP)
    assert nchg==3

    btc=cm.download_spot_15m("BTCUSDT",START,END,a.spot_cache)
    eth=cm.download_spot_15m("ETHUSDT",START,END,a.spot_cache)
    core_marks,par=cm.build_core_marks(core,btc,eth,prev)
    px={}
    for sym in set(q.symbol):
        d=st.load_symbol(paths[sym]);px[sym]=d[["close"]]
    sp=st.short_paths(q,px)

    C,res=simulate_curve(core,q,core_marks,sp,prev)
    assert abs(res["final_equity"]-15.935169)<5e-5,res
    assert abs(res["mtm_mdd"]-.313555)<5e-5,res

    O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
    C.to_csv(O/"equity_curve_24bp_top10_final.csv",index=False)
    q.to_csv(O/"top10_candidates_24bp_1m_final.csv",index=False)
    par.to_csv(O/"core_spot15m_parity.csv",index=False)
    pd.DataFrame([res]).to_csv(O/"summary.csv",index=False)
    print("RESULT",res)

if __name__=="__main__":
    main()
