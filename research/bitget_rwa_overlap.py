from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from market_data.universe_collector import BitgetClient
from research.lsob_causal import run_backtest_causal
from research.vendor.lsob_reference import Config, compute_metrics

OUT = Path("research/results/bitget_rwa_overlap")
PAIRS = {
    "SP500USDT": "ES=F",
    "NDX100USDT": "NQ=F",
}
START = pd.Timestamp("2024-09-23T00:00:00Z")
END = pd.Timestamp("2026-09-22T23:45:00Z")


def bitget_1h(symbol: str) -> pd.DataFrame:
    """Fetch Bitget 1H directly to avoid unnecessary 15m backfill work."""
    client = BitgetClient()
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    interval_ms = 60 * 60 * 1000
    page_span_ms = 199 * interval_ms
    cursor = start_ms
    records = {}

    while cursor <= end_ms:
        logical_end = min(end_ms, cursor + page_span_ms)
        raw = client._get(
            "/api/v2/mix/market/history-candles",
            {
                "symbol": symbol,
                "productType": "usdt-futures",
                "granularity": "1H",
                "startTime": str(cursor),
                "endTime": str(logical_end + interval_ms),
                "limit": "200",
            },
        )
        for item in raw:
            if len(item) < 7:
                continue
            ts = int(item[0])
            if start_ms <= ts <= end_ms:
                records[ts] = item
        cursor = logical_end + interval_ms

    if not records:
        return pd.DataFrame()

    rows = [records[ts] for ts in sorted(records)]
    return pd.DataFrame(
        {
            "Timestamp": [pd.Timestamp(int(x[0]), unit="ms", tz="UTC") for x in rows],
            "Open": [float(x[1]) for x in rows],
            "High": [float(x[2]) for x in rows],
            "Low": [float(x[3]) for x in rows],
            "Close": [float(x[4]) for x in rows],
            "Volume": [float(x[5]) for x in rows],
        }
    )


def yahoo_1h(symbol: str) -> pd.DataFrame:
    raw = yf.Ticker(symbol).history(
        start=START.to_pydatetime(),
        end=(END + pd.Timedelta(days=1)).to_pydatetime(),
        interval="1h",
        auto_adjust=False,
        prepost=True,
        actions=False,
        repair=False,
    )
    if raw.empty:
        raw = yf.Ticker(symbol).history(
            period="2y",
            interval="1h",
            auto_adjust=False,
            prepost=True,
            actions=False,
            repair=False,
        )
    if raw.empty:
        return pd.DataFrame()
    raw = raw.reset_index()
    dt_col = "Datetime" if "Datetime" in raw.columns else "Date"
    out = pd.DataFrame(
        {
            "Timestamp": pd.to_datetime(raw[dt_col], utc=True),
            "Open": pd.to_numeric(raw["Open"], errors="coerce"),
            "High": pd.to_numeric(raw["High"], errors="coerce"),
            "Low": pd.to_numeric(raw["Low"], errors="coerce"),
            "Close": pd.to_numeric(raw["Close"], errors="coerce"),
            "Volume": pd.to_numeric(raw.get("Volume", 0), errors="coerce").fillna(0),
        }
    ).dropna(subset=["Open", "High", "Low", "Close"])
    return out.sort_values("Timestamp").drop_duplicates("Timestamp").reset_index(drop=True)


def config(d: int, cost_mode: str) -> Config:
    if cost_mode == "zero":
        slip = maker = taker = 0.0
    elif cost_mode == "bitget_proxy":
        slip = 0.0005
        maker = 0.00015
        taker = 0.00045
    else:
        raise ValueError(cost_mode)
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
        slippage_pct=slip,
        leverage=10.0,
        maint_margin_frac=0.0125,
        maker_fee=maker,
        taker_fee=taker,
        entry_is_taker=False,
    )


def side_stats(trades, side):
    xs = [t for t in trades if t.side == side]
    if not xs:
        return 0, np.nan, np.nan
    pnl = np.array([t.pnl for t in xs], float)
    gp = pnl[pnl > 0].sum()
    gl = abs(pnl[pnl <= 0].sum())
    return (
        len(xs),
        float(gp / gl) if gl > 0 else np.inf,
        float(np.mean([t.r_multiple for t in xs])),
    )


