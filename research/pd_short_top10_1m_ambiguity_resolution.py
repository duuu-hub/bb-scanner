#!/usr/bin/env python3
import argparse, io, zipfile
from pathlib import Path
import pandas as pd
import requests
import pd_breakdown_core_mtm as cm
import pd_short_top10_cost_slippage_stress as st

START=st.START
END=st.END

def download_um_1m(symbol, month, cache_dir):
    cache_dir=Path(cache_dir);cache_dir.mkdir(parents=True,exist_ok=True)
    ym=pd.Timestamp(month).strftime("%Y-%m")
    cache=cache_dir/f"{symbol}-1m-{ym}.csv.gz"
    if cache.exists():
        d=pd.read_csv(cache,compression="gzip")
    else:
        url=f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/{symbol}-1m-{ym}.zip"
        r=requests.get(url,timeout=90);r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            member=next(n for n in z.namelist() if n.endswith(".csv"))
            raw=pd.read_csv(z.open(member),header=None)
        d=raw.iloc[:,[0,2,3]].copy();d.columns=["open_time","high","low"]
        d.to_csv(cache,index=False,compression="gzip")
    d["dt"]=cm.normalize_ms(d.open_time)
    d["high"]=pd.to_numeric(d.high,errors="coerce")
    d["low"]=pd.to_numeric(d.low,errors="coerce")
    return d[["dt","high","low"]].dropna().sort_values("dt").drop_duplicates("dt").set_index("dt")

def resolve_one(row, one):
    bar_start=pd.Timestamp(row.exit15)-pd.Timedelta(minutes=15)
    fill=float(row.entry);rd=float(row.risk_dist);sl=fill+rd;tp=fill-3*rd
    w=one[(one.index>=bar_start)&(one.index<bar_start+pd.Timedelta(minutes=15))]
    if len(w)!=15:
        raise AssertionError(f"1m bars !=15 {row.symbol} {bar_start} n={len(w)}")
    for ot,b in w.iterrows():
        hs=float(b.high)>=sl;ht=float(b.low)<=tp
        if hs and ht:
            return dict(resolved_exit=ot+pd.Timedelta(minutes=1),resolved_px=sl,
                        resolution="SL_AMBIG_1M",minute=ot,sl=sl,tp=tp)
        if hs:
            return dict(resolved_exit=ot+pd.Timedelta(minutes=1),resolved_px=sl,
                        resolution="SL_1M",minute=ot,sl=sl,tp=tp)
        if ht:
            return dict(resolved_exit=ot+pd.Timedelta(minutes=1),resolved_px=tp,
                        resolution="TP_1M",minute=ot,sl=sl,tp=tp)
    raise AssertionError(f"15m ambiguous but no 1m barrier hit {row.symbol} {bar_start}")

def apply_resolutions(S,res,cost_bp):
    X=S.copy()
    idx={(r.symbol,pd.Timestamp(r.entry_time)):r for r in res.itertuples()}
    changed=0
    for i,r in X.iterrows():
        k=(r.symbol,pd.Timestamp(r.entry_time))
        if k not in idx: continue
        z=idx[k]
        X.at[i,"exit15"]=pd.Timestamp(z.resolved_exit)
        X.at[i,"reason"]=z.resolution
        fill=float(r.entry);rd=float(r.risk_dist)
        gross=(fill-float(z.resolved_px))/fill
        riskpx=rd/fill
        X.at[i,"r_net"]=(gross-cost_bp/10000.0)/riskpx
        changed+=1
    return X,changed

