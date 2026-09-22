import argparse
import math
from pathlib import Path
import numpy as np
import pandas as pd

from precision_backtest import (
    load_with_extras, build_signals, fetch_all_minutes, calc_pf, FEE_PCT, MIN
)
from exposure_risk import simulate

TP_GRID = [2.5, 3.0, 4.0, 5.0, 6.0, 7.5, 10.0, 12.5]
BASE_HORIZON = {
    "L1_MOMENTUM_1H10": 720,
    "L2_EXPLOSIVE_4H30": 60,
    "L3_4H_LAG": 720,
    "S1_EXTREME_7_7": 720,
    "S2_15M_LAG_NEAR1": 240,
    "S3_PERSIST_8": 60,
}
HORIZON_GRID = {
    "L1_MOMENTUM_1H10": [240, 480, 720, 1440],
    "L2_EXPLOSIVE_4H30": [30, 60, 120, 240],
    "L3_4H_LAG": [240, 480, 720, 1440],
    "S1_EXTREME_7_7": [240, 480, 720, 1440],
    "S2_15M_LAG_NEAR1": [120, 240, 480, 720],
    "S3_PERSIST_8": [30, 60, 120, 240],
}
SL = {
    "L1_MOMENTUM_1H10": 5.0,
    "L2_EXPLOSIVE_4H30": 2.5,
    "L3_4H_LAG": 4.0,
    "S1_EXTREME_7_7": 4.0,
    "S2_15M_LAG_NEAR1": 4.0,
    "S3_PERSIST_8": 5.0,
}


def add_rv4h(source):
    """Pre-signal 4h realized volatility from 16 completed 15m returns.

    We intentionally shift by one snapshot so the volatility classifier never
    uses the signal-boundary return itself.
    """
    x=source.sort_values(["symbol","ts"]).copy()
    g=x.groupby("symbol",sort=False)
    r=g["price"].pct_change()
    # rolling std of 16 15m returns, scaled to a 4h standard deviation
    rv=(r.groupby(x["symbol"]).rolling(16,min_periods=12).std().reset_index(level=0,drop=True)
        * math.sqrt(16) * 100.0)
    x["rv4h_pct"]=rv.groupby(x["symbol"]).shift(1)
    return x


def dynamic_trade(sig, minute_df, delay, tp, horizon):
    if minute_df is None or minute_df.empty:
        return None
    signal_ts=int(sig.ts)
    entry_candle_ts=signal_ts+(delay-1)*MIN
    er=minute_df[minute_df["ts"]==entry_candle_ts]
    if er.empty:
        return None
    entry=float(er.iloc[-1]["close"])
    entry_time=signal_ts+delay*MIN
    end=entry_time+int(horizon)*MIN
    path=minute_df[(minute_df["ts"]>=entry_time)&(minute_df["ts"]<end)]
    if path.empty:
        return None

    direction=sig.direction
    sl=SL[sig.strategy]
    if direction=="LONG":
        tp_price=entry*(1+tp/100)
        sl_price=entry*(1-sl/100)
    else:
        tp_price=entry*(1-tp/100)
        sl_price=entry*(1+sl/100)

    outcome="TIME"
    exit_price=float(path.iloc[-1]["close"])
    exit_ts=int(path.iloc[-1]["ts"])+MIN
    for bar in path.itertuples(index=False):
        if direction=="LONG":
            hit_tp=float(bar.high)>=tp_price
            hit_sl=float(bar.low)<=sl_price
        else:
            hit_tp=float(bar.low)<=tp_price
            hit_sl=float(bar.high)>=sl_price
        if hit_tp and hit_sl:
            outcome="SL"; exit_price=sl_price; exit_ts=int(bar.ts)+MIN; break
        if hit_sl:
            outcome="SL"; exit_price=sl_price; exit_ts=int(bar.ts)+MIN; break
        if hit_tp:
            outcome="TP"; exit_price=tp_price; exit_ts=int(bar.ts)+MIN; break

    gross=(exit_price/entry-1)*100 if direction=="LONG" else (1-exit_price/entry)*100
    return {
        "delay_min":delay, "symbol":sig.symbol, "strategy":sig.strategy,
        "direction":direction, "split":sig.split, "signal_ts":signal_ts,
        "entry_ts":entry_time, "exit_ts":exit_ts,
        "entry_price":entry, "exit_price":exit_price,
        "tp_pct":tp, "sl_pct":sl, "horizon_min":int(horizon),
        "outcome":outcome, "gross_pct":gross, "net_pct":gross-FEE_PCT,
        "rv4h_pct":float(sig.rv4h_pct), "vol_bucket":sig.vol_bucket,
    }


