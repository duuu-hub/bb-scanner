from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from research.lsob_causal import run_backtest_causal
from research.vendor.lsob_reference import Config, compute_metrics

OUT = Path("research/results/tradfi_smc_smoke")
INSTRUMENTS = {
    "SPY": {"prepost": False, "kind": "ETF"},
    "QQQ": {"prepost": False, "kind": "ETF"},
    "ES=F": {"prepost": True, "kind": "FUTURE"},
    "NQ=F": {"prepost": True, "kind": "FUTURE"},
}


def get_data(symbol: str, interval: str, prepost: bool) -> pd.DataFrame:
    t = yf.Ticker(symbol)
    df = t.history(
        period="60d",
        interval=interval,
        auto_adjust=False,
        prepost=prepost,
        actions=False,
        repair=False,
    )
    if df.empty:
        raise RuntimeError(f"No Yahoo data for {symbol} {interval}")
    df = df.reset_index()
    dt_col = "Datetime" if "Datetime" in df.columns else "Date"
    out = pd.DataFrame({
        "Timestamp": pd.to_datetime(df[dt_col], utc=True),
        "Open": pd.to_numeric(df["Open"], errors="coerce"),
        "High": pd.to_numeric(df["High"], errors="coerce"),
        "Low": pd.to_numeric(df["Low"], errors="coerce"),
        "Close": pd.to_numeric(df["Close"], errors="coerce"),
        "Volume": pd.to_numeric(df.get("Volume", 0), errors="coerce").fillna(0),
    }).dropna(subset=["Open", "High", "Low", "Close"])
    return out.sort_values("Timestamp").drop_duplicates("Timestamp").reset_index(drop=True)


def cfg(d: int, expiry: int, raw_cost: bool) -> Config:
    # Cross-asset smoke first tests structural expectancy without pretending
    # crypto percentage fees are appropriate for CME futures or ETFs.
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
        slippage_pct=0.0 if raw_cost else 0.0001,
        leverage=10.0,
        maint_margin_frac=0.0125,
        maker_fee=0.0 if raw_cost else 0.00005,
        taker_fee=0.0 if raw_cost else 0.00005,
        entry_is_taker=False,
    )


def side_stats(trades, side):
    xs=[t for t in trades if t.side==side]
    if not xs:
        return 0,np.nan,np.nan
    pnl=np.array([t.pnl for t in xs],float)
    gp=pnl[pnl>0].sum()
    gl=abs(pnl[pnl<=0].sum())
    return len(xs), float(gp/gl) if gl>0 else np.inf, float(np.mean([t.r_multiple for t in xs]))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows=[]
    coverage=[]
    for symbol,meta in INSTRUMENTS.items():
        for interval in ("15m","1h"):
            df=get_data(symbol,interval,meta["prepost"])
            coverage.append({
                "symbol":symbol,"kind":meta["kind"],"interval":interval,
                "candles":len(df),"start":df["Timestamp"].min(),"end":df["Timestamp"].max(),
            })
            for d in (3,4):
                expiry=24
                for raw_cost in (True,False):
                    c=cfg(d,expiry,raw_cost)
                    trades,equity,inval=run_backtest_causal(df,c)
                    m=compute_metrics(trades,equity,c)
                    ln,lpf,ler=side_stats(trades,"long")
                    sn,spf,ser=side_stats(trades,"short")
                    rows.append({
                        "symbol":symbol,"kind":meta["kind"],"interval":interval,
                        "d":d,"cost_mode":"zero_cost" if raw_cost else "10bp_slip_plus_fees_proxy",
                        "trades":m.get("trades",0),"pf":m.get("profit_factor",np.nan),
                        "expectancy_r":m.get("expectancy_r",np.nan),
                        "return_pct":m.get("total_return_pct",np.nan),
                        "max_dd_pct":m.get("max_drawdown_pct",np.nan),
                        "long_trades":ln,"long_pf":lpf,"long_expectancy_r":ler,
                        "short_trades":sn,"short_pf":spf,"short_expectancy_r":ser,
                    })
            print(f"[DONE] {symbol} {interval} candles={len(df)}",flush=True)

    rdf=pd.DataFrame(rows)
    cdf=pd.DataFrame(coverage)
    rdf.to_csv(OUT/"results.csv",index=False)
    cdf.to_csv(OUT/"coverage.csv",index=False)
    print("=== COVERAGE ===")
    print(cdf.to_string(index=False))
    print("\n=== RESULTS ===")
    print(rdf.to_string(index=False))


if __name__ == "__main__":
    main()
