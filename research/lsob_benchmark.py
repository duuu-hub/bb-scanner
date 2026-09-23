from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from vendor.lsob_reference import Config, compute_metrics, run_backtest

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "market_data_store" / "bitget" / "15m"
OUT_ROOT = ROOT / "research" / "results"


def load_bitget_15m(symbol: str) -> pd.DataFrame:
    files = sorted((DATA_ROOT / symbol).glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No stored 15m data for {symbol}: {DATA_ROOT / symbol}")
    parts = []
    usecols = ["timestamp_ms", "open", "high", "low", "close", "base_volume"]
    for path in files:
        parts.append(pd.read_csv(path, usecols=usecols))
    raw = pd.concat(parts, ignore_index=True)
    raw = raw.drop_duplicates("timestamp_ms").sort_values("timestamp_ms")
    return pd.DataFrame({
        "Timestamp": pd.to_datetime(raw["timestamp_ms"], unit="ms", utc=True),
        "Open": raw["open"].astype(float),
        "High": raw["high"].astype(float),
        "Low": raw["low"].astype(float),
        "Close": raw["close"].astype(float),
        "Volume": raw["base_volume"].astype(float),
    }).reset_index(drop=True)


def to_timeframe(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "15m":
        return df.copy()
    if timeframe != "1h":
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    x = df.set_index("Timestamp")
    out = x.resample("1h", label="left", closed="left").agg(
        Open=("Open", "first"),
        High=("High", "max"),
        Low=("Low", "min"),
        Close=("Close", "last"),
        Volume=("Volume", "sum"),
    ).dropna()
    return out.reset_index()


def clip(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    return df.loc[(df["Timestamp"] >= start_ts) & (df["Timestamp"] <= end_ts)].reset_index(drop=True)


def side_stats(trades, side: str) -> dict:
    xs = [t for t in trades if t.side == side]
    if not xs:
        return {"count": 0, "win_rate": np.nan, "expectancy_r": np.nan, "profit_factor": np.nan}
    pnls = np.array([t.pnl for t in xs], dtype=float)
    rs = np.array([t.r_multiple for t in xs], dtype=float)
    gp = pnls[pnls > 0].sum()
    gl = abs(pnls[pnls <= 0].sum())
    return {
        "count": len(xs),
        "win_rate": float((pnls > 0).mean() * 100.0),
        "expectancy_r": float(rs.mean()),
        "profit_factor": float(gp / gl) if gl > 0 else float("inf"),
    }


def run_one(symbol: str, timeframe: str, start: str, end: str, displacement: int) -> tuple[dict, pd.DataFrame]:
    base = load_bitget_15m(symbol)
    df = clip(to_timeframe(base, timeframe), start, end)
    if len(df) < 100:
        raise RuntimeError(f"Too few candles for {symbol} {timeframe}: {len(df)}")

    cfg = Config(
        csv_path="unused",
        swing_left=3,
        swing_right=3,
        sweep_buffer_pct=0.0005,
        sl_buffer_pct=0.0005,
        stop_mode="zone",
        displacement_bars=displacement,
        expiry_bars=24,
        max_swing_age=100,
        rrr=2.0,
        risk_pct=2.0,
        initial_equity=1000.0,
        slippage_pct=0.0005,
        leverage=10.0,
        maint_margin_frac=0.0125,
        maker_fee=0.00015,
        taker_fee=0.00045,
        entry_is_taker=False,
    )
    trades, equity, inval = run_backtest(df, cfg)
    m = compute_metrics(trades, equity, cfg)
    longs = side_stats(trades, "long")
    shorts = side_stats(trades, "short")

    row = {
        "symbol": symbol,
        "timeframe": timeframe,
        "start": str(df["Timestamp"].iloc[0]),
        "end": str(df["Timestamp"].iloc[-1]),
        "candles": len(df),
        "displacement_bars": displacement,
        "expiry_bars": cfg.expiry_bars,
        "rrr": cfg.rrr,
        "trades": m.get("trades", 0),
        "longs": m.get("longs", 0),
        "shorts": m.get("shorts", 0),
        "win_rate": m.get("win_rate", np.nan),
        "profit_factor": m.get("profit_factor", np.nan),
        "expectancy_r": m.get("expectancy_r", np.nan),
        "total_return_pct": m.get("total_return_pct", np.nan),
        "max_drawdown_pct": m.get("max_drawdown_pct", np.nan),
        "long_pf": longs["profit_factor"],
        "long_expectancy_r": longs["expectancy_r"],
        "short_pf": shorts["profit_factor"],
        "short_expectancy_r": shorts["expectancy_r"],
        "filled": inval.get("filled", 0),
        "expired": inval.get("C_expired", 0),
        "new_sweep_invalidated": inval.get("D_new_sweep", 0),
        "zone_broken": inval.get("B_zone_not_respected", 0),
    }

    tdf = pd.DataFrame([{
        "side": t.side,
        "sweep_time": t.sweep_time,
        "entry_time": t.entry_time,
        "entry": t.entry,
        "stop": t.stop,
        "target": t.target,
        "exit_time": t.exit_time,
        "exit_price": t.exit_price,
        "outcome": t.outcome,
        "pnl": t.pnl,
        "r_multiple": t.r_multiple,
        "fees": t.fees,
        "ob_high": t.ob_high,
        "ob_low": t.ob_low,
    } for t in trades])
    return row, tdf


def main() -> None:
    p = argparse.ArgumentParser(description="Run upstream LSOB logic on stored Bitget 15m data.")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--timeframes", default="1h,15m")
    p.add_argument("--start", default="2024-08-24")
    p.add_argument("--end", default="2026-08-24")
    p.add_argument("--displacement-bars", default="3")
    p.add_argument("--outdir", default=str(OUT_ROOT))
    args = p.parse_args()

    timeframes = [x.strip().lower() for x in args.timeframes.split(",") if x.strip()]
    displacements = [int(x.strip()) for x in args.displacement_bars.split(",") if x.strip()]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    rows = []
    for tf in timeframes:
        for d in displacements:
            print(f"[RUN] {args.symbol} {tf} displacement={d} {args.start}..{args.end}", flush=True)
            row, trades = run_one(args.symbol, tf, args.start, args.end, d)
            rows.append(row)
            trade_path = outdir / f"lsob_{args.symbol}_{tf}_d{d}_trades.csv"
            trades.to_csv(trade_path, index=False)
            print(json.dumps(row, indent=2, default=str), flush=True)

    summary = pd.DataFrame(rows)
    summary_path = outdir / "lsob_benchmark_summary.csv"
    summary.to_csv(summary_path, index=False)
    (outdir / "lsob_benchmark_summary.json").write_text(
        json.dumps(rows, indent=2, default=str), encoding="utf-8"
    )
    print("\n=== SUMMARY ===")
    print(summary.to_string(index=False))
    print(f"\nWrote {summary_path}")


if __name__ == "__main__":
    main()
