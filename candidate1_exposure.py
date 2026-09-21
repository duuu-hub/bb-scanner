import argparse
from pathlib import Path
import pandas as pd

from precision_backtest import (
    load_with_extras, build_signals, fetch_all_minutes, one_trade
)
from exposure_risk import simulate

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",required=True)
    ap.add_argument("--outdir",default="candidate1_exposure_results")
    ap.add_argument("--workers",type=int,default=6)
    ap.add_argument("--extra-symbols",default="LSKUSDT,TUTUSDT,LABUSDT,ALLOUSDT")
    args=ap.parse_args()

    out=Path(args.outdir); out.mkdir(parents=True,exist_ok=True)

    source,added,fail=load_with_extras(args.source,args.extra_symbols)
    signals=build_signals(source)
    signals.to_csv(out/"candidate1_signals.csv",index=False)
    print(f"[C1] symbols={source['symbol'].nunique()} signals={len(signals)} added={added} extra_fail={fail}")

    minute_map,fetch_fail=fetch_all_minutes(signals,args.workers)
    print(f"[1M] fetch_failures={len(fetch_fail)}")

    rows=[]
    for sig in signals.itertuples(index=False):
        m=minute_map.get(sig.symbol)
        for delay in (1,2,3):
            tr=one_trade(sig,m,delay)
            if tr:
                rows.append(tr)
    trades=pd.DataFrame(rows)
    trades.to_csv(out/"candidate1_trades.csv",index=False)

    sums=[]; snaps=[]; curves=[]
    for delay in (1,2,3):
        for cap in (1.0,2.0,3.0):
            row,snap,curve=simulate(trades,minute_map,delay,cap)
            sums.append(row)
            if not snap.empty:
                snap["candidate"]="CANDIDATE1_6STRAT"
                snaps.append(snap)
            curve["delay_min"]=delay
            curve["cap_multiple"]=cap
            curves.append(curve)

    summary=pd.DataFrame(sums)
    summary.to_csv(out/"candidate1_exposure_summary.csv",index=False)
    if snaps:
        pd.concat(snaps,ignore_index=True).to_csv(out/"candidate1_worst_snapshots.csv",index=False)
    pd.concat(curves,ignore_index=True).to_csv(
        out/"candidate1_equity_curves.csv.gz",index=False,compression="gzip"
    )

    print("\n=== CANDIDATE1 6-STRATEGY EXPOSURE SUMMARY ===")
    print(summary.to_string(index=False))

if __name__=="__main__":
    main()
