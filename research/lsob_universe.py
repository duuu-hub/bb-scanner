from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from market_data.universe_store import load_range, resample_ohlcv
from research.vendor.lsob_reference import Config, compute_metrics, run_backtest

DEFAULT_ROOT = Path("market_data_store/bitget/research_auto100_15m")
DEFAULT_OUT = Path("research/results/lsob_auto100")

VARIANTS = {
    "1h_d3_e24": {"timeframe": "1h", "displacement": 3, "expiry": 24},
    "1h_d4_e24": {"timeframe": "1h", "displacement": 4, "expiry": 24},
    "15m_d3_e24": {"timeframe": "15m", "displacement": 3, "expiry": 24},
    "15m_d4_e24": {"timeframe": "15m", "displacement": 4, "expiry": 24},
    "15m_d12_e96": {"timeframe": "15m", "displacement": 12, "expiry": 96},
    "15m_d16_e96": {"timeframe": "15m", "displacement": 16, "expiry": 96},
}


def lsob_frame(group: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    x = group[
        ["symbol", "timestamp_ms", "datetime_utc", "open", "high", "low", "close", "base_volume", "quote_volume"]
    ].copy()
    if timeframe != "15m":
        x = resample_ohlcv(x, timeframe)
    return pd.DataFrame(
        {
            "Timestamp": pd.to_datetime(x["datetime_utc"], utc=True),
            "Open": x["open"].astype(float),
            "High": x["high"].astype(float),
            "Low": x["low"].astype(float),
            "Close": x["close"].astype(float),
            "Volume": x["base_volume"].astype(float),
        }
    ).dropna().reset_index(drop=True)


def make_config(displacement: int, expiry: int, cost_mult: float = 1.0) -> Config:
    return Config(
        csv_path="unused",
        swing_left=3,
        swing_right=3,
        sweep_buffer_pct=0.0005,
        sl_buffer_pct=0.0005,
        stop_mode="zone",
        displacement_bars=displacement,
        expiry_bars=expiry,
        max_swing_age=100,
        rrr=2.0,
        risk_pct=2.0,
        initial_equity=1000.0,
        slippage_pct=0.0005 * cost_mult,
        leverage=10.0,
        maint_margin_frac=0.0125,
        maker_fee=0.00015 * cost_mult,
        taker_fee=0.00045 * cost_mult,
        entry_is_taker=False,
    )


def pooled_metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "trades": 0,
            "win_rate": np.nan,
            "profit_factor": np.nan,
            "expectancy_r": np.nan,
            "long_trades": 0,
            "long_pf": np.nan,
            "long_expectancy_r": np.nan,
            "short_trades": 0,
            "short_pf": np.nan,
            "short_expectancy_r": np.nan,
        }

    def block(x: pd.DataFrame) -> tuple[int, float, float]:
        if x.empty:
            return 0, np.nan, np.nan
        pnl = x["pnl"].to_numpy(float)
        r = x["r_multiple"].to_numpy(float)
        gp = pnl[pnl > 0].sum()
        gl = abs(pnl[pnl <= 0].sum())
        pf = gp / gl if gl > 0 else np.inf
        return len(x), float(pf), float(r.mean())

    pnl = df["pnl"].to_numpy(float)
    gp = pnl[pnl > 0].sum()
    gl = abs(pnl[pnl <= 0].sum())
    n_l, pf_l, er_l = block(df.loc[df["side"] == "long"])
    n_s, pf_s, er_s = block(df.loc[df["side"] == "short"])
    return {
        "trades": len(df),
        "win_rate": float((pnl > 0).mean() * 100.0),
        "profit_factor": float(gp / gl) if gl > 0 else np.inf,
        "expectancy_r": float(df["r_multiple"].mean()),
        "long_trades": n_l,
        "long_pf": pf_l,
        "long_expectancy_r": er_l,
        "short_trades": n_s,
        "short_pf": pf_s,
        "short_expectancy_r": er_s,
    }


def symbol_robustness(symbol_rows: pd.DataFrame) -> dict:
    eligible = symbol_rows.loc[symbol_rows["trades"] >= 5].copy()
    if eligible.empty:
        return {
            "symbols_tested": int(len(symbol_rows)),
            "symbols_ge5_trades": 0,
            "pct_symbols_positive_expectancy": np.nan,
            "pct_symbols_pf_gt_1": np.nan,
            "median_symbol_expectancy_r": np.nan,
            "median_symbol_pf": np.nan,
            "median_symbol_max_dd_pct": np.nan,
        }
    return {
        "symbols_tested": int(len(symbol_rows)),
        "symbols_ge5_trades": int(len(eligible)),
        "pct_symbols_positive_expectancy": float((eligible["expectancy_r"] > 0).mean() * 100.0),
        "pct_symbols_pf_gt_1": float((eligible["profit_factor"] > 1).mean() * 100.0),
        "median_symbol_expectancy_r": float(eligible["expectancy_r"].median()),
        "median_symbol_pf": float(eligible["profit_factor"].median()),
        "median_symbol_max_dd_pct": float(eligible["max_drawdown_pct"].median()),
    }


