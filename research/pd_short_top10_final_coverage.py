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

def pf(v):
    v=np.asarray(v,float)
    gp=v[v>0].sum(); gl=-v[v<0].sum()
    return gp/gl if gl>0 else np.inf

def add_flat_blocks(core):
    x=core.sort_values("dt").copy()
    x["flat"]=(x.base==0)
    start=x.flat.ne(x.flat.shift(fill_value=False))
    x["state_block"]=start.cumsum()
    flat=x[x.flat].copy()
    ids={b:i+1 for i,b in enumerate(flat.state_block.drop_duplicates())}
    x["flat_block_id"]=x.state_block.map(ids).where(x.flat)
    return x

def admit(q):
    active=[]; accepted=[]
    for et,g in q.sort_values(["entry_time","selector_rank","symbol"]).groupby("entry_time",sort=True):
        active=[p for p in active if p["exit15"]>et]
        free=MAX_SLOTS-len(active)
        if free<=0:
            continue
        for r in g.head(free).itertuples():
            p=dict(symbol=r.symbol,entry_time=r.entry_time,exit15=r.exit15,
                   r_net=float(r.r_net),selector_rank=int(r.selector_rank),
                   reason=r.reason,risk_dist=float(r.risk_dist),entry=float(r.entry))
            active.append(p); accepted.append(p)
    return pd.DataFrame(accepted)

def union_active_bars(A,core):
    grid=pd.date_range(START,END,freq="15min",inclusive="left")
    active=np.zeros(len(grid),dtype=bool)
    for r in A.itertuples():
        lo=grid.searchsorted(r.entry_time,side="left")
        hi=grid.searchsorted(r.exit15,side="left")
        active[lo:hi]=True
    day=pd.Series(grid.floor("D"),index=np.arange(len(grid)))
    cmap=core.set_index("dt").base.to_dict()
    flat=np.array([int(cmap.get(d,0))==0 for d in day],dtype=bool)
    return dict(
        total_flat_15m=int(flat.sum()),
        active_flat_15m=int((flat&active).sum()),
        flat_time_coverage=float((flat&active).sum()/flat.sum()) if flat.sum() else np.nan,
        any_short_15m=int(active.sum())
    )

def build_block_table(core,q,A):
    c=add_flat_blocks(core)
    day_to_block=c.set_index("dt").flat_block_id.to_dict()
    flat=c[c.flat].groupby("flat_block_id").agg(
        start=("dt","min"),days=("dt","size")
    ).reset_index()
    flat["end_exclusive"]=flat.start+pd.to_timedelta(flat.days,unit="D")

    qe=q.copy(); qe["day"]=qe.entry_time.dt.floor("D"); qe["flat_block_id"]=qe.day.map(day_to_block)
    ae=A.copy(); ae["day"]=ae.entry_time.dt.floor("D"); ae["flat_block_id"]=ae.day.map(day_to_block)

    sig=qe.groupby("flat_block_id").agg(
        signal_events=("entry_time","nunique"),
        candidate_trades=("symbol","size"),
        candidate_avg_r=("r_net","mean")
    )
    act=ae.groupby("flat_block_id").agg(
        admitted_events=("entry_time","nunique"),
        trades=("symbol","size"),
        avg_r=("r_net","mean"),
        win_rate=("r_net",lambda x:float((x>0).mean())),
        trade_pf=("r_net",pf),
        sum_r=("r_net","sum")
    )
    out=flat.merge(sig,left_on="flat_block_id",right_index=True,how="left").merge(
        act,left_on="flat_block_id",right_index=True,how="left")
    for c0 in ["signal_events","candidate_trades","admitted_events","trades"]:
        out[c0]=out[c0].fillna(0).astype(int)
    return out.sort_values("start")

