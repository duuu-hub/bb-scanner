import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from precision_backtest import (
    build_signals, fetch_all_minutes, calc_pf, FEE_PCT, MIN, load_with_extras
)
from exposure_risk import simulate

# Deliberately small/structured grids. The goal is robustness, not fitting every
# wiggle in 120 days of data.
TP_GRID = [7.5, 10.0, 12.5]
EXIT_GRID = {
    "L1_MOMENTUM_1H10": {
        "sl": [4.0, 5.0, 6.0],
        "horizon": [480, 720, 1440],
        "base": (10.0, 5.0, 720),
    },
    "L2_EXPLOSIVE_4H30": {
        "sl": [2.0, 2.5, 3.0],
        "horizon": [30, 60, 120],
        "base": (10.0, 2.5, 60),
    },
    "L3_4H_LAG": {
        "sl": [3.0, 4.0, 5.0],
        "horizon": [480, 720, 1440],
        "base": (10.0, 4.0, 720),
    },
    "S1_EXTREME_7_7": {
        "sl": [3.0, 4.0, 5.0],
        "horizon": [240, 480, 720],
        "base": (10.0, 4.0, 720),
    },
    "S2_15M_LAG_NEAR1": {
        "sl": [3.0, 4.0, 5.0],
        "horizon": [120, 240, 480],
        "base": (10.0, 4.0, 240),
    },
    "S3_PERSIST_8": {
        "sl": [4.0, 5.0, 6.0],
        "horizon": [30, 60, 120],
        "base": (10.0, 5.0, 60),
    },
}

MIN_DEV_N = {
    "L1_MOMENTUM_1H10": 24,
    "L2_EXPLOSIVE_4H30": 12,
    "L3_4H_LAG": 12,
    "S1_EXTREME_7_7": 12,
    "S2_15M_LAG_NEAR1": 12,
    "S3_PERSIST_8": 12,
}

# Selection is stressed by this extra round-trip execution cost on top of the
# existing 0.12% fee assumption.
SELECT_EXTRA_COST_PCT = 0.20


def filter_defs(strategy):
    T = [( "BASE", lambda d: np.ones(len(d), dtype=bool), 0 )]
    if strategy == "L1_MOMENTUM_1H10":
        for c in [20, 25, 30, 35]:
            T.append((f"ret4h<={c}", lambda d,c=c: d["ret_4h"].to_numpy() <= c, 1))
        for x in [12.5, 15.0]:
            T.append((f"ret1h>={x}", lambda d,x=x: d["ret_1h"].to_numpy() >= x, 1))
    elif strategy == "L2_EXPLOSIVE_4H30":
        for c in [60, 75, 100]:
            T.append((f"ret4h<={c}", lambda d,c=c: d["ret_4h"].to_numpy() <= c, 1))
        for x in [5, 10]:
            T.append((f"ret1h>={x}", lambda d,x=x: d["ret_1h"].to_numpy() >= x, 1))
        T += [
            ("ret1h>=5&ret4h<=60", lambda d: (d["ret_1h"].to_numpy()>=5)&(d["ret_4h"].to_numpy()<=60), 2),
            ("ret1h>=5&ret4h<=75", lambda d: (d["ret_1h"].to_numpy()>=5)&(d["ret_4h"].to_numpy()<=75), 2),
        ]
    elif strategy == "L3_4H_LAG":
        for c in [10, 15, 20]:
            T.append((f"ret4h<={c}", lambda d,c=c: d["ret_4h"].to_numpy() <= c, 1))
        for x in [2.5, 5.0]:
            T.append((f"ret1h>={x}", lambda d,x=x: d["ret_1h"].to_numpy() >= x, 1))
    elif strategy == "S1_EXTREME_7_7":
        for c in [30, 35, 40, 50]:
            T.append((f"ret4h<={c}", lambda d,c=c: d["ret_4h"].to_numpy() <= c, 1))
        for lo,hi in [(10,35),(15,35),(15,40),(20,40)]:
            T.append((f"{lo}<=ret4h<={hi}", lambda d,lo=lo,hi=hi: (d["ret_4h"].to_numpy()>=lo)&(d["ret_4h"].to_numpy()<=hi), 2))
    elif strategy == "S2_15M_LAG_NEAR1":
        for c in [5,10,15]:
            T.append((f"ret1h<={c}", lambda d,c=c: d["ret_1h"].to_numpy() <= c, 1))
        for c in [20,30,40]:
            T.append((f"ret4h<={c}", lambda d,c=c: d["ret_4h"].to_numpy() <= c, 1))
        for z in [-0.75,-0.50,-0.25]:
            T.append((f"15Mdist>={z}", lambda d,z=z: d["15M_dist"].to_numpy() >= z, 1))
        T += [
            ("15Mdist>=-0.75&ret4h<=30", lambda d: (d["15M_dist"].to_numpy()>=-0.75)&(d["ret_4h"].to_numpy()<=30), 2),
            ("15Mdist>=-0.50&ret4h<=30", lambda d: (d["15M_dist"].to_numpy()>=-0.50)&(d["ret_4h"].to_numpy()<=30), 2),
        ]
    elif strategy == "S3_PERSIST_8":
        for r in [5,6]:
            T.append((f"rank>={r}", lambda d,r=r: d["rank"].to_numpy() >= r, 1))
        for x in [0,5]:
            T.append((f"ret1h>={x}", lambda d,x=x: d["ret_1h"].to_numpy() >= x, 1))
        for c in [20,30]:
            T.append((f"ret4h<={c}", lambda d,c=c: d["ret_4h"].to_numpy() <= c, 1))
        T.append(("rank>=5&ret4h<=30", lambda d: (d["rank"].to_numpy()>=5)&(d["ret_4h"].to_numpy()<=30), 2))
    return T


