from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

from research.lsob_causal import run_backtest_causal
from research.vendor.lsob_reference import Config, compute_metrics

OUT=Path("research/results/smc_candidate_filter")
ROOT_MONTHLY=Path("market_data_store/bitget/15m")
ROOT_LIQ=Path("market_data_store/bitget/liquid_crypto_15m")
NDX_PATH=Path("research/results/bitget_rwa_overlap/NDX100USDT_common_1h.csv.gz")

CANDIDATES=[
    ("ETH_D3","ETHUSDT",3,"full_history"),
    ("BTC_D3","BTCUSDT",3,"full_history"),
    ("BTC_D4","BTCUSDT",4,"full_history"),
    ("XRP_D3","XRPUSDT",3,"liquid_730d"),
    ("NDX_D4","NDX100USDT",4,"ndx_common"),
]
COST_MULTS=(1.0,1.25,1.5,2.0)


def load_monthly(sym):
    frames=[]
    for p in sorted((ROOT_MONTHLY/sym).glob("*.csv")):
        x=pd.read_csv(p)
        if "timestamp_ms" in x:
            x["Timestamp"]=pd.to_datetime(x["timestamp_ms"],unit="ms",utc=True)
        else:
            x["Timestamp"]=pd.to_datetime(x["datetime_utc"],utc=True)
        x=x.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","base_volume":"Volume"})
        if "Volume" not in x: x["Volume"]=0.0
        frames.append(x[["Timestamp","Open","High","Low","Close","Volume"]])
    if not frames:
        raise RuntimeError(f"no monthly data {sym}")
    return normalize(pd.concat(frames,ignore_index=True))


def load_liquid(sym):
    x=pd.read_csv(ROOT_LIQ/f"{sym}.csv.gz",compression="gzip")
    x["Timestamp"]=pd.to_datetime(x["timestamp_ms"],unit="ms",utc=True)
    x=x.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","base_volume":"Volume"})
    return normalize(x[["Timestamp","Open","High","Low","Close","Volume"]])


def load_ndx():
    x=pd.read_csv(NDX_PATH,compression="gzip")
    x["Timestamp"]=pd.to_datetime(x["Timestamp"],utc=True)
    return normalize(x[["Timestamp","Open","High","Low","Close","Volume"]],already_1h=True)


def normalize(df,already_1h=False):
    z=df.copy()
    for c in ["Open","High","Low","Close","Volume"]:
        z[c]=pd.to_numeric(z[c],errors="coerce")
    z=z.dropna(subset=["Timestamp","Open","High","Low","Close"])
    z=z.drop_duplicates("Timestamp").sort_values("Timestamp")
    if already_1h:
        return z.reset_index(drop=True)
    return (z.set_index("Timestamp").resample("1h",label="left",closed="left").agg(
        Open=("Open","first"),High=("High","max"),Low=("Low","min"),
        Close=("Close","last"),Volume=("Volume","sum")
    ).dropna().reset_index())


def cfg(d,mult):
    return Config(
        csv_path="unused",swing_left=3,swing_right=3,
        sweep_buffer_pct=.0005,sl_buffer_pct=.0005,
        stop_mode="zone",displacement_bars=d,expiry_bars=24,
        max_swing_age=100,rrr=2.0,risk_pct=2.0,initial_equity=1000.0,
        slippage_pct=.0005*mult,leverage=10.0,maint_margin_frac=.0125,
        maker_fee=.00015*mult,taker_fee=.00045*mult,entry_is_taker=False
    )


def pf_from_r(rs):
    rs=np.asarray(rs,dtype=float)
    if len(rs)==0:return np.nan
    gp=rs[rs>0].sum(); gl=abs(rs[rs<=0].sum())
    return float(gp/gl) if gl>0 else np.inf


def max_loss_streak(rs):
    best=cur=0
    for r in rs:
        if r<0:
            cur+=1; best=max(best,cur)
        else:
            cur=0
    return best


def rolling_stats(rs,n=20):
    rs=np.asarray(rs,dtype=float)
    if len(rs)<n:return np.nan,np.nan
    av=[]; pfs=[]
    for i in range(n,len(rs)+1):
        x=rs[i-n:i]
        av.append(float(np.mean(x)))
        pfs.append(pf_from_r(x))
    finite=[x for x in pfs if np.isfinite(x)]
    return float(min(av)), float(min(finite)) if finite else np.nan


def period_rows(name,trades,freq):
    if not trades:return []
    df=pd.DataFrame({
        "entry_time":[pd.Timestamp(t.entry_time) for t in trades],
        "r":[t.r_multiple for t in trades],
    }).sort_values("entry_time")
    key=df["entry_time"].dt.to_period(freq).astype(str)
    rows=[]
    for p,g in df.groupby(key):
        rs=g["r"].to_numpy(float)
        rows.append({
            "candidate":name,"freq":freq,"period":p,"trades":len(g),
            "pf":pf_from_r(rs),"expectancy_r":float(rs.mean()),
            "wins":int((rs>0).sum()),"losses":int((rs<0).sum()),
        })
    return rows


