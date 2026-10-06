#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import body80_demo
import trend_continuation_first_touch as base

EXACT_CANDIDATE = "BODY80_90_H12_17"
ROBUST_CANDIDATE = "B80_90_H12_17"
EXACT_RUN = 37381087242
ROBUST_RUN = 37381429984
DATA_RUN = 36095439671
EVENT_RUN = 36934882492
CUT = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp() * 1000)


class Audit:
    def __init__(self):
        self.checks: list[dict] = []

    def check(self, name: str, condition: bool, detail=None):
        row = {"name": name, "pass": bool(condition)}
        if detail is not None:
            row["detail"] = detail
        self.checks.append(row)
        if not condition:
            print(f"[FAIL] {name}: {detail}", flush=True)
        else:
            print(f"[PASS] {name}", flush=True)

    @property
    def passed(self) -> bool:
        return all(x["pass"] for x in self.checks)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def one(root: str | Path, pattern: str) -> Path:
    hits = sorted(Path(root).glob(pattern))
    if len(hits) != 1:
        raise RuntimeError(f"expected one {pattern} under {root}, got {len(hits)}")
    return hits[0]


def recursive_one(root: str | Path, filename: str) -> Path:
    hits = sorted(Path(root).rglob(filename))
    if len(hits) != 1:
        raise RuntimeError(f"expected one {filename} under {root}, got {len(hits)}")
    return hits[0]


def pf(vals: np.ndarray) -> float | None:
    gp = vals[vals > 0].sum()
    gl = -vals[vals < 0].sum()
    return float(gp / gl) if gl > 0 else None


def perf(g: pd.DataFrame, cost: float) -> dict:
    x = g["gross_pct"].astype(float).to_numpy() - cost
    return {
        "n": int(len(x)),
        "ev_pct": float(x.mean()) if len(x) else None,
        "pf": pf(x) if len(x) else None,
        "sum_net_pct": float(x.sum()) if len(x) else 0.0,
    }


def close(a, b, tol=1e-9) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


def chronology_smokes(audit: Audit):
    old_loader = base.load_1m_day
    old_resolver = base.resolve_collision_1m
    bar = 1_800_000_000_000
    tp, sl = 103.0, 95.0

    def rows_with(firsts):
        rows = []
        for i in range(15):
            h, l = 101.0, 99.0
            if i < len(firsts) and firsts[i] is not None:
                h, l = firsts[i]
            rows.append((bar + i * base.MIN_MS, h, l))
        return rows

    try:
        base.load_1m_day = lambda symbol, day: rows_with([(104.0, 94.0)])
        r = base.resolve_collision_1m("XUSDT", bar, tp, sl)
        audit.check("chronology_same_1m_both_is_loss", r["status"] == "SL", r)

        base.load_1m_day = lambda symbol, day: rows_with([(104.0, 99.0), (101.0, 94.0)])
        r = base.resolve_collision_1m("XUSDT", bar, tp, sl)
        audit.check("chronology_tp_first_is_win", r["status"] == "TP", r)

        base.load_1m_day = lambda symbol, day: rows_with([(101.0, 94.0), (104.0, 99.0)])
        r = base.resolve_collision_1m("XUSDT", bar, tp, sl)
        audit.check("chronology_sl_first_is_loss", r["status"] == "SL", r)

        base.load_1m_day = lambda symbol, day: None
        r = base.resolve_collision_1m("XUSDT", bar, tp, sl)
        audit.check("chronology_missing_1m_is_data_gap", r["status"] == "DATA_GAP", r)

        base.load_1m_day = lambda symbol, day: rows_with([])
        r = base.resolve_collision_1m("XUSDT", bar, tp, sl)
        audit.check("chronology_unreproduced_collision_is_exit_mismatch", r["status"] == "EXIT_MISMATCH", r)

        raw = {
            "ts": np.array([bar, bar + base.BAR_MS, bar + 2 * base.BAR_MS, bar + 3 * base.BAR_MS], dtype=np.int64),
            "open": np.array([100.0, 100.0, 100.0, 100.0]),
            "high": np.array([101.0, 104.0, 101.0, 101.0]),
            "low": np.array([99.0, 99.0, 99.0, 99.0]),
            "close": np.array([100.0, 103.0, 100.0, 100.0]),
        }
        base.TP_PCT = 3.0
        rr = base.event_outcomes("XUSDT", raw, bar, 100.0, 5.0)["1h"]
        audit.check("event_outcomes_later_tp", rr["status"] == "TP", rr)

        rr = base.event_outcomes("XUSDT", raw, bar + 1, 100.0, 5.0)["1h"]
        audit.check("event_outcomes_entry_timestamp_mismatch", rr["status"] == "ENTRY_MISMATCH", rr)

        raw_gap = {
            "ts": np.array([bar, bar + base.BAR_MS, bar + 3 * base.BAR_MS, bar + 4 * base.BAR_MS], dtype=np.int64),
            "open": np.array([100.0, 100.0, 100.0, 100.0]),
            "high": np.array([101.0, 101.0, 101.0, 101.0]),
            "low": np.array([99.0, 99.0, 99.0, 99.0]),
            "close": np.array([100.0, 100.0, 100.0, 100.0]),
        }
        rr = base.event_outcomes("XUSDT", raw_gap, bar, 100.0, 5.0)["1h"]
        audit.check("event_outcomes_gap_excluded", rr["status"] == "DATA_GAP", rr)
    finally:
        base.load_1m_day = old_loader
        base.resolve_collision_1m = old_resolver


