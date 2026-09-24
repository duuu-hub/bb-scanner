from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

DATA_ROOT = Path("market_data_store/bitget/15m")
OUT_ROOT = Path("research_output/btc_eth_regime_stage3")
SYMBOLS = ("BTCUSDT", "ETHUSDT")

SPLITS = (
    ("DISCOVERY", None, pd.Timestamp("2024-01-01", tz="UTC")),
    ("VALIDATION", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-07-01", tz="UTC")),
    ("HOLDOUT", pd.Timestamp("2025-07-01", tz="UTC"), None),
)

BASE_WINDOW = 30
BASE_ER_QUANTILE = 0.50
BASE_COST_RT = 0.25


def load_15m(symbol: str) -> pd.DataFrame:
    files = sorted((DATA_ROOT / symbol).glob("*.csv"))
    if not files:
        raise RuntimeError(f"missing {symbol}")
    x = pd.concat([pd.read_csv(p) for p in files], ignore_index=True)
    x["datetime_utc"] = pd.to_datetime(x["datetime_utc"], utc=True)
    for col in ("open", "high", "low", "close", "base_volume", "quote_volume"):
        x[col] = pd.to_numeric(x[col], errors="coerce")
    return (
        x.drop_duplicates("timestamp_ms")
        .dropna(subset=["open", "high", "low", "close"])
        .sort_values("datetime_utc")
        .reset_index(drop=True)
    )


def daily_frame(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.set_index("datetime_utc")
    return (
        x.resample("1D", label="left", closed="left")
        .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
        .dropna()
        .reset_index()
    )


def build_panel(raw15: dict[str, pd.DataFrame], window: int) -> tuple[pd.DataFrame, float]:
    ds = {}
    for symbol in SYMBOLS:
        d = daily_frame(raw15[symbol])[["datetime_utc", "open", "close"]].copy()
        c = d["close"]
        d[f"{symbol}_fwd"] = c.pct_change().shift(-1) * 100.0
        d[f"{symbol}_retN"] = c / c.shift(window) - 1.0
        path = c.diff().abs().rolling(window, min_periods=max(10, window // 2)).sum()
        d[f"{symbol}_erN"] = (c - c.shift(window)).abs() / path.replace(0, np.nan)
        d = d.rename(columns={"open": f"{symbol}_open", "close": f"{symbol}_close"})
        ds[symbol] = d

    x = ds["BTCUSDT"].merge(ds["ETHUSDT"], on="datetime_utc", how="inner")
    x["btc_sign"] = np.sign(x["BTCUSDT_retN"])
    x["eth_sign"] = np.sign(x["ETHUSDT_retN"])
    x["agree"] = (x["btc_sign"] == x["eth_sign"]) & (x["btc_sign"] != 0)
    x["common_sign"] = x["btc_sign"].where(x["agree"], 0).fillna(0.0)
    x["market_er"] = (x["BTCUSDT_erN"] + x["ETHUSDT_erN"]) / 2.0
    discovery = x[x["datetime_utc"] < pd.Timestamp("2024-01-01", tz="UTC")]
    return x, float(discovery["market_er"].quantile(BASE_ER_QUANTILE))


def split_mask(x: pd.DataFrame, split: str) -> pd.Series:
    for name, start, end in SPLITS:
        if name != split:
            continue
        mask = pd.Series(True, index=x.index)
        if start is not None:
            mask &= x["datetime_utc"] >= start
        if end is not None:
            mask &= x["datetime_utc"] < end
        return mask
    raise KeyError(split)


def mdd(returns_pct: np.ndarray) -> float:
    if len(returns_pct) == 0:
        return math.nan
    curve = np.cumprod(1.0 + returns_pct / 100.0)
    full = np.r_[1.0, curve]
    peaks = np.maximum.accumulate(full)
    return float(((full / peaks) - 1.0).min() * 100.0)


def perf(returns_pct: np.ndarray) -> dict:
    values = np.asarray(returns_pct, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return {
            "days": 0, "total_return_pct": math.nan, "cagr_pct": math.nan,
            "sharpe": math.nan, "mdd_pct": math.nan, "avg_daily_pct": math.nan,
        }
    wealth = float(np.prod(1.0 + values / 100.0))
    years = len(values) / 365.25
    std = float(np.std(values, ddof=0))
    return {
        "days": len(values),
        "total_return_pct": (wealth - 1.0) * 100.0,
        "cagr_pct": (wealth ** (1.0 / years) - 1.0) * 100.0 if years > 0 and wealth > 0 else math.nan,
        "sharpe": float(np.mean(values) / std * math.sqrt(365.25)) if std > 0 else math.nan,
        "mdd_pct": mdd(values),
        "avg_daily_pct": float(np.mean(values)),
    }


def episode_stats(position: pd.Series) -> dict:
    p = position.fillna(0).astype(float).to_numpy()
    episodes = []
    start = None
    side = 0.0
    for i, value in enumerate(p):
        if value != side:
            if side != 0 and start is not None:
                episodes.append((side, i - start))
            if value != 0:
                start = i
            else:
                start = None
            side = value
    if side != 0 and start is not None:
        episodes.append((side, len(p) - start))
    durations = [d for _, d in episodes]
    return {
        "episodes": len(episodes),
        "median_episode_days": float(np.median(durations)) if durations else math.nan,
        "mean_episode_days": float(np.mean(durations)) if durations else math.nan,
        "long_episodes": sum(1 for s, _ in episodes if s > 0),
        "short_episodes": sum(1 for s, _ in episodes if s < 0),
    }


def make_positions(
    x: pd.DataFrame,
    er_threshold: float,
    *,
    delay_days: int = 0,
    direction_mode: str = "BOTH",
) -> dict[str, pd.Series]:
    common = x["common_sign"].where(x["market_er"] >= er_threshold, 0.0)
    if direction_mode == "LONG_ONLY":
        common = common.where(common > 0, 0.0)
    elif direction_mode == "SHORT_ONLY":
        common = common.where(common < 0, 0.0)
    elif direction_mode != "BOTH":
        raise KeyError(direction_mode)

    if delay_days > 0:
        common = common.shift(delay_days).fillna(0.0)
    return {symbol: common.copy() for symbol in SYMBOLS}


def portfolio_returns(
    x: pd.DataFrame,
    positions: dict[str, pd.Series],
    roundtrip_cost_pct: float,
) -> tuple[pd.Series, dict[str, pd.Series], pd.Series]:
    one_way = roundtrip_cost_pct / 2.0
    portfolio = pd.Series(0.0, index=x.index)
    per_symbol = {}
    turnover = pd.Series(0.0, index=x.index)

    for symbol in SYMBOLS:
        p = positions[symbol].astype(float)
        gross = p * x[f"{symbol}_fwd"].fillna(0.0)
        change = p.diff().abs().fillna(p.abs())
        cost = change * one_way
        net = gross - cost
        per_symbol[symbol] = net
        portfolio += net / len(SYMBOLS)
        turnover += change / len(SYMBOLS)

    return portfolio, per_symbol, turnover


def bootstrap_months(
    dates: pd.Series,
    returns: pd.Series,
    split: str,
    reps: int = 5000,
    seed: int = 31,
) -> dict:
    mask = split_mask(pd.DataFrame({"datetime_utc": dates}), split)
    z = pd.DataFrame({"date": dates[mask], "ret": returns[mask].to_numpy()}).dropna()
    z["month"] = z["date"].dt.to_period("M").astype(str)
    monthly_log = []
    for _, g in z.groupby("month"):
        vals = g["ret"].to_numpy(dtype=float)
        wealth = np.prod(1.0 + vals / 100.0)
        monthly_log.append(math.log(max(wealth, 1e-12)))
    if len(monthly_log) < 2:
        return {"months": len(monthly_log), "mean_month_log_ci_lo": math.nan, "mean_month_log_ci_hi": math.nan}
    arr = np.asarray(monthly_log)
    rng = np.random.default_rng(seed)
    means = np.empty(reps)
    for i in range(reps):
        means[i] = rng.choice(arr, size=len(arr), replace=True).mean()
    return {
        "months": len(arr),
        "mean_month_log_ci_lo": float(np.quantile(means, 0.025)),
        "mean_month_log_ci_hi": float(np.quantile(means, 0.975)),
    }


def top_month_removal(dates: pd.Series, returns: pd.Series, split: str) -> list[dict]:
    mask = split_mask(pd.DataFrame({"datetime_utc": dates}), split)
    z = pd.DataFrame({"date": dates[mask], "ret": returns[mask].to_numpy()}).dropna()
    z["month"] = z["date"].dt.to_period("M").astype(str)
    month_total = {}
    for month, g in z.groupby("month"):
        month_total[month] = (np.prod(1.0 + g["ret"].to_numpy() / 100.0) - 1.0) * 100.0
    ranked = sorted(month_total, key=month_total.get, reverse=True)
    out = []
    for k in (0, 1, 3, 5):
        drop = set(ranked[:k])
        vals = z.loc[~z["month"].isin(drop), "ret"].to_numpy(dtype=float)
        p = perf(vals)
        out.append({"split": split, "remove_top_months": k, **p})
    return out


def evaluate_variant(
    raw15: dict[str, pd.DataFrame],
    window: int,
    er_quantile: float,
    cost_rt: float,
    delay_days: int,
    direction_mode: str,
    label: str,
) -> tuple[list[dict], list[dict], dict]:
    x, _ = build_panel(raw15, window)
    discovery = x[x["datetime_utc"] < pd.Timestamp("2024-01-01", tz="UTC")]
    threshold = float(discovery["market_er"].quantile(er_quantile))
    positions = make_positions(
        x, threshold, delay_days=delay_days, direction_mode=direction_mode
    )
    portfolio, per_symbol, turnover = portfolio_returns(x, positions, cost_rt)

    rows = []
    symbol_rows = []
    for split, _, _ in SPLITS:
        mask = split_mask(x, split)
        vals = portfolio[mask].to_numpy(dtype=float)
        p = perf(vals)
        ep = episode_stats(positions["BTCUSDT"][mask])
        boot = bootstrap_months(x["datetime_utc"], portfolio, split)
        rows.append(
            {
                "label": label,
                "window_days": window,
                "er_quantile": er_quantile,
                "er_threshold_discovery": threshold,
                "roundtrip_cost_pct": cost_rt,
                "delay_days": delay_days,
                "direction_mode": direction_mode,
                "split": split,
                **p,
                "avg_turnover_units": float(turnover[mask].mean()),
                "active_day_pct": float((positions["BTCUSDT"][mask] != 0).mean() * 100.0),
                **ep,
                **boot,
            }
        )
        for symbol in SYMBOLS:
            sp = perf(per_symbol[symbol][mask].to_numpy(dtype=float))
            symbol_rows.append(
                {
                    "label": label,
                    "window_days": window,
                    "er_quantile": er_quantile,
                    "roundtrip_cost_pct": cost_rt,
                    "delay_days": delay_days,
                    "direction_mode": direction_mode,
                    "split": split,
                    "symbol": symbol,
                    **sp,
                }
            )
    return rows, symbol_rows, {"x": x, "portfolio": portfolio, "positions": positions, "threshold": threshold}


def benchmark_buy_hold(raw15: dict[str, pd.DataFrame]) -> pd.DataFrame:
    x, _ = build_panel(raw15, BASE_WINDOW)
    combined = (x["BTCUSDT_fwd"].fillna(0.0) + x["ETHUSDT_fwd"].fillna(0.0)) / 2.0
    rows = []
    for split, _, _ in SPLITS:
        vals = combined[split_mask(x, split)].to_numpy(dtype=float)
        rows.append({"benchmark": "50_50_BTC_ETH_BUY_HOLD", "split": split, **perf(vals)})
    return pd.DataFrame(rows)


def yearly_base(base_obj: dict) -> pd.DataFrame:
    x = base_obj["x"].copy()
    x["portfolio"] = base_obj["portfolio"]
    x["position"] = base_obj["positions"]["BTCUSDT"]
    x["year"] = x["datetime_utc"].dt.year
    rows = []
    for year, g in x.groupby("year"):
        vals = g["portfolio"].to_numpy(dtype=float)
        p = perf(vals)
        rows.append(
            {
                "year": int(year),
                **p,
                "active_day_pct": float((g["position"] != 0).mean() * 100.0),
                "long_day_pct": float((g["position"] > 0).mean() * 100.0),
                "short_day_pct": float((g["position"] < 0).mean() * 100.0),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    raw15 = {symbol: load_15m(symbol) for symbol in SYMBOLS}

    all_rows = []
    all_symbol_rows = []
    base_obj = None

    variants = []

    # Main pre-registered candidate.
    variants.append((30, 0.50, 0.25, 0, "BOTH", "BASE_30D_Q50"))

    # Cost stress, with rules unchanged.
    for cost in (0.12, 0.50):
        variants.append((30, 0.50, cost, 0, "BOTH", f"COST_{cost:.2f}"))

    # Parameter-neighborhood checks; report all, do not select a winner.
    for window in (20, 60, 90):
        variants.append((window, 0.50, 0.25, 0, "BOTH", f"WINDOW_{window}D"))
    for q in (0.40, 0.60):
        variants.append((30, q, 0.25, 0, "BOTH", f"ER_Q{int(q*100)}"))

    # Staleness / execution robustness.
    for delay in (1, 2):
        variants.append((30, 0.50, 0.25, delay, "BOTH", f"DELAY_{delay}D"))

    # Direction decomposition.
    variants.append((30, 0.50, 0.25, 0, "LONG_ONLY", "LONG_ONLY"))
    variants.append((30, 0.50, 0.25, 0, "SHORT_ONLY", "SHORT_ONLY"))

    seen = set()
    for args in variants:
        key = args
        if key in seen:
            continue
        seen.add(key)
        rows, symbol_rows, obj = evaluate_variant(raw15, *args)
        all_rows.extend(rows)
        all_symbol_rows.extend(symbol_rows)
        if args[-1] == "BASE_30D_Q50":
            base_obj = obj

    summary = pd.DataFrame(all_rows)
    symbol_summary = pd.DataFrame(all_symbol_rows)
    summary.to_csv(OUT_ROOT / "robustness_summary.csv", index=False)
    symbol_summary.to_csv(OUT_ROOT / "symbol_summary.csv", index=False)

    if base_obj is None:
        raise RuntimeError("base candidate missing")
    yearly = yearly_base(base_obj)
    yearly.to_csv(OUT_ROOT / "base_yearly.csv", index=False)

    concentration = pd.DataFrame(
        top_month_removal(base_obj["x"]["datetime_utc"], base_obj["portfolio"], "VALIDATION")
        + top_month_removal(base_obj["x"]["datetime_utc"], base_obj["portfolio"], "HOLDOUT")
    )
    concentration.to_csv(OUT_ROOT / "top_month_removal.csv", index=False)

    benchmark = benchmark_buy_hold(raw15)
    benchmark.to_csv(OUT_ROOT / "benchmark_buy_hold.csv", index=False)

    meta = {
        "schema_version": 1,
        "candidate": "BTC/ETH common 30d trend direction, active only when both agree and average 30d efficiency ratio is >= discovery median",
        "discovery_cutoff": "2024-01-01T00:00:00Z",
        "validation": "2024-01-01 through 2025-06-30 UTC",
        "holdout": "2025-07-01 through latest stored completed day",
        "base_roundtrip_cost_pct": BASE_COST_RT,
        "cost_model": "0.125% per unit position change for 0.25% roundtrip; flip long<->short costs 0.25%",
        "execution": "signal at daily close; position earns next close-to-close return",
        "robustness_grid": {
            "window_days": [20, 30, 60, 90],
            "discovery_er_quantiles": [0.40, 0.50, 0.60],
            "roundtrip_cost_pct": [0.12, 0.25, 0.50],
            "signal_delay_days": [0, 1, 2],
            "direction_modes": ["BOTH", "LONG_ONLY", "SHORT_ONLY"],
        },
        "important": "Neighborhood variants are robustness diagnostics, not threshold optimization. Base remains 30d/Q50.",
    }
    (OUT_ROOT / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print("=== STAGE3 META ===")
    print(json.dumps(meta, indent=2))

    print("\n=== BASE CANDIDATE ===")
    print(summary[summary["label"] == "BASE_30D_Q50"].to_string(index=False))

    print("\n=== ROBUSTNESS VALIDATION/HOLDOUT ===")
    view = summary[summary["split"].isin(["VALIDATION", "HOLDOUT"])][
        [
            "label", "split", "days", "total_return_pct", "cagr_pct", "sharpe",
            "mdd_pct", "active_day_pct", "episodes", "median_episode_days",
            "mean_month_log_ci_lo", "mean_month_log_ci_hi",
        ]
    ]
    print(view.to_string(index=False))

    print("\n=== PER SYMBOL BASE ===")
    print(symbol_summary[symbol_summary["label"] == "BASE_30D_Q50"].to_string(index=False))

    print("\n=== BASE YEARLY ===")
    print(yearly.to_string(index=False))

    print("\n=== TOP MONTH REMOVAL ===")
    print(concentration.to_string(index=False))

    print("\n=== BUY HOLD BENCHMARK ===")
    print(benchmark.to_string(index=False))


if __name__ == "__main__":
    main()
