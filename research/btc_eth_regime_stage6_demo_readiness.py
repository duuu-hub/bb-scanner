from __future__ import annotations

import io
import json
import math
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BITGET_ROOT = Path("market_data_store/bitget/15m")
OUT_ROOT = Path("research_output/btc_eth_regime_stage6_demo_readiness")
SYMBOLS = ("BTCUSDT", "ETHUSDT")

# Frozen from Stage3/4. Stage5 showed LONG-only is structurally stronger,
# but LONG-only is post-hoc and therefore treated as a validation candidate.
WINDOW_DAYS = 30
ER_THRESHOLD = 0.193654
BASE_RT_COST_PCT = 0.25

BINANCE_BASE = "https://data.binance.vision/data/spot/monthly/klines"
START_MONTH = pd.Timestamp("2017-08-01", tz="UTC")
END_MONTH = pd.Timestamp("2026-08-01", tz="UTC")

PERIODS = (
    ("BACKWARD_2017_2019", pd.Timestamp("2017-09-15", tz="UTC"), pd.Timestamp("2019-08-10", tz="UTC")),
    ("OLD_2019_2023", pd.Timestamp("2019-08-10", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    ("VALIDATION_2024_2025H1", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-07-01", tz="UTC")),
    ("HOLDOUT_2025H2_2026AUG", pd.Timestamp("2025-07-01", tz="UTC"), pd.Timestamp("2026-09-01", tz="UTC")),
    ("FULL", pd.Timestamp("2017-09-15", tz="UTC"), pd.Timestamp("2026-09-01", tz="UTC")),
)


def month_iter(start: pd.Timestamp, end: pd.Timestamp):
    cur = start
    while cur <= end:
        yield cur
        cur = cur + pd.offsets.MonthBegin(1)


def _to_dt(values: pd.Series) -> pd.Series:
    raw = pd.to_numeric(values, errors="coerce")
    ms = np.where(raw > 1e14, raw / 1000.0, raw)
    return pd.to_datetime(ms, unit="ms", utc=True, errors="coerce")


def download_binance_daily(symbol: str) -> pd.DataFrame:
    cols = [
        "open_time","open","high","low","close","volume","close_time",
        "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
    ]
    session = requests.Session()
    session.headers.update({"User-Agent":"bb-scanner-stage6/1.0"})
    frames = []
    for month in month_iter(START_MONTH, END_MONTH):
        ym = month.strftime("%Y-%m")
        name = f"{symbol}-1d-{ym}.zip"
        url = f"{BINANCE_BASE}/{symbol}/1d/{name}"
        r = session.get(url, timeout=30)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            members = [m for m in zf.namelist() if m.endswith(".csv")]
            if not members:
                continue
            with zf.open(members[0]) as fh:
                frames.append(pd.read_csv(fh, header=None, names=cols))
        time.sleep(0.005)
    if not frames:
        raise RuntimeError(symbol)
    x = pd.concat(frames, ignore_index=True)
    x["datetime_utc"] = _to_dt(x["open_time"])
    for c in ("open","high","low","close"):
        x[c] = pd.to_numeric(x[c], errors="coerce")
    return (
        x.dropna(subset=["datetime_utc","open","high","low","close"])
        .drop_duplicates("datetime_utc")
        .sort_values("datetime_utc")
        [["datetime_utc","open","high","low","close"]]
        .reset_index(drop=True)
    )


def load_bitget_15m(symbol: str) -> pd.DataFrame:
    files = sorted((BITGET_ROOT / symbol).glob("*.csv"))
    if not files:
        raise RuntimeError(f"missing {symbol}")
    x = pd.concat([pd.read_csv(p) for p in files], ignore_index=True)
    x["datetime_utc"] = pd.to_datetime(x["datetime_utc"], utc=True)
    for c in ("open","high","low","close"):
        x[c] = pd.to_numeric(x[c], errors="coerce")
    return (
        x.drop_duplicates("timestamp_ms")
        .dropna(subset=["open","high","low","close"])
        .sort_values("datetime_utc")
        .reset_index(drop=True)
    )


def make_daily_from_15m(x: pd.DataFrame) -> pd.DataFrame:
    return (
        x.set_index("datetime_utc")
        .resample("1D", label="left", closed="left")
        .agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"))
        .dropna()
        .reset_index()
    )


def signal_panel(data: dict[str,pd.DataFrame]) -> pd.DataFrame:
    parts = {}
    for s in SYMBOLS:
        d = data[s][["datetime_utc","open","close"]].copy()
        c = d["close"]
        d[f"{s}_ret30"] = c / c.shift(WINDOW_DAYS) - 1.0
        path = c.diff().abs().rolling(WINDOW_DAYS, min_periods=15).sum()
        d[f"{s}_er30"] = (c-c.shift(WINDOW_DAYS)).abs()/path.replace(0,np.nan)
        d = d.rename(columns={"open":f"{s}_open","close":f"{s}_close"})
        parts[s] = d
    x = parts["BTCUSDT"].merge(parts["ETHUSDT"], on="datetime_utc", how="inner")
    x["btc_sign"] = np.sign(x["BTCUSDT_ret30"])
    x["eth_sign"] = np.sign(x["ETHUSDT_ret30"])
    x["agree"] = (x["btc_sign"] == x["eth_sign"]) & (x["btc_sign"] != 0)
    x["market_er"] = (x["BTCUSDT_er30"]+x["ETHUSDT_er30"])/2.0

    # Candidate after Stage5 decomposition: long only.
    x["candidate_long"] = (
        (x["btc_sign"] > 0)
        & (x["eth_sign"] > 0)
        & (x["market_er"] >= ER_THRESHOLD)
    ).astype(float)

    # Controls / ablations. Not candidates to optimize; only test whether
    # cross-confirmation and persistence add incremental information.
    x["agree_long_no_er"] = ((x["btc_sign"] > 0) & (x["eth_sign"] > 0)).astype(float)
    x["btc_long_only"] = ((x["btc_sign"] > 0) & (x["BTCUSDT_er30"] >= ER_THRESHOLD)).astype(float)
    x["eth_long_only"] = ((x["eth_sign"] > 0) & (x["ETHUSDT_er30"] >= ER_THRESHOLD)).astype(float)
    return x


def perf(vals: np.ndarray) -> dict:
    vals = np.asarray(vals,dtype=float)
    vals = vals[np.isfinite(vals)]
    if len(vals)==0:
        return {"n":0,"return_pct":math.nan,"cagr_pct":math.nan,"sharpe":math.nan,"mdd_pct":math.nan,"avg_pct":math.nan}
    wealth = float(np.prod(1+vals/100))
    years = len(vals)/365.25
    curve = np.cumprod(1+vals/100)
    full = np.r_[1.0,curve]
    peaks = np.maximum.accumulate(full)
    dd = (full/peaks-1)*100
    sd = float(np.std(vals))
    return {
        "n":len(vals),
        "return_pct":(wealth-1)*100,
        "cagr_pct":(wealth**(1/years)-1)*100 if wealth>0 and years>0 else math.nan,
        "sharpe":float(np.mean(vals)/sd*math.sqrt(365.25)) if sd>0 else math.nan,
        "mdd_pct":float(dd.min()),
        "avg_pct":float(np.mean(vals)),
    }


def period_mask(dates: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    return (dates>=start)&(dates<end)


def next_open_daily_returns(panel: pd.DataFrame, position_col: str, rt_cost: float, carry_active_day_pct: float=0.0, delay_days: int=0) -> pd.Series:
    p = panel[position_col].shift(delay_days).fillna(0.0).astype(float)
    # Signal is known after day t close. Position for day t+1 is entered at t+1 open
    # and marked at t+1 close. Crypto is continuous but this removes close-entry idealization.
    btc_intraday = (panel["BTCUSDT_close"]/panel["BTCUSDT_open"]-1.0)*100.0
    eth_intraday = (panel["ETHUSDT_close"]/panel["ETHUSDT_open"]-1.0)*100.0
    # Shift because p[t] applies to next day's open->close.
    p_exec = p.shift(1).fillna(0.0)
    gross = p_exec * (btc_intraday+eth_intraday)/2.0
    change = p_exec.diff().abs().fillna(p_exec.abs())
    cost = change*(rt_cost/2.0)
    carry = p_exec.abs()*carry_active_day_pct
    return gross-cost-carry


def close_to_close_returns(panel: pd.DataFrame, position_col: str, rt_cost: float) -> pd.Series:
    p = panel[position_col].fillna(0.0).astype(float)
    btc_fwd = panel["BTCUSDT_close"].pct_change().shift(-1)*100.0
    eth_fwd = panel["ETHUSDT_close"].pct_change().shift(-1)*100.0
    gross = p*(btc_fwd.fillna(0)+eth_fwd.fillna(0))/2.0
    change=p.diff().abs().fillna(p.abs())
    return gross-change*(rt_cost/2.0)


def summarize_series(panel: pd.DataFrame, name: str, returns: pd.Series) -> list[dict]:
    rows=[]
    for period,start,end in PERIODS:
        g=returns[period_mask(panel["datetime_utc"],start,end)].to_numpy(dtype=float)
        rows.append({"test":name,"period":period,**perf(g)})
    return rows


def rolling_windows(panel: pd.DataFrame, returns: pd.Series, days: int) -> pd.DataFrame:
    rows=[]
    start=panel["datetime_utc"].min()+pd.Timedelta(days=max(WINDOW_DAYS+5,days))
    end=panel["datetime_utc"].max()
    cur=start
    while cur+pd.Timedelta(days=days)<=end:
        stop=cur+pd.Timedelta(days=days)
        mask=(panel["datetime_utc"]>=cur)&(panel["datetime_utc"]<stop)
        vals=returns[mask].to_numpy(dtype=float)
        p=perf(vals)
        rows.append({"window_days":days,"start":cur,"end":stop,**p})
        cur += pd.Timedelta(days=30)
    return pd.DataFrame(rows)


def bootstrap_months(panel: pd.DataFrame, returns: pd.Series, start: pd.Timestamp, end: pd.Timestamp, reps: int=10000, seed: int=771) -> dict:
    z=pd.DataFrame({"date":panel["datetime_utc"],"ret":returns})
    z=z[(z["date"]>=start)&(z["date"]<end)].dropna()
    z["month"]=z["date"].dt.to_period("M").astype(str)
    logs=[]
    for _,g in z.groupby("month"):
        wealth=np.prod(1+g["ret"].to_numpy(dtype=float)/100.0)
        logs.append(math.log(max(float(wealth),1e-12)))
    if len(logs)<2:
        return {"months":len(logs),"lo":math.nan,"hi":math.nan}
    arr=np.asarray(logs)
    rng=np.random.default_rng(seed)
    means=np.empty(reps)
    for i in range(reps):
        means[i]=rng.choice(arr,size=len(arr),replace=True).mean()
    return {"months":len(arr),"lo":float(np.quantile(means,.025)),"hi":float(np.quantile(means,.975))}


def bitget_15m_exec_test(raw15: dict[str,pd.DataFrame], delay_minutes: int, rt_cost: float, carry_active_day_pct: float=0.0) -> pd.Series:
    daily_data={s:make_daily_from_15m(raw15[s]) for s in SYMBOLS}
    sig=signal_panel(daily_data)[["datetime_utc","candidate_long"]].copy()
    sig["effective_ts"]=sig["datetime_utc"]+pd.Timedelta(days=1)+pd.Timedelta(minutes=delay_minutes)

    base=raw15["BTCUSDT"][["datetime_utc","open","close"]].rename(columns={"open":"btc_open","close":"btc_close"})
    eth=raw15["ETHUSDT"][["datetime_utc","open","close"]].rename(columns={"open":"eth_open","close":"eth_close"})
    x=base.merge(eth,on="datetime_utc",how="inner").sort_values("datetime_utc").reset_index(drop=True)

    # Map last known desired position after its execution delay.
    m=pd.merge_asof(
        x,
        sig[["effective_ts","candidate_long"]].sort_values("effective_ts"),
        left_on="datetime_utc",
        right_on="effective_ts",
        direction="backward"
    )
    p=m["candidate_long"].fillna(0.0).astype(float)
    btc_ret=(m["btc_close"]/m["btc_open"]-1.0)*100.0
    eth_ret=(m["eth_close"]/m["eth_open"]-1.0)*100.0
    gross=p*(btc_ret+eth_ret)/2.0
    change=p.diff().abs().fillna(p.abs())
    cost=change*(rt_cost/2.0)
    # approximate adverse carry spread evenly through 96 quarter-hours/day
    carry=p.abs()*(carry_active_day_pct/96.0)
    out=gross-cost-carry
    out.index=m["datetime_utc"]
    return out


def fifteen_min_period_summary(ret: pd.Series, name: str) -> list[dict]:
    rows=[]
    for period,start,end in PERIODS:
        if period=="BACKWARD_2017_2019":
            continue
        vals=ret[(ret.index>=start)&(ret.index<end)].to_numpy(dtype=float)
        # Annualization differs because 15m bars; create direct wealth/MDD and per-day Sharpe approximation.
        vals=vals[np.isfinite(vals)]
        if not len(vals):
            continue
        wealth=np.prod(1+vals/100.0)
        curve=np.cumprod(1+vals/100.0)
        full=np.r_[1.0,curve]
        dd=(full/np.maximum.accumulate(full)-1)*100.0
        days=max((min(end,ret.index.max()+pd.Timedelta(minutes=15))-max(start,ret.index.min())).total_seconds()/86400.0,1.0)
        # aggregate to daily for Sharpe
        daily=(pd.Series(vals,index=ret[(ret.index>=start)&(ret.index<end)].index[:len(vals)])
               .groupby(lambda ts: ts.floor("D"))
               .apply(lambda z:(np.prod(1+z.to_numpy()/100)-1)*100))
        sd=float(daily.std(ddof=0))
        rows.append({
            "test":name,"period":period,"bars":len(vals),
            "return_pct":float((wealth-1)*100),
            "cagr_pct":float((wealth**(365.25/days)-1)*100) if wealth>0 else math.nan,
            "daily_sharpe":float(daily.mean()/sd*math.sqrt(365.25)) if sd>0 else math.nan,
            "mdd_pct":float(dd.min()),
        })
    return rows


def main() -> None:
    OUT_ROOT.mkdir(parents=True,exist_ok=True)

    # Independent-source long history.
    binance={s:download_binance_daily(s) for s in SYMBOLS}
    panel=signal_panel(binance)

    rows=[]
    tests={
        "CANDIDATE_CLOSE_IDEAL": close_to_close_returns(panel,"candidate_long",BASE_RT_COST_PCT),
        "CANDIDATE_NEXT_OPEN": next_open_daily_returns(panel,"candidate_long",BASE_RT_COST_PCT),
        "CANDIDATE_NEXT_OPEN_DELAY1D": next_open_daily_returns(panel,"candidate_long",BASE_RT_COST_PCT,delay_days=1),
        "CANDIDATE_NEXT_OPEN_DELAY2D": next_open_daily_returns(panel,"candidate_long",BASE_RT_COST_PCT,delay_days=2),
        "ABLATE_NO_ER_NEXT_OPEN": next_open_daily_returns(panel,"agree_long_no_er",BASE_RT_COST_PCT),
    }
    for carry in (0.01,0.03,0.05):
        tests[f"CANDIDATE_CARRY_{carry:.2f}_DAY"]=next_open_daily_returns(panel,"candidate_long",BASE_RT_COST_PCT,carry_active_day_pct=carry)
    for cost in (0.50,1.00):
        tests[f"CANDIDATE_RT_COST_{cost:.2f}"]=next_open_daily_returns(panel,"candidate_long",cost)

    for name,ret in tests.items():
        rows.extend(summarize_series(panel,name,ret))
    summary=pd.DataFrame(rows)
    summary.to_csv(OUT_ROOT/"daily_stress_summary.csv",index=False)

    # Rolling window stability on execution-realistic candidate.
    base_exec=tests["CANDIDATE_NEXT_OPEN"]
    rolling=pd.concat([
        rolling_windows(panel,base_exec,180),
        rolling_windows(panel,base_exec,365),
        rolling_windows(panel,base_exec,730),
    ],ignore_index=True)
    rolling.to_csv(OUT_ROOT/"rolling_windows.csv",index=False)

    roll_stats=[]
    for d,g in rolling.groupby("window_days"):
        roll_stats.append({
            "window_days":int(d),
            "windows":len(g),
            "positive_return_pct":float((g["return_pct"]>0).mean()*100.0),
            "positive_sharpe_pct":float((g["sharpe"]>0).mean()*100.0),
            "median_return_pct":float(g["return_pct"].median()),
            "worst_return_pct":float(g["return_pct"].min()),
            "worst_mdd_pct":float(g["mdd_pct"].min()),
            "median_sharpe":float(g["sharpe"].median()),
            "worst_sharpe":float(g["sharpe"].min()),
        })
    pd.DataFrame(roll_stats).to_csv(OUT_ROOT/"rolling_summary.csv",index=False)

    boots=[]
    for period,start,end in PERIODS:
        b=bootstrap_months(panel,base_exec,start,end)
        boots.append({"period":period,**b})
    pd.DataFrame(boots).to_csv(OUT_ROOT/"monthly_bootstrap.csv",index=False)

    # Exact-ish execution delay stress on stored Bitget perpetual 15m bars.
    raw15={s:load_bitget_15m(s) for s in SYMBOLS}
    intraday_rows=[]
    for delay in (0,15,60,240):
        ret=bitget_15m_exec_test(raw15,delay,BASE_RT_COST_PCT,0.0)
        intraday_rows.extend(fifteen_min_period_summary(ret,f"BITGET_DELAY_{delay}M"))
    for carry in (0.01,0.03):
        ret=bitget_15m_exec_test(raw15,15,BASE_RT_COST_PCT,carry)
        intraday_rows.extend(fifteen_min_period_summary(ret,f"BITGET_DELAY15M_CARRY_{carry:.2f}D"))
    intraday=pd.DataFrame(intraday_rows)
    intraday.to_csv(OUT_ROOT/"bitget_15m_execution_stress.csv",index=False)

    meta={
        "candidate":"LONG-only post-hoc decomposition of frozen BTC/ETH 30d agreement + ER rule",
        "frozen_window_days":WINDOW_DAYS,
        "frozen_er_threshold":ER_THRESHOLD,
        "base_roundtrip_cost_pct":BASE_RT_COST_PCT,
        "demo_readiness_tests":[
            "next-day open execution instead of ideal close-to-close",
            "1d and 2d signal delay",
            "0.50% and 1.00% roundtrip cost stress",
            "adverse carry of 0.01/0.03/0.05% per active day",
            "ER ablation",
            "180d/365d/730d rolling windows",
            "monthly bootstrap",
            "Bitget perpetual 15m execution delay 0/15/60/240m",
        ],
        "warning":"LONG-only choice was motivated by Stage5 decomposition and is not pristine OOS. Demo can be justified as forward validation only, not as proven live alpha.",
    }
    (OUT_ROOT/"meta.json").write_text(json.dumps(meta,indent=2),encoding="utf-8")

    print("=== META ===")
    print(json.dumps(meta,indent=2))
    print("\n=== DAILY STRESS ===")
    print(summary.to_string(index=False))
    print("\n=== ROLLING SUMMARY ===")
    print(pd.DataFrame(roll_stats).to_string(index=False))
    print("\n=== MONTHLY BOOTSTRAP ===")
    print(pd.DataFrame(boots).to_string(index=False))
    print("\n=== BITGET 15M EXECUTION STRESS ===")
    print(intraday.to_string(index=False))


if __name__=="__main__":
    main()
