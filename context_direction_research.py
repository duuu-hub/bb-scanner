from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

MIN = 60_000
BAR_MS = 15 * MIN
DAY_MS = 24 * 60 * MIN
YEAR_MS = 365 * DAY_MS


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--artifact-dir", default="prior_artifact/regime_breadth_results")
    p.add_argument("--market-root", default="market_data_store/bitget/15m")
    p.add_argument("--outdir", default="context_direction_results")
    return p.parse_args()


def calc_pf(values):
    x = pd.to_numeric(values, errors="coerce").dropna()
    wins = x[x > 0].sum()
    losses = -x[x < 0].sum()
    if losses <= 0:
        return float("inf") if wins > 0 else float("nan")
    return float(wins / losses)


def load_symbol(root: Path, symbol: str) -> pd.DataFrame:
    files = sorted((root / symbol).glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no market files for {symbol}")
    df = pd.concat(
        [pd.read_csv(path, usecols=["timestamp_ms", "close"]) for path in files],
        ignore_index=True,
    )
    df = df.drop_duplicates("timestamp_ms", keep="last").sort_values("timestamp_ms").reset_index(drop=True)
    df["timestamp_ms"] = pd.to_numeric(df["timestamp_ms"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["timestamp_ms", "close"])
    df = df[df["close"] > 0].copy()
    df["timestamp_ms"] = df["timestamp_ms"].astype("int64")
    df["available_ts"] = df["timestamp_ms"] + BAR_MS
    logret = np.log(df["close"]).diff()
    df["rv7d"] = logret.rolling(7 * 24 * 4, min_periods=7 * 24 * 4).std()
    return df


def asof_index(times, ts):
    return int(np.searchsorted(times, ts, side="right") - 1)


def trailing_percentile(values, times, idx, lookback_ms):
    if idx < 0 or not math.isfinite(float(values[idx])):
        return float("nan")
    start_ts = int(times[idx]) - lookback_ms
    lo = int(np.searchsorted(times, start_ts, side="left"))
    hist = values[lo:idx + 1]
    hist = hist[np.isfinite(hist)]
    if len(hist) < 30:
        return float("nan")
    cur = float(values[idx])
    return float((hist <= cur).mean() * 100.0)


def market_context_at(df, signal_ts):
    times = df["available_ts"].to_numpy(dtype=np.int64)
    closes = df["close"].to_numpy(dtype=float)
    rv = df["rv7d"].to_numpy(dtype=float)
    idx = asof_index(times, signal_ts)
    if idx < 96:
        return {}

    def ret(bars):
        if idx - bars < 0:
            return float("nan")
        return (closes[idx] / closes[idx - bars] - 1.0) * 100.0

    return {
        "ret_4h": ret(16),
        "ret_24h": ret(96),
        "rv7d_pctile_365d": trailing_percentile(rv, times, idx, YEAR_MS),
    }


def breadth_value(breadth, ts, col):
    times = breadth["ts"].to_numpy(dtype=np.int64)
    idx = asof_index(times, ts)
    if idx < 0:
        return float("nan")
    value = breadth.iloc[idx][col]
    return float(value) if pd.notna(value) else float("nan")


def breadth_pctile(breadth, ts, col):
    times = breadth["ts"].to_numpy(dtype=np.int64)
    vals = pd.to_numeric(breadth[col], errors="coerce").to_numpy(dtype=float)
    idx = asof_index(times, ts)
    return trailing_percentile(vals, times, idx, YEAR_MS)


def tercile(value):
    if not math.isfinite(value):
        return "UNKNOWN"
    if value < 33.333333:
        return "LOW"
    if value < 66.666667:
        return "MID"
    return "HIGH"


def sign_state(value):
    if not math.isfinite(value):
        return "UNKNOWN"
    if value > 0:
        return "EXPANDING"
    if value < 0:
        return "CONTRACTING"
    return "FLAT"


def alignment(a, b):
    if not (math.isfinite(a) and math.isfinite(b)):
        return "UNKNOWN"
    if a > 0 and b > 0:
        return "BOTH_UP"
    if a < 0 and b < 0:
        return "BOTH_DOWN"
    return "MIXED"


def summarize_factor(trades, factor):
    rows = []
    for (strategy, delay, direction, value), g in trades.groupby(
        ["base_strategy", "delay_min", "direction", factor], dropna=False
    ):
        rows.append({
            "factor": factor,
            "factor_value": value,
            "strategy": strategy,
            "delay_min": int(delay),
            "direction": direction,
            "n": int(len(g)),
            "win_rate_pct": float((g["net_pct"] > 0).mean() * 100.0),
            "avg_net_pct": float(g["net_pct"].mean()),
            "median_net_pct": float(g["net_pct"].median()),
            "profit_factor": calc_pf(g["net_pct"]),
            "sum_net_pct": float(g["net_pct"].sum()),
        })
    return pd.DataFrame(rows)


def direction_compare(summary):
    ids = ["factor", "factor_value", "strategy", "delay_min"]
    metrics = ["n", "win_rate_pct", "avg_net_pct", "profit_factor", "sum_net_pct"]
    long = summary[summary["direction"] == "LONG"][ids + metrics].rename(
        columns={m: f"long_{m}" for m in metrics}
    )
    short = summary[summary["direction"] == "SHORT"][ids + metrics].rename(
        columns={m: f"short_{m}" for m in metrics}
    )
    out = long.merge(short, on=ids, how="outer")
    out["short_minus_long_avg_net"] = out["short_avg_net_pct"] - out["long_avg_net_pct"]
    return out.sort_values(ids).reset_index(drop=True)


def main():
    args = parse_args()
    artifact_dir = Path(args.artifact_dir)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    trades = pd.read_csv(artifact_dir / "long_vs_short_trades.csv.gz")
    breadth = pd.read_csv(artifact_dir / "breadth_timeseries.csv.gz").sort_values("ts").reset_index(drop=True)

    root = Path(args.market_root)
    btc = load_symbol(root, "BTCUSDT")
    eth = load_symbol(root, "ETHUSDT")

    rows = []
    for ts in sorted(int(x) for x in trades["signal_ts"].dropna().unique()):
        bc = market_context_at(btc, ts)
        ec = market_context_at(eth, ts)

        near = breadth_value(breadth, ts, "weekly_near3_pct_24h_med")
        if not math.isfinite(near):
            near = breadth_value(breadth, ts, "weekly_near3_pct")

        pctl = breadth_pctile(breadth, ts, "weekly_near3_pct_24h_med")
        d1 = near - breadth_value(breadth, ts - DAY_MS, "weekly_near3_pct_24h_med")
        d3 = near - breadth_value(breadth, ts - 3 * DAY_MS, "weekly_near3_pct_24h_med")
        d7 = near - breadth_value(breadth, ts - 7 * DAY_MS, "weekly_near3_pct_24h_med")

        rows.append({
            "signal_ts": ts,
            "breadth_near3_pct": near,
            "breadth_near3_pctile_365d": pctl,
            "breadth_level": tercile(pctl),
            "breadth_delta_1d": d1,
            "breadth_delta_3d": d3,
            "breadth_delta_7d": d7,
            "breadth_trend_1d": sign_state(d1),
            "breadth_trend_3d": sign_state(d3),
            "breadth_trend_7d": sign_state(d7),
            "btc_ret_4h": bc.get("ret_4h", float("nan")),
            "btc_ret_24h": bc.get("ret_24h", float("nan")),
            "eth_ret_4h": ec.get("ret_4h", float("nan")),
            "eth_ret_24h": ec.get("ret_24h", float("nan")),
            "btc_rv7d_pctile_365d": bc.get("rv7d_pctile_365d", float("nan")),
        })

    ctx = pd.DataFrame(rows)
    ctx["btc_vol_state"] = ctx["btc_rv7d_pctile_365d"].map(tercile)
    ctx["btc_eth_24h_alignment"] = [
        alignment(a, b) for a, b in zip(ctx["btc_ret_24h"], ctx["eth_ret_24h"])
    ]
    ctx["btc_eth_4h_alignment"] = [
        alignment(a, b) for a, b in zip(ctx["btc_ret_4h"], ctx["eth_ret_4h"])
    ]
    ctx["breadth_level_x_trend3d"] = ctx["breadth_level"] + "__" + ctx["breadth_trend_3d"]
    ctx.to_csv(outdir / "signal_market_context.csv", index=False)

    enriched = trades.merge(ctx, on="signal_ts", how="left")
    enriched["quarter"] = pd.to_datetime(enriched["signal_ts"], unit="ms", utc=True).dt.to_period("Q").astype(str)
    enriched.to_csv(outdir / "trades_with_market_context.csv.gz", index=False, compression="gzip")

    factors = [
        "breadth_level",
        "breadth_trend_1d",
        "breadth_trend_3d",
        "breadth_trend_7d",
        "breadth_level_x_trend3d",
        "btc_vol_state",
        "btc_eth_4h_alignment",
        "btc_eth_24h_alignment",
    ]
    summary = pd.concat([summarize_factor(enriched, f) for f in factors], ignore_index=True)
    summary.to_csv(outdir / "context_factor_summary.csv", index=False)

    compare = direction_compare(summary)
    compare.to_csv(outdir / "context_long_vs_short_compare.csv", index=False)

    qrows = []
    for factor in ("breadth_level_x_trend3d", "btc_eth_24h_alignment", "btc_vol_state"):
        for (strategy, delay, direction, value, quarter), g in enriched.groupby(
            ["base_strategy", "delay_min", "direction", factor, "quarter"], dropna=False
        ):
            qrows.append({
                "factor": factor,
                "factor_value": value,
                "strategy": strategy,
                "delay_min": int(delay),
                "direction": direction,
                "quarter": quarter,
                "n": int(len(g)),
                "avg_net_pct": float(g["net_pct"].mean()),
                "profit_factor": calc_pf(g["net_pct"]),
                "sum_net_pct": float(g["net_pct"].sum()),
            })
    pd.DataFrame(qrows).to_csv(outdir / "quarter_stability.csv", index=False)

    base = compare[compare["delay_min"] == 1].copy()
    base = base[(base["long_n"].fillna(0) >= 5) & (base["short_n"].fillna(0) >= 5)]
    print("\n=== +1m CONTEXT LONG vs SHORT (n>=5 each) ===")
    with pd.option_context("display.max_rows", 300, "display.max_columns", 30, "display.width", 260):
        print(base.to_string(index=False))

    print("\n=== RESEARCH NOTES ===")
    print("Point-in-time only: BTC/ETH features use completed 15m candles available by signal time.")
    print("Breadth and BTC-vol levels use trailing 365d terciles, not outcome-optimized thresholds.")
    print("Breadth trend uses only sign of 1d/3d/7d change.")
    print("Exploratory only: historical AUTO50 universe has survivorship bias.")
    print(f"[DONE] unique_signals={len(ctx)} trade_rows={len(enriched)}")


if __name__ == "__main__":
    main()
