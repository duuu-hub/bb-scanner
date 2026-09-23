from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

from research.lsob_causal import run_backtest_causal
from research.vendor.lsob_reference import Config, compute_metrics

OUT=Path("research/results/tradfi_smc_long_attempt")
INSTRUMENTS={"SPY":False,"QQQ":False,"ES=F":True,"NQ=F":True}


def cfg(d):
    return Config(
        csv_path="unused", swing_left=3, swing_right=3,
        sweep_buffer_pct=0.0005, sl_buffer_pct=0.0005,
        stop_mode="zone", displacement_bars=d, expiry_bars=24,
        max_swing_age=100, rrr=2.0, risk_pct=2.0,
        initial_equity=1000.0, slippage_pct=0.0,
        leverage=10.0, maint_margin_frac=0.0125,
        maker_fee=0.0, taker_fee=0.0, entry_is_taker=False,
    )


def side_stats(trades,side):
    xs=[t for t in trades if t.side==side]
    if not xs:return 0,np.nan,np.nan
    rs=np.array([t.r_multiple for t in xs],float)
    gp=rs[rs>0].sum(); gl=abs(rs[rs<=0].sum())
    return len(xs), float(gp/gl) if gl>0 else np.inf, float(rs.mean())


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows=[]; coverage=[]
    for symbol,prepost in INSTRUMENTS.items():
        try:
            raw=yf.Ticker(symbol).history(
                period="2y",interval="1h",auto_adjust=False,
                prepost=prepost,actions=False,repair=False,
            )
        except Exception as e:
            coverage.append({"symbol":symbol,"status":"error","error":str(e)})
            continue
        if raw.empty:
            coverage.append({"symbol":symbol,"status":"empty"})
            continue
        raw=raw.reset_index()
        dtcol="Datetime" if "Datetime" in raw.columns else "Date"
        df=pd.DataFrame({
            "Timestamp":pd.to_datetime(raw[dtcol],utc=True),
            "Open":pd.to_numeric(raw["Open"],errors="coerce"),
            "High":pd.to_numeric(raw["High"],errors="coerce"),
            "Low":pd.to_numeric(raw["Low"],errors="coerce"),
            "Close":pd.to_numeric(raw["Close"],errors="coerce"),
            "Volume":pd.to_numeric(raw.get("Volume",0),errors="coerce").fillna(0),
        }).dropna().sort_values("Timestamp").reset_index(drop=True)
        coverage.append({
            "symbol":symbol,"status":"ok","candles":len(df),
            "start":df["Timestamp"].min(),"end":df["Timestamp"].max()
        })
        t0=df["Timestamp"].min(); t1=df["Timestamp"].max()+pd.Timedelta(hours=1)
        mid=t0+(t1-t0)/2
        for period,start,end in [("full",t0,t1),("first_half",t0,mid),("second_half",mid,t1)]:
            sub=df[(df["Timestamp"]>=start)&(df["Timestamp"]<end)].reset_index(drop=True)
            for d in (3,4):
                c=cfg(d)
                trades,equity,inval=run_backtest_causal(sub,c)
                m=compute_metrics(trades,equity,c)
                ln,lpf,ler=side_stats(trades,"long")
                sn,spf,ser=side_stats(trades,"short")
                rows.append({
                    "symbol":symbol,"period":period,"d":d,"candles":len(sub),
                    "trades":m.get("trades",0),"pf":m.get("profit_factor",np.nan),
                    "expectancy_r":m.get("expectancy_r",np.nan),
                    "return_pct":m.get("total_return_pct",np.nan),
                    "max_dd_pct":m.get("max_drawdown_pct",np.nan),
                    "long_trades":ln,"long_pf":lpf,"long_expectancy_r":ler,
                    "short_trades":sn,"short_pf":spf,"short_expectancy_r":ser,
                })
    pd.DataFrame(coverage).to_csv(OUT/"coverage.csv",index=False)
    pd.DataFrame(rows).to_csv(OUT/"results.csv",index=False)
    print(pd.DataFrame(coverage).to_string(index=False))
    print(pd.DataFrame(rows).to_string(index=False))


if __name__=="__main__":
    main()