def self_test():
    t=pd.Timestamp("2025-01-01T00:00:00Z")
    one=pd.DataFrame({"high":[100.2]*15,"low":[99.8]*15},
                     index=pd.date_range(t,t+pd.Timedelta(minutes=14),freq="1min",tz="UTC"))
    one.iloc[3,one.columns.get_loc("low")]=96.5
    row=pd.Series(dict(symbol="X",entry=100.,risk_dist=1.,exit15=t+pd.Timedelta(minutes=15)))
    z=resolve_one(row,one)
    assert z["resolution"]=="TP_1M" and z["minute"]==t+pd.Timedelta(minutes=3),z
    one.iloc[2,one.columns.get_loc("high")]=101.2
    z=resolve_one(row,one)
    assert z["resolution"]=="SL_1M" and z["minute"]==t+pd.Timedelta(minutes=2),z
    one.iloc[1,one.columns.get_loc("high")]=101.2
    one.iloc[1,one.columns.get_loc("low")]=96.5
    z=resolve_one(row,one)
    assert z["resolution"]=="SL_AMBIG_1M" and z["minute"]==t+pd.Timedelta(minutes=1),z
    print("SELF_TEST_PASS")

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
    btc=cm.download_spot_15m("BTCUSDT",START,END,a.spot_cache)
    eth=cm.download_spot_15m("ETHUSDT",START,END,a.spot_cache)
    core_marks,par=cm.build_core_marks(core,btc,eth,prev)
    px={}
    for sym in set(U.symbol):
        d=st.load_symbol(paths[sym]);px[sym]=d[["close"]]

    O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
    par.to_csv(O/"core_spot15m_parity.csv",index=False)

    slip_resolved={}
    audit=[]
    cache={}
    for slip in st.SLIPS:
        base=st.refine(U,paths,24,slip)
        amb=base[(base.core_active==0)&(base.reason=="SL_AMBIG_15M")].copy()
        assert len(amb)==3,(slip,len(amb))
        rr=[]
        for r in amb.itertuples():
            month=pd.Timestamp(r.exit15).to_period("M").start_time.tz_localize("UTC")
            key=(r.symbol,month)
            if key not in cache:
                cache[key]=download_um_1m(r.symbol,month,a.one_cache)
            z=resolve_one(r,cache[key])
            rec=dict(slip_bp=slip,symbol=r.symbol,entry_time=r.entry_time,ambiguous_15m_end=r.exit15,
                     fill_entry=r.entry,risk_dist=r.risk_dist,selector_rank=r.selector_rank,**z)
            rr.append(rec);audit.append(rec)
        slip_resolved[slip]=pd.DataFrame(rr)

    A=pd.DataFrame(audit).sort_values(["slip_bp","ambiguous_15m_end","symbol"])
    A.to_csv(O/"ambiguous_1m_resolution.csv",index=False)

    rows=[]
    for slip in st.SLIPS:
        for cost in st.COSTS:
            S=st.refine(U,paths,cost,slip)
            q=S[S.core_active==0].copy()
            q,nchg=apply_resolutions(q,slip_resolved[slip],cost)
            assert nchg==3,(slip,cost,nchg)
            sp=st.short_paths(q,px)
            combo=st.simulate(core,q,core_marks,sp,.01,10,True,True,prev)
            short=st.simulate(core,q,core_marks,sp,.01,10,False,True,prev)
            rc=q.reason.value_counts().to_dict()
            rows.append(dict(cost_bp=cost,entry_slip_bp=slip,resolved_1m=nchg,
                             combo_equity=combo["final_equity"],combo_booked_mdd=combo["booked_mdd"],combo_mtm_mdd=combo["mtm_mdd"],
                             combo_trades=combo["accepted"],combo_events=combo["events"],combo_max_open=combo["max_open"],
                             short_equity=short["final_equity"],short_booked_mdd=short["booked_mdd"],short_mtm_mdd=short["mtm_mdd"],
                             short_trades=short["accepted"],short_events=short["events"],short_max_open=short["max_open"],
                             tp=rc.get("TP",0)+rc.get("TP_1M",0),
                             sl=rc.get("SL",0)+rc.get("SL_1M",0)+rc.get("SL_AMBIG_1M",0),
                             tp_1m=rc.get("TP_1M",0),sl_1m=rc.get("SL_1M",0),ambig1m=rc.get("SL_AMBIG_1M",0),
                             timeout=rc.get("TIME",0)))
    R=pd.DataFrame(rows)
    R.to_csv(O/"top10_cost_slippage_1m_resolved.csv",index=False)
    print("AMBIGUOUS_1M")
    print(A.to_string(index=False))
    print("RESULTS")
    print(R.to_string(index=False))

if __name__=="__main__":
    main()
