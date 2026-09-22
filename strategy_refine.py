import argparse
from pathlib import Path
import pandas as pd

from precision_backtest import load_with_extras, build_signals, fetch_all_minutes, one_trade
from exposure_risk import simulate

VARIANTS = {
    "BASE": lambda x: pd.Series(True, index=x.index),
    "NO_S3": lambda x: x["strategy"] != "S3_PERSIST_8",
    "C1_1": lambda x: (
        x["strategy"].isin(["L1_MOMENTUM_1H10","L2_EXPLOSIVE_4H30","L3_4H_LAG","S2_15M_LAG_NEAR1"])
        | ((x["strategy"]=="S1_EXTREME_7_7") & (x["ret_4h"] <= 35.0))
    ),
    "GUARD": lambda x: (
        (x["strategy"]=="L1_MOMENTUM_1H10")
        | ((x["strategy"]=="L2_EXPLOSIVE_4H30") & (x["ret_4h"] <= 75.0))
        | (x["strategy"]=="L3_4H_LAG")
        | ((x["strategy"]=="S1_EXTREME_7_7") & (x["ret_4h"] <= 35.0))
        | (x["strategy"]=="S2_15M_LAG_NEAR1")
    ),
    "SHARP": lambda x: (
        (x["strategy"]=="L1_MOMENTUM_1H10")
        | ((x["strategy"]=="L2_EXPLOSIVE_4H30") & (x["ret_4h"] <= 75.0))
        | ((x["strategy"]=="L3_4H_LAG") & (x["ret_4h"] >= 5.0))
        | ((x["strategy"]=="S1_EXTREME_7_7") & (x["ret_4h"] <= 35.0))
        | (x["strategy"]=="S2_15M_LAG_NEAR1")
    ),
    "CORE_LONG": lambda x: x["direction"]=="LONG",
    "CORE_S1C35": lambda x: (
        x["direction"].eq("LONG")
        | ((x["strategy"]=="S1_EXTREME_7_7") & (x["ret_4h"] <= 35.0))
    ),
    "CORE_S1C35_S3": lambda x: (
        x["direction"].eq("LONG")
        | ((x["strategy"]=="S1_EXTREME_7_7") & (x["ret_4h"] <= 35.0))
        | (x["strategy"]=="S3_PERSIST_8")
    ),
}

COSTS = [0.12, 0.20, 0.30, 0.50]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--outdir", default="strategy_refine_results")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--extra-symbols", default="LSKUSDT,TUTUSDT,LABUSDT,ALLOUSDT")
    args=ap.parse_args()
    out=Path(args.outdir); out.mkdir(parents=True, exist_ok=True)

    source,added,fail=load_with_extras(args.source,args.extra_symbols)
    signals=build_signals(source)
    minute_map,fetch_fail=fetch_all_minutes(signals,args.workers)
    print(f"[DATA] signals={len(signals)} symbols={signals.symbol.nunique()} fetch_fail={len(fetch_fail)}")

    rows=[]
    for sig in signals.itertuples(index=False):
        m=minute_map.get(sig.symbol)
        for delay in (1,2,3):
            tr=one_trade(sig,m,delay)
            if tr:
                rows.append(tr)
    trades=pd.DataFrame(rows)

    # Bring signal-time features into the trade rows.
    sf=signals[["symbol","ts","strategy","ret_1h","ret_4h","rank","exact_count","streak"]].rename(columns={"ts":"signal_ts"})
    trades=trades.merge(sf,on=["symbol","signal_ts","strategy"],how="left")
    trades.to_csv(out/"all_candidate1_trades.csv",index=False)

    summaries=[]
    strat_rows=[]
    for vname,fn in VARIANTS.items():
        keep_keys=signals.loc[fn(signals),["symbol","ts","strategy"]].rename(columns={"ts":"signal_ts"})
        vt=trades.merge(keep_keys.assign(_keep=1),on=["symbol","signal_ts","strategy"],how="inner")

        for split in ("train70","test30","ALL"):
            base=vt if split=="ALL" else vt[vt["split"]==split]
            for total_cost in COSTS:
                extra=max(0.0,total_cost-0.12)
                stressed=base.copy()
                stressed["net_pct"]=stressed["net_pct"]-extra
                for delay in (1,2,3):
                    row,snap,curve=simulate(stressed,minute_map,delay,2.0)
                    row.update({
                        "variant":vname,
                        "split":split,
                        "total_cost_pct":total_cost,
                    })
                    summaries.append(row)

        # Strategy diagnostics at baseline cost.
        for split in ("train70","test30"):
            z=vt[vt["split"]==split]
            for delay in (1,2,3):
                zz=z[z["delay_min"]==delay]
                for st,g in zz.groupby("strategy"):
                    pos=g.loc[g.net_pct>0,"net_pct"].sum()
                    neg=-g.loc[g.net_pct<0,"net_pct"].sum()
                    strat_rows.append({
                        "variant":vname,"split":split,"delay_min":delay,
                        "strategy":st,"n":len(g),
                        "win_rate_pct":(g.net_pct>0).mean()*100,
                        "avg_net_pct":g.net_pct.mean(),
                        "profit_factor":pos/neg if neg>0 else 999.0,
                    })

    summary=pd.DataFrame(summaries)
    summary.to_csv(out/"variant_portfolio_summary.csv",index=False)
    pd.DataFrame(strat_rows).to_csv(out/"variant_strategy_summary.csv",index=False)

    cols=["variant","split","total_cost_pct","delay_min","trades_taken","skipped_cap",
          "win_rate_pct","profit_factor","return_pct","max_drawdown_pct",
          "max_open_positions","max_losing_positions"]
    print("\n=== 200% PORTFOLIO: TEST30 ===")
    print(summary[(summary.split=="test30") & (summary.total_cost_pct==0.12)][cols].to_string(index=False))
    print("\n=== COST STRESS: TEST30 +1 ===")
    print(summary[(summary.split=="test30") & (summary.delay_min==1)][cols].to_string(index=False))
    print("\n=== FULL +1 BASE COST ===")
    print(summary[(summary.split=="ALL") & (summary.delay_min==1) & (summary.total_cost_pct==0.12)][cols].to_string(index=False))

if __name__=="__main__":
    main()
