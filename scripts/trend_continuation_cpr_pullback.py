#!/usr/bin/env python3
"""CPR attempt for 5Y trend-continuation LONG.

Frozen baseline:
- signal: 8h return >= +20% and cross-sectional top 10%, fresh transition only
- old entry: next 15m open
- old exit: TP +3%, SL -5%, max hold 6h

CPR change (one causal change only):
- do NOT chase immediately
- after signal, allow a 2h setup window
- require pullback from the original next-open reference by 0.5/1.0/1.5/2.0%
- after pullback has happened, require a completed 15m close > prior 15m high
- enter at the NEXT 15m open
- exit stays TP +3%, SL -5%, max 6h
- 20/40bp round-trip costs
- Train/Holdout split remains signal-year based
- one active setup/trade per symbol; overlapping later signals are skipped
"""
from __future__ import annotations
import argparse, glob, json, os
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
import trend_continuation_first_touch as base

BAR_MS = base.BAR_MS
DAY_MS = base.DAY_MS
PULLBACKS = (0.5, 1.0, 1.5, 2.0)
SETUP_BARS = 8  # 2h
TP_PCT = 3.0
SL_PCT = 5.0
HOLD_LABEL = "6h"
COST_BPS = (20, 40)

base.TP_PCT = TP_PCT

def sym_from_path(p: str) -> str:
    return base.sym_from_path(p)

def find_entry(raw, ref_ts: int, ref_price: float, depth_pct: float):
    ts, op, hi, lo, cl = raw["ts"], raw["open"], raw["high"], raw["low"], raw["close"]
    idx = int(np.searchsorted(ts, ref_ts))
    if idx >= len(ts) or int(ts[idx]) != int(ref_ts):
        return {"status":"ENTRY_MISMATCH"}
    if ref_price <= 0 or abs(float(op[idx]) / ref_price - 1.0) > 1e-9:
        return {"status":"ENTRY_MISMATCH"}

    trigger = ref_price * (1.0 - depth_pct / 100.0)
    pulled = False
    for k in range(SETUP_BARS):
        j = idx + k
        expected = ref_ts + k * BAR_MS
        if j >= len(ts) or int(ts[j]) != int(expected):
            return {"status":"DATA_GAP"}
        if float(lo[j]) <= trigger:
            pulled = True
        if pulled and j > 0 and float(cl[j]) > float(hi[j-1]):
            e = j + 1
            ets = ref_ts + (k + 1) * BAR_MS
            if e >= len(ts) or int(ts[e]) != int(ets):
                return {"status":"DATA_GAP"}
            return {
                "status":"ENTRY",
                "entry_ts":int(ets),
                "entry":float(op[e]),
                "reclaim_bar_ts":int(ts[j]),
                "bars_to_entry":k+1,
            }
    return {"status":"NO_SETUP"}

def replay_depth(events, raw_paths, depth_pct: float, split: str):
    a = events[events["split"].eq(split)].sort_values(["symbol","entry_ts"])
    gross_vals = []
    statuses = Counter()
    misc = Counter()
    bars_to_entry = []
    accepted_ts = []

    for sym, g in a.groupby("symbol", sort=True):
        p = raw_paths.get(sym)
        if p is None:
            misc["ENTRY_MISMATCH"] += len(g)
            continue
        raw = base.load_raw_symbol(p)
        busy_until = -1
        for row in g.itertuples(index=False):
            sig_ref_ts = int(row.entry_ts)
            if sig_ref_ts < busy_until:
                misc["overlap_skips"] += 1
                continue

            # Reserve setup window so nearby fresh-transition signals cannot duplicate one setup.
            setup_deadline = sig_ref_ts + SETUP_BARS * BAR_MS
            busy_until = setup_deadline

            ent = find_entry(raw, sig_ref_ts, float(row.entry), depth_pct)
            st = ent["status"]
            if st == "NO_SETUP":
                misc["no_setup"] += 1
                continue
            if st != "ENTRY":
                misc[st] += 1
                continue

            rs = base.event_outcomes(sym, raw, ent["entry_ts"], ent["entry"], SL_PCT)
            rec = rs[HOLD_LABEL]
            rst = rec.get("status")
            if rst in ("DATA_GAP","ENTRY_MISMATCH","EXIT_MISMATCH") or "gross_pct" not in rec:
                misc[rst] += 1
                continue

            busy_until = int(rec["exit_ts"])
            gross_vals.append(float(rec["gross_pct"]))
            statuses[rst] += 1
            bars_to_entry.append(int(ent["bars_to_entry"]))
            accepted_ts.append(int(ent["entry_ts"]))

    return gross_vals, statuses, misc, bars_to_entry, accepted_ts

