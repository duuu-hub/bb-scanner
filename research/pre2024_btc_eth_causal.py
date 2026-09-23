from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

from research.lsob_causal import run_backtest_causal
from research.vendor.lsob_reference import Config, compute_metrics

ROOT=Path("market_data_store/bitget/15m")
OUT=Path("research/results/pre2024_causal")
SYMBOLS=["BTCUSDT","ETHUSDT"]
CUTOFF=pd.Timestamp("2024-09-23T00:00:00Z")


def load_symbol(symbol):
    frames=[]
    for p in sorted((ROOT/symbol).glob("*.csv")):
        x=pd.read_csv(p)
        frames.append(x)
    df=pd.concat(frames,ignore_index=True)
    if "timestamp_ms" in df.columns:
        df["Timestamp"]=pd.to_datetime(df["timestamp_ms"],unit="ms",utc=True)
    elif "datetime_utc" in df.columns:
        df["Timestamp"]=pd.to_datetime(df["datetime_utc"],utc=True)
    else:
        raise RuntimeError(f"timestamp column missing {symbol}: {df.columns.tolist()}")
    ren={}
    for lo,hi in [("open","Open"),("high","High"),("low","Low"),("close","Close"),("base_volume","Volume")]:
        if lo in df.columns: ren[lo]=hi
    df=df.rename(columns=ren)
    for c in ["Open","High","Low","Close","Volume"]:
        if c not in df.columns:
            if c=="Volume": df[c]=0.0
            else: raise RuntimeError(f"{symbol}: missing {c}")
        df[c]=pd.to_numeric(df[c],errors="coerce")
    return df[["Timestamp","Open","High","Low","Close","Volume"]].dropna().drop_duplicates("Timestamp").sort_values("Timestamp").reset_index(drop=True)


def to_1h(df):
    x=df.set_index("Timestamp").resample("1h",label="left",closed="left").agg(
        Open=("Open","first"),High=("High","max"),Low=("Low","min"),
        Close=("Close","last"),Volume=("Volume","sum")
    ).dropna().reset_index()
    return x


def cfg(d):
    return Config(
        csv_path="unused",swing_left=3,swing_right=3,
        sweep_buffer_pct=0.0005,sl_buffer_pct=0.0005,
        stop_mode="zone",displacement_bars=d,expiry_bars=24,
        max_swing_age=100,rrr=2.0,risk_pct=2.0,initial_equity=1000.0,
        slippage_pct=0.0005,leverage=10.0,maint_margin_frac=0.0125,
        maker_fee=0.00015,taker_fee=0.00045,entry_is_taker=False
    )


def side_stats(trades,side):
    xs=[t for t in trades if t.side==side]
    if not xs:return 0,np.nan,np.nan
    pnl=np.array([t.pnl for t in xs],float)
    gp=pnl[pnl>0].sum(); gl=abs(pnl[pnl<=0].sum())
    return len(xs),float(gp/gl) if gl>0 else np.inf,float(np.mean([t.r_multiple for t in xs]))


def eval_block(symbol,df,label,start,end,d):
    sub=df[(df["Timestamp"]>=start)&(df["Timestamp"]<end)].reset_index(drop=True)
    c=cfg(d); trades,equity,inv=run_backtest_causal(sub,c); m=compute_metrics(trades,equity,c)
    ln,lpf,ler=side_stats(trades,"long"); sn,spf,ser=side_stats(trades,"short")
    return dict(symbol=symbol,period=label,start=start,end=end,d=d,candles=len(sub),
        trades=m.get("trades",0),pf=m.get("profit_factor",np.nan),expectancy_r=m.get("expectancy_r",np.nan),
        return_pct=m.get("total_return_pct",np.nan),max_dd_pct=m.get("max_drawdown_pct",np.nan),
        long_trades=ln,long_pf=lpf,long_expectancy_r=ler,
        short_trades=sn,short_pf=spf,short_expectancy_r=ser)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows=[]; coverage=[]
    for symbol in SYMBOLS:
        raw=load_symbol(symbol)
        one=to_1h(raw)
        one=one[one["Timestamp"]<CUTOFF].reset_index(drop=True)
        start=one["Timestamp"].min(); end=CUTOFF
        coverage.append({"symbol":symbol,"first":start,"last":one["Timestamp"].max(),"candles":len(one)})
        periods=[
            ("full_pre2024",start,end),
            ("2019_2021",start,pd.Timestamp("2022-01-01T00:00:00Z")),
            ("2022_2023",pd.Timestamp("2022-01-01T00:00:00Z"),pd.Timestamp("2024-01-01T00:00:00Z")),
            ("2024_pre_cutoff",pd.Timestamp("2024-01-01T00:00:00Z"),end),
        ]
        for label,s,e in periods:
            for d in (3,4):
                rows.append(eval_block(symbol,one,label,s,e,d))
        print(f"[DONE] {symbol} {start}..{one['Timestamp'].max()} candles={len(one)}",flush=True)
    pd.DataFrame(coverage).to_csv(OUT/"coverage.csv",index=False)
    pd.DataFrame(rows).to_csv(OUT/"results.csv",index=False)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__=="__main__":
    main()