def load_event_rows(events_path: Path, keys: set[tuple[str, int]]) -> dict[tuple[str, int], dict]:
    out = {}
    use = ["ts", "symbol", "lookback", "tail", "threshold_pct", "strength_ret_pct", "pct"]
    for ch in pd.read_csv(events_path, compression="infer", usecols=use, chunksize=200_000):
        q = ch[
            ch["lookback"].eq("8h")
            & np.isclose(ch["tail"].astype(float), 0.10)
            & np.isclose(ch["threshold_pct"].astype(float), 20.0)
        ].copy()
        if q.empty:
            continue
        q["ts"] = q["ts"].astype("int64")
        for r in q.itertuples(index=False):
            key = (str(r.symbol), int(r.ts))
            if key in keys:
                out[key] = {
                    "strength_ret_pct": float(r.strength_ret_pct),
                    "pct": float(r.pct),
                }
        if len(out) == len(keys):
            break
    return out


def reconstruct_trade(row, raw_root: Path, event: dict) -> tuple[bool, dict]:
    sym = str(row.symbol)
    hits = sorted(raw_root.rglob(f"{sym}.csv.gz"))
    if len(hits) != 1:
        return False, {"reason": "raw_symbol_file_count", "count": len(hits), "symbol": sym}
    p = hits[0]
    d = pd.read_csv(
        p,
        compression="gzip",
        usecols=["open_time", "open", "high", "low", "close"],
        dtype={"open_time": "int64", "open": "float64", "high": "float64", "low": "float64", "close": "float64"},
    ).sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    ts = d.open_time.to_numpy(np.int64)
    si = int(np.searchsorted(ts, int(row.signal_ts)))
    ei = int(np.searchsorted(ts, int(row.entry_ts)))
    if si >= len(ts) or ei >= len(ts) or int(ts[si]) != int(row.signal_ts) or int(ts[ei]) != int(row.entry_ts):
        return False, {"reason": "timestamp_missing", "symbol": sym}

    o = float(d.open.iloc[si]); h = float(d.high.iloc[si]); l = float(d.low.iloc[si]); c = float(d.close.iloc[si])
    rng = h - l
    body = max(c - o, 0.0) / rng if rng > 0 else 0.0
    hour = int(pd.Timestamp(int(row.signal_ts), unit="ms", tz="UTC").hour)
    next_open = float(d.open.iloc[ei])
    raw = {
        "ts": ts,
        "open": d.open.to_numpy(float),
        "high": d.high.to_numpy(float),
        "low": d.low.to_numpy(float),
        "close": d.close.to_numpy(float),
    }
    base.TP_PCT = 3.0
    rr = base.event_outcomes(sym, raw, int(row.entry_ts), next_open, 5.0)["6h"]
    ok = (
        c > o
        and body >= 0.80
        and body < 0.90
        and 12 <= hour <= 17
        and int(row.entry_ts) == int(row.signal_ts) + base.BAR_MS
        and event["strength_ret_pct"] >= 20.0
        and event["pct"] >= 0.90
        and rr.get("status") == str(row.status)
        and close(rr.get("gross_pct"), float(row.gross_pct), 1e-7)
    )
    return ok, {
        "symbol": sym,
        "signal_ts": int(row.signal_ts),
        "entry_ts": int(row.entry_ts),
        "body_ratio_rebuilt": body,
        "hour_utc": hour,
        "strength8h_pct": event["strength_ret_pct"],
        "cross_section_pct": event["pct"] * 100.0,
        "status_expected": str(row.status),
        "status_rebuilt": rr.get("status"),
        "gross_expected": float(row.gross_pct),
        "gross_rebuilt": rr.get("gross_pct"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--exact", required=True)
    ap.add_argument("--robust", required=True)
    ap.add_argument("--partial0", required=True)
    ap.add_argument("--raw0", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    cfg_path = Path(a.config); src_path = Path(a.source)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    source = src_path.read_text(encoding="utf-8")
    audit = Audit()

    # Frozen strategy / execution contract.
    audit.check("cfg_experiment_id", cfg.get("experiment_id") == "BODY80_90_UTC12_17_FORWARD_V1")
    audit.check("cfg_classification_exploratory_forward", cfg.get("classification") == "EXPLORATORY_FORWARD")
    audit.check("cfg_direction_long", cfg.get("direction") == "LONG")
    audit.check("cfg_timeframe_15m", cfg.get("timeframe") == "15m")
    audit.check("cfg_lookback_32_bars", cfg.get("lookback_bars") == 32)
    audit.check("cfg_min_8h_return_20pct", close(cfg.get("min_8h_return_pct"), 20.0))
    audit.check("cfg_top_tail_10pct", close(cfg.get("cross_section_top_tail"), 0.10))
    audit.check("cfg_fresh_transition", cfg.get("fresh_transition_only") is True)
    audit.check("cfg_body_min_080", close(cfg.get("body_ratio_min"), 0.80))
    audit.check("cfg_body_max_090_exclusive", close(cfg.get("body_ratio_max_exclusive"), 0.90))
    audit.check("cfg_signal_hour_start_12utc", cfg.get("signal_candle_utc_hour_min") == 12)
    audit.check("cfg_signal_hour_end_17utc", cfg.get("signal_candle_utc_hour_max") == 17)
    audit.check("cfg_market_entry", cfg.get("entry_order_type") == "MARKET")
    audit.check("cfg_signal_ttl_under_1m", 0 < int(cfg.get("signal_ttl_seconds", 0)) < 60)
    audit.check("cfg_tp_3pct", close(cfg.get("tp_pct"), 3.0))
    audit.check("cfg_sl_5pct", close(cfg.get("sl_pct"), 5.0))
    audit.check("cfg_hold_6h", cfg.get("max_hold_minutes") == 360)
    audit.check("cfg_demo_only", cfg.get("trading_mode") == "DEMO" and cfg.get("live_trading_enabled") is False)
    audit.check("cfg_position_size_30pct", close(cfg.get("position_size_pct"), 30.0))
    audit.check("cfg_max_positions_6", cfg.get("max_open_positions") == 6)
    audit.check("cfg_max_exposure_200pct", close(cfg.get("max_total_exposure_pct"), 200.0))
    audit.check("cfg_same_symbol_disabled", cfg.get("allow_additional_same_symbol") is False)
    audit.check("cfg_exchange_tp_sl_required", cfg.get("require_exchange_tp_sl") is True)
    audit.check("cfg_cost_20_40_roundtrip", cfg.get("historical_cost_scenarios_roundtrip_pct") == [0.20, 0.40])
    audit.check("cfg_funding_disclosed_excluded", "excluded" in str(cfg.get("funding_mode", "")).lower())
    audit.check("cfg_cross_exchange_disclosed", cfg.get("historical_exchange") == "BINANCE_USDM" and cfg.get("forward_exchange") == "BITGET_DEMO_USDT_FUTURES")
    audit.check("cfg_state_isolated_from_long3", cfg.get("state_path") == "state/body80_demo_state.json")
    audit.check("cfg_log_isolated_from_long3", cfg.get("log_path") == "logs/body80_demo_executions.jsonl")

    # Source safety / causality.
    audit.check("src_uses_demo_authenticated_client", "BitgetDemoClassic" in source)
    audit.check("src_no_live_api_secret_names", "BITGET_API_KEY" not in source and "BITGET_SECRET_KEY" not in source)
    audit.check("src_uses_completed_bar_cutoff", "ts + BAR_MS <= boundary_ms" in source)
    audit.check("src_rejects_noncontiguous_15m", "!= BAR_MS" in source)
    audit.check("src_current_8h_is_32bar_difference", "signal[4] / x[-33][4] - 1.0" in source)
    audit.check("src_previous_8h_for_fresh", "x[-2][4] / x[-34][4] - 1.0" in source)
    audit.check("src_cross_section_rank_average", 'rank(pct=True, method="average")' in source)
    audit.check("src_fresh_transition_no_repeat", "current & ~previous" in source)
    audit.check("src_demo_catalog_execution_filter", "DEMO_SYMBOL_UNSUPPORTED" in source)
    audit.check("src_signal_late_rejected", "SIGNAL_TOO_LATE" in source)
    audit.check("src_spread_guard", "SPREAD_TOO_WIDE" in source)
    audit.check("src_market_order_only", '"orderType": "market"' in source)
    audit.check("src_preset_tp_sl", "presetStopSurplusPrice" in source and "presetStopLossPrice" in source)
    audit.check("src_capacity_limits_shared_account", "MAX_OPEN_POSITIONS" in source and "MAX_TOTAL_EXPOSURE" in source)

    exact_trades_path = recursive_one(a.exact, "trades_all.csv.gz")
    exact_summary_path = recursive_one(a.exact, "summary.json")
    robust_trades_path = recursive_one(a.robust, "trades_all.csv.gz")
    robust_summary_path = recursive_one(a.robust, "summary.json")
    partial_path = recursive_one(a.partial0, "trades.csv.gz")
    events_path = recursive_one(a.events, "continuation_events.csv.gz")

    exact = pd.read_csv(exact_trades_path, compression="gzip")
    robust = pd.read_csv(robust_trades_path, compression="gzip")
    es = json.loads(exact_summary_path.read_text(encoding="utf-8"))
    rs = json.loads(robust_summary_path.read_text(encoding="utf-8"))

    c = exact[exact["variant"].eq(EXACT_CANDIDATE)].copy()
    train = c[(c.signal_ts < CUT) & (c.exit_ts < CUT)]
    hold = c[c.signal_ts >= CUT]
    audit.check("artifact_exact_candidate_n_807", len(c) == 807, len(c))
    audit.check("artifact_train_n_209", len(train) == 209, len(train))
    audit.check("artifact_validation_n_598", len(hold) == 598, len(hold))
    audit.check("split_no_train_exit_leak", int(((c.signal_ts < CUT) & (c.exit_ts >= CUT)).sum()) == 0)

    p20t, p20h = perf(train, .20), perf(hold, .20)
    p40t, p40h = perf(train, .40), perf(hold, .40)
    esv = es["variants"][EXACT_CANDIDATE]
    audit.check("recompute_train_pf20", close(p20t["pf"], esv["train"]["20bp"]["pf"], 1e-10), [p20t, esv["train"]["20bp"]])
    audit.check("recompute_holdout_pf20", close(p20h["pf"], esv["holdout"]["20bp"]["pf"], 1e-10), [p20h, esv["holdout"]["20bp"]])
    audit.check("recompute_train_pf40", close(p40t["pf"], esv["train"]["40bp"]["pf"], 1e-10), [p40t, esv["train"]["40bp"]])
    audit.check("recompute_holdout_pf40", close(p40h["pf"], esv["holdout"]["40bp"]["pf"], 1e-10), [p40h, esv["holdout"]["40bp"]])
    audit.check("historical_train_positive_40bp", p40t["pf"] > 1 and p40t["ev_pct"] > 0, p40t)
    audit.check("historical_validation_positive_40bp_seen", p40h["pf"] > 1 and p40h["ev_pct"] > 0, p40h)

    rr = rs["variants"][ROBUST_CANDIDATE]["delay0"]
    audit.check("robust_delay0_n_matches_exact", rr["all"]["20bp"]["n"] == len(c))
    audit.check("robust_delay0_pf_matches_exact", close(rr["all"]["20bp"]["pf"], esv["all"]["20bp"]["pf"], 1e-10))
    audit.check("latency_plus1m_degrades_holdout", rs["variants"][ROBUST_CANDIDATE]["delay1"]["holdout"]["20bp"]["pf"] < 1.0)
    audit.check("forward_market_entry_matches_latency_risk", cfg.get("entry_order_type") == "MARKET" and int(cfg["signal_ttl_seconds"]) < 60)
    audit.check("historical_max_concurrent_exceeds_current_cap_disclosed", int(rr["max_concurrent"]) == 8 and int(cfg["max_open_positions"]) == 6)

    # Mandatory canonical chronology smoke checks.
    chronology_smokes(audit)

    # Representative reconstruction: ten distinct frozen candidate trades from raw shard 0.
    partial = pd.read_csv(partial_path, compression="gzip")
    samples = partial[partial["variant"].eq(EXACT_CANDIDATE)].sort_values(["signal_ts", "symbol"]).head(10).copy()
    audit.check("representative_samples_at_least_10", len(samples) == 10, len(samples))
    keys = {(str(r.symbol), int(r.signal_ts)) for r in samples.itertuples(index=False)}
    event_rows = load_event_rows(events_path, keys)
    audit.check("representative_events_resolved_10", len(event_rows) == 10, len(event_rows))

    config_hash = sha256(cfg_path)
    source_hash = sha256(src_path)
    data_files = sorted(Path(a.raw0).rglob("*USDT.csv.gz"))
    audit.check("raw_shard0_present", len(data_files) > 0, len(data_files))
    audit.check("provenance_exact_run_fixed", EXACT_RUN == 37381087242)
    audit.check("provenance_robust_run_fixed", ROBUST_RUN == 37381429984)
    audit.check("provenance_data_run_fixed", DATA_RUN == 36095439671)
    audit.check("provenance_event_run_fixed", EVENT_RUN == 36934882492)

    rounds = []
    clean_streak = 0
    for idx, row in enumerate(samples.itertuples(index=False), start=1):
        key = (str(row.symbol), int(row.signal_ts))
        event = event_rows.get(key)
        ok, detail = reconstruct_trade(row, Path(a.raw0), event or {"strength_ret_pct": -999, "pct": -999})
        round_ok = (
            ok
            and sha256(cfg_path) == config_hash
            and sha256(src_path) == source_hash
            and key in event_rows
        )
        if round_ok:
            clean_streak += 1
        else:
            clean_streak = 0
        rounds.append({
            "round": idx,
            "pass": bool(round_ok),
            "config_sha256": config_hash,
            "source_sha256": source_hash,
            "trade": detail,
            "clean_streak_after_round": clean_streak,
        })
        print(f"[ROUND {idx}] {'PASS' if round_ok else 'FAIL'} {detail}", flush=True)

    audit.check("ten_consecutive_clean_reconstruction_rounds", clean_streak >= 10, clean_streak)

    report = {
        "experiment_id": cfg["experiment_id"],
        "classification": "AUDITED_EXPLORATORY_FORWARD" if audit.passed else "AUDIT_FAILED",
        "repository": "duuu-hub/bb-scanner",
        "branch": "research-rank5-binance-15m-5y",
        "ruleset_version": "2026-10-02",
        "historical_provenance": {
            "exact_subgroup_run": EXACT_RUN,
            "execution_robustness_run": ROBUST_RUN,
            "raw_data_run": DATA_RUN,
            "global_events_run": EVENT_RUN,
            "historical_market": "Binance USD-M USDT perpetual",
            "forward_market": "Bitget Demo USDT futures",
            "funding": "excluded",
            "historical_costs_roundtrip_pct": [0.20, 0.40],
        },
        "config_sha256": config_hash,
        "source_sha256": source_hash,
        "checks_total": len(audit.checks),
        "checks_passed": sum(x["pass"] for x in audit.checks),
        "checks": audit.checks,
        "clean_rounds": rounds,
        "clean_streak": clean_streak,
    }
    (out / "body80_demo_audit.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("BODY80_DEMO_AUDIT " + json.dumps({"passed": audit.passed, "checks": len(audit.checks), "clean_streak": clean_streak}), flush=True)
    return 0 if audit.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