def robustness_score(df):
    """Select on TRAIN only: maximize the worst entry-delay expectancy."""
    rows=[]
    for d,g in df.groupby("delay_min"):
        r=g["net_pct"]
        rows.append({
            "delay":int(d), "n":len(g), "avg":r.mean(),
            "pf":calc_pf(r), "win":(r>0).mean()*100,
        })
    if not rows:
        return (-999,-999,-999), rows
    avgs=[r["avg"] for r in rows]
    pfs=[min(r["pf"],20) if math.isfinite(r["pf"]) else 20 for r in rows]
    # Primary objective: positive even at the worst +1/+2/+3 entry delay.
    # Secondary: mean expectancy, then mean PF.
    score=(min(avgs), float(np.mean(avgs)), float(np.mean(pfs)))
    return score, rows


def eval_tp(sig, minute_map, tp, horizon_map):
    rows=[]
    for r in sig.itertuples(index=False):
        for d in (1,2,3):
            tr=dynamic_trade(r,minute_map.get(r.symbol),d,tp,horizon_map[r.strategy])
            if tr: rows.append(tr)
    return pd.DataFrame(rows)


def eval_mapping(sig, minute_map, tp_map, horizon_map):
    rows=[]
    for r in sig.itertuples(index=False):
        tp=tp_map[r.vol_bucket]
        horizon=horizon_map[r.strategy]
        for d in (1,2,3):
            tr=dynamic_trade(r,minute_map.get(r.symbol),d,tp,horizon)
            if tr: rows.append(tr)
    return pd.DataFrame(rows)


