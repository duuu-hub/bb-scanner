from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from market_data.universe_store import load_range, resample_ohlcv
from research.lsob_causal import run_backtest_causal
from research.vendor.lsob_reference import Config, compute_metrics, run_backtest

ROOT = Path("market_data_store/bitget/research_auto100_15m")
OUT = Path("research/results/liquid_smc")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]
VARIANTS = {
    "1h_d3": (3, 24),
    "1h_d4": (4, 24),
}


def frame(group: pd.DataFrame) -> pd.DataFrame:
    x = resample_ohlcv(group.copy(), "1h")
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


def cfg(d: int, expiry: int) -> Config:
    return Config(
        csv_path="unused",
        swing_left=3,
        swing_right=3,
        sweep_buffer_pct=0.0005,
        sl_buffer_pct=0.0005,
        stop_mode="zone",
        displacement_bars=d,
        expiry_bars=expiry,
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


def side_pf(trades, side: str) -> tuple[int, float, float]:
    xs = [t for t in trades if t.side == side]
    if not xs:
        return 0, np.nan, np.nan
    pnl = np.array([t.pnl for t in xs], float)
    gp = pnl[pnl > 0].sum()
    gl = abs(pnl[pnl <= 0].sum())
    pf = gp / gl if gl > 0 else np.inf
    er = float(np.mean([t.r_multiple for t in xs]))
    return len(xs), float(pf), er


def row(symbol, variant, engine, trades, equity, config):
    m = compute_metrics(trades, equity, config)
    ln, lpf, ler = side_pf(trades, "long")
    sn, spf, ser = side_pf(trades, "short")
    return {
        "symbol": symbol,
        "variant": variant,
        "engine": engine,
        "trades": m.get("trades", 0),
        "profit_factor": m.get("profit_factor", np.nan),
        "expectancy_r": m.get("expectancy_r", np.nan),
        "win_rate": m.get("win_rate", np.nan),
        "return_pct": m.get("total_return_pct", np.nan),
        "max_dd_pct": m.get("max_drawdown_pct", np.nan),
        "long_trades": ln,
        "long_pf": lpf,
        "long_expectancy_r": ler,
        "short_trades": sn,
        "short_pf": spf,
        "short_expectancy_r": ser,
    }


def pooled(rows: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (variant, engine), g in rows.groupby(["variant", "engine"]):
        weights = g["trades"].fillna(0).astype(float)
        n = weights.sum()
        out.append(
            {
                "variant": variant,
                "engine": engine,
                "symbols": len(g),
                "trades": int(n),
                "median_pf": float(g["profit_factor"].median()),
                "median_expectancy_r": float(g["expectancy_r"].median()),
                "pct_symbols_pf_gt_1": float((g["profit_factor"] > 1).mean() * 100),
                "trade_weighted_expectancy_r": (
                    float(np.average(g["expectancy_r"].fillna(0), weights=weights))
                    if n > 0
                    else np.nan
                ),
            }
        )
    return pd.DataFrame(out)


def main():
    selection = json.loads((ROOT / "selection.json").read_text(encoding="utf-8"))
    end = pd.Timestamp(selection["last_day_utc"], tz="UTC")
    start = end - pd.Timedelta(days=119)
    data = load_range(
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d") + " 23:59:59",
        symbols=SYMBOLS,
        root=ROOT,
    )
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    trade_rows = []

    for symbol in SYMBOLS:
        group = data.loc[data["symbol"] == symbol].copy()
        df = frame(group)
        for variant, (d, expiry) in VARIANTS.items():
            config = cfg(d, expiry)
            for engine, runner in (
                ("reference", run_backtest),
                ("causal", run_backtest_causal),
            ):
                trades, equity, inval = runner(df, config)
                rows.append(row(symbol, variant, engine, trades, equity, config))
                for t in trades:
                    trade_rows.append(
                        {
                            "symbol": symbol,
                            "variant": variant,
                            "engine": engine,
                            "side": t.side,
                            "sweep_time": t.sweep_time,
                            "entry_time": t.entry_time,
                            "exit_time": t.exit_time,
                            "pnl": t.pnl,
                            "r_multiple": t.r_multiple,
                        }
                    )

    sdf = pd.DataFrame(rows)
    pdf = pooled(sdf)
    tdf = pd.DataFrame(trade_rows)
    sdf.to_csv(OUT / "causal_audit_symbols.csv", index=False)
    pdf.to_csv(OUT / "causal_audit_summary.csv", index=False)
    tdf.to_csv(OUT / "causal_audit_trades.csv", index=False)

    print("=== SYMBOLS ===")
    print(sdf.to_string(index=False))
    print("\n=== SUMMARY ===")
    print(pdf.to_string(index=False))


if __name__ == "__main__":
    main()
