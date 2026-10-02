#!/usr/bin/env python3
"""EXPLORATORY descriptive statistics for extreme-extension pump cycles.

Experiment ID: EXTREME-THRESHOLD-STATS-2026-10-03
Status: EXPLORATORY (descriptive; not a validated trading strategy)

Frozen design before outcome:
- Data: Binance USD-M USDT perpetual frozen 15m artifact run 36095439671.
- Listing warmup: 90 calendar days from first available bar per symbol.
- Causal cycle trough: running lowest low after warmup/reset.
- Cycle reset: when a later 15m LOW is <= 50% of the prior running peak.
  The reset bar is assigned to the new cycle; its HIGH is not added to the old peak.
- Thresholds: first touch of 10x, 15x, 20x, 30x, 50x current cycle trough.
- No TP/SL, no trade-selection optimization, no parameter tuning.
- Descriptive fixed-horizon short proxy: threshold level vs exact +N day 15m OPEN,
  N in {1,2,3,5,7}. Gross only; fees/funding/slippage excluded.
- Ultimate-peak metrics use only confirmed/closed cycles. Open cycles are excluded
  from final-peak statistics because their terminal peak is unknowable.
- TRAIN: event and confirmed cycle close both before 2025-01-01 UTC.
- VALIDATION_SEEN: event at/after 2025-01-01 UTC.
- Events touching before split but closing after split are CROSS_SPLIT.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DAY_MS = 86_400_000
WARMUP_MS = 90 * DAY_MS
SPLIT_MS = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
THRESHOLDS = (10, 15, 20, 30, 50)
REACH_TARGETS = (15, 20, 30, 50, 100, 200)
HORIZON_DAYS = (1, 2, 3, 5, 7)


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


def basic(vals):
    vals = [v for v in vals if v is not None and math.isfinite(v)]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": sum(vals) / len(vals),
        "median": statistics.median(vals),
        "p25": pctile(vals, 0.25),
        "p75": pctile(vals, 0.75),
        "p90": pctile(vals, 0.90),
        "p95": pctile(vals, 0.95),
        "max": max(vals),
        "min": min(vals),
    }


def load_symbol(path: Path):
    rows = []
    daily_qv = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for x in r:
            try:
                ts = int(x["open_time"])
                op = float(x["open"])
                hi = float(x["high"])
                lo = float(x["low"])
                cl = float(x["close"])
                qv = float(x.get("quote_volume") or 0.0)
            except Exception:
                continue
            if min(op, hi, lo, cl) <= 0:
                continue
            rows.append((ts, op, hi, lo, cl))
            day = ts // DAY_MS
            daily_qv[day] = daily_qv.get(day, 0.0) + max(qv, 0.0)
    med_qv = statistics.median(daily_qv.values()) if daily_qv else 0.0
    return rows, med_qv, len(daily_qv)


def split_label(touch_ts, close_ts):
    if touch_ts < SPLIT_MS:
        if close_ts is not None and close_ts < SPLIT_MS:
            return "TRAIN"
        return "CROSS_SPLIT"
    return "VALIDATION_SEEN"


def analyze_symbol(symbol, rows, median_qv):
    if len(rows) < 2:
        return []

    first_ts = rows[0][0]
    start_ts = first_ts + WARMUP_MS
    ts_to_idx = {r[0]: i for i, r in enumerate(rows)}

    events = []
    active_event_ids = []

    trough = None
    trough_ts = None
    peak = None
    peak_ts = None
    fired = set()

    def close_cycle(close_ts, final_peak, final_peak_ts):
        if not active_event_ids:
            return
        for eid in active_event_ids:
            e = events[eid]
            e["cycle_closed"] = True
            e["cycle_close_ts"] = close_ts
            e["final_peak"] = final_peak
            e["final_peak_ts"] = final_peak_ts
            e["final_peak_x"] = final_peak / e["trough"]
            e["additional_from_threshold_x"] = final_peak / e["threshold_level"]
            e["adverse_from_threshold_pct"] = (final_peak / e["threshold_level"] - 1.0) * 100.0
            e["hours_touch_to_peak"] = max(0.0, (final_peak_ts - e["touch_ts"]) / 3_600_000.0)
            e["split"] = split_label(e["touch_ts"], close_ts)

    for i, (ts, op, hi, lo, cl) in enumerate(rows):
        if ts < start_ts:
            continue

        if trough is None:
            trough = lo
            trough_ts = ts
            peak = hi
            peak_ts = ts
            fired = set()
            active_event_ids = []
            continue

        # Close old cycle causally using the peak known before this reset bar.
        if peak is not None and lo <= 0.5 * peak:
            close_cycle(ts, peak, peak_ts)
            trough = lo
            trough_ts = ts
            peak = hi
            peak_ts = ts
            fired = set()
            active_event_ids = []
            continue

        # A new lower trough invalidates old threshold geometry before any touch on this bar.
        if lo < trough:
            trough = lo
            trough_ts = ts
            peak = hi
            peak_ts = ts
            fired = set()
            active_event_ids = []
            continue

        if peak is None or hi > peak:
            peak = hi
            peak_ts = ts

        for thr in THRESHOLDS:
            if thr in fired:
                continue
            level = thr * trough
            if hi >= level:
                fired.add(thr)
                e = {
                    "symbol": symbol,
                    "threshold_x": thr,
                    "trough": trough,
                    "trough_ts": trough_ts,
                    "threshold_level": level,
                    "touch_ts": ts,
                    "touch_high": hi,
                    "median_daily_quote_volume": median_qv,
                    "cycle_closed": False,
                    "cycle_close_ts": None,
                    "final_peak": None,
                    "final_peak_ts": None,
                    "final_peak_x": None,
                    "additional_from_threshold_x": None,
                    "adverse_from_threshold_pct": None,
                    "hours_touch_to_peak": None,
                    "split": "OPEN_CYCLE" if ts >= SPLIT_MS else "OPEN_OR_CROSS_SPLIT",
                    "_touch_idx": i,
                }
                events.append(e)
                active_event_ids.append(len(events) - 1)

    # Open cycle intentionally remains unfinalized for ultimate-peak stats.
    # Fixed-horizon metrics are still descriptive when exact future bars exist.
    for e in events:
        touch_idx = e.pop("_touch_idx")
        level = e["threshold_level"]
        for d in HORIZON_DAYS:
            target_ts = e["touch_ts"] + d * DAY_MS
            xi = ts_to_idx.get(target_ts)
            if xi is None or xi <= touch_idx:
                e[f"d{d}_short_gross_pct"] = None
                e[f"d{d}_mae_pct"] = None
                continue
            exit_open = rows[xi][1]
            gross = (level - exit_open) / level * 100.0
            max_high = max(r[2] for r in rows[touch_idx:xi + 1])
            mae = max(0.0, (max_high / level - 1.0) * 100.0)
            e[f"d{d}_short_gross_pct"] = gross
            e[f"d{d}_mae_pct"] = mae

    return events


def threshold_summary(events):
    out = {}
    for thr in THRESHOLDS:
        es = [e for e in events if e["threshold_x"] == thr]
        closed = [e for e in es if e["cycle_closed"]]
        item = {
            "events_total": len(es),
            "closed_cycles_n": len(closed),
            "open_or_cross_n": len(es) - len(closed),
            "unique_symbols": len({e["symbol"] for e in es}),
            "final_peak_x": basic([e["final_peak_x"] for e in closed]),
            "additional_from_threshold_x": basic([e["additional_from_threshold_x"] for e in closed]),
            "adverse_from_threshold_pct": basic([e["adverse_from_threshold_pct"] for e in closed]),
            "hours_touch_to_peak": basic([e["hours_touch_to_peak"] for e in closed]),
            "reach_probabilities_closed_pct": {},
            "horizons": {},
        }
        for target in REACH_TARGETS:
            eligible = [e for e in closed if target > thr]
            if not eligible:
                continue
            n = sum((e["final_peak_x"] or 0) >= target for e in eligible)
            item["reach_probabilities_closed_pct"][f"reach_{target}x"] = 100.0 * n / len(eligible)

        for d in HORIZON_DAYS:
            gross = [e.get(f"d{d}_short_gross_pct") for e in es]
            gross = [x for x in gross if x is not None and math.isfinite(x)]
            mae = [e.get(f"d{d}_mae_pct") for e in es]
            mae = [x for x in mae if x is not None and math.isfinite(x)]
            item["horizons"][f"d{d}"] = {
                "short_gross": basic(gross),
                "short_win_rate_pct": (100.0 * sum(x > 0 for x in gross) / len(gross)) if gross else None,
                "mae": basic(mae),
            }
        out[str(thr)] = item
    return out


def top_symbol_counts(events, n=20):
    c = Counter(e["symbol"] for e in events)
    return [{"symbol": s, "events": k} for s, k in c.most_common(n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    paths = sorted(Path(args.data_root).rglob("*USDT.csv.gz"))
    if not paths:
        raise SystemExit("no *USDT.csv.gz files found")

    all_events = []
    symbol_stats = []
    for i, p in enumerate(paths, 1):
        rows, med_qv, active_days = load_symbol(p)
        symbol = p.name.replace(".csv.gz", "")
        evs = analyze_symbol(symbol, rows, med_qv)
        all_events.extend(evs)
        symbol_stats.append({
            "symbol": symbol,
            "median_daily_quote_volume": med_qv,
            "active_days": active_days,
            "events": len(evs),
        })
        if i % 25 == 0 or i == len(paths):
            print(f"processed {i}/{len(paths)} symbols events={len(all_events)}", flush=True)

    liq_vals = [s["median_daily_quote_volume"] for s in symbol_stats if s["active_days"] > 0]
    q25 = pctile(liq_vals, 0.25)
    low_syms = {s["symbol"] for s in symbol_stats if s["median_daily_quote_volume"] <= q25}

    train_events = [e for e in all_events if e["split"] == "TRAIN"]
    val_events = [e for e in all_events if e["split"] == "VALIDATION_SEEN"]
    low_events = [e for e in all_events if e["symbol"] in low_syms]

    closed = [e for e in all_events if e["cycle_closed"]]
    top_events = sorted(
        closed,
        key=lambda e: (e["final_peak_x"] if e["final_peak_x"] is not None else -1),
        reverse=True,
    )

    result = {
        "experiment": {
            "id": "EXTREME-THRESHOLD-STATS-2026-10-03",
            "class": "EXPLORATORY",
            "purpose": "Descriptive statistics after extreme low-to-high cycle extension thresholds.",
            "market": "Binance USD-M USDT perpetual frozen artifact universe",
            "data_artifact_run_id": 36095439671,
            "interval": "15m",
            "listing_warmup_days": 90,
            "cycle_reset_drawdown_pct": 50,
            "thresholds_x": list(THRESHOLDS),
            "reach_targets_x": list(REACH_TARGETS),
            "fixed_horizon_days": list(HORIZON_DAYS),
            "fixed_horizon_price": "exact +N day 15m OPEN",
            "costs": "not applicable to descriptive cycle stats; short proxy is GROSS ONLY",
            "funding": "excluded",
            "train": "2021-01-01 through 2024-12-31 UTC; cycle close must also be before split",
            "validation": "2025-01-01 through available 2026 data; VALIDATION/SEEN",
            "canonical_intrabar_note": "No TP/SL chronology is inferred. Threshold event is first parent-bar HIGH touch; fixed-horizon exits use exact timestamp OPEN.",
            "survivorship_bias": "not independently resolved here; universe is exactly the frozen artifact universe",
        },
        "symbols_total": len(symbol_stats),
        "events_total": len(all_events),
        "closed_events_total": len(closed),
        "low_liquidity_q25_median_daily_quote_volume": q25,
        "all": threshold_summary(all_events),
        "train": threshold_summary(train_events),
        "validation_seen": threshold_summary(val_events),
        "low_liquidity_bottom_quartile": threshold_summary(low_events),
        "top_symbol_event_counts": top_symbol_counts(all_events),
        "top_closed_events": [
            {
                k: e.get(k)
                for k in (
                    "symbol", "threshold_x", "trough", "trough_ts", "threshold_level",
                    "touch_ts", "final_peak", "final_peak_ts", "final_peak_x",
                    "additional_from_threshold_x", "adverse_from_threshold_pct",
                    "hours_touch_to_peak", "split"
                )
            }
            for e in top_events[:100]
        ],
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    fields = [
        "symbol", "threshold_x", "trough", "trough_ts", "threshold_level",
        "touch_ts", "touch_high", "median_daily_quote_volume",
        "cycle_closed", "cycle_close_ts", "final_peak", "final_peak_ts",
        "final_peak_x", "additional_from_threshold_x", "adverse_from_threshold_pct",
        "hours_touch_to_peak", "split",
    ]
    for d in HORIZON_DAYS:
        fields += [f"d{d}_short_gross_pct", f"d{d}_mae_pct"]
    with (out / "events.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_events)

    rows = []
    for scope_name, scope in (
        ("ALL", result["all"]),
        ("TRAIN", result["train"]),
        ("VALIDATION_SEEN", result["validation_seen"]),
        ("LOW_LIQ_Q25", result["low_liquidity_bottom_quartile"]),
    ):
        for thr in THRESHOLDS:
            x = scope[str(thr)]
            row = {
                "scope": scope_name,
                "threshold_x": thr,
                "events_total": x["events_total"],
                "closed_cycles_n": x["closed_cycles_n"],
                "unique_symbols": x["unique_symbols"],
                "final_peak_x_median": x["final_peak_x"].get("median"),
                "final_peak_x_p90": x["final_peak_x"].get("p90"),
                "final_peak_x_max": x["final_peak_x"].get("max"),
                "adverse_pct_median": x["adverse_from_threshold_pct"].get("median"),
                "adverse_pct_p90": x["adverse_from_threshold_pct"].get("p90"),
                "adverse_pct_max": x["adverse_from_threshold_pct"].get("max"),
                "hours_to_peak_median": x["hours_touch_to_peak"].get("median"),
            }
            for d in HORIZON_DAYS:
                h = x["horizons"][f"d{d}"]
                row[f"d{d}_short_mean_pct"] = h["short_gross"].get("mean")
                row[f"d{d}_short_median_pct"] = h["short_gross"].get("median")
                row[f"d{d}_short_win_rate_pct"] = h["short_win_rate_pct"]
                row[f"d{d}_mae_p90_pct"] = h["mae"].get("p90")
            rows.append(row)

    if rows:
        with (out / "threshold_summary.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    print("RESULT_JSON")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
