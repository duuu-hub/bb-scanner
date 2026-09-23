from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from precision_backtest import build_signals, calc_pf, fetch_all_minutes, one_trade


LONG_STRATEGIES = {
    "L1_MOMENTUM_1H10",
    "L2_EXPLOSIVE_4H30",
    "L3_4H_LAG",
}

EXCLUDED_BREADTH_SYMBOLS = {
    "BTCUSDT",
    "ETHUSDT",
}

REGIME_RULES = {
    "55_45": (55.0, 45.0),
    "60_40": (60.0, 40.0),
    "65_35": (65.0, 35.0),
}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--outdir", default="regime_breadth_results")
    p.add_argument("--workers", type=int, default=6)
    return p.parse_args()


def _pct_mean(s: pd.Series) -> float:
    if s.empty:
        return np.nan
    return float(s.mean() * 100.0)


def build_breadth(source: pd.DataFrame) -> pd.DataFrame:
    needed = [
        "symbol", "ts",
        "1W_above", "1W_dist", "1W_above_basis", "1W_below_lower",
        "1D_above", "1D_dist", "1D_above_basis", "1D_below_lower",
        "4H_above", "4H_dist", "4H_above_basis", "4H_below_lower",
    ]
    missing = [c for c in needed if c not in source.columns]
    if missing:
        raise RuntimeError(f"snapshot source missing breadth columns: {missing}")

    x = source[needed].copy()
    x = x[~x["symbol"].isin(EXCLUDED_BREADTH_SYMBOLS)].copy()
    if x.empty:
        raise RuntimeError("breadth universe is empty after exclusions")

    for tf in ("1W", "1D", "4H"):
        x[f"{tf}_near3"] = x[f"{tf}_dist"] >= -3.0

    grouped = x.groupby("ts", sort=True)
    out = grouped.agg(
        universe_count=("symbol", "nunique"),
        weekly_upper_pct=("1W_above", _pct_mean),
        weekly_near3_pct=("1W_near3", _pct_mean),
        weekly_mid_pct=("1W_above_basis", _pct_mean),
        weekly_lower_pct=("1W_below_lower", _pct_mean),
        daily_upper_pct=("1D_above", _pct_mean),
        daily_near3_pct=("1D_near3", _pct_mean),
        daily_mid_pct=("1D_above_basis", _pct_mean),
        daily_lower_pct=("1D_below_lower", _pct_mean),
        h4_upper_pct=("4H_above", _pct_mean),
        h4_near3_pct=("4H_near3", _pct_mean),
        h4_mid_pct=("4H_above_basis", _pct_mean),
        h4_lower_pct=("4H_below_lower", _pct_mean),
    ).reset_index()

    # 24h trailing median prevents regime labels from flipping on one noisy 15m reading.
    # This is backward-looking only: current and prior 95 completed snapshot buckets.
    for col in (
        "weekly_mid_pct", "daily_mid_pct",
        "weekly_upper_pct", "weekly_near3_pct", "weekly_lower_pct",
        "daily_upper_pct", "daily_near3_pct", "daily_lower_pct",
    ):
        out[f"{col}_24h_med"] = out[col].rolling(96, min_periods=24).median()

    for name, (bull_cut, bear_cut) in REGIME_RULES.items():
        w = out["weekly_mid_pct_24h_med"]
        d = out["daily_mid_pct_24h_med"]
        regime = np.full(len(out), "RANGE", dtype=object)
        regime[(w >= bull_cut) & (d >= bull_cut)] = "BULL"
        regime[(w <= bear_cut) & (d <= bear_cut)] = "BEAR"
        regime[w.isna() | d.isna()] = "UNKNOWN"
        out[f"regime_{name}"] = regime

    out["time_utc"] = pd.to_datetime(out["ts"], unit="ms", utc=True)
    return out


def mirrored_short_signals(long_signals: pd.DataFrame) -> pd.DataFrame:
    short = long_signals.copy()
    short["direction"] = "SHORT"
    short["strategy"] = short["strategy"].astype(str) + "__MIRROR_SHORT"
    return short


def summarize(trades: pd.DataFrame, regime_col: str) -> pd.DataFrame:
    rows = []
    if trades.empty:
        return pd.DataFrame()

    for (base_strategy, direction, delay, regime), g in trades.groupby(
        ["base_strategy", "direction", "delay_min", regime_col],
        dropna=False,
    ):
        rows.append({
            "regime_rule": regime_col,
            "strategy": base_strategy,
            "direction": direction,
            "delay_min": int(delay),
            "regime": regime,
            "n": int(len(g)),
            "win_rate_pct": float((g["net_pct"] > 0).mean() * 100.0),
            "avg_net_pct": float(g["net_pct"].mean()),
            "median_net_pct": float(g["net_pct"].median()),
            "profit_factor": float(calc_pf(g["net_pct"])),
            "sum_net_pct": float(g["net_pct"].sum()),
            "worst_trade_pct": float(g["net_pct"].min()),
            "best_trade_pct": float(g["net_pct"].max()),
            "tp_rate_pct": float((g["outcome"] == "TP").mean() * 100.0),
            "sl_rate_pct": float((g["outcome"] == "SL").mean() * 100.0),
        })
    return pd.DataFrame(rows)


