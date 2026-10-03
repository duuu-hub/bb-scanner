"""Preregistered V21 contract-price versus mark-price dislocation reversal."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from scripts import day_edge_lab as base
from scripts import day_edge_canonical as canonical
from scripts import relative_pullback_portfolio as account
from scripts import btc_factor_lag_v6 as history
from scripts import cross_sectional_ranks_v9 as source_helpers
from scripts import mark_price_archive_v21 as mark_archive
from scripts import official_minute_provenance as official
from scripts import premium_absorption_v8 as minute_audit
from scripts import relative_pullback_v1 as chronology
from scripts.cross_sectional_leader_v9 import strict_accounts
from scripts.shock_confirmation_v3 import load

BAR = base.BAR
HOLDS = (16, 32)
EXITS = ("MARK", "R15", "R25")
ROOT = Path(__file__).resolve().parents[1]
# The transaction-price universe is intentionally identical to V20, but V21
# carries its own immutable copy so every result branch is self-contained.
CONTEXT = ROOT / "research/contract-mark-dislocation-v21/FROZEN_CONTEXT.json"
digest = source_helpers.digest

COLUMNS = [
    "symbol", "key", "signal_time", "decision_time", "entry_time", "entry", "sl", "side",
    "risk_pct", "score", "atr_mult", "prior_atr", "buy_share", "volume_multiple",
    "excursion_atr", "close_tolerance_atr", "volume_threshold", "contract_extreme",
    "mark_extreme", "mark_open", "mark_high", "mark_low", "mark_close", "mark_prior_close",
    "mark_range_atr", "contract_mark_excursion_atr", "contract_mark_close_gap_atr",
    "structural_stop", "mark_target", "clv", "known_entry_gap", "tp", "max_hold_bars",
    "status", "exit_time", "exit", "reason", "gross_return", "variant", "policy",
    "exit_type", "hold_min", "net40_fraction", "net40_R", "split",
]


def configurations():
    return [
        {
            "key": f"CMD_S{side:+d}_E{int(excursion * 100):02d}_T{int(tolerance * 100):02d}_V{int(volume * 100):03d}",
            "side": side,
            "excursion": excursion,
            "tolerance": tolerance,
            "volume": volume,
        }
        for side in (1, -1)
        for excursion in (0.10, 0.25)
        for tolerance in (0.10, 0.25)
        for volume in (1.25, 1.75)
    ]


def policies():
    return [
        {**cfg, "hold": hold, "exit_type": exit_type, "policy": f'{cfg["key"]}__H{hold}__{exit_type}'}
        for cfg in configurations()
        for hold in HOLDS
        for exit_type in EXITS
    ]


def development_rejections(row):
    reasons = []
    if row["n"] < 300:
        reasons.append("N_LT_300")
    if row["symbols"] < 60:
        reasons.append("SYMBOLS_LT_60")
    if row["top_symbol_share_pct"] > 30:
        reasons.append("TOP_SYMBOL_GT_30PCT")
    for key in ("net40_mean_bp", "net40_R_mean"):
        if row[key] is None or not np.isfinite(row[key]) or row[key] <= 0:
            reasons.append(key.upper() + "_NONPOSITIVE")
    for year in (2021, 2022, 2023):
        if row.get(f"year_{year}_n", 0) < 30:
            reasons.append(f"{year}_N_LT_30")
        if row.get(f"year_{year}_days", 0) < 20:
            reasons.append(f"{year}_DATES_LT_20")
        for metric in ("net40_R", "net40_mean_bp", "day_R"):
            if row.get(f"year_{year}_{metric}", -999) <= 0:
                reasons.append(f"{year}_{metric}_NONPOSITIVE")
    return reasons


def features(raw, quote, buy_quote):
    result = history.features(raw, quote, buy_quote)
    result["quote"] = quote.copy()
    result["prior_quote_mean"] = np.full(len(quote), np.nan)
    for start, end in base.segments(raw[0]):
        prior = pd.Series(quote[start:end]).rolling(96, min_periods=96).mean().shift(1).to_numpy()
        result["prior_quote_mean"][start:end] = prior
    result["volume_multiple"] = np.divide(
        quote,
        result["prior_quote_mean"],
        out=np.full(len(quote), np.nan),
        where=result["prior_quote_mean"] > 0,
    )
    return result


def signal_mask(cfg, raw, features_, mark):
    t, _, high, low, close = raw
    side = cfg["side"]
    atr = features_["prior_atr"]
    previous_mark_close = np.r_[np.nan, mark["mark_close"][:-1]]
    contiguous = np.r_[False, np.diff(t) == BAR]
    previous_available = np.r_[False, mark["mark_available"][:-1]]
    mark_range = np.maximum(abs(mark["mark_high"] - previous_mark_close), abs(mark["mark_low"] - previous_mark_close))
    contract_extreme = low if side == 1 else high
    mark_extreme = mark["mark_low"] if side == 1 else mark["mark_high"]
    excursion = side * (mark_extreme - contract_extreme)
    close_gap = abs(close - mark["mark_close"])
    midpoint = (high + low) / 2
    rejected = side * (close - midpoint) > 0
    flow = features_["buy_share"] <= 0.45 if side == 1 else features_["buy_share"] >= 0.55
    finite = (
        np.isfinite(atr)
        & (atr > 0)
        & np.isfinite(previous_mark_close)
        & np.isfinite(mark["mark_open"])
        & np.isfinite(mark["mark_high"])
        & np.isfinite(mark["mark_low"])
        & np.isfinite(mark["mark_close"])
    )
    return (
        features_["eligible"]
        & mark["mark_available"]
        & previous_available
        & contiguous
        & finite
        & (features_["volume_multiple"] >= cfg["volume"])
        & (mark_range <= atr)
        & (excursion >= cfg["excursion"] * atr)
        & (close_gap <= cfg["tolerance"] * atr)
        & rejected
        & flow
    )


def intents(symbol, cfg, raw, features_, mark, start, end):
    t, open_, high, low, close = raw
    rows = []
    excluded = Counter()
    last_intent = -10_000
    for i in np.flatnonzero(signal_mask(cfg, raw, features_, mark)):
        if i - last_intent < 16:
            excluded["INTENT_COOLDOWN"] += 1
            continue
        last_intent = int(i)
        j = i + 1
        if j >= len(t) or not start <= t[j] < end:
            continue
        if t[j] != t[i] + BAR:
            excluded["ENTRY_PATH_GAP"] += 1
            continue
        side = cfg["side"]
        entry = float(open_[j])
        atr = float(features_["prior_atr"][i])
        if not np.isfinite(entry) or entry <= 0 or not np.isfinite(atr) or atr <= 0:
            excluded["INVALID_ENTRY_OR_ATR"] += 1
            continue
        gap = side * (entry / close[i] - 1)
        if gap > 0.005:
            excluded["ENTRY_CATCHUP_GAP"] += 1
            continue
        contract_extreme = float(low[i] if side == 1 else high[i])
        mark_extreme = float(mark["mark_low"][i] if side == 1 else mark["mark_high"][i])
        structural_stop = contract_extreme - side * 0.10 * atr
        raw_distance = side * (entry - structural_stop)
        if raw_distance <= 0:
            excluded["STRUCTURAL_STOP_WRONG_SIDE"] += 1
            continue
        distance = max(raw_distance, 0.005 * entry)
        if distance / entry > 0.06:
            excluded["STOP_ABOVE_6PCT"] += 1
            continue
        stop = entry - side * distance
        if stop <= 0:
            excluded["NONPOSITIVE_LEVEL"] += 1
            continue
        mark_prior_close = float(mark["mark_close"][i - 1])
        mark_range = max(abs(mark["mark_high"][i] - mark_prior_close), abs(mark["mark_low"][i] - mark_prior_close))
        excursion = side * (mark_extreme - contract_extreme)
        close_gap = abs(close[i] - mark["mark_close"][i])
        risk = distance / entry
        rows.append(
            {
                "symbol": symbol,
                "key": cfg["key"],
                "signal_time": int(t[i]),
                "decision_time": int(t[i] + BAR),
                "entry_time": int(t[j]),
                "entry_index": int(j),
                "entry": entry,
                "sl": float(stop),
                "side": int(side),
                "risk_pct": float(risk),
                "score": float((excursion / atr) * np.sqrt(features_["volume_multiple"][i]) / risk),
                "atr_mult": 3.0,
                "prior_atr": atr,
                "buy_share": float(features_["buy_share"][i]),
                "volume_multiple": float(features_["volume_multiple"][i]),
                "excursion_atr": cfg["excursion"],
                "close_tolerance_atr": cfg["tolerance"],
                "volume_threshold": cfg["volume"],
                "contract_extreme": contract_extreme,
                "mark_extreme": mark_extreme,
                "mark_open": float(mark["mark_open"][i]),
                "mark_high": float(mark["mark_high"][i]),
                "mark_low": float(mark["mark_low"][i]),
                "mark_close": float(mark["mark_close"][i]),
                "mark_prior_close": mark_prior_close,
                "mark_range_atr": float(mark_range / atr),
                "contract_mark_excursion_atr": float(excursion / atr),
                "contract_mark_close_gap_atr": float(close_gap / atr),
                "structural_stop": float(structural_stop),
                "mark_target": float(mark["mark_close"][i]),
                "clv": float(features_["clv"][i]),
                "known_entry_gap": float(gap),
            }
        )
    return rows, excluded


def policy_rows(symbol, chosen, raw, features_, mark, start, end):
    rows, counts, bad = [], Counter(), []
    grouped = {}
    for policy in chosen:
        grouped.setdefault(policy["key"], []).append(policy)
    for group in grouped.values():
        seeds, excluded = intents(symbol, group[0], raw, features_, mark, start, end)
        counts.update({group[0]["key"] + "/" + key: value for key, value in excluded.items()})
        for policy in group:
            for seed in seeds:
                risk = abs(seed["entry"] - seed["sl"])
                if policy["exit_type"] == "MARK":
                    tp = seed["mark_target"]
                elif policy["exit_type"] == "R15":
                    tp = seed["entry"] + seed["side"] * 1.5 * risk
                elif policy["exit_type"] == "R25":
                    tp = seed["entry"] + seed["side"] * 2.5 * risk
                else:
                    raise ValueError("unknown V21 exit")
                if seed["side"] * (tp - seed["entry"]) <= 0:
                    counts[policy["policy"] + "/TARGET_WRONG_SIDE"] += 1
                    continue
                trade = {**seed, "tp": float(tp), "max_hold_bars": policy["hold"]}
                result = canonical.resolve(trade, raw, features_, "TP2", end)
                counts[policy["policy"] + "/" + result["status"]] += 1
                if result["status"] != "RESOLVED":
                    bad.append({"symbol": symbol, "policy": policy["policy"], "entry_time": trade["entry_time"], "status": result["status"]})
                    continue
                row = {key: value for key, value in trade.items() if key != "entry_index"}
                row.update(result, variant=policy["policy"], policy=policy["policy"], exit_type=policy["exit_type"])
                row["hold_min"] = (row["exit_time"] - row["entry_time"]) / 60_000
                exit_price = row["exit"] * (1 - row["side"] * 0.001) if row["reason"] == "SL" else row["exit"]
                ratio = exit_price / row["entry"]
                net = row["side"] * (ratio - 1) - 0.002 * (1 + ratio) - 0.0002 * row["hold_min"] / 1440
                row["net40_fraction"] = float(net)
                row["net40_R"] = float(net / account.stop_loss_fraction(row, 0.002))
                rows.append(row)
    return rows, counts, bad


def mark_coverage(originals):
    totals = {str(year): {"eligible": 0, "known": 0} for year in (2021, 2022, 2023)}
    seen = set()
    for meta in originals:
        for coin in meta["coverage"]:
            if coin["symbol"] in seen:
                raise ValueError("duplicate mark coverage symbol")
            seen.add(coin["symbol"])
            for year, values in coin["mark_year_coverage"].items():
                if not 0 <= values["known"] <= values["eligible"]:
                    raise ValueError("invalid mark coverage counts")
                if year in totals:
                    totals[year]["eligible"] += values["eligible"]
                    totals[year]["known"] += values["known"]
    for values in totals.values():
        values["unavailable"] = values["eligible"] - values["known"]
        values["fraction"] = values["known"] / values["eligible"] if values["eligible"] else None
    passed = all(values["eligible"] > 0 and values["known"] * 100 >= 95 * values["eligible"] for values in totals.values())
    return {"passed": passed, "minimum_fraction": 0.95, "years": totals, "symbols": len(seen)}


def verify_source(source_check, paths, btc_path, context_path=CONTEXT):
    context = json.loads(context_path.read_text())
    source = json.loads(source_check.read_text())
    if source["status"] != "VERIFIED" or len(source["shards"]) != 1 or source["shards"][0] not in range(8):
        raise ValueError("one verified original shard required")
    if source["baseline_sha256"] != context["baseline_sha256"] or digest(btc_path) != context["btc_sha256"]:
        raise ValueError("frozen baseline/BTC mismatch")
    verified = {item["symbol"]: item["sha256"] for item in source["files"]}
    if len(verified) != len(source["files"]) or len(paths) != len(verified) or not paths:
        raise ValueError("source file missing/duplicate")
    actual = {}
    for path in paths:
        symbol, sha = path.name[:-7], digest(path)
        if symbol in actual or verified.get(symbol) != sha or context["expected_market_sha256"].get(symbol) != sha:
            raise ValueError("source mismatch/duplicate " + symbol)
        actual[symbol] = sha
    if actual != verified:
        raise ValueError("omitted verified source")
    return source, context, actual


def scan(data, btc_path, out, minute_cache, mark_cache, stage, source_check,
         selection_path=None, context_path=CONTEXT, mark_loader=None):
    paths = sorted(data.rglob("*.csv.gz"))
    source, context, sources = verify_source(source_check, paths, btc_path, context_path)
    start, end = source_helpers.interval(stage)
    if stage == "DEV":
        chosen = policies()
    else:
        selected = json.loads(selection_path.read_text())
        chosen = selected["policies"]
        info = {item["policy"]: item for item in policies()}
        if (selected["status"] != "ACCOUNT_REPLAY_REQUIRED" or not chosen
                or selected["source_context_sha256"] != digest(context_path)):
            raise ValueError("no consistent frozen selection")
        if any(item["policy"] not in info or any(item[key] != value for key, value in info[item["policy"]].items())
               for item in chosen):
            raise ValueError("unregistered/altered selection")
    out.mkdir(parents=True, exist_ok=True)
    chronology.MINUTE_CACHE_DIR = minute_cache
    chronology.chronology.one_min = minute_audit.audited_minutes
    chronology.chronology.CACHE.clear()
    official.INPUTS.clear()
    minute_audit.MINUTE_INPUTS.clear()
    minute_audit.MINUTE_SLICES.clear()
    minute_audit.SLICE_DIR = out / "minute_evidence"
    minute_audit.MINUTE_RAW_DIR = out / "minute_original_archives"
    minute_audit.SLICE_DIR.mkdir(exist_ok=True)
    minute_audit.MINUTE_RAW_DIR.mkdir(exist_ok=True)
    mark_archive.INPUTS.clear()
    shutil.copyfile(source_check, out / "source_check.json")
    shutil.copyfile(context_path, out / "frozen_context.json")
    loader = mark_loader or mark_archive.load_symbol
    rows, counts, bad, coverage = [], Counter(), [], []

    def checkpoint(complete):
        ledger = out / "independent_candidates.csv.gz"
        pd.DataFrame(rows, columns=COLUMNS).to_csv(
            ledger, index=False, compression={"method": "gzip", "mtime": 0}
        )
        pd.DataFrame(bad, columns=["symbol", "policy", "entry_time", "status"]).to_csv(
            out / "exclusions.csv", index=False
        )
        (out / "minute_inputs.json").write_text(json.dumps(list(minute_audit.MINUTE_INPUTS.values()), indent=2))
        (out / "minute_slices.json").write_text(json.dumps(list(minute_audit.MINUTE_SLICES.values()), indent=2))
        (out / "mark_inputs.json").write_text(json.dumps(list(mark_archive.INPUTS.values()), indent=2))
        meta = {
            "complete": complete, "stage": stage, "shard": source["shards"][0],
            "counts": dict(counts), "coverage": coverage, "source_files": len(paths),
            "market_hashes": sources, "policies": chosen, "ledger_rows": len(rows),
            "ledger_sha256": digest(ledger), "exclusions": len(bad),
            "baseline_sha256": context["baseline_sha256"],
            "source_check_sha256": digest(source_check),
            "source_context_sha256": digest(context_path), "btc_sha256": digest(btc_path),
            "mark_months": len(mark_archive.INPUTS),
            "mark_validated_months": sum(item.get("status") == "VALIDATED_OFFICIAL_CHECKSUM"
                                          for item in mark_archive.INPUTS.values()),
            "mark_gap_months": sum(item.get("status") == "MARK_DATA_GAP"
                                    for item in mark_archive.INPUTS.values()),
            "minute_months": len(minute_audit.MINUTE_INPUTS),
            "minute_official_checksums_verified": sum(bool(item.get("checksum_verified"))
                                                       for item in minute_audit.MINUTE_INPUTS.values()),
            "selection_sha256": digest(selection_path) if selection_path else None,
        }
        (out / "scan_meta.json").write_text(json.dumps(meta, indent=2, allow_nan=False))

    try:
        for number, path in enumerate(paths, 1):
            symbol = path.name[:-7]
            raw, quote, buy = load(path, end)
            if not len(raw[0]) or raw[0][0] >= base.DEV_END - 30 * base.DAY:
                coverage.append({"symbol": symbol, "status": "NO_DEVELOPMENT_HISTORY", "outcomes": 0,
                                 "mark_year_coverage": {}})
                continue
            f = features(raw, quote, buy)
            eligible = f["eligible"] & (raw[0] + BAR >= start) & (raw[0] + BAR < end)
            if eligible.any():
                mark_data, valid_months = loader(symbol, raw[0], start, end, mark_cache, out / "mark_evidence")
            else:
                mark_data = (np.array([], np.int64),) + (np.array([], float),) * 4
                valid_months = set()
            aligned = mark_archive.align(raw[0], mark_data, valid_months, end)
            decision_year = pd.to_datetime(raw[0] + BAR, unit="ms", utc=True).tz_convert("Asia/Seoul").year
            year_coverage = {
                str(year): {
                    "eligible": int((eligible & (decision_year == year)).sum()),
                    "known": int((eligible & aligned["mark_available"] & (decision_year == year)).sum()),
                }
                for year in sorted(set(decision_year[eligible]))
            }
            resolved, excluded, malformed = policy_rows(symbol, chosen, raw, f, aligned, start, end)
            for row in resolved:
                row["split"] = stage
            rows.extend(resolved)
            bad.extend(malformed)
            counts.update({stage + "/" + key: value for key, value in excluded.items()})
            coverage.append({
                "symbol": symbol, "status": "SCANNED", "bars": len(raw[0]), "outcomes": len(resolved),
                "eligible_bars": int(eligible.sum()),
                "known_mark_bars": int((eligible & aligned["mark_available"]).sum()),
                "unavailable_mark_bars": int((eligible & ~aligned["mark_available"]).sum()),
                "mark_year_coverage": year_coverage, "valid_mark_months": sorted(valid_months),
                "market_sha256": sources[symbol],
                "mark_source_prefix": ("https://data.binance.vision/?prefix=data/futures/um/monthly/"
                                       f"markPriceKlines/{symbol}/15m/"),
            })
            chronology.chronology.CACHE.clear()
            if number % 8 == 0 or number == len(paths):
                checkpoint(False)
                print("V21_CONTRACT_MARK_SCAN", stage, number, "/", len(paths),
                      "parameterized_outcomes", len(rows), flush=True)
        if not coverage:
            raise ValueError("no frozen market coverage")
    except BaseException:
        checkpoint(False)
        raise
    checkpoint(True)
    print("V21_CONTRACT_MARK_SCAN_DONE", stage, len(rows), "chronology_exclusions", len(bad), flush=True)


def select(parts, out, context_path=CONTEXT):
    out.mkdir(parents=True, exist_ok=True)
    paths = sorted(parts.rglob("independent_candidates.csv.gz"))
    if len(paths) != 8:
        raise ValueError("incomplete development shards")
    context = json.loads(context_path.read_text())
    source_context_sha = digest(context_path)
    originals, frames, sources, shards = [], [], {}, set()
    for path in paths:
        meta = json.loads((path.parent / "scan_meta.json").read_text())
        if (not meta["complete"] or meta["stage"] != "DEV" or meta["ledger_sha256"] != digest(path)
                or meta["source_context_sha256"] != source_context_sha
                or meta["btc_sha256"] != context["btc_sha256"]
                or meta["baseline_sha256"] != context["baseline_sha256"] or meta["policies"] != policies()):
            raise ValueError("incomplete/altered development scan")
        if meta["shard"] in shards:
            raise ValueError("duplicate development shard")
        shards.add(meta["shard"])
        for symbol, sha in meta["market_hashes"].items():
            if symbol in sources or context["expected_market_sha256"].get(symbol) != sha:
                raise ValueError("source catalogue mismatch/duplicate")
            sources[symbol] = sha
        frame = pd.read_csv(path)
        if len(frame) != meta["ledger_rows"]:
            raise ValueError("ledger count mismatch")
        originals.append(meta)
        frames.append(frame)
    if shards != set(range(8)) or sources != context["expected_market_sha256"]:
        raise ValueError("incomplete global development universe")
    coverage_gate = mark_coverage(originals)
    nonempty = [frame for frame in frames if len(frame)]
    data = pd.concat(nonempty, ignore_index=True) if nonempty else frames[0].iloc[:0].copy()
    if data.duplicated(["symbol", "variant", "entry_time"]).any():
        raise ValueError("duplicate development intent")
    if len(data) and (not data["split"].eq("DEV").all()
                      or not data.entry_time.between(base.START, base.DEV_END - 1).all()):
        raise ValueError("development stage leakage")
    info = {item["policy"]: item for item in policies()}
    if not set(data.variant).issubset(info) or not set(data.symbol).issubset(sources):
        raise ValueError("unregistered policy or source symbol")
    grouped = {key: group for key, group in data.groupby("variant")}
    table = []
    for name, policy in info.items():
        group = grouped.get(name, data.iloc[:0])
        years = pd.to_datetime(group.entry_time, unit="ms", utc=True).dt.tz_convert("Asia/Seoul").dt.year
        pnl, risk_r = group.net40_fraction, group.net40_R
        gain, loss = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
        symbol_gain = group.groupby("symbol").net40_fraction.sum().clip(lower=0)
        row = {
            **policy, "n": len(group), "symbols": group.symbol.nunique(),
            "net40_mean_bp": float(pnl.mean() * 10000) if len(group) else None,
            "net40_R_mean": float(risk_r.mean()) if len(group) else None,
            "net40_pf": float(gain / loss) if loss else None,
            "win_pct": float((pnl > 0).mean() * 100) if len(group) else None,
            "top_symbol_share_pct": float(symbol_gain.max() / symbol_gain.sum() * 100)
            if symbol_gain.sum() > 0 else 100.0,
        }
        for year, year_group in group.groupby(years):
            days = (year_group.entry_time.to_numpy(np.int64) + account.KOREA_OFFSET) // base.DAY
            day_r = year_group.groupby(days).net40_R.mean()
            row.update({
                f"year_{year}_net40_R": float(year_group.net40_R.mean()),
                f"year_{year}_net40_mean_bp": float(year_group.net40_fraction.mean() * 10000),
                f"year_{year}_day_R": float(day_r.mean()), f"year_{year}_days": len(day_r),
                f"year_{year}_n": len(year_group),
            })
        row["rejections"] = "|".join(development_rejections(row))
        table.append(row)
    table.sort(key=lambda row: (-min(row.get(f"year_{year}_day_R", -999) for year in (2021, 2022, 2023)),
                                row["policy"]))
    chosen, used, side_count = [], set(), Counter()
    for row in table:
        if row["rejections"] or row["key"] in used or side_count[row["side"]] >= 3:
            continue
        chosen.append({**info[row["policy"]], "development_net40_R": row["net40_R_mean"],
                       "development_worst_year_day_R": min(row[f"year_{year}_day_R"]
                                                           for year in (2021, 2022, 2023))})
        used.add(row["key"])
        side_count[row["side"]] += 1
        if len(chosen) == 6:
            break
    status = ("SOURCE_COVERAGE_INSUFFICIENT" if not coverage_gate["passed"] else
              "ACCOUNT_REPLAY_REQUIRED" if chosen else "NO_DEVELOPMENT_POLICY_SURVIVOR")
    decision = {
        "status": status, "cells_examined": 96, "selected": chosen, "policies": chosen,
        "union_name": "CONTRACT_MARK_DISLOCATION_UNION", "source_context_sha256": source_context_sha,
        "source_symbols": len(sources), "mark_coverage": coverage_gate,
        "economic_surviving_cells": sum(not row["rejections"] for row in table),
        "rejection_counts": dict(Counter(reason for row in table
                                          for reason in row["rejections"].split("|") if reason)),
        "note": "Frozen contract-versus-mark dislocation reversal. Overlapping diagnostics are not executable account growth.",
    }
    pd.DataFrame(table).to_csv(out / "development_policy_cells.csv", index=False)
    (out / "selection.json").write_text(json.dumps(decision, indent=2, allow_nan=False))
    selection_hash = digest(out / "selection.json")
    selected_names = {item["policy"] for item in chosen}
    for number, (path, meta) in enumerate(zip(paths, originals)):
        shard = pd.read_csv(path)
        shard = shard[shard.variant.isin(selected_names)]
        target = out / f"dev-{number}"
        target.mkdir(exist_ok=True)
        shard.to_csv(target / "independent_candidates.csv.gz", index=False, compression="gzip")
        (target / "scan_meta.json").write_text(json.dumps({
            "complete": True, "stage": "DEV", "shard": meta["shard"],
            "selection_sha256": selection_hash, "source_development_ledger_sha256": meta["ledger_sha256"],
            "source_context_sha256": source_context_sha, "coverage": meta["coverage"],
        }, indent=2))
    print("V21_CONTRACT_MARK_SELECT", json.dumps(decision), flush=True)


def accounts(data, parts, out, selection_path):
    canonical.accounts(data, parts, out, selection_path, ("DEV", "GATE"), expected_shards=16,
                       all_kst_days=True)
    shutil.copyfile(out / "survivors.json", out / "shared_engine_decision.json")
    original = json.loads((out / "survivors.json").read_text())
    result = strict_accounts(pd.read_csv(out / "summary.csv"), pd.read_csv(out / "year_quarter.csv"),
                             original["integrity"])
    (out / "survivors.json").write_text(json.dumps(result, indent=2, allow_nan=False))
    print("V21_STRICT_ACCOUNT_DECISION", json.dumps(result), flush=True)


def smoke(out):
    """Synthetic source -> eight shards -> coverage gate -> all 96 cells."""
    out.mkdir(parents=True, exist_ok=True)
    # Forty contiguous days in each DEV year exercise the per-year 95% gate
    # without pretending this synthetic no-signal fixture is market evidence.
    segments = [
        int(pd.Timestamp(value, tz="UTC").timestamp() * 1000) + np.arange(40 * 96, dtype=np.int64) * BAR
        for value in ("2021-09-01", "2022-01-01", "2023-01-01")
    ]
    t = np.concatenate(segments)
    n = len(t)
    rng = np.random.default_rng(62103)
    close = np.exp(1 + np.cumsum(rng.normal(0, 0.001, n)))

    def write(path, values):
        pd.DataFrame({"open_time": t, "open": values, "high": values * 1.001,
                      "low": values * 0.999, "close": values, "quote_volume": np.full(n, 1e6),
                      "taker_buy_quote": np.full(n, 5e5)}).to_csv(path, index=False, compression="gzip")

    btc = out / "BTCUSDT.csv.gz"
    write(btc, close)
    hashes = {}
    for shard in range(8):
        directory = out / "market" / str(shard)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"X{shard:02d}USDT.csv.gz"
        write(path, close)
        hashes[path.name[:-7]] = digest(path)
    context_path = out / "synthetic-context.json"
    context_path.write_text(json.dumps({"baseline_sha256": "a" * 64, "btc_sha256": digest(btc),
                                        "expected_market_sha256": hashes}))

    def fake_mark(_symbol, times, _start, _end, _cache, _evidence):
        values = close[:len(times)]
        return (times.copy(), values.copy(), values * 1.0005, values * 0.9995, values.copy()), {
            pd.Timestamp(value, unit="ms", tz="UTC").strftime("%Y-%m") for value in times
        }

    for shard in range(8):
        symbol = f"X{shard:02d}USDT"
        check = out / f"check-{shard}.json"
        check.write_text(json.dumps({"status": "VERIFIED", "shards": [shard], "baseline_sha256": "a" * 64,
                                     "files": [{"symbol": symbol, "sha256": hashes[symbol]}]}))
        scan(out / "market" / str(shard), btc, out / "parts" / str(shard), out / "minute-cache",
             out / "mark-cache", "DEV", check, context_path=context_path, mark_loader=fake_mark)
    select(out / "parts", out / "selected", context_path)
    cells = pd.read_csv(out / "selected/development_policy_cells.csv")
    assert len(cells) == 96
    selection = json.loads((out / "selected/selection.json").read_text())
    assert selection["mark_coverage"]["passed"] and selection["status"] == "NO_DEVELOPMENT_POLICY_SURVIVOR"
    report = {"synthetic_only": True, "scans": 8, "all96_cells_preserved": True,
              "market_profitability_claim": False}
    (out / "smoke.json").write_text(json.dumps(report, indent=2))
    print("V21_SYNTHETIC_PIPELINE_PASS", json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("scan")
    for name in ("data", "btc", "out", "minute-cache", "mark-cache", "source-check"):
        command.add_argument("--" + name, type=Path, required=True)
    command.add_argument("--stage", choices=("DEV", "GATE"), required=True)
    command.add_argument("--selection", type=Path)
    command = sub.add_parser("select")
    command.add_argument("--parts", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command = sub.add_parser("accounts")
    for name in ("data", "parts", "out", "selection"):
        command.add_argument("--" + name, type=Path, required=True)
    command = sub.add_parser("smoke")
    command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "scan":
        scan(args.data, args.btc, args.out, args.minute_cache, args.mark_cache, args.stage,
             args.source_check, args.selection)
    elif args.command == "select":
        select(args.parts, args.out)
    elif args.command == "accounts":
        accounts(args.data, args.parts, args.out, args.selection)
    else:
        smoke(args.out)


if __name__ == "__main__":
    main()
