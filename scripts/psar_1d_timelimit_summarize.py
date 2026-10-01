"""Audit and aggregate the four frozen limits; no holdout-driven retuning."""
import argparse
from collections import Counter
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd
from psar_1d_short_timelimit import validate, LIMITS, THRESHOLD, CUT

KEY=["symbol","signal_ts"]
START=int(pd.Timestamp("2021-01-01",tz="UTC").timestamp()*1000)
END=int(pd.Timestamp("2027-01-01",tz="UTC").timestamp()*1000)


def metrics(x,cost_bp=0):
    r=x.pnl_pct-cost_bp/100.
    gp=float(r[r>0].sum());gl=float(-r[r<0].sum())
    time=x[x.outcome=="time"]
    net_time=time.pnl_pct-cost_bp/100.
    hold=(x.exit_ts-x.fill_ts)/3600000.
    return dict(
        n=len(x),win_pct=float((r>0).mean()*100),
        positive_n=int((r>0).sum()),negative_n=int((r<0).sum()),zero_n=int((r==0).sum()),
        pf=gp/gl if gl else None,mean_pct=float(r.mean()),median_pct=float(r.median()),
        tp_pct=float((x.outcome=="win").mean()*100),sl_pct=float((x.outcome=="loss").mean()*100),
        time_pct=float((x.outcome=="time").mean()*100),
        time_positive_pct=float((net_time>0).mean()*100) if len(time) else None,
        time_negative_pct=float((net_time<0).mean()*100) if len(time) else None,
        mean_hold_hours=float(hold.mean()),median_hold_hours=float(hold.median()),
    )


def periods(x):
    return [("all",x),("train",x[x.signal_ts<CUT]),("holdout",x[x.signal_ts>=CUT])]


def baseline_select(x,size=.3,max_pos=6,max_gross=2.):
    """Inherited slot rule, used only as a diagnostic baseline."""
    slots=min(max_pos,int(np.floor(max_gross/size+1e-12)))
    opened={}
    ids=[]
    for ts,g in x.groupby("fill_ts",sort=True):
        opened={s:e for s,e in opened.items() if e>ts}
        for r in g.sort_values(["stop_pct","symbol"],kind="mergesort").itertuples():
            if r.symbol in opened or len(opened)>=slots:
                continue
            opened[r.symbol]=int(r.exit_ts);ids.append(r.Index)
    return ids


