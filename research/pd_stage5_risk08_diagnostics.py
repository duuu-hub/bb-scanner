#!/usr/bin/env python3
import argparse, importlib.util
from pathlib import Path
import numpy as np
import pandas as pd

def load_base(path):
    spec=importlib.util.spec_from_file_location("base_mtm",path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m

def period_stats(curve,freq):
    x=curve.set_index("time").equity
    if freq=="Y":
        grp=x.groupby(x.index.year)
        labels=[str(k) for k in grp.groups]
    else:
        per=x.index.to_period("Q")
        grp=x.groupby(per)
        labels=[str(k) for k in grp.groups]
    rows=[]
    for label,(_,g) in zip(labels,grp):
        start=float(g.iloc[0]);end=float(g.iloc[-1])
        peak=g.cummax();mdd=float(((peak-g)/peak).max())
        rows.append(dict(period=label,start_equity=start,end_equity=end,return_pct=(end/start-1)*100,mdd_pct=mdd*100))
    return pd.DataFrame(rows)

def analyze_dd(curve):
    x=curve.copy()
    x["peak"]=x.equity.cummax()
    x["dd"]=(x["peak"]-x.equity)/x["peak"]
    ti=int(x.dd.idxmax());tr=x.loc[ti]
    peak_eq=float(tr["peak"])
    pre=x.loc[:ti]
    pi=int(pre[pre.equity>=peak_eq-1e-12].index[-1]);pr=x.loc[pi]
    post=x.loc[ti+1:]
    rec=post[post.equity>=peak_eq-1e-12]
    ri=int(rec.index[0]) if len(rec) else None
    rr=x.loc[ri] if ri is not None else None
    return dict(
        peak_time=pr.time,trough_time=tr.time,recovery_time=(rr.time if rr is not None else pd.NaT),
        peak_equity=float(pr.equity),trough_equity=float(tr.equity),mdd_pct=float(tr.dd*100),
        days_peak_to_trough=(tr.time-pr.time).total_seconds()/86400.0,
        days_to_recovery=((rr.time-pr.time).total_seconds()/86400.0 if rr is not None else np.nan),
        core_contribution_peak_to_trough=float(tr.core_component-pr.core_component),
        pd_contribution_peak_to_trough=float(tr.pd_component-pr.pd_component),
        cash_change_peak_to_trough=float(tr.cash-pr.cash),
        core_unrealized_at_trough=float(tr.core_unrealized),
        pd_unrealized_at_trough=float(tr.pd_unrealized),
        open_pd_at_trough=int(tr.open_pd)
    )

def run_case(base,H,S,core_marks,px,bps,rf):
    amap=H.set_index("held_day").core_long.to_dict()
    cm={d:g.set_index("mark_time").gross.to_dict() for d,g in core_marks.groupby("day")}
    S2=S.copy();S2["mark_pid"]=np.arange(len(S2))
    pm={}
    for r in S2.itertuples():
        ser=px[r.symbol]
        cost_r=(bps/10000.0)/float(r.risk_pct)
        marks={r.entry_time:-cost_r}
        w=ser[(ser.index>=r.entry_time)&(ser.index+pd.Timedelta(minutes=15)<r.exit_time)]
        for ot,z in w.iterrows():
            marks[ot+pd.Timedelta(minutes=15)]=(float(r.entry)-float(z.close))/float(r.riskdist)-cost_r
        pm[int(r.mark_pid)]=marks

    cash=1.0;openp=[];core_day=None;core_stake=0.0;core_gross=0.0
    prev=int(H.attrs.get("initial_prev_state",0))
    core_realized=0.0;pd_realized=0.0
    entries={et:g for et,g in S2.groupby("entry_time",sort=True)}
    rows=[];accepted=[]

    def realize_pd(t):
        nonlocal cash,pd_realized,openp
        done=[p for p in openp if p["exit_time"]<=t]
        for p in sorted(done,key=lambda x:(x["exit_time"],x["mark_pid"])):
            pnl=p["stake"]*p["r_real"]
            cash+=pnl;pd_realized+=pnl;openp.remove(p)

    def mark_row(t):
        core_unr=core_stake*core_gross if core_day is not None and int(amap.get(core_day,0)) else 0.0
        pd_unr=0.0
        for p in openp:
            marks=pm[p["mark_pid"]]
            ks=[k for k in marks if k<=t]
            rr=marks[max(ks)] if ks else 0.0
            pd_unr+=p["stake"]*rr
        eq=cash+core_unr+pd_unr
        rows.append(dict(time=t,equity=eq,cash=cash,
                         core_component=core_realized+core_unr,
                         pd_component=pd_realized+pd_unr,
                         core_unrealized=core_unr,pd_unrealized=pd_unr,
                         open_pd=len(openp),core_active=int(amap.get(t.floor("D"),0))))
        return eq

    for t in pd.date_range(base.START,base.END,freq="15min",inclusive="both"):
        if t.floor("D")==t:
            if core_day is not None and int(amap.get(core_day,0)):
                core_gross=cm[core_day][t]
                mark_row(t)
                pnl=core_stake*core_gross
                cash+=pnl;core_realized+=pnl
            realize_pd(t)
            if t>=base.END:
                mark_row(t)
                break
            state=int(amap.get(t,0));base_cash=cash
            fee=abs(state-prev)*base.CORE_HALF_FEE
            if fee:
                cost=base_cash*fee
                cash-=cost;core_realized-=cost
            core_day=t;core_stake=base_cash if state else 0.0;core_gross=0.0;prev=state
        else:
            realize_pd(t)
            if core_day is not None and int(amap.get(core_day,0)):
                core_gross=cm[core_day][t]

        if t in entries and int(amap.get(t.floor("D"),0))==0:
            g=entries[t];free=10-len(openp);base_cash=cash
            for r in g.head(max(0,free)).itertuples():
                rr=(float(r.net_return)+.0008-bps/10000.0)/float(r.risk_pct)
                p=dict(mark_pid=int(r.mark_pid),entry_time=r.entry_time,exit_time=r.exit_time,
                       stake=base_cash*rf,r_real=rr,symbol=r.symbol)
                openp.append(p);accepted.append(p)
        mark_row(t)

    curve=pd.DataFrame(rows).drop_duplicates("time",keep="last").sort_values("time").reset_index(drop=True)
    assert not openp
    return curve,accepted

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--base-script",required=True)
    ap.add_argument("--core",required=True);ap.add_argument("--selected",required=True);ap.add_argument("--um",required=True)
    ap.add_argument("--out",required=True);ap.add_argument("--spot-cache",default="spot15m")
    a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
    base=load_base(a.base_script)
    H=base.prepare_core(a.core)
    C=pd.read_csv(a.core,parse_dates=["datetime_utc"]).sort_values("datetime_utc").copy()
    C["held_day"]=C.datetime_utc.dt.floor("D")+pd.Timedelta(days=1)
    C["core_long"]=(C.position>0).astype(int)
    before=C[C.held_day<base.START]
    H.attrs["initial_prev_state"]=int(before.core_long.iloc[-1]) if len(before) else 0
    S,amap=base.prepare_pd(a.selected,H)
    btc=base.download_spot_15m("BTCUSDT",base.START,base.END,a.spot_cache)
    eth=base.download_spot_15m("ETHUSDT",base.START,base.END,a.spot_cache)
    core_marks,par=base.build_core_paths(H,btc,eth)
    px=base.load_um(a.um,set(S.symbol))

    summaries=[]
    for bps in [8,24]:
        curve,acc=run_case(base,H,S,core_marks,px,bps,0.008)
        peak=curve.equity.cummax();dd=((peak-curve.equity)/peak)
        final=float(curve.equity.iloc[-1]);mdd=float(dd.max())
        expected={8:(10.991664,0.294156),24:(9.410156,0.299689)}[bps]
        assert abs(final-expected[0])<5e-6,(bps,final,expected[0])
        assert abs(mdd-expected[1])<5e-6,(bps,mdd,expected[1])
        curve.to_csv(O/f"equity_curve_{bps}bp.csv",index=False)
        ys=period_stats(curve,"Y");ys["cost_bps"]=bps;ys.to_csv(O/f"yearly_{bps}bp.csv",index=False)
        qs=period_stats(curve,"Q");qs["cost_bps"]=bps;qs.to_csv(O/f"quarterly_{bps}bp.csv",index=False)
        d=analyze_dd(curve);d["cost_bps"]=bps;d["pd_trades"]=len(acc);d["pd_events"]=len(set(p["entry_time"] for p in acc))
        summaries.append(d)

    D=pd.DataFrame(summaries);D.to_csv(O/"worst_drawdowns.csv",index=False)
    Y=pd.concat([pd.read_csv(O/"yearly_8bp.csv"),pd.read_csv(O/"yearly_24bp.csv")],ignore_index=True)
    Q=pd.concat([pd.read_csv(O/"quarterly_8bp.csv"),pd.read_csv(O/"quarterly_24bp.csv")],ignore_index=True)
    print("WORST_DD")
    print(D.to_string(index=False))
    print("YEARLY")
    print(Y.to_string(index=False))
    print("QUARTERLY")
    print(Q.to_string(index=False))

if __name__=="__main__":
    main()