def one_trade(signal, minute_df, delay, tp, sl, horizon):
    if minute_df is None or minute_df.empty:
        return None
    signal_ts = int(signal.ts)
    entry_candle_ts = signal_ts + (delay - 1) * MIN
    er = minute_df[minute_df["ts"] == entry_candle_ts]
    if er.empty:
        return None
    entry = float(er.iloc[-1]["close"])
    if not math.isfinite(entry) or entry <= 0:
        return None
    entry_ts = signal_ts + delay * MIN
    end_ts = entry_ts + int(horizon) * MIN
    path = minute_df[(minute_df["ts"] >= entry_ts) & (minute_df["ts"] < end_ts)]
    if path.empty:
        return None

    direction = signal.direction
    if direction == "LONG":
        tp_px = entry * (1 + tp/100)
        sl_px = entry * (1 - sl/100)
    else:
        tp_px = entry * (1 - tp/100)
        sl_px = entry * (1 + sl/100)

    outcome = "TIME"
    exit_price = float(path.iloc[-1]["close"])
    exit_ts = int(path.iloc[-1]["ts"]) + MIN
    for b in path.itertuples(index=False):
        if direction == "LONG":
            hit_tp = float(b.high) >= tp_px
            hit_sl = float(b.low) <= sl_px
        else:
            hit_tp = float(b.low) <= tp_px
            hit_sl = float(b.high) >= sl_px
        if hit_tp and hit_sl:
            outcome, exit_price, exit_ts = "SL", sl_px, int(b.ts)+MIN
            break
        if hit_sl:
            outcome, exit_price, exit_ts = "SL", sl_px, int(b.ts)+MIN
            break
        if hit_tp:
            outcome, exit_price, exit_ts = "TP", tp_px, int(b.ts)+MIN
            break

    gross = ((exit_price/entry)-1)*100 if direction=="LONG" else (1-(exit_price/entry))*100
    return {
        "signal_id": int(signal.signal_id),
        "delay_min": int(delay),
        "symbol": signal.symbol,
        "strategy": signal.strategy,
        "direction": direction,
        "split_outer": signal.split_outer,
        "dev_fold": int(signal.dev_fold),
        "entry_ts": int(entry_ts),
        "exit_ts": int(exit_ts),
        "entry_price": entry,
        "exit_price": exit_price,
        "tp_pct": float(tp),
        "sl_pct": float(sl),
        "horizon_min": int(horizon),
        "outcome": outcome,
        "net_pct": float(gross - FEE_PCT),
    }