def backtest_row(pair, source, df, d, cost_mode):
    cfg = config(d, cost_mode)
    trades, equity, inval = run_backtest_causal(df, cfg)
    m = compute_metrics(trades, equity, cfg)
    ln, lpf, ler = side_stats(trades, "long")
    sn, spf, ser = side_stats(trades, "short")
    return {
        "pair": pair,
        "source": source,
        "d": d,
        "cost_mode": cost_mode,
        "candles": len(df),
        "trades": m.get("trades", 0),
        "pf": m.get("profit_factor", np.nan),
        "expectancy_r": m.get("expectancy_r", np.nan),
        "return_pct": m.get("total_return_pct", np.nan),
        "max_dd_pct": m.get("max_drawdown_pct", np.nan),
        "long_trades": ln,
        "long_pf": lpf,
        "long_expectancy_r": ler,
        "short_trades": sn,
        "short_pf": spf,
        "short_expectancy_r": ser,
    }, trades


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    contracts = BitgetClient().active_usdt_perpetuals()
    meta = {str(x.get("symbol")): x for x in contracts}

    coverage_rows = []
    metric_rows = []
    signal_rows = []
    similarity_rows = []

    for bg_symbol, yf_symbol in PAIRS.items():
        if bg_symbol not in meta:
            raise RuntimeError(f"{bg_symbol} not active on Bitget")
        print(f"[PAIR] {bg_symbol} vs {yf_symbol}", flush=True)

        bg1 = bitget_1h(bg_symbol)
        if bg1.empty:
            raise RuntimeError(f"No Bitget history for {bg_symbol}")
        yf1 = yahoo_1h(yf_symbol)
        if yf1.empty:
            raise RuntimeError(f"No Yahoo history for {yf_symbol}")

        common = sorted(set(bg1["Timestamp"]) & set(yf1["Timestamp"]))
        if len(common) < 100:
            raise RuntimeError(f"Too few common 1H bars for {bg_symbol}: {len(common)}")

        bgc = bg1.loc[bg1["Timestamp"].isin(common)].sort_values("Timestamp").reset_index(drop=True)
        yfc = yf1.loc[yf1["Timestamp"].isin(common)].sort_values("Timestamp").reset_index(drop=True)

        if not bgc["Timestamp"].equals(yfc["Timestamp"]):
            raise RuntimeError(f"timestamp alignment failed for {bg_symbol}")

        pair = f"{bg_symbol}__{yf_symbol}"
        coverage_rows.append(
            {
                "pair": pair,
                "bitget_symbol": bg_symbol,
                "tradfi_symbol": yf_symbol,
                "bitget_contract_isRwa": meta[bg_symbol].get("isRwa"),
                "bitget_launchTime": meta[bg_symbol].get("launchTime"),
                "bitget_raw_1h_rows": len(bg1),
                "bitget_1h_rows": len(bg1),
                "tradfi_1h_rows": len(yf1),
                "common_1h_rows": len(common),
                "common_start": common[0],
                "common_end": common[-1],
            }
        )

        bg_ret = bgc["Close"].pct_change()
        yf_ret = yfc["Close"].pct_change()
        mask = bg_ret.notna() & yf_ret.notna()
        rb = bg_ret[mask].reset_index(drop=True)
        ry = yf_ret[mask].reset_index(drop=True)
        level_bg = bgc["Close"] / bgc["Close"].iloc[0]
        level_yf = yfc["Close"] / yfc["Close"].iloc[0]

        similarity_rows.append(
            {
                "pair": pair,
                "common_1h_rows": len(common),
                "return_corr_pearson": float(rb.corr(ry)),
                "return_corr_spearman": float(rb.rank().corr(ry.rank())),
                "normalized_level_corr": float(level_bg.corr(level_yf)),
                "median_abs_return_diff_bp": float((rb - ry).abs().median() * 10000.0),
                "p95_abs_return_diff_bp": float((rb - ry).abs().quantile(0.95) * 10000.0),
            }
        )

        pair_trades = {}
        for d in (3, 4):
            for source, df in (("bitget", bgc), ("tradfi", yfc)):
                for cost_mode in (("zero", "bitget_proxy") if source == "bitget" else ("zero",)):
                    row, trades = backtest_row(pair, source, df, d, cost_mode)
                    metric_rows.append(row)
                    pair_trades[(d, source, cost_mode)] = trades

            bg_trades = pair_trades[(d, "bitget", "zero")]
            yf_trades = pair_trades[(d, "tradfi", "zero")]
            bg_keys = {(pd.Timestamp(t.entry_time), t.side) for t in bg_trades}
            yf_keys = {(pd.Timestamp(t.entry_time), t.side) for t in yf_trades}
            inter = bg_keys & yf_keys
            union = bg_keys | yf_keys
            signal_rows.append(
                {
                    "pair": pair,
                    "d": d,
                    "bitget_signals": len(bg_keys),
                    "tradfi_signals": len(yf_keys),
                    "exact_same_hour_side": len(inter),
                    "jaccard": float(len(inter) / len(union)) if union else np.nan,
                    "bitget_match_pct": float(len(inter) / len(bg_keys) * 100.0) if bg_keys else np.nan,
                    "tradfi_match_pct": float(len(inter) / len(yf_keys) * 100.0) if yf_keys else np.nan,
                }
            )

        bgc.to_csv(OUT / f"{bg_symbol}_common_1h.csv.gz", index=False, compression="gzip")
        yfc.to_csv(OUT / f"{yf_symbol.replace('=','_')}_common_1h.csv.gz", index=False, compression="gzip")

    pd.DataFrame(coverage_rows).to_csv(OUT / "coverage.csv", index=False)
    pd.DataFrame(similarity_rows).to_csv(OUT / "price_similarity.csv", index=False)
    pd.DataFrame(metric_rows).to_csv(OUT / "backtest_comparison.csv", index=False)
    pd.DataFrame(signal_rows).to_csv(OUT / "signal_overlap.csv", index=False)

    print("\n=== COVERAGE ===")
    print(pd.DataFrame(coverage_rows).to_string(index=False))
    print("\n=== PRICE SIMILARITY ===")
    print(pd.DataFrame(similarity_rows).to_string(index=False))
    print("\n=== BACKTEST ===")
    print(pd.DataFrame(metric_rows).to_string(index=False))
    print("\n=== SIGNAL OVERLAP ===")
    print(pd.DataFrame(signal_rows).to_string(index=False))


if __name__ == "__main__":
    main()
