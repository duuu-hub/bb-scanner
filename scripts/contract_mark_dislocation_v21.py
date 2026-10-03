"""Preregistered V21 contract-price versus mark-price dislocation reversal."""
from __future__ import annotations

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

BAR = base.BAR
HOLDS = (16, 32)
EXITS = ("MARK", "R15", "R25")

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
