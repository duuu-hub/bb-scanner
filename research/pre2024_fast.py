from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
from research.lsob_causal import run_backtest_causal
from research.vendor.lsob_reference import Config, compute_metrics

ROOT=Path("market_data_store/bitget/15m")
OUT=Path("research/results/pre2024_fast")
CUTOFF=pd.Timestamp("2024-09-23T00:00:00Z")

def load(sym):
    fs=[]
    for p in sorted((ROOT/sym).glob("*.csv")):
        x=pd.read_csv(p)
        if "timestamp_ms" in x:
            x["Timestamp"]=pd.to_datetime(x["timestamp_ms"],unit="ms",utc=True)
        else:
            x["Timestamp"]=pd.to_datetime(x["datetime_utc"],utc=True)
        x=x.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","base_volume":"Volume"})
        if "Volume" not in x: x["Volume"]=0.0
        fs.append(x[["Timestamp","Open","High","Low","Close","Volume"]])
    d=pd.concat(fs,ignore_index=True).drop_duplicates("Timestamp").sort_values("Timestamp")
    for c in ["Open","High","Low","Close","Volume"]: d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d.dropna(subset=["Open","High","Low","Close"])
    h=d.set_index("Timestamp").resample("1h",label="left",closed="left").agg(
        Open=("Open","first"),High=("High","max"),Low=("Low","min"),Close=("Close","last"),Volume=("Volume","sum")
    ).dropna().reset_index()
    return h[h["Timestamp"]<CUTOFF].reset_index(drop=True)

def cfg(n):
    return Config(csv_path="unused",swing_left=3,swing_right=3,sweep_buffer_pct=.0005,sl_buffer_pct=.0005,
        stop_mode="zone",displacement_bars=n,expiry_bars=24,max_swing_age=100,rrr=2.0,risk_pct=2.0,
        initial_equity=1000.,slippage_pct=.0005,leverage=10.,maint_margin_frac=.0125,
        maker_fee=.00015,taker_fee=.00045,entry_is_taker=False)

def side(trades,s):
    x=[t for t in trades if t.side==s]
    if not x:return 0,np.nan,np.nan
    p=np.array([t.pnl for t in x]); gp=p[p>0].sum(); gl=abs(p[p<=0].sum())
    return len(x),float(gp/gl) if gl else np.inf,float(np.mean([t.r_multiple for t in x]))

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows=[]
    for sym in ["BTCUSDT","ETHUSDT"]:
        df=load(sym)
        for n in [3,4]:
            c=cfg(n); tr,eq,inv=run_backtest_causal(df,c); m=compute_metrics(tr,eq,c)
            ln,lpf,ler=side(tr,"long"); sn,spf,ser=side(tr,"short")
            rows.append(dict(symbol=sym,d=n,start=df.Timestamp.min(),end=df.Timestamp.max(),candles=len(df),
                trades=m.get("trades",0),pf=m.get("profit_factor",np.nan),expectancy_r=m.get("expectancy_r",np.nan),
                return_pct=m.get("total_return_pct",np.nan),max_dd_pct=m.get("max_drawdown_pct",np.nan),
                long_trades=ln,long_pf=lpf,long_expectancy_r=ler,short_trades=sn,short_pf=spf,short_expectancy_r=ser))
            print(rows[-1],flush=True)
    pd.DataFrame(rows).to_csv(OUT/"results.csv",index=False)

if __name__=="__main__": main()
