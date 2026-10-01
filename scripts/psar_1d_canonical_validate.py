"""Reconcile legacy row counts, audit causality, freeze TRAIN, replay annual folds."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import psar_1d_canonical_engine as e
from psar_1d_canonical_audit import run_audit


def save_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False))


def load_legacy(directory):
    files = sorted(Path(directory).rglob("psar-distance-age-1d-*.json"))
    if len(files) != 8:
        raise ValueError(f"expected eight historical shards, found {len(files)}")
    rows = []
    for path in files:
        data = json.loads(path.read_text())
        assert data["definition"]["tf"] == "1d"
        assert data["definition"]["horizons"] == [1, 2, 4, 8, 16, 24]
        rows.extend({k: r[k] for k in ("symbol", "ts", "d0", "ret8")} for r in data["rows"] if r["side"] == "BEAR" and r["age"] == 3)
        print(f"LEGACY {path.name} age3_total={len(rows)}", flush=True)
    frame = pd.DataFrame(rows).sort_values(["symbol", "ts"]).reset_index(drop=True)
    if frame.duplicated(["symbol", "ts"]).any():
        raise ValueError("duplicate historical shard signal")
    return frame


def legacy_filter(frame):
    q = frame.loc[frame.ts < e.CUTOFF, "d0"].quantile([0, 1 / 3, 2 / 3, 1]).to_numpy()
    band = pd.cut(frame.d0, q, labels=["LOW", "MID", "HIGH"], include_lowest=True)
    return frame[band.isin(["MID", "HIGH"])].copy(), q


def period_metrics(frame):
    return {name: e.metrics(frame.loc[mask, "ret8"] - .4) for name, mask in
            (("TRAIN", frame.ts < e.CUTOFF), ("HOLDOUT", frame.ts >= e.CUTOFF))}


def reconcile(raw, old, out):
    r24, r12 = raw[raw.legacy24], raw[raw.legacy12]
    join = old.merge(r24, on=["symbol", "ts"], how="outer", suffixes=("_old", "_raw"), indicator=True)
    common = join[join._merge == "both"]
    mismatches = common[(abs(common.d0_old - common.d0_raw) > 1e-11) | (abs(common.ret8_old - common.ret8_raw) > 1e-10)]
    join[join._merge != "both"].to_csv(out / "historical_raw_key_mismatch.csv", index=False)
    mismatches.to_csv(out / "historical_raw_feature_mismatch.csv", index=False)
    of, oq = legacy_filter(old)
    nf, nq = legacy_filter(r12)
    old_keys = set(zip(of.symbol, of.ts))
    new_keys = set(zip(nf.symbol, nf.ts))
    changes = []
    source = raw.set_index(["symbol", "ts"])
    for sym, ts in sorted(old_keys.symmetric_difference(new_keys)):
        r = source.loc[(sym, ts)]
        reason = "tail_24_vs_12_or_short_segment" if not r.legacy24 else "train_tercile_boundary_shift"
        changes.append({"symbol": sym, "ts": int(ts), "date_utc": pd.Timestamp(ts, unit="ms", tz="UTC").isoformat(),
                        "period": "TRAIN" if ts < e.CUTOFF else "HOLDOUT", "change": "ADDED" if (sym, ts) in new_keys else "REMOVED",
                        "reason": reason, "remaining_days": int(r.remaining_days), "d0": float(r.d0), "ret8_pct": float(r.ret8)})
    delta = pd.DataFrame(changes)
    delta.to_csv(out / "legacy_6094_vs_6288_delta.csv", index=False)
    by_reason = delta.groupby(["period", "change", "reason"]).size().reset_index(name="n").to_dict("records") if len(delta) else []
    by_symbol = delta.groupby(["period", "symbol", "change"]).size().reset_index(name="n").to_dict("records") if len(delta) else []
    report = {"historical_rows": len(old), "raw_tail24_rows": len(r24), "raw_tail12_rows": len(r12),
              "historical_raw_common": len(common), "historical_only": int((join._merge == "left_only").sum()),
              "raw24_only": int((join._merge == "right_only").sum()), "feature_mismatches": len(mismatches),
              "old_edges": oq.tolist(), "new_edges": nq.tolist(), "old_filtered": period_metrics(of), "new_filtered": period_metrics(nf),
              "delta_by_reason": by_reason, "delta_by_symbol": by_symbol,
              "mechanism": "legacy study required 24 future daily bars; horizon/reflip study required 12. Short segment gates also used that maximum. Recomputing TRAIN terciles can move boundary signals."}
    save_json(out / "reconciliation.json", report)
    if report["historical_only"] or report["raw24_only"] or report["feature_mismatches"]:
        raise ValueError("historical artifact not reproduced; inspect reconciliation outputs")
    assert report["old_filtered"]["TRAIN"]["n"] == 4303
    assert report["old_filtered"]["HOLDOUT"]["n"] == 6094
    assert report["new_filtered"]["TRAIN"]["n"] == 4304
    assert report["new_filtered"]["HOLDOUT"]["n"] == 6288
    print("RECONCILE_PASS historical=6094 new=6288", flush=True)
    return report


def btc_regimes(cases):
    frames = []
    for sym, segment, t, o, h, l, c in cases:
        if sym != "BTCUSDT":
            continue
        confirmed = pd.Series(c).rolling(200, min_periods=200).mean().shift(1).to_numpy()
        previous = np.r_[np.nan, c[:-1]]
        regime = np.where(np.isfinite(confirmed), np.where(previous >= confirmed, "BTC_ABOVE_200D", "BTC_BELOW_200D"), "UNKNOWN")
        frames.append(pd.DataFrame({"ts": t, "regime": regime}))
    if not frames:
        return pd.DataFrame(columns=["ts", "regime"])
    frame = pd.concat(frames, ignore_index=True)
    assert not frame.duplicated("ts").any()
    return frame


def grouped_report(frame, maximum, dimension):
    rows = []
    for key, group in frame.groupby(dimension, sort=True):
        hold = group[f"hold{maximum}"].to_numpy(int)
        for cost in (.2, .4):
            rows.append({dimension: str(key), "cost_bp": int(cost * 100), **e.metrics(e.returns(group, maximum) - cost),
                         "avg_hold_days": float(hold.mean()), "independent_entry_days": int(group.ts.nunique())})
    return rows


def replay_walk_forward(frame):
    folds = []
    for mode in ("EXPANDING", "ROLLING_2Y"):
        for year in range(2023, 2027):
            start = int(pd.Timestamp(f"{year}-01-01", tz="UTC").timestamp() * 1000)
            end = int(pd.Timestamp(f"{year + 1}-01-01", tz="UTC").timestamp() * 1000)
            lower = int(pd.Timestamp(f"{year - 2}-01-01", tz="UTC").timestamp() * 1000) if mode == "ROLLING_2Y" else int(frame.ts.min())
            pool = frame[frame.ts >= lower]
            train = e.train_pool(pool, start)
            if len(train) < 100:
                continue
            q = e.freeze_threshold(pool, start)
            selected_train = e.select_d0(train, q)
            maximum, scores = e.choose_train_maximum(selected_train)
            test = e.select_d0(frame[(frame.ts >= start) & (frame.ts < end) & (frame.max_exit_ts <= end)], q)
            folds.append({"mode": mode, "test_year": year, "train_start_utc": pd.Timestamp(lower, unit="ms", tz="UTC").isoformat(),
                          "freeze_utc": pd.Timestamp(start, unit="ms", tz="UTC").isoformat(), "d0_threshold": q,
                          "train_n": len(selected_train), "selected_max_days": maximum, "train_candidates": scores,
                          "test": {str(int(cost * 100)): e.metrics(e.returns(test, maximum) - cost) for cost in (.2, .4, .8)},
                          "test_entry_days": int(test.ts.nunique()), "test_avg_hold_days": float(test[f"hold{maximum}"].mean()) if len(test) else None})
    return {"kind": "retrospective annual expanding and rolling two-year train-only replay",
            "selection": "TRAIN reflip PF at 40bp; D0 q1/3 from purged TRAIN; deterministic shorter-hold tie break",
            "honesty": "all years are already observed research data; these folds are not untouched forward OOS", "folds": folds}


def paired_day_bootstrap(frame, maximum):
    day = pd.DataFrame({"ts": frame.ts, "improvement": e.returns(frame, maximum) - e.returns(frame, maximum, False)})
    daily = day.groupby("ts").improvement.mean()
    calendar = np.arange(int(day.ts.min()), int(day.ts.max()) + e.DAY, e.DAY)
    series = daily.reindex(calendar).to_numpy(float)
    rng = np.random.default_rng(61001)
    estimates = []
    block = 14
    for _ in range(1000):
        starts = rng.integers(0, len(series), int(np.ceil(len(series) / block)))
        values = np.concatenate([series[(start + np.arange(block)) % len(series)] for start in starts])[:len(series)]
        estimates.append(float(np.nanmean(values)))
    return {"independent_entry_days": len(daily), "equal_weight_day_improvement_pct": float(daily.mean()),
            "calendar_block_days": block, "resamples": 1000,
            "ci95_equal_weight_day_improvement_pct": np.quantile(estimates, [.025, .975]).tolist(),
            "scope": "paired reflip-minus-fixed improvement; temporal blocks and same-day clustering, not account returns"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--legacy", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    raw, manifest, stats, cases = e.collect(args.data)
    raw.to_csv(out / "canonical_all_signals.csv.gz", index=False)
    save_json(out / "input_manifest.json", manifest)
    old = load_legacy(args.legacy)
    rec = reconcile(raw, old, out)
    audit = run_audit(cases, raw, manifest)
    save_json(out / "audit_30_by_10.json", audit)
    q = e.freeze_threshold(raw)
    train = e.select_d0(e.train_pool(raw), q)
    maximum, train_scores = e.choose_train_maximum(train)
    # Persist TRAIN choice before evaluating HOLDOUT outcomes.
    frozen = {"age": 3, "age_definition": "i minus flip index; flip OPEN is age zero", "d0_threshold": q,
              "d0_rule": "d0 > TRAIN q1/3; no upper bound", "selected_max_days": maximum,
              "exit": "first BULL OPEN or maximum OPEN", "cost_selection_bp": 40,
              "train_purge": "max eight-day exit timestamp <= 2025-01-01 UTC",
              "train_n": len(train), "train_signal_hash": e.digest_frame(train), "train_candidates": train_scores}
    save_json(out / "frozen_train_choice.json", frozen)
    print(f"TRAIN_FROZEN max={maximum} d0={q} n={len(train)}", flush=True)
    holdout = e.select_d0(raw[raw.ts >= e.CUTOFF], q)
    all_filtered = e.select_d0(raw, q)
    all_filtered["year"] = pd.to_datetime(all_filtered.ts, unit="ms", utc=True).dt.year
    all_filtered = all_filtered.merge(btc_regimes(cases), on="ts", how="left")
    all_filtered["regime"] = all_filtered.regime.fillna("UNKNOWN")
    year = grouped_report(all_filtered, maximum, "year")
    regime = grouped_report(all_filtered.assign(period=np.where(all_filtered.ts < e.CUTOFF, "TRAIN", "HOLDOUT")), maximum, "regime")
    regime_split = []
    for period, group in all_filtered.groupby(np.where(all_filtered.ts < e.CUTOFF, "TRAIN", "HOLDOUT")):
        regime_split.extend({"period": period, **r} for r in grouped_report(group, maximum, "regime"))
    wf = replay_walk_forward(raw)
    save_json(out / "walk_forward.json", wf)
    pd.DataFrame(year).to_csv(out / "yearly.csv", index=False)
    pd.DataFrame(regime_split).to_csv(out / "regimes.csv", index=False)
    for name, frame in (("TRAIN", train), ("HOLDOUT", holdout)):
        frame.to_csv(out / f"filtered_{name.lower()}.csv.gz", index=False)
    stress = {}
    for name, frame in (("TRAIN", train), ("HOLDOUT", holdout)):
        stress[name] = {str(int(cost * 100)): e.metrics(e.returns(frame, maximum) - cost) for cost in (.2, .4, .6, .8, 1.)}
    summary = {"contract": frozen, "data_stats": stats, "all_signal_hash": e.digest_frame(raw),
               "period_utc": {"first_signal": pd.Timestamp(raw.ts.min(), unit="ms", tz="UTC").isoformat(), "last_signal": pd.Timestamp(raw.ts.max(), unit="ms", tz="UTC").isoformat()},
               "reconciliation": {k: v for k, v in rec.items() if k != "delta_by_symbol"},
               "audit": {"distinct": 30, "consecutive_clean": 10, "real_segments_per_round": len(cases)},
               "train_candidates_40bp": train_scores, "holdout_candidates_40bp": e.candidates(holdout),
               "selected_cost_stress": stress, "yearly": year, "regimes": regime_split,
               "paired_holdout_reflip_improvement": paired_day_bootstrap(holdout, maximum),
               "walk_forward": wf,
               "limitations": ["HOLDOUT and replay years were already inspected; not pristine OOS",
                               "Signal statistics do not impose maximum positions, exposure, or liquidation; no account return or MDD claim",
                               "Flat 20-100bp round-trip costs; historical funding is not available or included",
                               "No TP/SL; exits execute at observed daily OPEN; no one-minute chronology required",
                               "Full-day source availability is a retrospective data-integrity filter; source universe includes historical listing/delisting coverage only"]}
    save_json(out / "summary.json", summary)
    print(json.dumps({"selected_max_days": maximum, "selected_cost_stress": stress,
                      "audit": summary["audit"], "reconciliation": summary["reconciliation"],
                      "yearly": year, "walk_forward": [{"mode": f["mode"], "year": f["test_year"], "max": f["selected_max_days"], "test40": f["test"]["40"]} for f in wf["folds"]]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
