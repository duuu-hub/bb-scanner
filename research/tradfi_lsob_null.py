from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from research.lsob_causal import run_backtest_causal
from research.vendor.lsob_reference import Config

OUT=Path("research/results/tradfi_lsob_null")
CASES={"ES=F":3,"NQ=F":4}
REPS=1000
WINDOW_HOURS=24*30


def get_data(symbol):
    raw=yf.Ticker(symbol).history(
        period="2y",interval="1h",auto_adjust=False,
        prepost=True,actions=False,repair=False,
    ).reset_index()
    dtcol="Datetime" if "Datetime" in raw.columns else "Date"
    return pd.DataFrame({
        "Timestamp":pd.to_datetime(raw[dtcol],utc=True),
        "Open":pd.to_numeric(raw["Open"],errors="coerce"),
        "High":pd.to_numeric(raw["High"],errors="coerce"),
        "Low":pd.to_numeric(raw["Low"],errors="coerce"),
        "Close":pd.to_numeric(raw["Close"],errors="coerce"),
        "Volume":pd.to_numeric(raw.get("Volume",0),errors="coerce").fillna(0),
    }).dropna().sort_values("Timestamp").reset_index(drop=True)


def cfg(d):
    return Config(
        csv_path="unused",swing_left=3,swing_right=3,
        sweep_buffer_pct=0.0005,sl_buffer_pct=0.0005,
        stop_mode="zone",displacement_bars=d,expiry_bars=24,
        max_swing_age=100,rrr=2.0,risk_pct=2.0,
        initial_equity=1000.0,slippage_pct=0.0,
        leverage=10.0,maint_margin_frac=0.0125,
        maker_fee=0.0,taker_fee=0.0,entry_is_taker=False,
    )


def simulate_random(df, idx, side, risk_frac, horizon):
    entry=float(df["Close"].iloc[idx])
    if side=="long":
        stop=entry*(1-risk_frac)
        target=entry*(1+2*risk_frac)
    else:
        stop=entry*(1+risk_frac)
        target=entry*(1-2*risk_frac)

    end=min(len(df)-1,idx+horizon)
    for j in range(idx+1,end+1):
        hi=float(df["High"].iloc[j]); lo=float(df["Low"].iloc[j])
        if side=="long":
            hit_stop=lo<=stop; hit_target=hi>=target
        else:
            hit_stop=hi>=stop; hit_target=lo<=target
        if hit_stop:
            return -1.0
        if hit_target:
            return 2.0

    close=float(df["Close"].iloc[end])
    move=(close-entry)/entry
    signed=move if side=="long" else -move
    return float(signed/risk_frac) if risk_frac>0 else 0.0


def matched_candidates(df, trade):
    ts=df["Timestamp"]
    center=trade.entry_time
    lo_time=center-pd.Timedelta(hours=WINDOW_HOURS)
    hi_time=center+pd.Timedelta(hours=WINDOW_HOURS)
    horizon=max(1,trade.exit_idx-trade.entry_idx)
    mask=(ts>=lo_time)&(ts<=hi_time)
    idxs=np.flatnonzero(mask.to_numpy())
    idxs=idxs[(idxs>10)&(idxs+horizon<len(df))]
    # exclude a small neighborhood around the true trade
    idxs=idxs[np.abs(idxs-trade.entry_idx)>6]
    return idxs


def run_case(symbol,d):
    df=get_data(symbol)
    trades,equity,inval=run_backtest_causal(df,cfg(d))
    if not trades:
        raise RuntimeError(f"no trades for {symbol}")

    actual=np.array([t.r_multiple for t in trades],float)
    rng=np.random.default_rng(20260923+d)
    rep_means=[]

    candidate_sets=[]
    for t in trades:
        cand=matched_candidates(df,t)
        if len(cand)==0:
            raise RuntimeError(f"no matched candidates {symbol} {t.entry_time}")
        candidate_sets.append(cand)

    for _ in range(REPS):
        rs=[]
        for t,cand in zip(trades,candidate_sets):
            idx=int(rng.choice(cand))
            risk_frac=abs(t.entry-t.stop)/t.entry
            horizon=max(1,t.exit_idx-t.entry_idx)
            rs.append(simulate_random(df,idx,t.side,risk_frac,horizon))
        rep_means.append(float(np.mean(rs)))

    rep=np.array(rep_means,float)
    actual_mean=float(actual.mean())
    p_one_sided=float((np.sum(rep>=actual_mean)+1)/(len(rep)+1))
    return {
        "symbol":symbol,"d":d,"trades":len(trades),
        "actual_mean_r":actual_mean,
        "null_mean_r":float(rep.mean()),
        "null_median_r":float(np.median(rep)),
        "null_p05_r":float(np.quantile(rep,0.05)),
        "null_p95_r":float(np.quantile(rep,0.95)),
        "actual_minus_null_mean_r":actual_mean-float(rep.mean()),
        "one_sided_p":p_one_sided,
        "actual_long_trades":sum(t.side=="long" for t in trades),
        "actual_short_trades":sum(t.side=="short" for t in trades),
    }, pd.DataFrame({"null_mean_r":rep})


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows=[]
    for symbol,d in CASES.items():
        row,rep=run_case(symbol,d)
        rows.append(row)
        rep.to_csv(OUT/f"{symbol.replace('=','_')}_d{d}_null.csv",index=False)
        print(row,flush=True)
    pd.DataFrame(rows).to_csv(OUT/"summary.csv",index=False)


if __name__=="__main__":
    main()
