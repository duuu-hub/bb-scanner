from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from market_data.universe_store import load_range

DEFAULT_ROOT = Path("market_data_store/bitget/research_auto100_15m")
DEFAULT_RESULTS = Path("research/results/lsob_auto100")


def classify_row(row: pd.Series) -> tuple[str, int, int, int]:
    bull = 0
    bear = 0
    for key in ("positive_1h_pct", "positive_4h_pct", "positive_24h_pct"):
        value = row.get(key)
        if pd.isna(value):
            continue
        if value >= 55.0:
            bull += 1
        elif value <= 45.0:
            bear += 1

    for key in ("median_1h_pct", "median_4h_pct", "median_24h_pct"):
        value = row.get(key)
        if pd.isna(value):
            continue
        if value > 0:
            bull += 1
        elif value < 0:
            bear += 1

    score = bull - bear
    regime = "BULLISH" if score >= 3 else "BEARISH" if score <= -3 else "NEUTRAL"
    return regime, score, bull, bear


def build_causal_regime(data: pd.DataFrame) -> pd.DataFrame:
    """Build 15m cross-sectional regime known at the NEXT bar open."""
    x = data.loc[~data["symbol"].isin(["BTCUSDT", "ETHUSDT"])].copy()
    pivot = x.pivot_table(
        index="datetime_utc",
        columns="symbol",
        values="close",
        aggfunc="last",
    ).sort_index()

    metrics = pd.DataFrame(index=pivot.index)
    for label, bars in (("1h", 4), ("4h", 16), ("24h", 96)):
        ret = (pivot / pivot.shift(bars) - 1.0) * 100.0
        metrics[f"positive_{label}_pct"] = (ret > 0).sum(axis=1) / ret.notna().sum(axis=1) * 100.0
        metrics[f"median_{label}_pct"] = ret.median(axis=1, skipna=True)
        metrics[f"sample_{label}"] = ret.notna().sum(axis=1)

    classified = metrics.apply(classify_row, axis=1, result_type="expand")
    classified.columns = ["regime", "score", "bull_votes", "bear_votes"]
    metrics = pd.concat([metrics, classified], axis=1).reset_index()

    # Candle t is only completed at t+15m. Shift availability forward one bar
    # so a trade at time t cannot use t's close.
    metrics["known_at"] = pd.to_datetime(metrics["datetime_utc"], utc=True) + pd.Timedelta(minutes=15)
    return metrics.sort_values("known_at").reset_index(drop=True)


def pooled(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"trades": 0, "win_rate": np.nan, "profit_factor": np.nan, "expectancy_r": np.nan}
    pnl = df["pnl"].to_numpy(float)
    gp = pnl[pnl > 0].sum()
    gl = abs(pnl[pnl <= 0].sum())
    return {
        "trades": int(len(df)),
        "win_rate": float((pnl > 0).mean() * 100.0),
        "profit_factor": float(gp / gl) if gl > 0 else np.inf,
        "expectancy_r": float(df["r_multiple"].mean()),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=str(DEFAULT_ROOT))
    p.add_argument("--results", default=str(DEFAULT_RESULTS))
    p.add_argument("--days", type=int, default=120)
    args = p.parse_args()

    root = Path(args.root)
    results = Path(args.results)
    selection = json.loads((root / "selection.json").read_text(encoding="utf-8"))
    symbols = selection["symbols"]
    end_day = pd.Timestamp(selection["last_day_utc"], tz="UTC")
    start_day = end_day - pd.Timedelta(days=args.days - 1)

    data = load_range(
        start_day.strftime("%Y-%m-%d"),
        end_day.strftime("%Y-%m-%d") + " 23:59:59",
        symbols=symbols,
        root=root,
    )
    regime = build_causal_regime(data)
    regime.to_csv(results / "causal_regime_15m.csv", index=False)

    trades = pd.read_csv(results / "trades.csv")
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
    trades = trades.sort_values("entry_time")

    regime_cols = [
        "known_at", "regime", "score", "bull_votes", "bear_votes",
        "positive_1h_pct", "positive_4h_pct", "positive_24h_pct",
        "median_1h_pct", "median_4h_pct", "median_24h_pct",
    ]
    tagged = pd.merge_asof(
        trades,
        regime[regime_cols].sort_values("known_at"),
        left_on="entry_time",
        right_on="known_at",
        direction="backward",
    )
    tagged.to_csv(results / "trades_with_regime.csv", index=False)

    rows = []
    for variant, vg in tagged.groupby("variant"):
        for regime_name in ("BULLISH", "NEUTRAL", "BEARISH"):
            rg = vg.loc[vg["regime"] == regime_name]
            rows.append({"variant": variant, "regime": regime_name, "side": "ALL", **pooled(rg)})
            for side in ("long", "short"):
                rows.append(
                    {
                        "variant": variant,
                        "regime": regime_name,
                        "side": side.upper(),
                        **pooled(rg.loc[rg["side"] == side]),
                    }
                )

    out = pd.DataFrame(rows)
    out.to_csv(results / "regime_split.csv", index=False)

    distribution = (
        regime.groupby("regime")
        .agg(
            bars=("regime", "size"),
            median_score=("score", "median"),
            median_sample_24h=("sample_24h", "median"),
        )
        .reset_index()
    )
    distribution["pct_bars"] = distribution["bars"] / distribution["bars"].sum() * 100.0
    distribution.to_csv(results / "regime_distribution.csv", index=False)

    print("=== REGIME DISTRIBUTION ===")
    print(distribution.to_string(index=False))
    print("\n=== LSOB BY REGIME ===")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
