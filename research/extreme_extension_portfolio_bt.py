#!/usr/bin/env python3
"""Portfolio backtest for causal extreme-extension SHORT signals.

Frozen experiment:
- Data: Binance USD-M USDT perpetual frozen 5Y 15m dataset, run 36095439671.
- Threshold strategies tested independently: 20x, 30x, 40x, 50x.
- Ignore first 90 calendar days per symbol.
- At each 15m OPEN, use only causal state from completed prior bars.
- Enter at first OPEN >= threshold * causal cycle trough.
- Hold exactly 7 days; exit at the exact +7d 15m OPEN.
- One open position per symbol per strategy; overlapping same-symbol signals are skipped.
- Position notional = 5% of current portfolio equity at entry.
- Costs: main = 40bp round trip (20bp on entry + 20bp on exit).
  A 20bp round-trip sensitivity is also computed.
- Funding excluded.
- Portfolio MDD is measured on 15m close MTM. Intrabar stress MDD is also measured
  using each open short's bar HIGH (conservative simultaneous-high stress).
- Flat periods are union-complement periods with zero open positions.
"""
from __future__ import annotations
import argparse,csv,gzip,json,math,statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DAY_MS=86_400_000
WARMUP_MS=90*DAY_MS
HOLD_MS=7*DAY_MS
THRESHOLDS=(20,30,40,50)

def iso(ts):
    return datetime.fromtimestamp(ts/1000, tz=timezone.utc).isoformat().replace("+00:00","Z")

def load_rows(path):
    rows=[]
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        r=csv.DictReader(f)
        for x in r:
            try:
                ts=int(x["open_time"]); op=float(x["open"]); hi=float(x["high"]); lo=float(x["low"]); cl=float(x["close"])
            except Exception:
                continue
            if min(op,hi,lo,cl)<=0: continue
            rows.append((ts,op,hi,lo,cl))
    return rows

def generate_symbol_trades(symbol, rows):
    """Return per-threshold candidate trades with 15m close/high paths."""
    out={thr:[] for thr in THRESHOLDS}
    if len(rows)<2:return out
    first_ts=rows[0][0]
    start_ts=first_ts+WARMUP_MS
    ts_to_idx={r[0]:i for i,r in enumerate(rows)}
    trough=None; trough_ts=None; peak=None
    fired={thr:False for thr in THRESHOLDS}

    candidates=[]
    for i,(ts,op,hi,lo,cl) in enumerate(rows):
        if ts < start_ts:
            continue

        # OPEN decision from prior completed-bar state only.
        if trough is not None:
            entry_x=op/trough
            for thr in THRESHOLDS:
                if not fired[thr] and entry_x>=thr:
                    fired[thr]=True
                    exit_ts=ts+HOLD_MS
                    xi=ts_to_idx.get(exit_ts)
                    if xi is not None and xi>i:
                        candidates.append((thr,i,xi,{
                            "symbol":symbol,"threshold_x":thr,
                            "trough":trough,"trough_ts":trough_ts,
                            "entry_ts":ts,"entry":op,"entry_vs_trough_x":entry_x,
                            "exit_ts":exit_ts,"exit":rows[xi][1],
                        }))

        # Update state after bar completion.
        if trough is None:
            trough=lo; trough_ts=ts; peak=hi
            fired={thr:False for thr in THRESHOLDS}
            continue
        prior_peak=peak
        if prior_peak is not None and lo<=0.5*prior_peak:
            trough=lo; trough_ts=ts; peak=hi
            fired={thr:False for thr in THRESHOLDS}
            continue
        if lo<trough:
            trough=lo; trough_ts=ts; peak=hi
            fired={thr:False for thr in THRESHOLDS}
            continue
        if peak is None or hi>peak:
            peak=hi

    # Attach path excluding exit bar because we exit at its OPEN.
    for thr,ei,xi,t in candidates:
        path=[]
        for k in range(ei,xi):
            ts,op,hi,lo,cl=rows[k]
            path.append((ts,cl,hi))
        t["path"]=path
        out[thr].append(t)
    return out