def replay_closed_equity(x,ids,cost_bp,size=.3):
    """Diagnostic realized-equity replay, not mark-to-market MDD."""
    y=x.loc[ids].sort_values(["fill_ts","symbol"],kind="mergesort")
    events=[]
    for r in y.itertuples():
        events.extend([(int(r.fill_ts),1,r),(int(r.exit_ts),0,r)])
    events.sort(key=lambda q:(q[0],q[1],q[2].symbol))
    equity=peak=1.;drawdown=gp=gl=0.;opened={};wins=losses=streak=max_streak=0
    for ts,kind,r in events:
        if kind:
            opened[r.Index]=equity*size
        else:
            n=opened.pop(r.Index)
            pnl=n*(float(r.pnl_pct)-cost_bp/100.)/100.
            equity+=pnl
            if pnl>0:gp+=pnl;wins+=1;streak=0
            elif pnl<0:gl-=pnl;losses+=1;streak+=1;max_streak=max(max_streak,streak)
            peak=max(peak,equity)
            drawdown=max(drawdown,100.*(peak-equity)/peak)
            if equity<=0:break
    elapsed=(float(y.exit_ts.max())-float(y.fill_ts.min()))/86400000./365.25 if len(y) else 0
    return dict(
        n=len(y),return_pct=(equity-1.)*100.,
        cagr_pct=((equity**(1/elapsed)-1)*100.) if elapsed>0 and equity>0 else None,
        realized_drawdown_pct=drawdown,pf=gp/gl if gl else None,
        win_pct=100.*wins/(wins+losses) if wins+losses else None,
        max_losing_streak=max_streak,
        accepted_ids_sha256=__import__("hashlib").sha256(
            y[["symbol","signal_ts"]].to_csv(index=False).encode()).hexdigest(),
    )


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",default="in")
    ap.add_argument("--baseline",default="")
    ap.add_argument("--output",default="summary")
    a=ap.parse_args()
    dst=Path(a.output);dst.mkdir(parents=True,exist_ok=True)
    files=sorted(glob.glob(a.input+"/**/tl_*.csv.gz",recursive=True))
    assert len(files)==8
    frames=[];metas=[];symbols=set();counts=Counter()
    for path in files:
        m=json.load(open(path+".meta.json"))
        assert m["shards"]==8 and len(m["sources"])==m["selected_files"]
        names={r["symbol"] for r in m["sources"]}
        assert len(names)==len(m["sources"])
        assert not symbols&names
        symbols |= names
        assert m["variant"]==[2.75,1.2,.75] and m["limits"]==list(LIMITS)
        assert m["atr_threshold"]==THRESHOLD
        z=pd.read_csv(path);validate(z);assert len(z)==m["events"]
        assert set(z.symbol).issubset(names)
        frames.append(z);metas.append(m);counts.update(m["diagnostics"])
    assert {m["shard"] for m in metas}==set(range(8))
    assert len({m["commit"] for m in metas})==1
    assert len({m["script_sha256"] for m in metas})==1
    assert len({m["helper_sha256"] for m in metas})==1
    assert len(symbols)==metas[0]["total_input_files"]
    d=pd.concat(frames,ignore_index=True);validate(d)
    d=d[(d.signal_ts>=START)&(d.signal_ts<END)].copy()
    high=d[d.atr_pct>=THRESHOLD].copy()
    common_keys=high.groupby(KEY).limit_days.nunique()
    common_keys=common_keys[common_keys==len(LIMITS)].index
    common=high.set_index(KEY).loc[common_keys].reset_index()
    rows=[]
    for scope,z in (("native_horizon",high),("common_14d_horizon",common)):
        for days,g in z.groupby("limit_days"):
            for period,p in periods(g):
                for cost in (0,20,40):
                    row=metrics(p,cost)
                    row.update(scope=scope,limit_days=int(days),period=period,cost_bp=cost)
                    rows.append(row)
    raw=pd.DataFrame(rows);raw.to_csv(dst/"raw_summary.csv",index=False)
    # Diagnostic baseline preserves the old 6-slot / 30%-size rule.
    port=[]
    for days,g in common.groupby("limit_days"):
        for period,p in periods(g):
            ids=baseline_select(p)
            for cost in (20,40):
                row=replay_closed_equity(p,ids,cost)
                row.update(limit_days=int(days),period=period,cost_bp=cost,size_pct=30,max_pos=6,max_gross_pct=200)
                port.append(row)
    pd.DataFrame(port).to_csv(dst/"portfolio_diagnostic.csv",index=False)
    comparisons=[];baseline_check={}
    if a.baseline:
        paths=sorted(glob.glob(a.baseline+"/**/events_*.csv.gz",recursive=True))
        assert len(paths)==8
        keep=[]
        for path in paths:
            for x in pd.read_csv(path,chunksize=200000):
                x=x[(x.side=="SHORT")&(x.variant=="E2.75_SB1.2_R0.75")]
                keep.append(x)
        b=pd.concat(keep,ignore_index=True)
        assert not b.duplicated(KEY).any()
        b=b[(b.signal_ts>=START)&(b.signal_ts<END)&(b.atr_pct>=THRESHOLD)]
        baseline_check["total_events"]=len(b)
        baseline_check["unresolved_or_excluded"]=int((~b.outcome.isin(["win","loss"])).sum())
        b=b[b.outcome.isin(["win","loss"])&b.exit_ts.notna()].copy()
        base14=common[common.limit_days==14]
        merged=base14.merge(b,on=KEY,suffixes=("_tl","_unlimited"))
        for field in ("fill_ts","fill","sl","tp","atr_pct","stop_pct"):
            assert np.allclose(merged[field+"_tl"],merged[field+"_unlimited"])
        natural=merged[merged.outcome_tl!="time"]
        baseline_check["natural_14d_matches"]=len(natural)
        baseline_check["natural_outcome_mismatches"]=int((natural.outcome_tl!=natural.outcome_unlimited).sum())
        baseline_check["natural_exit_ts_mismatches"]=int((natural.exit_ts_tl!=natural.exit_ts_unlimited).sum())
        assert baseline_check["natural_outcome_mismatches"]==0
        assert baseline_check["natural_exit_ts_mismatches"]==0
        for scope,z in (("all_resolved_unlimited",b),("common_resolved_with_14d",b.merge(base14[KEY],on=KEY))):
            for period,p in periods(z):
                for cost in (0,20,40):
                    row=metrics(p,cost);row.update(scope=scope,period=period,cost_bp=cost)
                    comparisons.append(row)
        pd.DataFrame(comparisons).to_csv(dst/"unlimited_comparison.csv",index=False)
    # Predeclared decision rule: only train, same threshold, 40bp stress.
    train=raw[(raw.scope=="common_14d_horizon")&(raw.period=="train")&(raw.cost_bp==40)]
    candidates=train.loc[(train.pf>1)&(train.mean_pct>0),"limit_days"].astype(int).tolist()
    result=dict(
        commit=metas[0]["commit"],input_files=len(symbols),all_events=len(d),high_vol_events=len(high),
        common_signals=len(common_keys),source_start_utc=str(pd.to_datetime(d.signal_ts.min(),unit="ms",utc=True)),
        source_end_utc=str(pd.to_datetime(d.signal_ts.max(),unit="ms",utc=True)),
        threshold=THRESHOLD,limits=LIMITS,diagnostics=dict(counts),baseline_check=baseline_check,
        train_40bp_candidates=candidates,
        decision="NO_SIZING: all frozen limits fail train 40bp raw edge" if not candidates else "TRAIN_ONLY_PORTFOLIO_REQUIRED",
        limitations=["Funding is not included.","15m entry-bar timestamp convention.","Portfolio diagnostic uses realized equity, not MTM or liquidation modeling.",
                     "Unlimited baseline was produced by the preserved earlier run; excluded gaps can change sample membership."],
        raw=rows,portfolio_diagnostic=port,unlimited_comparison=comparisons,
    )
    with open(dst/"summary.json","w") as f:json.dump(result,f,indent=2,allow_nan=False)
    final=raw[(raw.scope=="common_14d_horizon")&(raw.period=="all")&(raw.cost_bp==20)]
    report=[
        "# PSAR 1D SHORT forced-time-limit audit",
        "",
        "Frozen E=2.75 ATR / SB=1.2 ATR / R=0.75; ATR% >= 10.174524905858567.",
        "All rows below are independent raw signals on the common 14-day-eligible set.",
        "",
        "| Limit days | N | Net win % | PF 20bp | Mean net % | Time exit % |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for r in final.itertuples():
        report.append(f"| {r.limit_days} | {r.n} | {r.win_pct:.3f} | {r.pf:.4f} | {r.mean_pct:.4f} | {r.time_pct:.3f} |")
    report.extend(["","Decision: "+result["decision"],"","Train 2021-2024; holdout 2025-2026. Sizing gates use train only.",
                   "Portfolio file is a realized-equity diagnostic. It is not a claim of true account MDD or practical optimal sizing.",
                   "Funding and tick-accurate maker fill times are not modeled."])
    (dst/"report.md").write_text("\n".join(report)+"\n")
    print("FULL_RESULT_AUDIT_PASS",len(d),len(symbols),flush=True)
    print(final.to_string(index=False),flush=True)
    print("TRAIN_40BP",train.to_string(index=False),flush=True)
    print("DECISION",result["decision"],flush=True)


if __name__=="__main__":
    main()