def run_symbol(symbol: str, group: pd.DataFrame, variant: str, spec: dict) -> tuple[dict, list[dict]]:
    frame = lsob_frame(group, spec["timeframe"])
    min_rows = 100 if spec["timeframe"] == "1h" else 300
    if len(frame) < min_rows:
        return {
            "symbol": symbol,
            "variant": variant,
            "candles": len(frame),
            "trades": 0,
            "profit_factor": np.nan,
            "expectancy_r": np.nan,
            "win_rate": np.nan,
            "total_return_pct": np.nan,
            "max_drawdown_pct": np.nan,
            "longs": 0,
            "shorts": 0,
        }, []

    cfg = make_config(spec["displacement"], spec["expiry"])
    trades, equity, inval = run_backtest(frame, cfg)
    metrics = compute_metrics(trades, equity, cfg)
    row = {
        "symbol": symbol,
        "variant": variant,
        "candles": len(frame),
        "trades": metrics.get("trades", 0),
        "profit_factor": metrics.get("profit_factor", np.nan),
        "expectancy_r": metrics.get("expectancy_r", np.nan),
        "win_rate": metrics.get("win_rate", np.nan),
        "total_return_pct": metrics.get("total_return_pct", np.nan),
        "max_drawdown_pct": metrics.get("max_drawdown_pct", np.nan),
        "longs": metrics.get("longs", 0),
        "shorts": metrics.get("shorts", 0),
        "filled": inval.get("filled", 0),
        "expired": inval.get("C_expired", 0),
    }
    trade_rows = []
    for t in trades:
        trade_rows.append(
            {
                "symbol": symbol,
                "variant": variant,
                "side": t.side,
                "sweep_time": t.sweep_time,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "outcome": t.outcome,
                "pnl": t.pnl,
                "r_multiple": t.r_multiple,
                "fees": t.fees,
            }
        )
    return row, trade_rows


def main() -> None:
    p = argparse.ArgumentParser(description="Broad-universe LSOB study on fixed crypto AUTO sample.")
    p.add_argument("--root", default=str(DEFAULT_ROOT))
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--outdir", default=str(DEFAULT_OUT))
    args = p.parse_args()

    root = Path(args.root)
    selection = json.loads((root / "selection.json").read_text(encoding="utf-8"))
    symbols = selection["symbols"]
    end_day = pd.Timestamp(selection["last_day_utc"], tz="UTC")
    start_day = end_day - pd.Timedelta(days=args.days - 1)
    start = start_day.strftime("%Y-%m-%d")
    end = end_day.strftime("%Y-%m-%d") + " 23:59:59"

    print(f"[LOAD] {len(symbols)} symbols {start}..{end}", flush=True)
    all_data = load_range(start, end, symbols=symbols, root=root)
    if all_data.empty:
        raise RuntimeError("No research candles loaded.")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    coverage = (
        all_data.groupby("symbol")["timestamp_ms"]
        .count()
        .rename("candles_15m")
        .reset_index()
    )
    coverage.to_csv(outdir / "coverage.csv", index=False)

    symbol_rows = []
    trade_rows = []
    groups = {s: g.copy() for s, g in all_data.groupby("symbol", sort=False)}

    total_jobs = len(symbols) * len(VARIANTS)
    done = 0
    for symbol in symbols:
        group = groups.get(symbol)
        if group is None or group.empty:
            for variant in VARIANTS:
                symbol_rows.append(
                    {
                        "symbol": symbol,
                        "variant": variant,
                        "candles": 0,
                        "trades": 0,
                        "profit_factor": np.nan,
                        "expectancy_r": np.nan,
                        "win_rate": np.nan,
                        "total_return_pct": np.nan,
                        "max_drawdown_pct": np.nan,
                        "longs": 0,
                        "shorts": 0,
                    }
                )
            continue

        for variant, spec in VARIANTS.items():
            row, trades = run_symbol(symbol, group, variant, spec)
            symbol_rows.append(row)
            trade_rows.extend(trades)
            done += 1
            if done == 1 or done % 50 == 0 or done == total_jobs:
                print(f"[LSOB] {done}/{total_jobs} {symbol} {variant} trades={row['trades']}", flush=True)

    sdf = pd.DataFrame(symbol_rows)
    tdf = pd.DataFrame(trade_rows)
    sdf.to_csv(outdir / "symbol_metrics.csv", index=False)
    tdf.to_csv(outdir / "trades.csv", index=False)

    summary_rows = []
    temporal_rows = []
    if not tdf.empty:
        tdf["entry_time"] = pd.to_datetime(tdf["entry_time"], utc=True)
        midpoint = start_day + (end_day - start_day) / 2

    for variant, spec in VARIANTS.items():
        trades = tdf.loc[tdf["variant"] == variant].copy() if not tdf.empty else pd.DataFrame()
        pooled = pooled_metrics(trades)
        robust = symbol_robustness(sdf.loc[sdf["variant"] == variant])
        row = {
            "variant": variant,
            **spec,
            **pooled,
            **robust,
        }
        summary_rows.append(row)

        if not trades.empty:
            for label, mask in (
                ("first_half", trades["entry_time"] < midpoint),
                ("second_half", trades["entry_time"] >= midpoint),
            ):
                temporal_rows.append(
                    {
                        "variant": variant,
                        "period": label,
                        **pooled_metrics(trades.loc[mask]),
                    }
                )

    summary = pd.DataFrame(summary_rows)
    temporal = pd.DataFrame(temporal_rows)
    summary.to_csv(outdir / "summary.csv", index=False)
    temporal.to_csv(outdir / "temporal_split.csv", index=False)

    meta = {
        "sample_size": len(symbols),
        "days": args.days,
        "start": start,
        "end": end,
        "source_root": str(root),
        "selection_seed": selection.get("seed"),
        "survivorship_note": selection.get("survivorship_note"),
        "variants": VARIANTS,
    }
    (outdir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n=== SUMMARY ===")
    print(summary.to_string(index=False))
    print("\n=== TEMPORAL SPLIT ===")
    print(temporal.to_string(index=False))


if __name__ == "__main__":
    main()