def metric(r, extra=0.0):
    r = pd.Series(r, dtype=float).dropna() - extra
    if r.empty:
        return {"n":0,"avg":np.nan,"pf":np.nan,"win":np.nan}
    return {
        "n": len(r),
        "avg": float(r.mean()),
        "pf": float(calc_pf(r)),
        "win": float((r>0).mean()*100),
    }


def variant_stats(trades, signal_ids, outer_part, folds=True, extra=0.0):
    z = trades[(trades["signal_id"].isin(signal_ids)) & (trades["split_outer"]==outer_part)]
    result={}
    for d in [1,2,3]:
        m=metric(z.loc[z.delay_min==d,"net_pct"], extra)
        for k,v in m.items(): result[f"d{d}_{k}"]=v
    avgs=[result[f"d{d}_avg"] for d in [1,2,3]]
    pfs=[result[f"d{d}_pf"] for d in [1,2,3]]
    result["min_delay_avg"]=float(np.nanmin(avgs))
    result["min_delay_pf"]=float(np.nanmin(pfs))
    result["mean_delay_avg"]=float(np.nanmean(avgs))
    if folds and outer_part=="DEV":
        fold_avg=[]
        fold_pf=[]
        fold_n=[]
        for f in range(4):
            q=z[z.dev_fold==f]
            vals=[]
            pvals=[]
            for d in [1,2,3]:
                m=metric(q.loc[q.delay_min==d,"net_pct"], extra)
                if m["n"] >= 2:
                    vals.append(m["avg"])
                    pvals.append(m["pf"])
            fold_n.append(int(q.signal_id.nunique()))
            fold_avg.append(float(np.mean(vals)) if vals else -999.0)
            fold_pf.append(float(np.mean([min(v,10) for v in pvals])) if pvals else 0.0)
        result["fold_positive_count"]=int(sum(v>0 for v in fold_avg))
        result["fold_worst_avg"]=float(min(fold_avg))
        result["fold_median_avg"]=float(np.median(fold_avg))
        result["fold_worst_pf"]=float(min(fold_pf))
        result["fold_n"]="|".join(map(str,fold_n))
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",required=True)
    ap.add_argument("--outdir",default="sharpen_results")
    ap.add_argument("--workers",type=int,default=6)
    ap.add_argument("--extra-symbols",default="LSKUSDT,TUTUSDT,LABUSDT,ALLOUSDT")
    args=ap.parse_args()
    out=Path(args.outdir); out.mkdir(parents=True,exist_ok=True)

    source,added,extra_fail=load_with_extras(args.source,args.extra_symbols)
    print(f"[SOURCE] extra_added={added} extra_failures={extra_fail}")
    signals=build_signals(source).copy()
    extra=source[["symbol","ts","15M_dist"]].drop_duplicates(["symbol","ts"])
    signals=signals.merge(extra,on=["symbol","ts"],how="left")
    signals=signals.reset_index(drop=True)
    signals["signal_id"]=np.arange(len(signals),dtype=int)

    # Fresh automated outer split: final 20% of timestamps is never used for
    # parameter selection in this script.
    all_ts=np.sort(signals.ts.unique())
    cut=all_ts[max(0,int(len(all_ts)*0.80)-1)]
    signals["split_outer"]=np.where(signals.ts<=cut,"DEV","OUTER20")

    dev_ts=np.sort(signals.loc[signals.split_outer=="DEV","ts"].unique())
    edges=np.array_split(dev_ts,4)
    fold_map={int(ts):i for i,a in enumerate(edges) for ts in a}
    signals["dev_fold"]=signals.ts.map(lambda t: fold_map.get(int(t),-1)).astype(int)
    signals.to_csv(out/"signals_outer_split.csv",index=False)

    # Max 24h path covers every exit candidate.
    fetch_sig=signals.copy()
    fetch_sig["horizon_min"]=1440
    minute_map,failures=fetch_all_minutes(fetch_sig,args.workers)
    print(f"[1M] symbols={len(minute_map)} failures={len(failures)}")

    # Precompute all exit variants once.
    rows=[]
    exit_keys={}
    for strategy,cfg in EXIT_GRID.items():
        sg=signals[signals.strategy==strategy]
        combos=[]
        for tp in TP_GRID:
            for sl in cfg["sl"]:
                for h in cfg["horizon"]:
                    combos.append((tp,sl,h))
        exit_keys[strategy]=combos
        for tp,sl,h in combos:
            for r in sg.itertuples(index=False):
                m=minute_map.get(r.symbol)
                for d in [1,2,3]:
                    tr=one_trade(r,m,d,tp,sl,h)
                    if tr:
                        tr["exit_key"]=f"{tp:g}/{sl:g}/{h}"
                        rows.append(tr)
        print(f"[PRECOMPUTE] {strategy}: {len(sg)} signals x {len(combos)} exits")
    all_trades=pd.DataFrame(rows)
    all_trades.to_csv(out/"all_variant_trades.csv.gz",index=False,compression="gzip")

    searches=[]
    selected={}
    for strategy,cfg in EXIT_GRID.items():
        sg=signals[(signals.strategy==strategy)&(signals.split_outer=="DEV")].copy()
        base_tp,base_sl,base_h=cfg["base"]
        candidates=[]
        for fname,ffn,complexity in filter_defs(strategy):
            mask=ffn(sg)
            ids=set(sg.loc[mask,"signal_id"].astype(int))
            if len(ids) < MIN_DEV_N[strategy]:
                continue
            for tp,sl,h in exit_keys[strategy]:
                key=f"{tp:g}/{sl:g}/{h}"
                t=all_trades[(all_trades.strategy==strategy)&(all_trades.exit_key==key)]
                st=variant_stats(t,ids,"DEV",folds=True,extra=SELECT_EXTRA_COST_PCT)
                # Conservative selection objective. We want all entry delays to
                # remain profitable after extra cost, and at least 3/4 dev
                # periods positive. Complexity and distance from baseline are
                # mildly penalized.
                exit_dev=(abs(tp-base_tp)/2.5 + abs(sl-base_sl)/1.0 +
                          (0 if h==base_h else 1))
                robust = (
                    st["min_delay_avg"] > 0
                    and st["min_delay_pf"] > 1.05
                    and st["fold_positive_count"] >= 3
                    and st["fold_worst_avg"] > -0.75
                )
                score=(
                    st["min_delay_avg"]
                    + 0.30*st["fold_median_avg"]
                    + 0.20*min(st["min_delay_pf"],3.0)
                    - 0.05*complexity
                    - 0.025*exit_dev
                )
                rec={
                    "strategy":strategy,"filter":fname,"complexity":complexity,
                    "tp_pct":tp,"sl_pct":sl,"horizon_min":h,
                    "exit_key":key,"dev_signal_n":len(ids),
                    "robust_pass":robust,"score":score,**st,
                }
                searches.append(rec)
                if robust:
                    candidates.append(rec)

        base_ids=set(sg.signal_id.astype(int))
        base_key=f"{base_tp:g}/{base_sl:g}/{base_h}"
        bt=all_trades[(all_trades.strategy==strategy)&(all_trades.exit_key==base_key)]
        base_st=variant_stats(bt,base_ids,"DEV",folds=True,extra=SELECT_EXTRA_COST_PCT)
        base_rec={
            "strategy":strategy,"filter":"BASE","complexity":0,
            "tp_pct":base_tp,"sl_pct":base_sl,"horizon_min":base_h,
            "exit_key":base_key,"dev_signal_n":len(base_ids),
            "robust_pass":(
                base_st["min_delay_avg"]>0 and base_st["min_delay_pf"]>1.05
                and base_st["fold_positive_count"]>=3
                and base_st["fold_worst_avg"]>-0.75
            ),
            "score":(
                base_st["min_delay_avg"]+0.30*base_st["fold_median_avg"]
                +0.20*min(base_st["min_delay_pf"],3.0)
            ),
            **base_st,
        }

        if candidates:
            best=max(candidates,key=lambda r:r["score"])
            # Do not change parameters for a microscopic development gain.
            # Baseline wins if it is robust and within 0.15 score.
            if base_rec["robust_pass"] and best["score"] < base_rec["score"]+0.15:
                best=base_rec
        else:
            best=base_rec

        # OFF is allowed only for a genuinely weak edge under development
        # stress. This avoids keeping a strategy merely to balance directions.
        if best["min_delay_avg"] < 0.10 or best["min_delay_pf"] < 1.08:
            selected[strategy]={"enabled":False,**best}
        else:
            selected[strategy]={"enabled":True,**best}
        print("[SELECT]",strategy,selected[strategy])

    search_df=pd.DataFrame(searches)
    search_df.to_csv(out/"development_search.csv.gz",index=False,compression="gzip")

    # Freeze selection, then inspect OUTER20.
    eval_rows=[]
    selected_trade_parts=[]
    base_trade_parts=[]
    for strategy,cfg in EXIT_GRID.items():
        rec=selected[strategy]
        sg_all=signals[signals.strategy==strategy]
        # locate filter callable by exact name
        fdict={name:fn for name,fn,_ in filter_defs(strategy)}
        filt=fdict[rec["filter"]]
        ids_sel=set(sg_all.loc[filt(sg_all),"signal_id"].astype(int))
        chosen=all_trades[
            (all_trades.strategy==strategy)&
            (all_trades.exit_key==rec["exit_key"])&
            (all_trades.signal_id.isin(ids_sel))
        ].copy()
        if rec["enabled"]:
            selected_trade_parts.append(chosen)

        base_tp,base_sl,base_h=cfg["base"]
        base_key=f"{base_tp:g}/{base_sl:g}/{base_h}"
        base=all_trades[(all_trades.strategy==strategy)&(all_trades.exit_key==base_key)].copy()
        base_trade_parts.append(base)

        for part in ["DEV","OUTER20"]:
            for mode,z,ids in [
                ("BASE",base,set(sg_all.signal_id.astype(int))),
                ("SHARP",chosen if rec["enabled"] else chosen.iloc[0:0],ids_sel if rec["enabled"] else set()),
            ]:
                st=variant_stats(z,ids,part,folds=False,extra=0.0) if ids else {}
                st_stress=variant_stats(z,ids,part,folds=False,extra=0.25) if ids else {}
                eval_rows.append({
                    "strategy":strategy,"part":part,"mode":mode,
                    "enabled":bool(rec["enabled"]) if mode=="SHARP" else True,
                    "filter":rec["filter"] if mode=="SHARP" else "BASE",
                    "tp_pct":rec["tp_pct"] if mode=="SHARP" else base_tp,
                    "sl_pct":rec["sl_pct"] if mode=="SHARP" else base_sl,
                    "horizon_min":rec["horizon_min"] if mode=="SHARP" else base_h,
                    **{f"raw_{k}":v for k,v in st.items()},
                    **{f"stress25_{k}":v for k,v in st_stress.items()},
                })

    base_trades=pd.concat(base_trade_parts,ignore_index=True)
    sharp_trades=pd.concat(selected_trade_parts,ignore_index=True) if selected_trade_parts else base_trades.iloc[0:0]
    base_trades["mode"]="BASE"
    sharp_trades["mode"]="SHARP"
    pd.concat([base_trades,sharp_trades],ignore_index=True).to_csv(out/"base_vs_sharp_trades.csv.gz",index=False,compression="gzip")
    pd.DataFrame(eval_rows).to_csv(out/"strategy_outer_evaluation.csv",index=False)

    sel_rows=[]
    for strategy,r in selected.items():
        sel_rows.append({
            "strategy":strategy,"enabled":r["enabled"],"filter":r["filter"],
            "tp_pct":r["tp_pct"],"sl_pct":r["sl_pct"],"horizon_min":r["horizon_min"],
            "dev_signal_n":r["dev_signal_n"],"dev_score":r["score"],
            "dev_min_delay_avg_after_extra20":r["min_delay_avg"],
            "dev_min_delay_pf_after_extra20":r["min_delay_pf"],
            "dev_positive_folds":r["fold_positive_count"],
            "dev_worst_fold_avg_after_extra20":r["fold_worst_avg"],
        })
    pd.DataFrame(sel_rows).to_csv(out/"selected_candidate3.csv",index=False)

    # Whole-strategy aggregate by outer split / delay / execution-cost stress.
    agg=[]
    for part in ["DEV","OUTER20","ALL"]:
        for mode,z in [("BASE",base_trades),("SHARP",sharp_trades)]:
            zz=z if part=="ALL" else z[z.split_outer==part]
            for cost in [0.0,0.10,0.25,0.50]:
                for d in [1,2,3]:
                    q=zz[zz.delay_min==d]
                    m=metric(q.net_pct,cost)
                    agg.append({
                        "part":part,"mode":mode,"extra_cost_pct":cost,
                        "delay_min":d,"signals":q.signal_id.nunique(),**m
                    })
    pd.DataFrame(agg).to_csv(out/"aggregate_robustness.csv",index=False)

    # User's current 30%-per-position / 200% cap semantics.
    port=[]
    snaps=[]
    for part in ["OUTER20","ALL"]:
        for mode,z in [("BASE",base_trades),("SHARP",sharp_trades)]:
            zz=z if part=="ALL" else z[z.split_outer==part]
            for cost in [0.0,0.25]:
                zc=zz.copy()
                zc["net_pct"]=zc["net_pct"]-cost
                for d in [1,2,3]:
                    row,ss,_=simulate(zc,minute_map,d,2.0)
                    row.update({"part":part,"mode":mode,"extra_cost_pct":cost})
                    port.append(row)
                    if not ss.empty:
                        ss["part"]=part; ss["mode"]=mode; ss["extra_cost_pct"]=cost
                        snaps.append(ss)
    pd.DataFrame(port).to_csv(out/"portfolio_200pct.csv",index=False)
    if snaps:
        pd.concat(snaps,ignore_index=True).to_csv(out/"portfolio_snapshots.csv",index=False)

    print("\n=== CANDIDATE 3 ===")
    print(pd.DataFrame(sel_rows).to_string(index=False))
    print("\n=== OUTER20 STRATEGY CHECK ===")
    ev=pd.DataFrame(eval_rows)
    cols=["strategy","mode","enabled","filter","tp_pct","sl_pct","horizon_min",
          "raw_d1_n","raw_d1_avg","raw_d1_pf","raw_d2_avg","raw_d2_pf","raw_d3_avg","raw_d3_pf",
          "stress25_min_delay_avg","stress25_min_delay_pf"]
    print(ev[ev.part=="OUTER20"][cols].to_string(index=False))
    print("\n=== OUTER20 / 200% PORTFOLIO ===")
    pp=pd.DataFrame(port)
    show=["part","mode","extra_cost_pct","delay_min","signals_available","trades_taken","skipped_cap",
          "win_rate_pct","profit_factor","return_pct","max_drawdown_pct",
          "max_open_positions","max_losing_positions"]
    print(pp[pp.part=="OUTER20"][show].to_string(index=False))


if __name__=="__main__":
    main()
