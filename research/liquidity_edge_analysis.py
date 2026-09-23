from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from market_data.universe_store import load_range, resample_ohlcv
from research.lsob_causal import run_backtest_causal
from research.vendor.lsob_reference import Config

ROOT = Path("market_data_store/bitget/research_auto100_15m")
OUT = Path("research/results/liquidity_edge")


def to_1h(group: pd.DataFrame) -> pd.DataFrame:
    x = resample_ohlcv(group.copy(), "1h")
    return pd.DataFrame({
        "Timestamp": pd.to_datetime(x["datetime_utc"], utc=True),
        "Open": x["open"].astype(float),
        "High": x["high"].astype(float),
        "Low": x["low"].astype(float),
        "Close": x["close"].astype(float),
        "Volume": x["base_volume"].astype(float),
    }).dropna().reset_index(drop=True)


def cfg(d: int) -> Config:
    return Config(
        csv_path="unused",
        swing_left=3,
        swing_right=3,
        sweep_buffer_pct=0.0005,
        sl_buffer_pct=0.0005,
        stop_mode="zone",
        displacement_bars=d,
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


def pf_r(rs: np.ndarray) -> float:
    if len(rs) == 0:
        return np.nan
    gp = rs[rs > 0].sum()
    gl = abs(rs[rs <= 0].sum())
    return float(gp / gl) if gl > 0 else np.inf


def main():
    selection = json.loads((ROOT / "selection.json").read_text(encoding="utf-8"))
    symbols = selection["symbols"]
    end = pd.Timestamp(selection["last_day_utc"], tz="UTC")
    start = end - pd.Timedelta(days=119)

    data = load_range(
        start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d") + " 23:59:59",
        symbols=symbols,
        root=ROOT,
    )
    data["quote_volume"] = pd.to_numeric(data["quote_volume"], errors="coerce").fillna(0.0)
    data["day"] = pd.to_datetime(data["datetime_utc"], utc=True).dt.floor("D")

    daily_quote = (
        data.groupby(["symbol", "day"])["quote_volume"]
        .sum()
        .rename("daily_quote_volume")
        .reset_index()
    )
    liq = (
        daily_quote.groupby("symbol")["daily_quote_volume"]
        .median()
        .rename("median_daily_quote_volume")
        .reset_index()
    )
    # Deterministic rank avoids qcut duplicate-edge failures.
    liq["liq_rank"] = liq["median_daily_quote_volume"].rank(method="first")
    liq["liquidity_quintile"] = pd.qcut(
        liq["liq_rank"], 5, labels=[1, 2, 3, 4, 5]
    ).astype(int)

    liq_map = liq.set_index("symbol").to_dict("index")
    symbol_rows = []
    trade_rows = []

    for symbol, group in data.groupby("symbol", sort=False):
        frame = to_1h(group)
        for d in (3, 4):
            trades, equity, inval = run_backtest_causal(frame, cfg(d))
            rs = np.array([t.r_multiple for t in trades], float)
            info = liq_map[symbol]
            symbol_rows.append({
                "symbol": symbol,
                "d": d,
                "median_daily_quote_volume": info["median_daily_quote_volume"],
                "liquidity_quintile": info["liquidity_quintile"],
                "trades": len(trades),
                "pf_r": pf_r(rs),
                "expectancy_r": float(rs.mean()) if len(rs) else np.nan,
            })
            for t in trades:
                trade_rows.append({
                    "symbol": symbol,
                    "d": d,
                    "liquidity_quintile": info["liquidity_quintile"],
                    "median_daily_quote_volume": info["median_daily_quote_volume"],
                    "side": t.side,
                    "r_multiple": t.r_multiple,
                })
        print(f"[DONE] {symbol}", flush=True)

    sdf = pd.DataFrame(symbol_rows)
    tdf = pd.DataFrame(trade_rows)

    qrows = []
    for d in (3, 4):
        for q in range(1, 6):
            sg = sdf.loc[(sdf["d"] == d) & (sdf["liquidity_quintile"] == q)]
            tg = tdf.loc[(tdf["d"] == d) & (tdf["liquidity_quintile"] == q)]
            rs = tg["r_multiple"].to_numpy(float)
            qrows.append({
                "d": d,
                "liquidity_quintile": q,
                "symbols": len(sg),
                "trades": len(tg),
                "pooled_pf_r": pf_r(rs),
                "pooled_expectancy_r": float(rs.mean()) if len(rs) else np.nan,
                "median_symbol_pf_r": float(sg["pf_r"].median()),
                "median_symbol_expectancy_r": float(sg["expectancy_r"].median()),
                "pct_symbols_pf_gt_1": float((sg["pf_r"] > 1).mean() * 100.0),
                "median_daily_quote_volume": float(sg["median_daily_quote_volume"].median()),
            })

    qdf = pd.DataFrame(qrows)
    corr_rows = []
    for d in (3, 4):
        g = sdf.loc[(sdf["d"] == d) & sdf["expectancy_r"].notna()].copy()
        g["log_liquidity"] = np.log10(g["median_daily_quote_volume"].clip(lower=1))
        exp_rank = g["expectancy_r"].rank(method="average")
        liq_rank = g["log_liquidity"].rank(method="average")
        pf_clean = g["pf_r"].replace([np.inf, -np.inf], np.nan)
        pf_mask = pf_clean.notna()
        pf_rank = pf_clean.loc[pf_mask].rank(method="average")
        liq_pf_rank = g.loc[pf_mask, "log_liquidity"].rank(method="average")
        corr_rows.append({
            "d": d,
            "symbols": len(g),
            "spearman_liquidity_vs_expectancy": float(liq_rank.corr(exp_rank)),
            "spearman_liquidity_vs_pf": float(liq_pf_rank.corr(pf_rank)),
        })
    cdf = pd.DataFrame(corr_rows)

    OUT.mkdir(parents=True, exist_ok=True)
    sdf.to_csv(OUT / "symbol_metrics.csv", index=False)
    tdf.to_csv(OUT / "trades.csv", index=False)
    qdf.to_csv(OUT / "quintiles.csv", index=False)
    cdf.to_csv(OUT / "correlations.csv", index=False)

    print("=== LIQUIDITY QUINTILES ===")
    print(qdf.to_string(index=False))
    print("\n=== CORRELATIONS ===")
    print(cdf.to_string(index=False))


if __name__ == "__main__":
    main()