def build_flat_periods(intervals,start_ts,end_ts):
    if start_ts>=end_ts:return []
    if not intervals:
        return [{"start_ts":start_ts,"end_ts":end_ts,"duration_ms":end_ts-start_ts}]
    ints=sorted((max(start_ts,a),min(end_ts,b)) for a,b in intervals if b>start_ts and a<end_ts)
    merged=[]
    for a,b in ints:
        if not merged or a>merged[-1][1]:
            merged.append([a,b])
        else:
            merged[-1][1]=max(merged[-1][1],b)
    flats=[]
    cur=start_ts
    for a,b in merged:
        if a>cur:flats.append({"start_ts":cur,"end_ts":a,"duration_ms":a-cur})
        cur=max(cur,b)
    if cur<end_ts:flats.append({"start_ts":cur,"end_ts":end_ts,"duration_ms":end_ts-cur})
    return flats

def simulate(trades, analysis_start, analysis_end, roundtrip_bps):
    # Enforce one open position per symbol by chronological candidate scan.
    accepted=[]; skipped=[]
    next_free={}
    for t in sorted(trades,key=lambda x:(x["entry_ts"],x["symbol"])):
        nf=next_free.get(t["symbol"],-1)
        if t["entry_ts"]<nf:
            skipped.append(t)
            continue
        accepted.append(t)
        next_free[t["symbol"]]=t["exit_ts"]

    # Build event maps and per-trade mark paths.
    entries=defaultdict(list); exits=defaultdict(list); marks=defaultdict(list)
    for tid,t in enumerate(accepted):
        t["_id"]=tid
        entries[t["entry_ts"]].append(t)
        exits[t["exit_ts"]].append(t)
        for ts,cl,hi in t["path"]:
            marks[ts].append((tid,cl,hi))

    equity=100.0
    cash=100.0
    open_pos={}
    peak_close_eq=100.0; mdd_close=0.0
    peak_stress_eq=100.0; mdd_stress=0.0
    equity_points=[]
    trade_results=[]
    fee_side=(roundtrip_bps/2.0)/10000.0

    all_ts=sorted(set(entries)|set(exits)|set(marks))
    for ts in all_ts:
        # Exits at OPEN first.
        if ts in exits:
            for t in exits[ts]:
                tid=t["_id"]
                pos=open_pos.pop(tid,None)
                if pos is None: continue
                pnl=pos["notional"]*(pos["entry"]-t["exit"])/pos["entry"]
                exit_fee=pos["notional"]*fee_side
                cash += pnl-exit_fee
                trade_results.append({
                    "symbol":t["symbol"],"entry_ts":t["entry_ts"],"exit_ts":t["exit_ts"],
                    "notional":pos["notional"],"pnl_after_exit_fee_and_entry_fee":pnl-pos["entry_fee"]-exit_fee,
                    "return_on_notional_pct":(pnl-pos["entry_fee"]-exit_fee)/pos["notional"]*100.0,
                })

        # Mark current equity at OPEN before same-timestamp entries, using last known prices if needed.
        # Same-timestamp entries all size from the same pre-entry portfolio equity.
        mtm_before=cash
        for pos in open_pos.values():
            px=pos.get("last_close",pos["entry"])
            mtm_before += pos["notional"]*(pos["entry"]-px)/pos["entry"]

        if ts in entries:
            base_eq=mtm_before
            for t in entries[ts]:
                notional=base_eq*0.05
                entry_fee=notional*fee_side
                cash -= entry_fee
                open_pos[t["_id"]]={
                    "symbol":t["symbol"],"entry":t["entry"],"notional":notional,
                    "entry_fee":entry_fee,"last_close":t["entry"],"last_high":t["entry"],
                }

        # Update marks for bars at ts.
        if ts in marks:
            for tid,cl,hi in marks[ts]:
                if tid in open_pos:
                    open_pos[tid]["last_close"]=cl
                    open_pos[tid]["last_high"]=hi

        close_eq=cash
        stress_eq=cash
        for pos in open_pos.values():
            close_eq += pos["notional"]*(pos["entry"]-pos["last_close"])/pos["entry"]
            stress_eq += pos["notional"]*(pos["entry"]-pos["last_high"])/pos["entry"]

        peak_close_eq=max(peak_close_eq,close_eq)
        dd=(close_eq/peak_close_eq-1.0)*100.0
        mdd_close=min(mdd_close,dd)

        peak_stress_eq=max(peak_stress_eq,stress_eq)
        dds=(stress_eq/peak_stress_eq-1.0)*100.0
        mdd_stress=min(mdd_stress,dds)

        equity_points.append((ts,close_eq,stress_eq,len(open_pos)))

    # Final equity after all closed (should have no open positions).
    final_equity=cash
    intervals=[(t["entry_ts"],t["exit_ts"]) for t in accepted]
    flats=build_flat_periods(intervals,analysis_start,analysis_end)
    total_window=max(1,analysis_end-analysis_start)
    flat_ms=sum(x["duration_ms"] for x in flats)
    longest=sorted(flats,key=lambda x:x["duration_ms"],reverse=True)
    for x in longest:
        x["start"]=iso(x["start_ts"]);x["end"]=iso(x["end_ts"])
        x["days"]=x["duration_ms"]/DAY_MS

    # Max concurrent from equity points.
    max_concurrent=max((p[3] for p in equity_points),default=0)
    active_ms=total_window-flat_ms

    return {
        "accepted_trades":len(accepted),
        "skipped_same_symbol_overlap":len(skipped),
        "final_equity":final_equity,
        "total_return_pct":(final_equity/100.0-1.0)*100.0,
        "mdd_close_pct":mdd_close,
        "mdd_intrabar_stress_pct":mdd_stress,
        "max_concurrent_positions":max_concurrent,
        "max_gross_exposure_pct_approx":max_concurrent*5.0,
        "flat_time_pct":flat_ms/total_window*100.0,
        "active_time_pct":active_ms/total_window*100.0,
        "flat_period_count":len(flats),
        "longest_flat_days":longest[0]["days"] if longest else 0.0,
        "top10_flat_periods":longest[:10],
        "trade_mean_return_on_notional_pct":statistics.mean([x["return_on_notional_pct"] for x in trade_results]) if trade_results else None,
        "trade_win_rate_pct":sum(x["return_on_notional_pct"]>0 for x in trade_results)/len(trade_results)*100.0 if trade_results else None,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-root",required=True)
    ap.add_argument("--out",required=True)
    a=ap.parse_args()
    paths=sorted(Path(a.data_root).rglob("*USDT.csv.gz"))
    all_by_thr={thr:[] for thr in THRESHOLDS}
    global_min=None; global_max=None
    for i,p in enumerate(paths,1):
        rows=load_rows(p)
        if not rows:continue
        global_min=rows[0][0] if global_min is None else min(global_min,rows[0][0])
        global_max=rows[-1][0] if global_max is None else max(global_max,rows[-1][0])
        sym=p.name.replace(".csv.gz","")
        d=generate_symbol_trades(sym,rows)
        for thr in THRESHOLDS:all_by_thr[thr].extend(d[thr])
        if i%25==0 or i==len(paths):
            print("processed",i,"/",len(paths),{thr:len(all_by_thr[thr]) for thr in THRESHOLDS},flush=True)

    analysis_start=global_min+WARMUP_MS
    analysis_end=global_max

    result={
        "definition":{
            "market":"Binance USD-M USDT perpetual frozen 5Y artifact run 36095439671",
            "interval":"15m",
            "thresholds_x":list(THRESHOLDS),
            "position_notional_pct_of_current_equity":5.0,
            "hold_days":7,
            "entry":"first causal 15m OPEN >= threshold * cycle trough",
            "same_symbol_overlap":"skip while prior same-symbol trade open",
            "main_cost_roundtrip_bps":40,
            "sensitivity_cost_roundtrip_bps":20,
            "funding_included":False,
            "analysis_window_start":iso(analysis_start),
            "analysis_window_end":iso(analysis_end),
            "flat_period_definition":"time with zero open positions across the strategy portfolio",
        },
        "strategies":{}
    }
    for thr in THRESHOLDS:
        main=simulate(all_by_thr[thr],analysis_start,analysis_end,40)
        sens=simulate(all_by_thr[thr],analysis_start,analysis_end,20)
        result["strategies"][str(thr)]={
            "candidate_signals":len(all_by_thr[thr]),
            "cost40":main,
            "cost20":{"final_equity":sens["final_equity"],"total_return_pct":sens["total_return_pct"],"mdd_close_pct":sens["mdd_close_pct"]},
        }

    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    (out/"summary.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print("PORTFOLIO_JSON")
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