def summarize(gross_vals, statuses, misc, bars_to_entry, accepted_ts, cost_bp, signal_n):
    vals = [x - cost_bp / 100.0 for x in gross_vals]
    n = len(vals)
    pos = sum(x for x in vals if x > 0)
    neg = -sum(x for x in vals if x < 0)
    span_days = None
    if accepted_ts:
        span_days = max(1.0, (max(accepted_ts)-min(accepted_ts))/DAY_MS + 1.0)
    return {
        "signal_n":int(signal_n),
        "n":n,
        "setup_fill_pct":(n/signal_n*100.0) if signal_n else None,
        "overlap_skips":int(misc["overlap_skips"]),
        "no_setup":int(misc["no_setup"]),
        "excluded":int(sum(v for k,v in misc.items() if k not in ("overlap_skips","no_setup"))),
        "excluded_detail":json.dumps({k:int(v) for k,v in misc.items() if k not in ("overlap_skips","no_setup")}, sort_keys=True),
        "tp_n":int(statuses["TP"]),
        "sl_n":int(statuses["SL"]),
        "time_n":int(statuses["TIME"]),
        "win_n":int(sum(x>0 for x in vals)),
        "sum_net_pct":float(sum(vals)),
        "gross_profit_net_pct":float(pos),
        "gross_loss_abs_net_pct":float(neg),
        "win_rate_net_pct":(sum(x>0 for x in vals)/n*100.0) if n else None,
        "avg_net_pct":(sum(vals)/n) if n else None,
        "pf_net":(pos/neg) if neg>0 else None,
        "avg_bars_to_entry":float(np.mean(bars_to_entry)) if bars_to_entry else None,
        "trades_per_day":(n/span_days) if n and span_days else None,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--events-in", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", required=True)
    a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)

    events=pd.read_csv(a.events_in,compression="infer")
    events=events[
        events["lookback"].eq("8h")
        & np.isclose(events["tail"].astype(float),0.10)
        & np.isclose(events["threshold_pct"].astype(float),20.0)
    ].copy()
    if events.empty:
        raise RuntimeError("baseline event filter produced zero rows")
    events["entry_ts"]=events["entry_ts"].astype("int64")
    events["ts"]=events["ts"].astype("int64")
    if "split" not in events:
        events["split"]=events["ts"].map(base.split_name)

    raw_paths={sym_from_path(p):p for p in glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True)}
    raw_syms=set(raw_paths)
    events=events[events["symbol"].isin(raw_syms)].copy()
    print("CPR_EVENTS",len(events),"symbols",events.symbol.nunique(),flush=True)

    rows=[]
    for depth in PULLBACKS:
        for split in ("TRAIN","HOLDOUT"):
            eg=events[events["split"].eq(split)]
            gross,statuses,misc,bars,ats=replay_depth(events,raw_paths,depth,split)
            for cost in COST_BPS:
                s=summarize(gross,statuses,misc,bars,ats,cost,len(eg))
                s.update({
                    "pullback_pct":depth,
                    "split":split,
                    "cost_bp":cost,
                    "tp_pct":TP_PCT,
                    "sl_pct":SL_PCT,
                    "time_limit":"6h",
                    "setup_window":"2h",
                    "reclaim":"15m close > prior 15m high; next 15m open entry",
                })
                rows.append(s)
            print("DEPTH_DONE",depth,split,"n",len(gross),"stats",dict(statuses),"misc",dict(misc),flush=True)

    cells=pd.DataFrame(rows)
    cells.to_csv(out/"cells.csv",index=False)
    summary={
        "design":{
            "baseline":"8h >= +20%, cross-sectional top10%, fresh transition",
            "pullbacks_pct":PULLBACKS,
            "setup_window_bars":SETUP_BARS,
            "reclaim":"completed 15m close > previous 15m high after pullback",
            "entry":"next 15m open",
            "tp_pct":TP_PCT,
            "sl_pct":SL_PCT,
            "hold":"6h",
            "cost_bp":COST_BPS,
            "collision_rule":"canonical 1m resolver inherited from baseline engine",
        },
        "integrity_stats":dict(base.STATS),
        "rows":rows,
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("CPR_RESULT_JSON")
    print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__":
    main()