def simulate_calendar(core,q,core_marks,short_paths,use_core):
    cdict={r.dt:r for r in core.sort_values("dt").itertuples()}
    cmaps={d:g.set_index("mark_time").gross.to_dict() for d,g in core_marks.groupby("day")}
    sp={(sym,et):g.set_index("mark_time").gross_r.to_dict() for (sym,et),g in short_paths.groupby(["symbol","entry_time"])}
    groups={et:g for et,g in q.groupby("entry_time",sort=True)}

    allcore=core.sort_values("dt")
    prev=0
    cash=1.; openp=[]; core_stake=0.; current_day=None; core_gross=0.
    boundaries={START:1.0}

    def realize(t):
        nonlocal cash,openp
        done=[p for p in openp if p["exit15"]<=t]
        if done:
            cash+=sum(p["stake"]*p["r_net"] for p in done)
            openp=[p for p in openp if p["exit15"]>t]

    def short_mark(t):
        eq=cash
        for p in openp:
            path=sp[(p["symbol"],p["entry_time"])]
            ks=[k for k in path if k<=t]
            gr=path[max(ks)] if ks else 0.
            eq+=p["stake"]*(gr-p["cost_r"])
        return eq

    for t in pd.date_range(START,END,freq="15min",inclusive="both"):
        if t.minute==0 and t.hour==0:
            if current_day is not None and use_core and int(cdict[current_day].base):
                core_gross=cmaps[current_day][t]
                cash+=core_stake*core_gross
            realize(t)
            if t.year!=START.year or t==START:
                boundaries[t]=short_mark(t)
            if t>=END:
                break
            row=cdict[t]; state=int(row.base); base_before=cash
            if use_core:
                fee=abs(state-prev)*cm.CORE_HALF_FEE
                if fee: cash-=base_before*fee
                core_stake=base_before if state else 0.
            else:
                core_stake=0.
            current_day=t; core_gross=0.; prev=state
        else:
            realize(t)
            d=t.floor("D")
            if use_core and int(cdict[d].base):
                core_gross=cmaps[d][t]
            else:
                core_gross=0.

        d=t.floor("D")
        if t in groups and int(cdict[d].base)==0:
            g=groups[t]; free=MAX_SLOTS-len(openp)
            if free>0:
                base=cash
                for r in g.head(free).itertuples():
                    riskpx=float(r.risk_dist)/float(r.entry)
                    openp.append(dict(symbol=r.symbol,entry_time=r.entry_time,exit15=r.exit15,
                                      stake=base*RISK_EACH,r_net=float(r.r_net),
                                      cost_r=(COST_BP/10000.0)/riskpx))
    boundaries[END]=short_mark(END)
    b=pd.Series(boundaries).sort_index()
    rows=[]
    for y in range(START.year,END.year+1):
        s=pd.Timestamp(f"{y}-01-01",tz="UTC")
        e=min(pd.Timestamp(f"{y+1}-01-01",tz="UTC"),END)
        if s>=END or s not in b.index or e not in b.index: continue
        rows.append(dict(year=y,start_equity=float(b.loc[s]),end_equity=float(b.loc[e]),
                         calendar_return=float(b.loc[e]/b.loc[s]-1)))
    return pd.DataFrame(rows),b

