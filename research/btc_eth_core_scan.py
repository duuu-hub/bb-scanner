from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DATA_ROOT = Path("market_data_store/bitget/15m")
OUT_ROOT = Path("research_output/btc_eth_core")
SYMBOLS = ("BTCUSDT", "ETHUSDT")

SPLITS = (
    ("DISCOVERY", None, pd.Timestamp("2024-01-01", tz="UTC")),
    ("VALIDATION", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-07-01", tz="UTC")),
    ("HOLDOUT", pd.Timestamp("2025-07-01", tz="UTC"), None),
)
COSTS = (0.0, 0.12, 0.25, 0.50)


@dataclass(frozen=True)
class Candidate:
    name: str
    timeframe: str
    stop_atr: float
    target_atr: float
    max_bars: int


CANDIDATES = (
    Candidate("DONCHIAN_TREND_1H", "1h", 2.0, 4.0, 48),
    Candidate("DONCHIAN_TREND_RVOL_1H", "1h", 2.0, 4.0, 48),
    Candidate("EMA20_PULLBACK_1H", "1h", 1.5, 3.0, 24),
    Candidate("BB_COMPRESSION_BREAKOUT_1H", "1h", 2.0, 4.0, 48),
    Candidate("BB_REENTRY_MEANREV_1H", "1h", 1.5, 1.5, 12),
    Candidate("MOMENTUM_RVOL_1H", "1h", 2.0, 3.0, 24),
    Candidate("DONCHIAN_TREND_4H", "4h", 2.0, 4.0, 12),
    Candidate("EMA20_PULLBACK_4H", "4h", 1.5, 3.0, 12),
)


def load_15m(symbol: str) -> pd.DataFrame:
    files = sorted((DATA_ROOT / symbol).glob("*.csv"))
    if not files:
        raise RuntimeError(f"No files for {symbol}")
    frames = []
    for path in files:
        df = pd.read_csv(path)
        frames.append(df)
    x = pd.concat(frames, ignore_index=True)
    x["datetime_utc"] = pd.to_datetime(x["datetime_utc"], utc=True)
    x = x.drop_duplicates("timestamp_ms").sort_values("datetime_utc")
    for col in ("open", "high", "low", "close", "base_volume", "quote_volume"):
        x[col] = pd.to_numeric(x[col], errors="coerce")
    x = x.dropna(subset=["open", "high", "low", "close"])
    return x.reset_index(drop=True)


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    rule = {"1h": "1h", "4h": "4h"}[tf]
    x = df.set_index("datetime_utc")
    out = x.resample(rule, label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        base_volume=("base_volume", "sum"),
        quote_volume=("quote_volume", "sum"),
    ).dropna(subset=["open", "high", "low", "close"]).reset_index()
    return out


def add_features(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    x = df.copy()
    c = x["close"]
    h = x["high"]
    l = x["low"]

    x["ema20"] = c.ewm(span=20, adjust=False).mean()
    x["ema50"] = c.ewm(span=50, adjust=False).mean()
    x["ema200"] = c.ewm(span=200, adjust=False).mean()

    prev_c = c.shift(1)
    tr = pd.concat(
        [(h - l).abs(), (h - prev_c).abs(), (l - prev_c).abs()],
        axis=1,
    ).max(axis=1)
    x["atr14"] = tr.rolling(14, min_periods=14).mean()

    basis = c.rolling(20, min_periods=20).mean()
    std = c.rolling(20, min_periods=20).std(ddof=0)
    x["bb_basis"] = basis
    x["bb_upper"] = basis + 2.0 * std
    x["bb_lower"] = basis - 2.0 * std
    x["bb_width_pct"] = (x["bb_upper"] - x["bb_lower"]) / basis * 100.0
    x["bb_width_med120"] = x["bb_width_pct"].shift(1).rolling(120, min_periods=60).median()

    x["donchian20_high"] = h.shift(1).rolling(20, min_periods=20).max()
    x["donchian20_low"] = l.shift(1).rolling(20, min_periods=20).min()

    x["rvol20"] = x["quote_volume"] / x["quote_volume"].shift(1).rolling(20, min_periods=10).median()

    bars4 = 4 if tf == "1h" else 1
    bars24 = 24 if tf == "1h" else 6
    x["ret4_pct"] = (c / c.shift(bars4) - 1.0) * 100.0
    x["ret24_pct"] = (c / c.shift(bars24) - 1.0) * 100.0

    # Causal momentum surprise: current 4h return standardized by the prior ~30 days.
    lookback = 24 * 30 if tf == "1h" else 6 * 30
    mean_prior = x["ret4_pct"].shift(1).rolling(lookback, min_periods=max(60, lookback // 3)).mean()
    std_prior = x["ret4_pct"].shift(1).rolling(lookback, min_periods=max(60, lookback // 3)).std(ddof=0)
    x["ret4_z"] = (x["ret4_pct"] - mean_prior) / std_prior.replace(0, np.nan)

    x["trend_strength_atr"] = (x["ema50"] - x["ema200"]).abs() / x["atr14"].replace(0, np.nan)
    return x


def signals(df: pd.DataFrame, name: str) -> pd.Series:
    c = df["close"]
    prev_c = c.shift(1)

    bull = df["ema50"] > df["ema200"]
    bear = df["ema50"] < df["ema200"]

    if name == "DONCHIAN_TREND_1H" or name == "DONCHIAN_TREND_4H":
        long_cond = bull & (c > df["donchian20_high"])
        short_cond = bear & (c < df["donchian20_low"])
    elif name == "DONCHIAN_TREND_RVOL_1H":
        long_cond = bull & (c > df["donchian20_high"]) & (df["rvol20"] >= 1.0)
        short_cond = bear & (c < df["donchian20_low"]) & (df["rvol20"] >= 1.0)
    elif name == "EMA20_PULLBACK_1H" or name == "EMA20_PULLBACK_4H":
        long_cond = (
            bull
            & (prev_c <= df["ema20"].shift(1))
            & (c > df["ema20"])
            & (df["ret24_pct"] > 0)
        )
        short_cond = (
            bear
            & (prev_c >= df["ema20"].shift(1))
            & (c < df["ema20"])
            & (df["ret24_pct"] < 0)
        )
    elif name == "BB_COMPRESSION_BREAKOUT_1H":
        compressed = df["bb_width_pct"].shift(1) <= 0.70 * df["bb_width_med120"]
        long_cond = compressed & bull & (c > df["donchian20_high"]) & (df["rvol20"] >= 1.2)
        short_cond = compressed & bear & (c < df["donchian20_low"]) & (df["rvol20"] >= 1.2)
    elif name == "BB_REENTRY_MEANREV_1H":
        range_like = df["trend_strength_atr"] < 2.0
        long_cond = (
            range_like
            & (prev_c < df["bb_lower"].shift(1))
            & (c >= df["bb_lower"])
        )
        short_cond = (
            range_like
            & (prev_c > df["bb_upper"].shift(1))
            & (c <= df["bb_upper"])
        )
    elif name == "MOMENTUM_RVOL_1H":
        long_cond = bull & (df["ret4_z"] >= 2.0) & (df["rvol20"] >= 1.5)
        short_cond = bear & (df["ret4_z"] <= -2.0) & (df["rvol20"] >= 1.5)
    else:
        raise KeyError(name)

    s = pd.Series(0, index=df.index, dtype=np.int8)
    s.loc[long_cond.fillna(False)] = 1
    s.loc[short_cond.fillna(False)] = -1
    return s


def backtest_symbol(
    symbol: str,
    df: pd.DataFrame,
    candidate: Candidate,
) -> list[dict]:
    sig = signals(df, candidate.name)
    trades = []
    i = 0
    n = len(df)

    while i < n - 2:
        direction = int(sig.iloc[i])
        atr = df["atr14"].iloc[i]
        if direction == 0 or not np.isfinite(atr) or atr <= 0:
            i += 1
            continue

        entry_i = i + 1
        entry = float(df["open"].iloc[entry_i])
        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue

        if direction > 0:
            stop = entry - candidate.stop_atr * float(atr)
            target = entry + candidate.target_atr * float(atr)
        else:
            stop = entry + candidate.stop_atr * float(atr)
            target = entry - candidate.target_atr * float(atr)

        last_i = min(n - 1, entry_i + candidate.max_bars - 1)
        exit_i = last_i
        exit_price = float(df["close"].iloc[last_i])
        reason = "TIME"

        for j in range(entry_i, last_i + 1):
            hi = float(df["high"].iloc[j])
            lo = float(df["low"].iloc[j])
            if direction > 0:
                stop_hit = lo <= stop
                target_hit = hi >= target
            else:
                stop_hit = hi >= stop
                target_hit = lo <= target

            # Conservative ambiguity rule: if both touched in one bar, count stop first.
            if stop_hit:
                exit_i = j
                exit_price = stop
                reason = "SL"
                break
            if target_hit:
                exit_i = j
                exit_price = target
                reason = "TP"
                break

        gross_pct = direction * (exit_price / entry - 1.0) * 100.0
        trades.append(
            {
                "symbol": symbol,
                "strategy": candidate.name,
                "timeframe": candidate.timeframe,
                "signal_ts": df["datetime_utc"].iloc[i].isoformat(),
                "entry_ts": df["datetime_utc"].iloc[entry_i].isoformat(),
                "exit_ts": df["datetime_utc"].iloc[exit_i].isoformat(),
                "direction": "LONG" if direction > 0 else "SHORT",
                "entry": entry,
                "exit": exit_price,
                "gross_pct": gross_pct,
                "exit_reason": reason,
                "signal_rvol20": float(df["rvol20"].iloc[i]) if np.isfinite(df["rvol20"].iloc[i]) else None,
                "signal_ret4_z": float(df["ret4_z"].iloc[i]) if np.isfinite(df["ret4_z"].iloc[i]) else None,
            }
        )

        # One open trade per strategy/symbol.
        i = exit_i + 1

    return trades


def pf(values: np.ndarray) -> float:
    gains = values[values > 0].sum()
    losses = -values[values < 0].sum()
    if losses <= 0:
        return math.inf if gains > 0 else math.nan
    return float(gains / losses)


def max_drawdown(values: np.ndarray) -> float:
    if len(values) == 0:
        return math.nan
    equity = np.cumprod(1.0 + values / 100.0)
    peaks = np.maximum.accumulate(np.r_[1.0, equity])
    curve = np.r_[1.0, equity]
    dd = curve / peaks - 1.0
    return float(dd.min() * 100.0)


def bootstrap_mean_ci(values: np.ndarray, seed: int = 7, reps: int = 3000) -> tuple[float, float]:
    if len(values) < 2:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    means = np.empty(reps)
    for k in range(reps):
        sample = rng.choice(values, size=len(values), replace=True)
        means[k] = sample.mean()
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def split_mask(ts: pd.Series, split_name: str) -> pd.Series:
    for name, start, end in SPLITS:
        if name != split_name:
            continue
        mask = pd.Series(True, index=ts.index)
        if start is not None:
            mask &= ts >= start
        if end is not None:
            mask &= ts < end
        return mask
    raise KeyError(split_name)


def summarize(trades: pd.DataFrame, strategy: str, scope: str, split: str, cost: float) -> dict:
    x = trades.loc[trades["strategy"] == strategy].copy()
    if scope != "COMBINED":
        x = x.loc[x["symbol"] == scope]
    ts = pd.to_datetime(x["entry_ts"], utc=True)
    x = x.loc[split_mask(ts, split).values]
    x = x.sort_values(["entry_ts", "symbol"])
    vals = x["gross_pct"].to_numpy(dtype=float) - cost
    lo, hi = bootstrap_mean_ci(vals)
    return {
        "strategy": strategy,
        "scope": scope,
        "split": split,
        "roundtrip_cost_pct": cost,
        "n": int(len(vals)),
        "win_rate_pct": float((vals > 0).mean() * 100.0) if len(vals) else math.nan,
        "avg_pct": float(vals.mean()) if len(vals) else math.nan,
        "median_pct": float(np.median(vals)) if len(vals) else math.nan,
        "pf": pf(vals) if len(vals) else math.nan,
        "mdd_pct": max_drawdown(vals),
        "sum_pct": float(vals.sum()) if len(vals) else math.nan,
        "mean_bootstrap_95_lo": lo,
        "mean_bootstrap_95_hi": hi,
    }


def yearly_summary(trades: pd.DataFrame) -> pd.DataFrame:
    x = trades.copy()
    x["year"] = pd.to_datetime(x["entry_ts"], utc=True).dt.year
    rows = []
    for (strategy, symbol, year), g in x.groupby(["strategy", "symbol", "year"]):
        vals = g["gross_pct"].to_numpy(dtype=float) - 0.25
        rows.append(
            {
                "strategy": strategy,
                "symbol": symbol,
                "year": int(year),
                "n": len(vals),
                "avg_pct_cost025": vals.mean() if len(vals) else math.nan,
                "pf_cost025": pf(vals) if len(vals) else math.nan,
                "sum_pct_cost025": vals.sum() if len(vals) else math.nan,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    frames: dict[tuple[str, str], pd.DataFrame] = {}
    data_meta = []
    for symbol in SYMBOLS:
        raw = load_15m(symbol)
        data_meta.append(
            {
                "symbol": symbol,
                "rows_15m": len(raw),
                "start": raw["datetime_utc"].min().isoformat(),
                "end": raw["datetime_utc"].max().isoformat(),
            }
        )
        for tf in ("1h", "4h"):
            frames[(symbol, tf)] = add_features(resample(raw, tf), tf)

    all_trades: list[dict] = []
    for candidate in CANDIDATES:
        for symbol in SYMBOLS:
            df = frames[(symbol, candidate.timeframe)]
            all_trades.extend(backtest_symbol(symbol, df, candidate))

    trades = pd.DataFrame(all_trades)
    if trades.empty:
        raise RuntimeError("No trades generated")

    trades.to_csv(OUT_ROOT / "trades.csv", index=False)

    summary_rows = []
    for candidate in CANDIDATES:
        for scope in (*SYMBOLS, "COMBINED"):
            for split, _, _ in SPLITS:
                for cost in COSTS:
                    summary_rows.append(
                        summarize(trades, candidate.name, scope, split, cost)
                    )
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(OUT_ROOT / "summary.csv", index=False)

    yearly = yearly_summary(trades)
    yearly.to_csv(OUT_ROOT / "yearly_cost025.csv", index=False)

    # Volume incremental-value check for the directly paired Donchian rules.
    paired = summary[
        summary["strategy"].isin(["DONCHIAN_TREND_1H", "DONCHIAN_TREND_RVOL_1H"])
        & (summary["scope"] == "COMBINED")
        & (summary["roundtrip_cost_pct"] == 0.25)
    ].copy()
    paired.to_csv(OUT_ROOT / "volume_incremental.csv", index=False)

    # Rank only by held-out evidence, but retain all results. This is a research triage,
    # not a parameter optimization: all candidate definitions were fixed before this run.
    holdout = summary[
        (summary["scope"] == "COMBINED")
        & (summary["split"] == "HOLDOUT")
        & (summary["roundtrip_cost_pct"] == 0.25)
    ].copy()
    holdout["score"] = (
        holdout["avg_pct"].clip(-5, 5)
        + 0.25 * (holdout["pf"].replace([np.inf], 5).clip(0, 5) - 1.0)
    )
    holdout = holdout.sort_values(["score", "n"], ascending=[False, False])
    holdout.to_csv(OUT_ROOT / "holdout_triage_cost025.csv", index=False)

    meta = {
        "schema_version": 1,
        "symbols": list(SYMBOLS),
        "data": data_meta,
        "splits": [
            {
                "name": name,
                "start": None if start is None else start.isoformat(),
                "end_exclusive": None if end is None else end.isoformat(),
            }
            for name, start, end in SPLITS
        ],
        "costs_roundtrip_pct": list(COSTS),
        "execution": {
            "signal": "completed bar close",
            "entry": "next bar open",
            "same_bar_tp_sl": "conservative stop first",
            "overlap": "one open trade per strategy-symbol",
        },
        "candidate_count": len(CANDIDATES),
        "candidate_names": [x.name for x in CANDIDATES],
        "note": "Broad fixed-family scan for research triage; no threshold retuning from results.",
    }
    (OUT_ROOT / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print("=== DATA ===")
    print(json.dumps(data_meta, indent=2))
    print("\n=== HOLDOUT TRIAGE @ 0.25% ROUNDTRIP COST ===")
    cols = ["strategy", "n", "win_rate_pct", "avg_pct", "pf", "mdd_pct", "mean_bootstrap_95_lo", "mean_bootstrap_95_hi"]
    print(holdout[cols].to_string(index=False))

    print("\n=== BTC / ETH HOLDOUT @ 0.25% ===")
    detail = summary[
        (summary["scope"].isin(SYMBOLS))
        & (summary["split"] == "HOLDOUT")
        & (summary["roundtrip_cost_pct"] == 0.25)
    ]
    print(detail[["strategy", "scope", "n", "avg_pct", "pf", "mdd_pct"]].to_string(index=False))

    print("\n=== VALIDATION COMBINED @ 0.25% ===")
    val = summary[
        (summary["scope"] == "COMBINED")
        & (summary["split"] == "VALIDATION")
        & (summary["roundtrip_cost_pct"] == 0.25)
    ]
    print(val[["strategy", "n", "avg_pct", "pf", "mdd_pct", "mean_bootstrap_95_lo", "mean_bootstrap_95_hi"]].to_string(index=False))


    print("\n=== DIRECTION DIAGNOSTIC @ 0.25% ===")
    direction_rows = []
    tx = trades.copy()
    tx["entry_dt"] = pd.to_datetime(tx["entry_ts"], utc=True)
    for strategy in [x.name for x in CANDIDATES]:
        for split_name, start, end in SPLITS:
            base = tx.loc[tx["strategy"] == strategy].copy()
            if start is not None:
                base = base.loc[base["entry_dt"] >= start]
            if end is not None:
                base = base.loc[base["entry_dt"] < end]
            for symbol_scope in (*SYMBOLS, "COMBINED"):
                scope_df = base if symbol_scope == "COMBINED" else base.loc[base["symbol"] == symbol_scope]
                for direction in ("LONG", "SHORT"):
                    g = scope_df.loc[scope_df["direction"] == direction]
                    vals = g["gross_pct"].to_numpy(dtype=float) - 0.25
                    direction_rows.append({
                        "strategy": strategy,
                        "split": split_name,
                        "scope": symbol_scope,
                        "direction": direction,
                        "n": len(vals),
                        "avg_pct": vals.mean() if len(vals) else math.nan,
                        "pf": pf(vals) if len(vals) else math.nan,
                        "mdd_pct": max_drawdown(vals),
                    })
    direction_df = pd.DataFrame(direction_rows)
    direction_df.to_csv(OUT_ROOT / "direction_cost025.csv", index=False)
    diag = direction_df[
        (direction_df["scope"] == "COMBINED")
        & (direction_df["split"].isin(["VALIDATION", "HOLDOUT"]))
    ]
    print(diag.to_string(index=False))

    print("\n=== YEARLY COMBINED DIRECTION @ 0.25% FOR NEAR-NEUTRAL FAMILIES ===")
    yd = tx.loc[tx["strategy"].isin(["MOMENTUM_RVOL_1H", "DONCHIAN_TREND_4H"])].copy()
    yd["year"] = yd["entry_dt"].dt.year
    yrows = []
    for (strategy, year, direction), g in yd.groupby(["strategy", "year", "direction"]):
        vals = g["gross_pct"].to_numpy(dtype=float) - 0.25
        yrows.append({
            "strategy": strategy,
            "year": int(year),
            "direction": direction,
            "n": len(vals),
            "avg_pct": vals.mean(),
            "pf": pf(vals),
            "sum_pct": vals.sum(),
        })
    ydiag = pd.DataFrame(yrows).sort_values(["strategy", "year", "direction"])
    ydiag.to_csv(OUT_ROOT / "yearly_direction_cost025.csv", index=False)
    print(ydiag.to_string(index=False))
    print("\n=== DONCHIAN VOLUME INCREMENTAL CHECK @ 0.25% ===")
    print(paired[["strategy", "split", "n", "avg_pct", "pf", "mdd_pct"]].to_string(index=False))


if __name__ == "__main__":
    main()