def comparison(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()

    id_cols = ["regime_rule", "strategy", "delay_min", "regime"]
    metric_cols = [
        "n", "win_rate_pct", "avg_net_pct", "profit_factor",
        "sum_net_pct", "worst_trade_pct", "best_trade_pct",
    ]
    parts = []
    for direction in ("LONG", "SHORT"):
        q = summary[summary["direction"] == direction][id_cols + metric_cols].copy()
        q = q.rename(columns={m: f"{direction.lower()}_{m}" for m in metric_cols})
        parts.append(q)

    if len(parts) != 2:
        return pd.DataFrame()

    out = parts[0].merge(parts[1], on=id_cols, how="outer")
    out["avg_net_short_minus_long"] = out["short_avg_net_pct"] - out["long_avg_net_pct"]
    return out.sort_values(id_cols).reset_index(drop=True)


def main():
    args = parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print("[STEP] load snapshots")
    source = pd.read_csv(args.source)

    print("[STEP] build market breadth and predeclared regimes")
    breadth = build_breadth(source)
    breadth.to_csv(outdir / "breadth_timeseries.csv.gz", index=False, compression="gzip")

    regime_counts = []
    for rule in REGIME_RULES:
        col = f"regime_{rule}"
        counts = breadth[col].value_counts(dropna=False)
        for label, n in counts.items():
            regime_counts.append({
                "regime_rule": col,
                "regime": label,
                "buckets": int(n),
            })
    pd.DataFrame(regime_counts).to_csv(outdir / "regime_bucket_counts.csv", index=False)

    print("[STEP] rebuild frozen L1/L2/L3 signal timestamps")
    signals = build_signals(source)
    signals = signals[signals["strategy"].isin(LONG_STRATEGIES)].copy()
    if signals.empty:
        raise RuntimeError("no L1/L2/L3 signals in source")

    signals["base_strategy"] = signals["strategy"]
    signal_context_cols = [
        "ts", "time_utc", "universe_count",
        "weekly_upper_pct", "weekly_near3_pct", "weekly_mid_pct", "weekly_lower_pct",
        "daily_upper_pct", "daily_near3_pct", "daily_mid_pct", "daily_lower_pct",
        "h4_upper_pct", "h4_near3_pct", "h4_mid_pct", "h4_lower_pct",
        *[f"regime_{r}" for r in REGIME_RULES],
    ]
    signals = signals.merge(
        breadth[signal_context_cols],
        on="ts",
        how="left",
        suffixes=("", "_breadth"),
    )
    signals.to_csv(outdir / "signals_with_regime.csv", index=False)

    print("[STEP] fetch targeted 1m windows once")
    minute_map, failures = fetch_all_minutes(signals, args.workers)
    if failures:
        pd.DataFrame(failures, columns=["symbol", "error"]).to_csv(
            outdir / "minute_fetch_failures.csv", index=False
        )

    print("[STEP] replay identical timestamps LONG vs mirrored SHORT at +1/+2/+3m")
    long_signals = signals.copy()
    short_signals = mirrored_short_signals(signals)

    context_map = signals.set_index(["symbol", "ts", "base_strategy"])[
        [f"regime_{r}" for r in REGIME_RULES]
        + [
            "weekly_upper_pct", "weekly_near3_pct", "weekly_mid_pct", "weekly_lower_pct",
            "daily_upper_pct", "daily_near3_pct", "daily_mid_pct", "daily_lower_pct",
        ]
    ]

    trade_rows = []
    for table, direction in ((long_signals, "LONG"), (short_signals, "SHORT")):
        for s in table.itertuples(index=False):
            minute_df = minute_map.get(s.symbol)
            for delay in (1, 2, 3):
                row = one_trade(s, minute_df, delay)
                if not row:
                    continue
                base_strategy = str(s.base_strategy)
                row["base_strategy"] = base_strategy
                row["direction"] = direction
                key = (s.symbol, int(s.ts), base_strategy)
                try:
                    ctx = context_map.loc[key]
                    if isinstance(ctx, pd.DataFrame):
                        ctx = ctx.iloc[-1]
                    for col, value in ctx.items():
                        row[col] = value
                except KeyError:
                    pass
                trade_rows.append(row)

    trades = pd.DataFrame(trade_rows)
    trades.to_csv(outdir / "long_vs_short_trades.csv.gz", index=False, compression="gzip")

    summaries = []
    for rule in REGIME_RULES:
        col = f"regime_{rule}"
        summaries.append(summarize(trades, col))
    summary = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()
    summary.to_csv(outdir / "long_vs_short_by_regime.csv", index=False)

    comp = comparison(summary)
    comp.to_csv(outdir / "long_vs_short_compare.csv", index=False)

    base = summary[
        (summary["regime_rule"] == "regime_60_40")
        & (summary["delay_min"] == 1)
    ].sort_values(["strategy", "regime", "direction"])

    print("\n=== BASE REGIME RULE 60/40, +1m ENTRY ===")
    if base.empty:
        print("No rows")
    else:
        print(base.to_string(index=False))

    print("\n=== IMPORTANT ===")
    print("This is exploratory. The current-active symbol universe has survivorship bias.")
    print("No regime threshold was optimized on trade outcomes; 55/45, 60/40, 65/35 are fixed sensitivity rules.")
    print("Final confirmation should use a frozen rule on a separate untouched period / forward data.")
    print(f"[DONE] signals={len(signals)} trades={len(trades)} breadth_buckets={len(breadth)}")


if __name__ == "__main__":
    main()
