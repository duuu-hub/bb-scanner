from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
from backtest import fetch_range, rows_to_df
from research.lsob_causal import run_backtest_causal
from research.vendor.lsob_reference import Config, compute_metrics

OUT=Path("research/results/xrp_long_oos")
START=pd.Timestamp("2019-01-01T00:00:00Z")
END=pd.Timestamp("2026-09-22T23:00:00Z")

def cfg(mult):
    return Config(
        csv_path="unused",swing_left=3,swing_right=3,
        sweep_buffer_pct=.0005,sl_buffer_pct=.0005,
        stop_mode="zone",displacement_bars=3,expiry_bars=24,
        max_swing_age=100,rrr=2.0,risk_pct=2.0,initial_equity=1000.0,
        slippage_pct=.0005*mult,leverage=10.0,maint_margin_frac=.0125,
        maker_fee=.00015*mult,taker_fee=.00045*mult,entry_is_taker=False
    )

def pf(rs):
    rs=np.asarray(rs,float)
    if len(rs)==0:return np.nan
    gp=rs[rs>0].sum(); gl=abs(rs[rs<=0].sum())
    return float(gp/gl) if gl>0 else np.inf

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows=fetch_range("XRPUSDT","1H",60,int(START.timestamp()*1000),int(END.timestamp()*1000))
    raw=rows_to_df(rows,60)
    df=pd.DataFrame({
        "Timestamp":pd.to_datetime(raw["ts"],unit="ms",utc=True),
        "Open":raw["open"],"High":raw["high"],"Low":raw["low"],"Close":raw["close"],
        "Volume":0.0,
    }).dropna().drop_duplicates("Timestamp").sort_values("Timestamp").reset_index(drop=True)

    stress=[]
    base_trades=None
    for m in (1.0,1.5,2.0):
        c=cfg(m)
        trades,equity,inv=run_backtest_causal(df,c)
        met=compute_metrics(trades,equity,c)
        stress.append({
            "cost_mult":m,"start":df.Timestamp.min(),"end":df.Timestamp.max(),"candles":len(df),
            "trades":len(trades),"pf":met.get("profit_factor",np.nan),
            "expectancy_r":met.get("expectancy_r",np.nan),
            "return_pct":met.get("total_return_pct",np.nan),
            "max_dd_pct":met.get("max_drawdown_pct",np.nan),
        })
        if m==1.0: base_trades=trades

    t=pd.DataFrame({
        "entry_time":[pd.Timestamp(x.entry_time) for x in base_trades],
        "r":[x.r_multiple for x in base_trades],
        "side":[x.side for x in base_trades],
    })
    period=[]
    for freq in ("Y","Q"):
        key=t.entry_time.dt.to_period(freq).astype(str)
        for p,g in t.groupby(key):
            rs=g.r.to_numpy(float)
            period.append({"freq":freq,"period":p,"trades":len(g),"pf":pf(rs),"expectancy_r":float(rs.mean())})
    pd.DataFrame(stress).to_csv(OUT/"stress.csv",index=False)
    pd.DataFrame(period).to_csv(OUT/"periods.csv",index=False)
    t.to_csv(OUT/"trades.csv",index=False)
    print("COVERAGE",df.Timestamp.min(),df.Timestamp.max(),len(df),flush=True)
    print(pd.DataFrame(stress).to_string(index=False),flush=True)
    print(pd.DataFrame(period).to_string(index=False),flush=True)

if __name__=="__main__":
    main()