def summarize_periods(rows,name,freq,min_n):
    z=pd.DataFrame([r for r in rows if r["candidate"]==name and r["freq"]==freq and r["trades"]>=min_n])
    if z.empty:
        return {
            f"{freq}_periods_n":0,f"{freq}_positive_exp_pct":np.nan,
            f"{freq}_pf_gt1_pct":np.nan,f"{freq}_median_exp":np.nan,
            f"{freq}_worst_exp":np.nan,
        }
    return {
        f"{freq}_periods_n":len(z),
        f"{freq}_positive_exp_pct":float((z.expectancy_r>0).mean()*100),
        f"{freq}_pf_gt1_pct":float((z.pf>1).mean()*100),
        f"{freq}_median_exp":float(z.expectancy_r.median()),
        f"{freq}_worst_exp":float(z.expectancy_r.min()),
    }


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    frames={}
    for name,sym,d,src in CANDIDATES:
        if src=="full_history": frames[name]=load_monthly(sym)
        elif src=="liquid_730d": frames[name]=load_liquid(sym)
        else: frames[name]=load_ndx()

    stress_rows=[]; base_trade_rows=[]; period_detail=[]; quality_rows=[]
    for name,sym,d,src in CANDIDATES:
        df=frames[name]
        base_trades=None
        for mult in COST_MULTS:
            c=cfg(d,mult)
            trades,equity,inv=run_backtest_causal(df,c)
            m=compute_metrics(trades,equity,c)
            rs=np.array([t.r_multiple for t in trades],float)
            stress_rows.append({
                "candidate":name,"symbol":sym,"d":d,"source":src,"cost_mult":mult,
                "start":df.Timestamp.min(),"end":df.Timestamp.max(),"candles":len(df),
                "trades":len(trades),"pf":m.get("profit_factor",np.nan),
                "expectancy_r":m.get("expectancy_r",np.nan),
                "return_pct":m.get("total_return_pct",np.nan),
                "max_dd_pct":m.get("max_drawdown_pct",np.nan),
            })
            if mult==1.0:
                base_trades=trades
                for t in trades:
                    base_trade_rows.append({
                        "candidate":name,"symbol":sym,"d":d,
                        "entry_time":t.entry_time,"exit_time":t.exit_time,
                        "side":t.side,"r_multiple":t.r_multiple,"pnl":t.pnl,
                    })

        rs=np.array([t.r_multiple for t in base_trades],float)
        monthly=period_rows(name,base_trades,"M")
        quarterly=period_rows(name,base_trades,"Q")
        yearly=period_rows(name,base_trades,"Y")
        period_detail.extend(monthly+quarterly+yearly)
        roll_avg,roll_pf=rolling_stats(rs,20)

        stress=pd.DataFrame([r for r in stress_rows if r["candidate"]==name]).set_index("cost_mult")
        base=stress.loc[1.0]
        c15=stress.loc[1.5]
        c20=stress.loc[2.0]

        qsum=summarize_periods(period_detail,name,"Q",5)
        msum=summarize_periods(period_detail,name,"M",3)
        ysum=summarize_periods(period_detail,name,"Y",20)

        span_days=(df.Timestamp.max()-df.Timestamp.min()).days
        maxls=max_loss_streak(rs)

        # Strict classification. Recent-regime-only edges are not accepted as
        # unconditional sub-strategies.
        if len(base_trades)<50:
            status="WATCH_ONLY"
            reason="sample_lt_50"
        elif float(base.pf)<1.05 or float(base.expectancy_r)<0.04:
            status="DROP"
            reason="base_edge_too_weak"
        elif float(c15.pf)<1.0:
            status="DROP"
            reason="fails_1.5x_cost"
        elif qsum["Q_periods_n"]>=4 and qsum["Q_positive_exp_pct"]<50:
            status="DROP"
            reason="quarter_stability_below_50pct"
        elif name.startswith("BTC_") and span_days>1500 and float(base.pf)<1.0:
            status="DROP"
            reason="long_history_pf_below_1"
        elif maxls>=10 and float(base.expectancy_r)<0.10:
            status="CONDITIONAL"
            reason="high_loss_streak_for_edge"
        elif float(c20.pf)>=1.0 and qsum.get("Q_positive_exp_pct",0)>=55:
            status="KEEP"
            reason="survives_2x_cost_and_time"
        else:
            status="CONDITIONAL"
            reason="edge_present_but_not_full_stress"

        quality_rows.append({
            "candidate":name,"symbol":sym,"d":d,"source":src,
            "span_days":span_days,"trades":len(base_trades),
            "base_pf":float(base.pf),"base_expectancy_r":float(base.expectancy_r),
            "pf_cost_1_5x":float(c15.pf),"exp_cost_1_5x":float(c15.expectancy_r),
            "pf_cost_2x":float(c20.pf),"exp_cost_2x":float(c20.expectancy_r),
            "max_loss_streak":maxls,"worst_20trade_avg_r":roll_avg,
            "worst_20trade_pf":roll_pf,**msum,**qsum,**ysum,
            "status":status,"reason":reason,
        })
        print("[FILTER]",quality_rows[-1],flush=True)

    pd.DataFrame(stress_rows).to_csv(OUT/"cost_stress.csv",index=False)
    pd.DataFrame(base_trade_rows).to_csv(OUT/"trades.csv",index=False)
    pd.DataFrame(period_detail).to_csv(OUT/"period_detail.csv",index=False)
    q=pd.DataFrame(quality_rows)
    q.to_csv(OUT/"filter_summary.csv",index=False)

    print("\n=== FILTER SUMMARY ===")
    print(q.to_string(index=False))


if __name__=="__main__":
    main()
