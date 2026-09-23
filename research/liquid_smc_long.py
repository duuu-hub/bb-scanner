from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.lsob_causal import run_backtest_causal
from research.vendor.lsob_reference import Config, compute_metrics, run_backtest

ROOT = Path("market_data_store/bitget/liquid_crypto_15m")
OUT = Path("research/results/liquid_smc_long")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]


def load_symbol(symbol: str) -> pd.DataFrame:
    df = pd.read_csv(ROOT / f"{symbol}.csv.gz", compression="gzip")
    df["datetime_utc"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "base_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).sort_values("datetime_utc")


def to_1h(df: pd.DataFrame) -> pd.DataFrame:
    x = df.set_index("datetime_utc").resample("1h", label="left", closed="left").agg(
        Open=("open", "first"),
        High=("high", "max"),
        Low=("low", "min"),
        Close=("close", "last"),
        Volume=("base_volume", "sum"),
    ).dropna()
    return x.reset_index().rename(columns={"datetime_utc": "Timestamp"})


def config(d: int, cost_mult: float = 1.0) -> Config:
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
        slippage_pct=0.0005 * cost_mult,
        leverage=10.0,
        maint_margin_frac=0.0125,
        maker_fee=0.00015 * cost_mult,
        taker_fee=0.00045 * cost_mult,
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


def eval_period(symbol, df, label, start, end, d, engine, runner, cost_mult):
    sub=df.loc[(df["Timestamp"]>=start)&(df["Timestamp"]<end)].reset_index(drop=True)
    cfg=config(d,cost_mult)
    trades,equity,inval=runner(sub,cfg)
    m=compute_metrics(trades,equity,cfg)
    ln,lpf,ler=side_stats(trades,"long")
    sn,spf,ser=side_stats(trades,"short")
    return {
        "symbol":symbol,"period":label,"d":d,"engine":engine,"cost_mult":cost_mult,
        "candles":len(sub),"trades":m.get("trades",0),
        "pf":m.get("profit_factor",np.nan),"expectancy_r":m.get("expectancy_r",np.nan),
        "return_pct":m.get("total_return_pct",np.nan),"max_dd_pct":m.get("max_drawdown_pct",np.nan),
        "long_trades":ln,"long_pf":lpf,"long_expectancy_r":ler,
        "short_trades":sn,"short_pf":spf,"short_expectancy_r":ser,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--causal-only", action="store_true")
    p.add_argument("--fast", action="store_true")
    args=p.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    rows=[]
    for symbol in SYMBOLS:
        df=to_1h(load_symbol(symbol))
        t0=df["Timestamp"].min()
        t1=df["Timestamp"].max()+pd.Timedelta(hours=1)
        mid=t0+(t1-t0)/2
        periods=[("full",t0,t1),("first_half",t0,mid),("second_half",mid,t1)]
        engines=[("causal",run_backtest_causal)] if (args.causal_only or args.fast) else [
            ("reference",run_backtest),("causal",run_backtest_causal)
        ]
        cost_mults=(1.0,) if args.fast else (1.0,2.0)
        for label,start,end in periods:
            for d in (3,4):
                for engine,runner in engines:
                    for cost_mult in cost_mults:
                        rows.append(eval_period(symbol,df,label,start,end,d,engine,runner,cost_mult))
        print(f"[DONE] {symbol}",flush=True)
    out=pd.DataFrame(rows)
    out.to_csv(OUT/"results.csv",index=False)

    full=out.loc[out["period"]=="full"].copy()
    summary=(full.groupby(["d","engine","cost_mult"])
        .agg(
            symbols=("symbol","nunique"),
            total_trades=("trades","sum"),
            median_pf=("pf","median"),
            median_expectancy_r=("expectancy_r","median"),
            pct_symbols_pf_gt_1=("pf",lambda x: float((x>1).mean()*100)),
            median_max_dd_pct=("max_dd_pct","median"),
        ).reset_index())
    summary.to_csv(OUT/"summary.csv",index=False)
    print("=== LONG LIQUID SUMMARY ===")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