def summary_by(df, cols):
    out=[]
    for keys,g in df.groupby(cols,dropna=False):
        if not isinstance(keys,tuple): keys=(keys,)
        row={c:v for c,v in zip(cols,keys)}
        r=g["net_pct"]
        row.update(n=len(g),win_rate_pct=(r>0).mean()*100,avg_net_pct=r.mean(),
                   profit_factor=calc_pf(r),tp_rate_pct=(g["outcome"]=="TP").mean()*100,
                   sl_rate_pct=(g["outcome"]=="SL").mean()*100,
                   time_rate_pct=(g["outcome"]=="TIME").mean()*100)
        out.append(row)
    return pd.DataFrame(out)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",required=True)
    ap.add_argument("--outdir",default="tp_horizon_results")
    ap.add_argument("--workers",type=int,default=6)
    ap.add_argument("--extra-symbols",default="LSKUSDT,TUTUSDT,LABUSDT,ALLOUSDT")
    args=ap.parse_args()
    out=Path(args.outdir); out.mkdir(parents=True,exist_ok=True)

    source,added,fail=load_with_extras(args.source,args.extra_symbols)
    source=add_rv4h(source)
    signals=build_signals(source)
    vol=source[["symbol","ts","rv4h_pct"]].drop_duplicates(["symbol","ts"])
    signals=signals.merge(vol,on=["symbol","ts"],how="left")
    signals=signals[signals["rv4h_pct"].notna()].copy()

    train_rv=signals.loc[signals["split"]=="train70","rv4h_pct"]
    q1,q2=train_rv.quantile([1/3,2/3]).tolist()
    signals["vol_bucket"]=pd.cut(
        signals["rv4h_pct"],[-np.inf,q1,q2,np.inf],
        labels=["LOW","MID","HIGH"],right=True
    ).astype(str)
    signals.to_csv(out/"signals_with_vol.csv",index=False)
    pd.DataFrame([{"low_mid_cut_pct":q1,"mid_high_cut_pct":q2,
                   "train_signal_n":len(train_rv)}]).to_csv(out/"vol_thresholds.csv",index=False)
    print(f"[VOL] train terciles LOW/MID={q1:.4f}% MID/HIGH={q2:.4f}% n={len(train_rv)}")

    # Fetch up to 24h so every horizon candidate uses the same minute cache.
    fetch_sig=signals.copy()
    fetch_sig["horizon_min"]=1440
    minute_map,fetch_fail=fetch_all_minutes(fetch_sig,args.workers)
    print(f"[1M] symbols={len(minute_map)} failures={len(fetch_fail)}")

    # Stage 1: TP by volatility bucket, original Candidate1 time limits.
    tp_search=[]
    tp_map={}
    train=signals[signals["split"]=="train70"]
    for bucket in ["LOW","MID","HIGH"]:
        sg=train[train["vol_bucket"]==bucket]
        best=None
        for tp in TP_GRID:
            tr=eval_tp(sg,minute_map,tp,BASE_HORIZON)
            score,details=robustness_score(tr)
            rec={"bucket":bucket,"tp_pct":tp,"score_worst_delay_avg":score[0],
                 "score_mean_avg":score[1],"score_mean_pf":score[2],"n_signal":len(sg)}
            for d in (1,2,3):
                z=[x for x in details if x["delay"]==d]
                if z:
                    rec[f"d{d}_avg"]=z[0]["avg"]; rec[f"d{d}_pf"]=z[0]["pf"]; rec[f"d{d}_win"]=z[0]["win"]
            tp_search.append(rec)
            key=score+( -abs(tp-7.5), )
            if best is None or key>best[0]:
                best=(key,tp)
        tp_map[bucket]=best[1]
        print(f"[SELECT TP] {bucket}: {best[1]}%")

    pd.DataFrame(tp_search).to_csv(out/"tp_grid_train.csv",index=False)

    # Stage 2: strategy horizon by chosen volatility TP map.
    horizon_search=[]
    horizon_map={}
    for strat,cands in HORIZON_GRID.items():
        sg=train[train["strategy"]==strat]
        best=None
        for h in cands:
            hm=BASE_HORIZON.copy(); hm[strat]=h
            tr=eval_mapping(sg,minute_map,tp_map,hm)
            score,details=robustness_score(tr)
            rec={"strategy":strat,"horizon_min":h,"score_worst_delay_avg":score[0],
                 "score_mean_avg":score[1],"score_mean_pf":score[2],"n_signal":len(sg)}
            for d in (1,2,3):
                z=[x for x in details if x["delay"]==d]
                if z:
                    rec[f"d{d}_avg"]=z[0]["avg"]; rec[f"d{d}_pf"]=z[0]["pf"]; rec[f"d{d}_win"]=z[0]["win"]
            horizon_search.append(rec)
            # Prefer current horizon on exact ties to avoid needless complexity.
            key=score+(-abs(h-BASE_HORIZON[strat]),)
            if best is None or key>best[0]:
                best=(key,h)
        horizon_map[strat]=best[1]
        print(f"[SELECT HORIZON] {strat}: {best[1]} min")

    pd.DataFrame(horizon_search).to_csv(out/"horizon_grid_train.csv",index=False)

    # One conservative TP re-check after horizon selection.
    tp_search2=[]; tp_map2={}
    for bucket in ["LOW","MID","HIGH"]:
        sg=train[train["vol_bucket"]==bucket]
        best=None
        for tp in TP_GRID:
            tr=eval_tp(sg,minute_map,tp,horizon_map)
            score,details=robustness_score(tr)
            rec={"bucket":bucket,"tp_pct":tp,"score_worst_delay_avg":score[0],
                 "score_mean_avg":score[1],"score_mean_pf":score[2],"n_signal":len(sg)}
            for d in (1,2,3):
                z=[x for x in details if x["delay"]==d]
                if z:
                    rec[f"d{d}_avg"]=z[0]["avg"]; rec[f"d{d}_pf"]=z[0]["pf"]; rec[f"d{d}_win"]=z[0]["win"]
            tp_search2.append(rec)
            key=score+(-abs(tp-tp_map[bucket]),)
            if best is None or key>best[0]: best=(key,tp)
        tp_map2[bucket]=best[1]
        print(f"[FINAL TP] {bucket}: {best[1]}%")
    tp_map=tp_map2
    pd.DataFrame(tp_search2).to_csv(out/"tp_grid_train_final.csv",index=False)

    # Baseline and selected mapping, all splits, all entry delays.
    baseline=eval_mapping(signals,minute_map,{"LOW":10.0,"MID":10.0,"HIGH":10.0},BASE_HORIZON)
    adaptive=eval_mapping(signals,minute_map,tp_map,horizon_map)
    baseline["mode"]="BASE_TP10"
    adaptive["mode"]="ADAPTIVE"
    both=pd.concat([baseline,adaptive],ignore_index=True)
    both.to_csv(out/"all_trades_compare.csv",index=False)

    stats=summary_by(both,["mode","split","delay_min"])
    stats.to_csv(out/"trade_summary.csv",index=False)

    bucket_stats=summary_by(both,["mode","split","delay_min","vol_bucket"])
    bucket_stats.to_csv(out/"bucket_summary.csv",index=False)

    strat_stats=summary_by(both,["mode","split","delay_min","strategy"])
    strat_stats.to_csv(out/"strategy_summary.csv",index=False)

    # Account-level test with user's current 30%-per-position and 200% cap.
    port=[]
    snap=[]
    for mode,df in [("BASE_TP10",baseline),("ADAPTIVE",adaptive)]:
        for split in ["train70","test30","ALL"]:
            use=df if split=="ALL" else df[df["split"]==split]
            for d in (1,2,3):
                row,ss,_curve=simulate(use,minute_map,d,2.0)
                row["mode"]=mode; row["split"]=split
                port.append(row)
                if not ss.empty:
                    ss["mode"]=mode; ss["split"]=split
                    snap.append(ss)
    pd.DataFrame(port).to_csv(out/"portfolio_200pct.csv",index=False)
    if snap: pd.concat(snap,ignore_index=True).to_csv(out/"portfolio_worst_snapshots.csv",index=False)

    mapping_rows=[{"parameter":"rv4h_low_mid_cut_pct","value":q1},
                  {"parameter":"rv4h_mid_high_cut_pct","value":q2}]
    mapping_rows += [{"parameter":f"tp_{k}_pct","value":v} for k,v in tp_map.items()]
    mapping_rows += [{"parameter":f"horizon_{k}_min","value":v} for k,v in horizon_map.items()]
    pd.DataFrame(mapping_rows).to_csv(out/"selected_mapping.csv",index=False)

    print("\n=== SELECTED MAPPING ===")
    print(f"RV4H cuts: {q1:.4f}% / {q2:.4f}%")
    print("TP:",tp_map)
    print("HORIZON:",horizon_map)
    print("\n=== TRADE TEST30 ===")
    print(stats[stats["split"]=="test30"].to_string(index=False))
    print("\n=== PORTFOLIO 200% TEST30 ===")
    ps=pd.DataFrame(port)
    print(ps[ps["split"]=="test30"][[
        "mode","delay_min","trades_taken","skipped_cap","win_rate_pct","profit_factor",
        "return_pct","max_drawdown_pct","max_open_positions","max_losing_positions"
    ]].to_string(index=False))

if __name__=="__main__":
    main()
