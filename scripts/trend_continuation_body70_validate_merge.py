#!/usr/bin/env python3
from __future__ import annotations
import glob, json
from pathlib import Path
import numpy as np
import pandas as pd
from trend_continuation_body70_validate import metrics, concurrency_stats, portfolio_proxy, DAY_MS, COSTS, DELAYS

def main():
    fs=sorted(glob.glob("shard_results/**/trades.csv.gz",recursive=True))
    if len(fs)!=8:
        raise RuntimeError(f"expected 8 shard trade files, got {len(fs)}")
    t=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    t=t.sort_values(["entry_ts","symbol"]).reset_index(drop=True)
    t.to_csv("merged_trades.csv.gz",index=False,compression="gzip")

    rows=[]
    for delay in DELAYS:
        td=t[t.delay_min.eq(delay)]
        for split in ("TRAIN","HOLDOUT"):
            z=td[td.split.eq(split)]
            for cost in COSTS:
                rec={"delay_min":int(delay),"split":split,"cost_bp":int(cost),**metrics(z,cost),**concurrency_stats(z)}
                rec["portfolio_risk_0_5"]=portfolio_proxy(z,cost,0.5)
                rec["portfolio_risk_1_0"]=portfolio_proxy(z,cost,1.0)
                rows.append(rec)

    canon=t[t.delay_min.eq(0)].copy()
    canon["year"]=pd.to_datetime(canon.signal_ts,unit="ms",utc=True).dt.year
    years=[{"year":int(y),**metrics(g,20)} for y,g in canon.groupby("year")]

    recent=[]
    if len(canon):
        max_ts=int(canon.signal_ts.max())
        for days in (120,180):
            g=canon[canon.signal_ts>=max_ts-days*DAY_MS]
            recent.append({"window_days":days,"anchor_max_ts":max_ts,**metrics(g,20)})

    canon["net20"]=canon.gross_pct-0.20
    syms=[]
    for sym,g in canon.groupby("symbol"):
        syms.append({"symbol":sym,"n":int(len(g)),"sum_net_pct":float(g.net20.sum()),"avg_net_pct":float(g.net20.mean())})
    syms=sorted(syms,key=lambda x:x["sum_net_pct"],reverse=True)
    top10_profit=sum(max(0,x["sum_net_pct"]) for x in syms[:10])
    total_positive=sum(max(0,x["sum_net_pct"]) for x in syms)
    concentration={
        "symbols":len(syms),
        "top10_positive_contribution_share":top10_profit/total_positive if total_positive>0 else None,
        "top10_symbols":syms[:10],
        "bottom10_symbols":syms[-10:],
    }

    summary={
        "shards":len(fs),
        "candidate":"8h>=20% top10 fresh transition + bullish body ratio>=0.70; next15m open; TP3 SL5 6h",
        "summary_rows":rows,
        "yearly_20bp_immediate":years,
        "recent_20bp_immediate":recent,
        "symbol_concentration_20bp_immediate":concentration,
    }
    Path("merged_summary.json").write_text(json.dumps(summary,indent=2,default=str))
    pd.DataFrame(years).to_csv("merged_yearly.csv",index=False)
    pd.DataFrame(recent).to_csv("merged_recent_windows.csv",index=False)
    pd.DataFrame(syms).to_csv("merged_symbol_stats.csv",index=False)
    print("BODY70_VALIDATION_MERGE_JSON")
    print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__": main()
