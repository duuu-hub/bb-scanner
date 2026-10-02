#!/usr/bin/env python3
"""EXPLORATORY portfolio study: pyramided SHORT entries after extreme pump extensions.

Experiment ID: EXTREME-PYRAMID-2026-10-03
Class: EXPLORATORY (VALIDATION period is already SEEN; not pristine OOS)

Frozen design before viewing this experiment's outcome:
- Data: Binance USD-M USDT perpetual frozen 15m artifact run 36095439671.
- Warmup: ignore first 90 calendar days of each symbol.
- Causal cycle trough and reset: cycle resets only after a later 15m LOW <= 50%
  of the prior running peak. A fresh lower low also resets threshold geometry.
- Pyramiding thresholds: 15x, 20x, 30x, 50x the causal cycle trough.
- Entry: resting SHORT limit at exact threshold * trough; a 15m HIGH touch fills.
  No TP/SL chronology is inferred in this experiment.
- Each filled leg exits at the exact +7 day 15m OPEN. If exact exit bar is absent,
  the leg is excluded.
- The per-cycle dollar budget is frozen from portfolio equity immediately before
  the first filled leg of that cycle. Later legs use the same frozen cycle budget.
- Funding excluded.
- Cost stress: 20bp and 40bp total round trip, split equally entry/exit.
- Portfolio is derivatives-style: notional is exposure, not cash spent.
- 15m close MDD and conservative bar-HIGH stress MDD are reported.
- No liquidation model is assumed; min stress equity <= 0 is labeled insolvent.

Pre-registered allocation grid, weights of per-cycle budget:
  equal        = [15:25%, 20:25%, 30:25%, 50:25%]
  back_mild    = [15:15%, 20:20%, 30:25%, 50:40%]
  back_strong  = [15:10%, 20:15%, 30:25%, 50:50%]
  tail         = [15: 5%, 20:10%, 30:25%, 50:60%]
  front        = [15:40%, 20:30%, 30:20%, 50:10%]
Reference single-entry controls (not eligible for selection):
  single20     = [20:100%]
  single30     = [30:100%]

Per-cycle total budget grid: 5%, 10%, 15% of equity at first leg fill.

TRAIN selection rule (40bp only, pyramids only):
1) positive TRAIN total return;
2) TRAIN close MDD no worse than -20%;
3) TRAIN stress MDD no worse than -25%;
4) min TRAIN stress equity > 0;
5) at least 5 distinct completed cycles;
6) for 10%/15% budgets, same allocation's next-lower budget must also be positive;
7) choose highest TRAIN total return, tie-break by smaller absolute stress MDD,
   then smaller budget, then allocation name.

VALIDATION_SEEN falsification flag for the frozen TRAIN-selected config:
- positive total return,
- stress MDD no worse than -35%,
- min stress equity > 0,
- at least 3 distinct cycles.
This flag is descriptive only because 2025-2026 has been inspected previously.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DAY_MS = 86_400_000
WARMUP_MS = 90 * DAY_MS
HOLD_MS = 7 * DAY_MS
SPLIT_MS = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
THRESHOLDS = (15, 20, 30, 50)

ALLOCATIONS = {
    "equal":       {15: 0.25, 20: 0.25, 30: 0.25, 50: 0.25},
    "back_mild":   {15: 0.15, 20: 0.20, 30: 0.25, 50: 0.40},
    "back_strong": {15: 0.10, 20: 0.15, 30: 0.25, 50: 0.50},
    "tail":        {15: 0.05, 20: 0.10, 30: 0.25, 50: 0.60},
    "front":       {15: 0.40, 20: 0.30, 30: 0.20, 50: 0.10},
    "single20":    {15: 0.00, 20: 1.00, 30: 0.00, 50: 0.00},
    "single30":    {15: 0.00, 20: 0.00, 30: 1.00, 50: 0.00},
}
PYRAMID_NAMES = ("equal", "back_mild", "back_strong", "tail", "front")
BUDGET_PCTS = (5.0, 10.0, 15.0)
COST_BPS = (20, 40)


def iso(ts):
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def pctile(vals, q):
    vals = [v for v in vals if v is not None and math.isfinite(v)]
    if not vals:
        return None
    xs = sorted(vals)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1 - w) + xs[hi] * w


def load_rows(path):
    rows = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for x in r:
            try:
                ts = int(x["open_time"])
                op = float(x["open"])
                hi = float(x["high"])
                lo = float(x["low"])
                cl = float(x["close"])
            except Exception:
                continue
            if min(op, hi, lo, cl) <= 0:
                continue
            rows.append((ts, op, hi, lo, cl))
    return rows


def generate_symbol_legs(symbol, rows):
    """Generate causal threshold legs and attach a 7-day mark path."""
    out = []
    if len(rows) < 2:
        return out

    first_ts = rows[0][0]
    start_ts = first_ts + WARMUP_MS
    ts_to_idx = {r[0]: i for i, r in enumerate(rows)}

    trough = None
    trough_ts = None
    peak = None
    fired = set()

    for i, (ts, op, hi, lo, cl) in enumerate(rows):
        if ts < start_ts:
            continue

        if trough is None:
            trough = lo
            trough_ts = ts
            peak = hi
            fired = set()
            continue

        prior_peak = peak
        if prior_peak is not None and lo <= 0.5 * prior_peak:
            trough = lo
            trough_ts = ts
            peak = hi
            fired = set()
            continue

        if lo < trough:
            trough = lo
            trough_ts = ts
            peak = hi
            fired = set()
            continue

        if peak is None or hi > peak:
            peak = hi

        cycle_id = f"{symbol}:{trough_ts}"
        for thr in THRESHOLDS:
            if thr in fired:
                continue
            level = thr * trough
            if hi >= level:
                fired.add(thr)
                exit_ts = ts + HOLD_MS
                xi = ts_to_idx.get(exit_ts)
                if xi is None or xi <= i:
                    continue
                path = []
                for k in range(i, xi):
                    pts, pop, phi, plo, pcl = rows[k]
                    path.append((pts, pcl, phi))
                out.append({
                    "symbol": symbol,
                    "cycle_id": cycle_id,
                    "trough_ts": trough_ts,
                    "trough": trough,
                    "threshold_x": thr,
                    "entry_ts": ts,
                    "entry": level,
                    "touch_high": hi,
                    "exit_ts": exit_ts,
                    "exit": rows[xi][1],
                    "path": path,
                })

    return out


def scope_filter(legs, scope):
    if scope == "ALL":
        return list(legs)
    if scope == "TRAIN":
        return [x for x in legs if x["entry_ts"] < SPLIT_MS and x["exit_ts"] < SPLIT_MS]
    if scope == "VALIDATION_SEEN":
        return [x for x in legs if x["entry_ts"] >= SPLIT_MS]
    raise ValueError(scope)


def simulate(legs, weights, budget_pct, roundtrip_bps):
    """Portfolio simulation allowing multiple same-cycle threshold legs."""
    active_legs = [x for x in legs if weights.get(x["threshold_x"], 0.0) > 0]
    active_legs.sort(key=lambda x: (x["entry_ts"], x["symbol"], x["threshold_x"]))

    entries = defaultdict(list)
    exits = defaultdict(list)
    marks = defaultdict(list)
    for lid, leg in enumerate(active_legs):
        leg = dict(leg)
        leg["_id"] = lid
        entries[leg["entry_ts"]].append(leg)
        exits[leg["exit_ts"]].append(leg)
        for ts, cl, hi in leg["path"]:
            marks[ts].append((lid, cl, hi))
        active_legs[lid] = leg

    all_ts = sorted(set(entries) | set(exits) | set(marks))
    if not all_ts:
        return {
            "legs": 0,
            "cycles": 0,
            "final_equity": 100.0,
            "total_return_pct": 0.0,
            "mdd_close_pct": 0.0,
            "mdd_stress_pct": 0.0,
            "min_close_equity": 100.0,
            "min_stress_equity": 100.0,
            "insolvent_stress": False,
            "max_concurrent_legs": 0,
            "max_concurrent_cycles": 0,
            "max_gross_notional_pct_of_equity": 0.0,
            "trade_win_rate_pct": None,
            "trade_mean_return_notional_pct": None,
            "cycle_win_rate_pct": None,
            "cycle_mean_return_on_budget_pct": None,
            "cycle_median_return_on_budget_pct": None,
            "cycle_p10_return_on_budget_pct": None,
            "cycle_worst_return_on_budget_pct": None,
        }

    fee_side = (roundtrip_bps / 2.0) / 10000.0
    cash = 100.0
    open_pos = {}
    cycle_budget = {}
    cycle_pnl = defaultdict(float)
    cycle_legs = defaultdict(int)
    cycle_first_equity = {}

    peak_close = 100.0
    peak_stress = 100.0
    mdd_close = 0.0
    mdd_stress = 0.0
    min_close_eq = 100.0
    min_stress_eq = 100.0
    max_concurrent_legs = 0
    max_concurrent_cycles = 0
    max_gross_pct = 0.0
    trade_returns = []

    def mtm_equity(use_high=False):
        eq = cash
        for p in open_pos.values():
            px = p["last_high"] if use_high else p["last_close"]
            eq += p["notional"] * (p["entry"] - px) / p["entry"]
        return eq

    for ts in all_ts:
        # 1) exact deadline exits at this 15m OPEN
        for leg in exits.get(ts, []):
            p = open_pos.pop(leg["_id"], None)
            if p is None:
                continue
            gross_pnl = p["notional"] * (p["entry"] - leg["exit"]) / p["entry"]
            exit_fee = p["notional"] * fee_side
            net_pnl = gross_pnl - p["entry_fee"] - exit_fee
            cash += gross_pnl - exit_fee
            cycle_pnl[p["cycle_id"]] += net_pnl
            cycle_legs[p["cycle_id"]] += 1
            trade_returns.append(net_pnl / p["notional"] * 100.0)

        # 2) size same-timestamp new cycle budgets from identical pre-entry equity
        pre_entry_eq = mtm_equity(use_high=False)
        same_ts = entries.get(ts, [])
        new_cycles = sorted({x["cycle_id"] for x in same_ts if x["cycle_id"] not in cycle_budget})
        for cid in new_cycles:
            cycle_budget[cid] = max(0.0, pre_entry_eq) * budget_pct / 100.0
            cycle_first_equity[cid] = pre_entry_eq

        # 3) threshold-limit entries
        for leg in same_ts:
            w = weights.get(leg["threshold_x"], 0.0)
            if w <= 0:
                continue
            budget = cycle_budget.get(leg["cycle_id"], max(0.0, pre_entry_eq) * budget_pct / 100.0)
            notional = budget * w
            if notional <= 0:
                continue
            entry_fee = notional * fee_side
            cash -= entry_fee
            open_pos[leg["_id"]] = {
                "cycle_id": leg["cycle_id"],
                "symbol": leg["symbol"],
                "threshold_x": leg["threshold_x"],
                "entry": leg["entry"],
                "notional": notional,
                "entry_fee": entry_fee,
                "last_close": leg["entry"],
                "last_high": leg["entry"],
            }

        # 4) end-of-bar marks for positions active during this bar
        for lid, cl, hi in marks.get(ts, []):
            if lid in open_pos:
                open_pos[lid]["last_close"] = cl
                open_pos[lid]["last_high"] = hi

        close_eq = mtm_equity(use_high=False)
        stress_eq = mtm_equity(use_high=True)

        peak_close = max(peak_close, close_eq)
        peak_stress = max(peak_stress, stress_eq)
        mdd_close = min(mdd_close, (close_eq / peak_close - 1.0) * 100.0 if peak_close > 0 else -100.0)
        mdd_stress = min(mdd_stress, (stress_eq / peak_stress - 1.0) * 100.0 if peak_stress > 0 else -100.0)
        min_close_eq = min(min_close_eq, close_eq)
        min_stress_eq = min(min_stress_eq, stress_eq)

        gross = sum(p["notional"] for p in open_pos.values())
        gross_pct = (gross / close_eq * 100.0) if close_eq > 0 else float("inf")
        if math.isfinite(gross_pct):
            max_gross_pct = max(max_gross_pct, gross_pct)

        max_concurrent_legs = max(max_concurrent_legs, len(open_pos))
        max_concurrent_cycles = max(max_concurrent_cycles, len({p["cycle_id"] for p in open_pos.values()}))

    final_equity = cash + sum(
        p["notional"] * (p["entry"] - p["last_close"]) / p["entry"]
        for p in open_pos.values()
    )

    completed_cycle_returns = []
    for cid, pnl in cycle_pnl.items():
        b = cycle_budget.get(cid)
        if b and b > 0 and cycle_legs[cid] > 0:
            completed_cycle_returns.append(pnl / b * 100.0)

    return {
        "legs": len(trade_returns),
        "cycles": len(completed_cycle_returns),
        "final_equity": final_equity,
        "total_return_pct": (final_equity / 100.0 - 1.0) * 100.0,
        "mdd_close_pct": mdd_close,
        "mdd_stress_pct": mdd_stress,
        "min_close_equity": min_close_eq,
        "min_stress_equity": min_stress_eq,
        "insolvent_stress": min_stress_eq <= 0,
        "max_concurrent_legs": max_concurrent_legs,
        "max_concurrent_cycles": max_concurrent_cycles,
        "max_gross_notional_pct_of_equity": max_gross_pct,
        "trade_win_rate_pct": (sum(x > 0 for x in trade_returns) / len(trade_returns) * 100.0) if trade_returns else None,
        "trade_mean_return_notional_pct": statistics.mean(trade_returns) if trade_returns else None,
        "cycle_win_rate_pct": (sum(x > 0 for x in completed_cycle_returns) / len(completed_cycle_returns) * 100.0) if completed_cycle_returns else None,
        "cycle_mean_return_on_budget_pct": statistics.mean(completed_cycle_returns) if completed_cycle_returns else None,
        "cycle_median_return_on_budget_pct": statistics.median(completed_cycle_returns) if completed_cycle_returns else None,
        "cycle_p10_return_on_budget_pct": pctile(completed_cycle_returns, 0.10),
        "cycle_worst_return_on_budget_pct": min(completed_cycle_returns) if completed_cycle_returns else None,
    }


def train_select(rows):
    eligible = []
    by_key = {(r["allocation"], r["budget_pct"]): r for r in rows if r["scope"] == "TRAIN" and r["cost_bps"] == 40}
    for name in PYRAMID_NAMES:
        for budget in BUDGET_PCTS:
            r = by_key.get((name, budget))
            if not r:
                continue
            if r["total_return_pct"] <= 0:
                continue
            if r["mdd_close_pct"] < -20.0 or r["mdd_stress_pct"] < -25.0:
                continue
            if r["min_stress_equity"] <= 0 or r["cycles"] < 5:
                continue
            if budget > 5:
                lower = by_key.get((name, budget - 5.0))
                if lower is None or lower["total_return_pct"] <= 0:
                    continue
            eligible.append(r)

    if not eligible:
        positives = [
            r for r in by_key.values()
            if r["allocation"] in PYRAMID_NAMES and r["total_return_pct"] > 0 and r["min_stress_equity"] > 0
        ]
        if not positives:
            return None, "NO_POSITIVE_TRAIN_CONFIG"
        positives.sort(key=lambda r: (
            -(r["total_return_pct"] / max(1e-9, abs(r["mdd_stress_pct"]))),
            r["budget_pct"], r["allocation"]
        ))
        return positives[0], "FALLBACK_RETURN_TO_STRESS_DD"

    eligible.sort(key=lambda r: (
        -r["total_return_pct"],
        abs(r["mdd_stress_pct"]),
        r["budget_pct"],
        r["allocation"],
    ))
    return eligible[0], "PRE_REGISTERED_TRAIN_RULE"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    paths = sorted(Path(args.data_root).rglob("*USDT.csv.gz"))
    if not paths:
        raise SystemExit("no *USDT.csv.gz files found")

    all_legs = []
    for i, p in enumerate(paths, 1):
        rows = load_rows(p)
        sym = p.name.replace(".csv.gz", "")
        all_legs.extend(generate_symbol_legs(sym, rows))
        if i % 25 == 0 or i == len(paths):
            print(f"processed {i}/{len(paths)} symbols legs={len(all_legs)}", flush=True)

    result_rows = []
    nested = {}
    for scope in ("TRAIN", "VALIDATION_SEEN", "ALL"):
        scope_legs = scope_filter(all_legs, scope)
        nested[scope] = {}
        for allocation, weights in ALLOCATIONS.items():
            nested[scope][allocation] = {}
            for budget in BUDGET_PCTS:
                nested[scope][allocation][str(int(budget))] = {}
                for cost in COST_BPS:
                    m = simulate(scope_legs, weights, budget, cost)
                    nested[scope][allocation][str(int(budget))][str(cost)] = m
                    result_rows.append({
                        "scope": scope,
                        "allocation": allocation,
                        "budget_pct": budget,
                        "cost_bps": cost,
                        **m,
                    })

    selected, selection_mode = train_select(result_rows)
    selected_summary = None
    validation_flag = None
    if selected is not None:
        name = selected["allocation"]
        budget = selected["budget_pct"]
        train40 = nested["TRAIN"][name][str(int(budget))]["40"]
        val40 = nested["VALIDATION_SEEN"][name][str(int(budget))]["40"]
        selected_summary = {
            "allocation": name,
            "weights": ALLOCATIONS[name],
            "budget_pct": budget,
            "selection_mode": selection_mode,
            "train_cost40": train40,
            "validation_seen_cost40": val40,
        }
        validation_flag = (
            val40["total_return_pct"] > 0
            and val40["mdd_stress_pct"] >= -35.0
            and val40["min_stress_equity"] > 0
            and val40["cycles"] >= 3
        )
        selected_summary["validation_seen_falsification_flag_pass"] = validation_flag

    result = {
        "experiment": {
            "id": "EXTREME-PYRAMID-2026-10-03",
            "class": "EXPLORATORY",
            "data_artifact_run_id": 36095439671,
            "market": "Binance USD-M USDT perpetual frozen 5Y artifact universe",
            "interval": "15m",
            "warmup_days": 90,
            "cycle_reset_drawdown_pct": 50,
            "thresholds_x": list(THRESHOLDS),
            "hold_days_per_leg": 7,
            "entry": "resting short limit at threshold * causal trough; exact threshold fill on 15m HIGH touch",
            "exit": "exact +7 day 15m OPEN",
            "cost_bps": list(COST_BPS),
            "funding": "excluded",
            "allocations": ALLOCATIONS,
            "budget_pcts": list(BUDGET_PCTS),
            "train": "entry and exit both before 2025-01-01 UTC",
            "validation_seen": "entry at/after 2025-01-01 UTC; previously inspected period, not pristine OOS",
            "selection_rule": "pre-registered in source docstring; uses TRAIN 40bp only",
            "no_liquidation_model": True,
        },
        "legs_total_raw": len(all_legs),
        "grid": nested,
        "train_selected": selected_summary,
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    fields = list(result_rows[0].keys()) if result_rows else []
    if fields:
        with (out / "grid.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(result_rows)

    selected_path = out / "selected.json"
    selected_path.write_text(json.dumps(selected_summary, indent=2), encoding="utf-8")

    print("PYRAMID_RESULT_JSON")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
