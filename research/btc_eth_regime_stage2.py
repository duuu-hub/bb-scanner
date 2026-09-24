from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

DATA_ROOT = Path("market_data_store/bitget/15m")
OUT_ROOT = Path("research_output/btc_eth_regime_stage2")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
TIMEFRAMES = ("30m", "1h", "4h", "12h", "1d")
FWD_HOURS = (24, 72, 168)
ROUNDTRIP_COST_PCT = 0.25
ONE_WAY_COST_PCT = ROUNDTRIP_COST_PCT / 2.0

SPLITS = (
    ("DISCOVERY", None, pd.Timestamp("2024-01-01", tz="UTC")),
    ("VALIDATION", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-07-01", tz="UTC")),
    ("HOLDOUT", pd.Timestamp("2025-07-01", tz="UTC"), None),
)

TF_RULE = {
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "12h": "12h",
    "1d": "1D",
}
TF_HOURS = {"30m": 0.5, "1h": 1.0, "4h": 4.0, "12h": 12.0, "1d": 24.0}


def load_15m(symbol: str) -> pd.DataFrame:
    files = sorted((DATA_ROOT / symbol).glob("*.csv"))
    if not files:
        raise RuntimeError(f"No stored 15m history for {symbol}")
    frames = [pd.read_csv(path) for path in files]
    x = pd.concat(frames, ignore_index=True)
    x["datetime_utc"] = pd.to_datetime(x["datetime_utc"], utc=True)
    for col in ("open", "high", "low", "close", "base_volume", "quote_volume"):
        x[col] = pd.to_numeric(x[col], errors="coerce")
    x = (
        x.drop_duplicates("timestamp_ms")
        .dropna(subset=["open", "high", "low", "close"])
        .sort_values("datetime_utc")
        .reset_index(drop=True)
    )
    return x


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    x = df.set_index("datetime_utc")
    out = (
        x.resample(TF_RULE[tf], label="left", closed="left")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            base_volume=("base_volume", "sum"),
            quote_volume=("quote_volume", "sum"),
        )
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    return out


def add_breakout_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    c = x["close"]
    x["ema50"] = c.ewm(span=50, adjust=False).mean()
    x["ema200"] = c.ewm(span=200, adjust=False).mean()
    x["donchian20_high"] = x["high"].shift(1).rolling(20, min_periods=20).max()
    x["donchian20_low"] = x["low"].shift(1).rolling(20, min_periods=20).min()
    raw_long = c > x["donchian20_high"]
    raw_short = c < x["donchian20_low"]
    x["raw_dir"] = np.select([raw_long, raw_short], [1, -1], default=0).astype(np.int8)
    x["ema_dir"] = np.select(
        [raw_long & (x["ema50"] > x["ema200"]), raw_short & (x["ema50"] < x["ema200"])],
        [1, -1],
        default=0,
    ).astype(np.int8)
    return x


def decluster_direction_events(df: pd.DataFrame, dir_col: str, min_gap_hours: float = 24.0) -> list[int]:
    out: list[int] = []
    last_by_dir: dict[int, pd.Timestamp] = {}
    prev_dir = 0
    for i, row in df.iterrows():
        direction = int(row[dir_col])
        if direction == 0:
            prev_dir = 0
            continue
        # Require a fresh breakout edge, not every bar that remains outside the channel.
        fresh = direction != prev_dir
        prev_dir = direction
        if not fresh:
            continue
        ts = row["datetime_utc"]
        last = last_by_dir.get(direction)
        if last is not None and (ts - last).total_seconds() < min_gap_hours * 3600:
            continue
        out.append(i)
        last_by_dir[direction] = ts
    return out


def build_market_context(one_hour: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict]:
    features = {}
    for symbol, raw in one_hour.items():
        x = raw[["datetime_utc", "close"]].copy()
        c = x["close"]
        r1 = c.pct_change()
        bars7 = 24 * 7
        bars30 = 24 * 30
        bars90 = 24 * 90
        x["ret7"] = c / c.shift(bars7) - 1.0
        x["ret30"] = c / c.shift(bars30) - 1.0
        rv30 = r1.rolling(bars30, min_periods=bars30 // 2).std(ddof=0) * math.sqrt(24 * 365)
        x["rv30_ann"] = rv30
        # 30-day return normalized by the realized volatility accumulated over 30 days.
        horizon_vol = r1.rolling(bars30, min_periods=bars30 // 2).std(ddof=0) * math.sqrt(bars30)
        x["trend30_norm"] = x["ret30"] / horizon_vol.replace(0, np.nan)
        path = c.diff().abs().rolling(bars30, min_periods=bars30 // 2).sum()
        x["er30"] = (c - c.shift(bars30)).abs() / path.replace(0, np.nan)
        x["dd90"] = c / c.shift(1).rolling(bars90, min_periods=bars30).max() - 1.0
        x = x.rename(
            columns={
                "close": f"{symbol}_close",
                "ret7": f"{symbol}_ret7",
                "ret30": f"{symbol}_ret30",
                "rv30_ann": f"{symbol}_rv30_ann",
                "trend30_norm": f"{symbol}_trend30_norm",
                "er30": f"{symbol}_er30",
                "dd90": f"{symbol}_dd90",
            }
        )
        features[symbol] = x

    ctx = features["BTCUSDT"].merge(features["ETHUSDT"], on="datetime_utc", how="inner")
    btc_t = ctx["BTCUSDT_trend30_norm"]
    eth_t = ctx["ETHUSDT_trend30_norm"]
    ctx["market_trend30_norm"] = (btc_t + eth_t) / 2.0
    ctx["market_abs_trend30_norm"] = ctx["market_trend30_norm"].abs()
    ctx["market_er30"] = (ctx["BTCUSDT_er30"] + ctx["ETHUSDT_er30"]) / 2.0
    ctx["market_rv30_ann"] = (ctx["BTCUSDT_rv30_ann"] + ctx["ETHUSDT_rv30_ann"]) / 2.0
    ctx["market_dd90"] = (ctx["BTCUSDT_dd90"] + ctx["ETHUSDT_dd90"]) / 2.0
    ctx["btc_eth_trend_agree"] = (
        np.sign(btc_t).astype(float) == np.sign(eth_t).astype(float)
    )
    ctx.loc[(btc_t == 0) | (eth_t == 0), "btc_eth_trend_agree"] = False

    discovery = ctx.loc[ctx["datetime_utc"] < pd.Timestamp("2024-01-01", tz="UTC")]
    frozen = {
        "er30_median_discovery": float(discovery["market_er30"].median()),
        "abs_trend30_median_discovery": float(discovery["market_abs_trend30_norm"].median()),
    }
    return ctx, frozen


def asof_context(ctx: pd.DataFrame, decision_ts: pd.Timestamp) -> pd.Series | None:
    # Context must be from a fully completed 1h bar strictly before the decision/entry time.
    times = ctx["datetime_utc"].to_numpy(dtype="datetime64[ns]")
    target = np.datetime64(decision_ts.tz_convert("UTC").tz_localize(None))
    idx = int(np.searchsorted(times, target, side="left")) - 1
    if idx < 0:
        return None
    return ctx.iloc[idx]


def forward_event_rows(
    symbol: str,
    tf: str,
    family: str,
    df: pd.DataFrame,
    dir_col: str,
    ctx: pd.DataFrame,
) -> list[dict]:
    rows: list[dict] = []
    event_indices = decluster_direction_events(df, dir_col, min_gap_hours=24.0)
    step_hours = TF_HOURS[tf]

    for i in event_indices:
        if i + 1 >= len(df):
            continue
        direction = int(df[dir_col].iloc[i])
        decision_i = i + 1
        decision_ts = df["datetime_utc"].iloc[decision_i]
        entry = float(df["open"].iloc[decision_i])
        if not np.isfinite(entry) or entry <= 0:
            continue

        context = asof_context(ctx, decision_ts)
        if context is None:
            continue

        base = {
            "symbol": symbol,
            "timeframe": tf,
            "family": family,
            "signal_bar_start_utc": df["datetime_utc"].iloc[i],
            "decision_ts_utc": decision_ts,
            "direction": direction,
            "entry": entry,
            "market_trend30_norm": context["market_trend30_norm"],
            "market_abs_trend30_norm": context["market_abs_trend30_norm"],
            "market_er30": context["market_er30"],
            "market_rv30_ann": context["market_rv30_ann"],
            "market_dd90": context["market_dd90"],
            "btc_eth_trend_agree": bool(context["btc_eth_trend_agree"]),
            "btc_trend30_norm": context["BTCUSDT_trend30_norm"],
            "eth_trend30_norm": context["ETHUSDT_trend30_norm"],
            "btc_ret30": context["BTCUSDT_ret30"],
            "eth_ret30": context["ETHUSDT_ret30"],
        }
        base["alignment_strength"] = direction * float(base["market_trend30_norm"])
        base["aligned_with_market"] = base["alignment_strength"] > 0

        for hours in FWD_HOURS:
            bars = int(round(hours / step_hours))
            if bars < 1:
                continue
            exit_i = decision_i + bars - 1
            if exit_i >= len(df):
                continue
            exit_price = float(df["close"].iloc[exit_i])
            gross = direction * (exit_price / entry - 1.0) * 100.0
            row = dict(base)
            row.update(
                {
                    "horizon_hours": hours,
                    "exit_ts_utc": df["datetime_utc"].iloc[exit_i],
                    "exit": exit_price,
                    "gross_pct": gross,
                    "net_pct_cost025": gross - ROUNDTRIP_COST_PCT,
                }
            )
            rows.append(row)
    return rows


def split_name(ts: pd.Timestamp) -> str:
    if ts < pd.Timestamp("2024-01-01", tz="UTC"):
        return "DISCOVERY"
    if ts < pd.Timestamp("2025-07-01", tz="UTC"):
        return "VALIDATION"
    return "HOLDOUT"


def profit_factor(values: np.ndarray) -> float:
    gains = values[values > 0].sum()
    losses = -values[values < 0].sum()
    if losses <= 0:
        return math.inf if gains > 0 else math.nan
    return float(gains / losses)


def bootstrap_mean_ci(values: np.ndarray, seed: int = 17, reps: int = 3000) -> tuple[float, float]:
    if len(values) < 2:
        return math.nan, math.nan
    rng = np.random.default_rng(seed)
    means = np.empty(reps)
    for i in range(reps):
        means[i] = rng.choice(values, size=len(values), replace=True).mean()
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def rule_mask(events: pd.DataFrame, rule: str, frozen: dict) -> pd.Series:
    align = events["btc_eth_trend_agree"] & events["aligned_with_market"]
    if rule == "BASE":
        return pd.Series(True, index=events.index)
    if rule == "ALIGN":
        return align
    if rule == "ALIGN_PERSIST":
        return align & (events["market_er30"] >= frozen["er30_median_discovery"])
    if rule == "ALIGN_PERSIST_STRONG":
        return (
            align
            & (events["market_er30"] >= frozen["er30_median_discovery"])
            & (
                events["market_abs_trend30_norm"]
                >= frozen["abs_trend30_median_discovery"]
            )
        )
    raise KeyError(rule)


def summarize_events(events: pd.DataFrame, frozen: dict) -> pd.DataFrame:
    rows = []
    for family in sorted(events["family"].unique()):
        for tf in TIMEFRAMES:
            for horizon in FWD_HOURS:
                base = events[
                    (events["family"] == family)
                    & (events["timeframe"] == tf)
                    & (events["horizon_hours"] == horizon)
                ].copy()
                if base.empty:
                    continue
                for split in ("DISCOVERY", "VALIDATION", "HOLDOUT"):
                    sx = base[base["split"] == split]
                    for rule in ("BASE", "ALIGN", "ALIGN_PERSIST", "ALIGN_PERSIST_STRONG"):
                        x = sx[rule_mask(sx, rule, frozen)]
                        vals = x["net_pct_cost025"].to_numpy(dtype=float)
                        lo, hi = bootstrap_mean_ci(vals)
                        rows.append(
                            {
                                "family": family,
                                "timeframe": tf,
                                "horizon_hours": horizon,
                                "split": split,
                                "rule": rule,
                                "n": len(vals),
                                "win_rate_pct": float((vals > 0).mean() * 100.0) if len(vals) else math.nan,
                                "avg_pct": float(vals.mean()) if len(vals) else math.nan,
                                "median_pct": float(np.median(vals)) if len(vals) else math.nan,
                                "pf": profit_factor(vals) if len(vals) else math.nan,
                                "bootstrap95_lo": lo,
                                "bootstrap95_hi": hi,
                            }
                        )
    return pd.DataFrame(rows)


def discovery_quintile_edges(series: pd.Series) -> list[float]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < 20:
        return []
    return [float(x) for x in clean.quantile([0.2, 0.4, 0.6, 0.8]).values]


def assign_frozen_bin(series: pd.Series, edges: list[float]) -> pd.Series:
    if len(edges) != 4:
        return pd.Series(np.nan, index=series.index)
    return pd.cut(
        series,
        bins=[-np.inf, *edges, np.inf],
        labels=["Q1", "Q2", "Q3", "Q4", "Q5"],
        include_lowest=True,
    )


def anatomy_tables(events: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    # Freeze bin edges using discovery events only. Inspect raw Donchian events pooled
    # across timeframes; bins are explanatory diagnostics, not trading thresholds.
    discovery = events[
        (events["split"] == "DISCOVERY")
        & (events["family"] == "DONCHIAN20_RAW")
        & (events["horizon_hours"] == 72)
    ].drop_duplicates(["symbol", "timeframe", "decision_ts_utc"])

    features = ("alignment_strength", "market_er30", "market_abs_trend30_norm", "market_rv30_ann", "market_dd90")
    edges = {feature: discovery_quintile_edges(discovery[feature]) for feature in features}

    work = events[
        (events["family"] == "DONCHIAN20_RAW")
        & (events["horizon_hours"] == 72)
    ].copy()
    rows = []
    for feature in features:
        work["bin"] = assign_frozen_bin(work[feature], edges[feature])
        for split in ("DISCOVERY", "VALIDATION", "HOLDOUT"):
            for tf in TIMEFRAMES:
                sx = work[(work["split"] == split) & (work["timeframe"] == tf)]
                for bucket in ("Q1", "Q2", "Q3", "Q4", "Q5"):
                    x = sx[sx["bin"].astype(str) == bucket]
                    vals = x["net_pct_cost025"].to_numpy(dtype=float)
                    rows.append(
                        {
                            "feature": feature,
                            "split": split,
                            "timeframe": tf,
                            "bin": bucket,
                            "n": len(vals),
                            "avg_pct": vals.mean() if len(vals) else math.nan,
                            "pf": profit_factor(vals) if len(vals) else math.nan,
                        }
                    )
    return pd.DataFrame(rows), edges


def max_drawdown_from_returns(returns_pct: np.ndarray) -> float:
    if len(returns_pct) == 0:
        return math.nan
    curve = np.cumprod(1.0 + returns_pct / 100.0)
    full = np.r_[1.0, curve]
    peaks = np.maximum.accumulate(full)
    return float(((full / peaks) - 1.0).min() * 100.0)


def daily_regime_test(raw15: dict[str, pd.DataFrame]) -> pd.DataFrame:
    daily = {}
    for symbol in SYMBOLS:
        d = resample(raw15[symbol], "1d")[["datetime_utc", "close"]].copy()
        c = d["close"]
        d["ret1_next"] = c.pct_change().shift(-1)
        d["ret30"] = c / c.shift(30) - 1.0
        path = c.diff().abs().rolling(30, min_periods=20).sum()
        d["er30"] = (c - c.shift(30)).abs() / path.replace(0, np.nan)
        d = d.rename(
            columns={
                "close": f"{symbol}_close",
                "ret1_next": f"{symbol}_ret1_next",
                "ret30": f"{symbol}_ret30",
                "er30": f"{symbol}_er30",
            }
        )
        daily[symbol] = d

    x = daily["BTCUSDT"].merge(daily["ETHUSDT"], on="datetime_utc", how="inner")
    x["btc_sign"] = np.sign(x["BTCUSDT_ret30"])
    x["eth_sign"] = np.sign(x["ETHUSDT_ret30"])
    x["agree"] = (x["btc_sign"] == x["eth_sign"]) & (x["btc_sign"] != 0)
    x["market_er30"] = (x["BTCUSDT_er30"] + x["ETHUSDT_er30"]) / 2.0

    discovery = x[x["datetime_utc"] < pd.Timestamp("2024-01-01", tz="UTC")]
    er_med = float(discovery["market_er30"].median())

    strategies = {}
    # Positions decided at today's close, applied to next close-to-close return.
    strategies["OWN_TSMOM30"] = {
        "BTCUSDT": np.sign(x["BTCUSDT_ret30"]).fillna(0),
        "ETHUSDT": np.sign(x["ETHUSDT_ret30"]).fillna(0),
    }
    common = x["btc_sign"].where(x["agree"], 0).fillna(0)
    strategies["CROSS_AGREE30"] = {"BTCUSDT": common, "ETHUSDT": common}
    common_persist = common.where(x["market_er30"] >= er_med, 0)
    strategies["CROSS_AGREE30_PERSIST"] = {
        "BTCUSDT": common_persist,
        "ETHUSDT": common_persist,
    }

    result_rows = []
    for strategy, pos in strategies.items():
        portfolio_net = pd.Series(0.0, index=x.index)
        portfolio_gross = pd.Series(0.0, index=x.index)
        turnover = pd.Series(0.0, index=x.index)
        for symbol in SYMBOLS:
            p = pos[symbol].astype(float)
            fwd = x[f"{symbol}_ret1_next"].fillna(0.0) * 100.0
            gross = p * fwd
            # 0.125% per unit position change: flat->long 0.125, long->flat 0.125,
            # long->short 0.25, giving a 0.25% round trip.
            tc = p.diff().abs().fillna(p.abs()) * ONE_WAY_COST_PCT
            portfolio_gross += gross / len(SYMBOLS)
            portfolio_net += (gross - tc) / len(SYMBOLS)
            turnover += p.diff().abs().fillna(p.abs()) / len(SYMBOLS)

        for split, start, end in SPLITS:
            mask = pd.Series(True, index=x.index)
            if start is not None:
                mask &= x["datetime_utc"] >= start
            if end is not None:
                mask &= x["datetime_utc"] < end
            vals = portfolio_net[mask].dropna().to_numpy(dtype=float)
            gross_vals = portfolio_gross[mask].dropna().to_numpy(dtype=float)
            if len(vals) == 0:
                continue
            total = float((np.prod(1.0 + vals / 100.0) - 1.0) * 100.0)
            years = len(vals) / 365.25
            cagr = float((np.prod(1.0 + vals / 100.0) ** (1.0 / years) - 1.0) * 100.0) if years > 0 else math.nan
            std = np.std(vals, ddof=0)
            sharpe = float(np.mean(vals) / std * math.sqrt(365.25)) if std > 0 else math.nan
            result_rows.append(
                {
                    "strategy": strategy,
                    "split": split,
                    "days": len(vals),
                    "total_return_pct": total,
                    "cagr_pct": cagr,
                    "sharpe": sharpe,
                    "mdd_pct": max_drawdown_from_returns(vals),
                    "avg_daily_net_pct": float(np.mean(vals)),
                    "avg_daily_gross_pct": float(np.mean(gross_vals)),
                    "avg_turnover_units": float(turnover[mask].mean()),
                    "active_day_pct": float((np.abs(gross_vals) > 0).mean() * 100.0),
                    "frozen_er30_median": er_med,
                }
            )
    return pd.DataFrame(result_rows)


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    raw15 = {symbol: load_15m(symbol) for symbol in SYMBOLS}
    data_meta = {
        symbol: {
            "rows": len(raw15[symbol]),
            "start": raw15[symbol]["datetime_utc"].min().isoformat(),
            "end": raw15[symbol]["datetime_utc"].max().isoformat(),
        }
        for symbol in SYMBOLS
    }

    one_hour = {symbol: resample(raw15[symbol], "1h") for symbol in SYMBOLS}
    ctx, frozen = build_market_context(one_hour)

    event_rows = []
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            frame = add_breakout_features(resample(raw15[symbol], tf))
            event_rows.extend(
                forward_event_rows(symbol, tf, "DONCHIAN20_RAW", frame, "raw_dir", ctx)
            )
            event_rows.extend(
                forward_event_rows(symbol, tf, "DONCHIAN20_EMA", frame, "ema_dir", ctx)
            )

    events = pd.DataFrame(event_rows)
    if events.empty:
        raise RuntimeError("No breakout events generated")
    events["decision_ts_utc"] = pd.to_datetime(events["decision_ts_utc"], utc=True)
    events["split"] = events["decision_ts_utc"].map(split_name)
    events.to_csv(OUT_ROOT / "events.csv", index=False)

    summary = summarize_events(events, frozen)
    summary.to_csv(OUT_ROOT / "event_rule_summary.csv", index=False)

    anatomy, bin_edges = anatomy_tables(events)
    anatomy.to_csv(OUT_ROOT / "anatomy_quintiles.csv", index=False)

    daily = daily_regime_test(raw15)
    daily.to_csv(OUT_ROOT / "daily_regime_summary.csv", index=False)

    meta = {
        "schema_version": 1,
        "purpose": "Stage-2 BTC/ETH regime anatomy and frozen, concept-driven filters",
        "data": data_meta,
        "timeframes": list(TIMEFRAMES),
        "forward_horizons_hours": list(FWD_HOURS),
        "roundtrip_cost_pct": ROUNDTRIP_COST_PCT,
        "event_decluster_hours": 24,
        "frozen_discovery_thresholds": frozen,
        "frozen_anatomy_bin_edges": bin_edges,
        "rules": {
            "BASE": "all fresh 20-bar breakout events",
            "ALIGN": "BTC and ETH 30d normalized trends agree and event direction matches market trend",
            "ALIGN_PERSIST": "ALIGN plus market 30d efficiency ratio >= discovery median",
            "ALIGN_PERSIST_STRONG": "ALIGN_PERSIST plus absolute normalized trend >= discovery median",
        },
        "bias_control": [
            "All regime thresholds are discovery-period medians, not selected on validation/holdout.",
            "Context uses only fully completed 1h bars strictly before the entry decision.",
            "Fresh breakout edges are declustered by 24h per direction.",
            "Validation and holdout are reported separately and not used to tune thresholds.",
        ],
    }
    (OUT_ROOT / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print("=== STAGE2 META ===")
    print(json.dumps(meta, indent=2))

    print("\n=== FROZEN RULES: VALIDATION/HOLDOUT @ 0.25% COST ===")
    view = summary[
        summary["split"].isin(["VALIDATION", "HOLDOUT"])
        & summary["rule"].isin(["BASE", "ALIGN", "ALIGN_PERSIST", "ALIGN_PERSIST_STRONG"])
    ].copy()
    # Focus console on 72h outcome: long enough for 4h/12h/1d structure, still actionable.
    view = view[view["horizon_hours"] == 72]
    print(
        view[
            [
                "family",
                "timeframe",
                "split",
                "rule",
                "n",
                "avg_pct",
                "pf",
                "bootstrap95_lo",
                "bootstrap95_hi",
            ]
        ].to_string(index=False)
    )

    print("\n=== ALIGNMENT-STRENGTH QUINTILES, RAW DONCHIAN, 72H ===")
    av = anatomy[anatomy["feature"] == "alignment_strength"]
    print(av.to_string(index=False))

    print("\n=== PERSISTENCE QUINTILES, RAW DONCHIAN, 72H ===")
    pv = anatomy[anatomy["feature"] == "market_er30"]
    print(pv.to_string(index=False))

    print("\n=== DAILY REGIME STRATEGIES ===")
    print(daily.to_string(index=False))


if __name__ == "__main__":
    main()