def self_test():
    c=pd.DataFrame({"dt":pd.date_range("2024-01-01",periods=7,freq="D",tz="UTC"),
                    "base":[1,0,0,1,0,0,0]})
    z=add_flat_blocks(c)
    b=z[z.flat].groupby("flat_block_id").size().tolist()
    assert b==[2,3],b
    q=pd.DataFrame([
        dict(symbol="A",entry_time=pd.Timestamp("2024-01-02 04:00",tz="UTC"),
             exit15=pd.Timestamp("2024-01-02 08:00",tz="UTC"),r_net=1.,selector_rank=1,reason="TP",risk_dist=1.,entry=100.),
        dict(symbol="B",entry_time=pd.Timestamp("2024-01-02 04:00",tz="UTC"),
             exit15=pd.Timestamp("2024-01-02 08:00",tz="UTC"),r_net=-1.,selector_rank=2,reason="SL",risk_dist=1.,entry=100.)])
    A=admit(q)
    assert len(A)==2
    print("SELF_TEST_PASS",{"flat_blocks":b,"accepted":len(A)})

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--self-test",action="store_true")
    ap.add_argument("--core");ap.add_argument("--canonical");ap.add_argument("--um")
    ap.add_argument("--out",default="out");ap.add_argument("--spot-cache",default="spot15m")
    ap.add_argument("--one-cache",default="um1m")
    a=ap.parse_args()
    if a.self_test:
        self_test(); return

    allcore=pd.read_csv(a.core,compression="gzip",parse_dates=["dt"])
    allcore["dt"]=pd.to_datetime(allcore.dt,utc=True)
    prev=int(allcore.loc[allcore.dt<START,"base"].iloc[-1]) if (allcore.dt<START).any() else 0
    core=allcore[(allcore.dt>=START)&(allcore.dt<END)].copy().reset_index(drop=True)

    U,paths=st.build_base(a.um,a.canonical,core)
    S=st.refine(U,paths,COST_BP,0)

    # Resolve every Core-OFF 15m ambiguity with official Binance UM 1m.
    amb=S[(S.core_active==0)&(S.reason=="SL_AMBIG_15M")].copy()
    assert len(amb)==3,len(amb)
    cache={}; rr=[]
    for r in amb.itertuples():
        month=pd.Timestamp(r.exit15).to_period("M").start_time.tz_localize("UTC")
        key=(r.symbol,month)
        if key not in cache:
            cache[key]=one.download_um_1m(r.symbol,month,a.one_cache)
        z=one.resolve_one(r,cache[key])
        rr.append(dict(symbol=r.symbol,entry_time=r.entry_time,**z))
    res=pd.DataFrame(rr)
    q=S[S.core_active==0].copy()
    q,nchg=one.apply_resolutions(q,res,COST_BP)
    assert nchg==3

    # Actual slot admission for final Top10 implementation.
    A=admit(q)
    assert len(A)==356 and A.entry_time.nunique()==36,(len(A),A.entry_time.nunique())

    # Yearly trade/event diagnostics.
    yearly=[]
    for y,g in A.groupby(A.entry_time.dt.year):
        ev=g.groupby("entry_time").r_net.mean()
        yearly.append(dict(year=int(y),events=int(g.entry_time.nunique()),trades=len(g),
                           trade_wr=float((g.r_net>0).mean()),trade_pf=pf(g.r_net),
                           avg_trade_r=float(g.r_net.mean()),sum_r=float(g.r_net.sum()),
                           event_wr=float((ev>0).mean()),event_pf=pf(ev),avg_event_r=float(ev.mean())))
    Y=pd.DataFrame(yearly)

    # Core flat block coverage.
    B=build_block_table(core,q,A)
    cov=union_active_bars(A,core)
    flat_days=int((core.base==0).sum())
    flat_blocks=len(B)
    blocks_with_signal=int((B.signal_events>0).sum())
    signal_days=int(q.entry_time.dt.floor("D").nunique())
    admitted_days=int(A.entry_time.dt.floor("D").nunique())
    coverage=dict(
        total_days=len(core),core_flat_days=flat_days,core_flat_day_rate=flat_days/len(core),
        flat_blocks=flat_blocks,blocks_with_signal=blocks_with_signal,
        block_coverage=blocks_with_signal/flat_blocks if flat_blocks else np.nan,
        flat_days_in_signaled_blocks=int(B.loc[B.signal_events>0,"days"].sum()),
        flat_days_block_coverage=float(B.loc[B.signal_events>0,"days"].sum()/flat_days),
        signal_events=int(q.entry_time.nunique()),signal_days=signal_days,
        admitted_events=int(A.entry_time.nunique()),admitted_days=admitted_days,
        signal_days_per_100_flat_days=100*signal_days/flat_days,
        **cov
    )

    # Candidate-level ON/OFF diagnostic before gate.
    evall=S.groupby("entry_time").agg(core_active=("core_active","first"),mean_r=("r_net","mean"),
                                      trades=("symbol","size")).reset_index()
    state=evall.groupby("core_active").agg(events=("entry_time","size"),
        avg_event_r=("mean_r","mean"),event_pf=("mean_r",pf),
        event_wr=("mean_r",lambda x:float((x>0).mean()))).reset_index()
    state["state"]=state.core_active.map({0:"CORE_OFF",1:"CORE_ON"})

    # Calendar returns: exact 15m marks, final accepted candidate set.
    btc=cm.download_spot_15m("BTCUSDT",START,END,a.spot_cache)
    eth=cm.download_spot_15m("ETHUSDT",START,END,a.spot_cache)
    core_marks,par=cm.build_core_marks(core,btc,eth,prev)
    px={}
    for sym in set(q.symbol):
        d=st.load_symbol(paths[sym]);px[sym]=d[["close"]]
    sp=st.short_paths(q,px)
    short_year,_=simulate_calendar(core,q,core_marks,sp,use_core=False)
    combo_year,_=simulate_calendar(core,q,core_marks,sp,use_core=True)
    cal=short_year.rename(columns={"calendar_return":"short_calendar_return",
        "start_equity":"short_start_equity","end_equity":"short_end_equity"}).merge(
        combo_year.rename(columns={"calendar_return":"combo_calendar_return",
        "start_equity":"combo_start_equity","end_equity":"combo_end_equity"}),on="year",how="outer")

    O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
    A.to_csv(O/"accepted_top10_final.csv",index=False)
    q.to_csv(O/"coreoff_top10_candidates_final.csv",index=False)
    Y.to_csv(O/"yearly_trade_event.csv",index=False)
    B.to_csv(O/"core_flat_blocks.csv",index=False)
    pd.DataFrame([coverage]).to_csv(O/"coverage_summary.csv",index=False)
    state.to_csv(O/"core_state_event_quality.csv",index=False)
    cal.to_csv(O/"calendar_returns.csv",index=False)
    par.to_csv(O/"core_spot15m_parity.csv",index=False)

    print("YEARLY")
    print(Y.to_string(index=False))
    print("COVERAGE")
    print(pd.DataFrame([coverage]).to_string(index=False))
    print("CORE_STATE")
    print(state.to_string(index=False))
    print("CALENDAR")
    print(cal.to_string(index=False))
    print("TOP_FLAT_BLOCKS_BY_DAYS")
    print(B.sort_values("days",ascending=False).head(15).to_string(index=False))

if __name__=="__main__":
    main()
