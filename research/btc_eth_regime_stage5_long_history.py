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
OUT_ROOT = Path("research_output/btc_eth_regime_stage5_long_history")
SYMBOLS = ("BTCUSDT", "ETHUSDT")

# Frozen from Stage3/4. Do not retune on Binance.
WINDOW_DAYS = 30
ER_THRESHOLD = 0.193654
ROUNDTRIP_COST_PCT = 0.25

BINANCE_BASE = "https://data.binance.vision/data/spot/monthly/klines"
START_MONTH = pd.Timestamp("2017-08-01", tz="UTC")
END_MONTH = pd.Timestamp("2026-08-01", tz="UTC")  # last fully archived month at run time

PERIODS = (
    ("BACKWARD_OOS_2017_2019", pd.Timestamp("2017-09-15", tz="UTC"), pd.Timestamp("2019-08-10", tz="UTC")),
    ("OVERLAP_OLD_2019_2023", pd.Timestamp("2019-08-10", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    ("VALIDATION_2024_2025H1", pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-07-01", tz="UTC")),
    ("HOLDOUT_2025H2_2026AUG", pd.Timestamp("2025-07-01", tz="UTC"), pd.Timestamp("2026-09-01", tz="UTC")),
    ("FULL_BINANCE", pd.Timestamp("2017-09-15", tz="UTC"), pd.Timestamp("2026-09-01", tz="UTC")),
)


def month_iter(start: pd.Timestamp, end: pd.Timestamp):
    cur = start
    while cur <= end:
        yield cur
        cur = cur + pd.offsets.MonthBegin(1)


def _timestamp_to_datetime(values: pd.Series) -> pd.Series:
    raw = pd.to_numeric(values, errors="coerce")
    # Binance spot archive switches to microseconds in 2025. Detect row-wise.
    ms = np.where(raw > 1e14, raw / 1000.0, raw)
    return pd.to_datetime(ms, unit="ms", utc=True, errors="coerce")


def download_binance_daily(symbol: str) -> pd.DataFrame:
    cols = [
        "open_time","open","high","low","close","volume","close_time",
        "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
    ]
    frames = []
    misses = []
    session = requests.Session()
    session.headers.update({"User-Agent": "bb-scanner-long-history/1.0"})

    for month in month_iter(START_MONTH, END_MONTH):
        ym = month.strftime("%Y-%m")
        name = f"{symbol}-1d-{ym}.zip"
        url = f"{BINANCE_BASE}/{symbol}/1d/{name}"
        response = session.get(url, timeout=30)
        if response.status_code == 404:
            misses.append(ym)
            continue
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            members = [m for m in zf.namelist() if m.endswith(".csv")]
            if not members:
                continue
            with zf.open(members[0]) as fh:
                frame = pd.read_csv(fh, header=None, names=cols)
                frames.append(frame)
        time.sleep(0.01)

    if not frames:
        raise RuntimeError(f"No Binance archive rows for {symbol}")

    x = pd.concat(frames, ignore_index=True)
    x["datetime_utc"] = _timestamp_to_datetime(x["open_time"])
    for col in ("open","high","low","close","volume","quote_volume"):
        x[col] = pd.to_numeric(x[col], errors="coerce")
    x = (
        x.dropna(subset=["datetime_utc","open","high","low","close"])
        .drop_duplicates("datetime_utc")
        .sort_values("datetime_utc")
        .reset_index(drop=True)
    )
    x.attrs["misses"] = misses
    return x[["datetime_utc","open","high","low","close","volume","quote_volume"]]


def load_bitget_daily(symbol: str) -> pd.DataFrame:
    files = sorted((BITGET_ROOT / symbol).glob("*.csv"))
    frames = [pd.read_csv(p) for p in files]
    x = pd.concat(frames, ignore_index=True)
    x["datetime_utc"] = pd.to_datetime(x["datetime_utc"], utc=True)
    for col in ("open","high","low","close"):
        x[col] = pd.to_numeric(x[col], errors="coerce")
    x = (
        x.drop_duplicates("timestamp_ms")
        .dropna(subset=["open","high","low","close"])
        .sort_values("datetime_utc")
        .set_index("datetime_utc")
        .resample("1D", label="left", closed="left")
        .agg(open=("open","first"), high=("high","max"), low=("low","min"), close=("close","last"))
        .dropna()
        .reset_index()
    )
    return x


def build_panel(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    parts = {}
    for symbol in SYMBOLS:
        d = data[symbol][["datetime_utc","close"]].copy()
        c = d["close"]
        d[f"{symbol}_fwd"] = c.pct_change().shift(-1) * 100.0
        d[f"{symbol}_ret30"] = c / c.shift(WINDOW_DAYS) - 1.0
        path = c.diff().abs().rolling(WINDOW_DAYS, min_periods=15).sum()
        d[f"{symbol}_er30"] = (c - c.shift(WINDOW_DAYS)).abs() / path.replace(0,np.nan)
        parts[symbol] = d

    x = parts["BTCUSDT"].merge(parts["ETHUSDT"], on="datetime_utc", how="inner")
    x["btc_sign"] = np.sign(x["BTCUSDT_ret30"])
    x["eth_sign"] = np.sign(x["ETHUSDT_ret30"])
    x["agree"] = (x["btc_sign"] == x["eth_sign"]) & (x["btc_sign"] != 0)
    x["market_er30"] = (x["BTCUSDT_er30"] + x["ETHUSDT_er30"]) / 2.0
    x["position"] = x["btc_sign"].where(
        x["agree"] & (x["market_er30"] >= ER_THRESHOLD), 0.0
    ).fillna(0.0)

    gross = x["position"] * (x["BTCUSDT_fwd"].fillna(0.0) + x["ETHUSDT_fwd"].fillna(0.0)) / 2.0
    change = x["position"].diff().abs().fillna(x["position"].abs())
    x["net_pct"] = gross - change * (ROUNDTRIP_COST_PCT / 2.0)
    x["gross_pct"] = gross
    x["turnover"] = change
    return x


def perf(vals: np.ndarray) -> dict:
    vals = np.asarray(vals, dtype=float)
    vals = vals[np.isfinite(vals)]
    if len(vals) == 0:
        return dict(days=0,total_return_pct=math.nan,cagr_pct=math.nan,sharpe=math.nan,mdd_pct=math.nan,avg_daily_pct=math.nan)
    wealth = float(np.prod(1.0 + vals / 100.0))
    years = len(vals) / 365.25
    curve = np.cumprod(1.0 + vals / 100.0)
    full = np.r_[1.0, curve]
    peaks = np.maximum.accumulate(full)
    dd = (full / peaks - 1.0) * 100.0
    std = float(np.std(vals, ddof=0))
    return {
        "days": len(vals),
        "total_return_pct": (wealth - 1.0) * 100.0,
        "cagr_pct": (wealth ** (1.0 / years) - 1.0) * 100.0 if wealth > 0 and years > 0 else math.nan,
        "sharpe": float(np.mean(vals) / std * math.sqrt(365.25)) if std > 0 else math.nan,
        "mdd_pct": float(dd.min()),
        "avg_daily_pct": float(np.mean(vals)),
    }


def bootstrap_months(x: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, reps: int=10000, seed: int=55) -> tuple[float,float,int]:
    z = x[(x["datetime_utc"] >= start) & (x["datetime_utc"] < end)][["datetime_utc","net_pct"]].copy()
    z["month"] = z["datetime_utc"].dt.to_period("M").astype(str)
    logs=[]
    for _,g in z.groupby("month"):
        wealth=np.prod(1.0+g["net_pct"].to_numpy(dtype=float)/100.0)
        logs.append(math.log(max(float(wealth),1e-12)))
    if len(logs)<2:
        return math.nan,math.nan,len(logs)
    arr=np.asarray(logs)
    rng=np.random.default_rng(seed)
    means=np.empty(reps)
    for i in range(reps):
        means[i]=rng.choice(arr,size=len(arr),replace=True).mean()
    return float(np.quantile(means,.025)),float(np.quantile(means,.975)),len(arr)


def period_table(panel: pd.DataFrame, source: str) -> pd.DataFrame:
    rows=[]
    for name,start,end in PERIODS:
        g=panel[(panel["datetime_utc"]>=start)&(panel["datetime_utc"]<end)]
        p=perf(g["net_pct"].to_numpy(dtype=float))
        lo,hi,months=bootstrap_months(panel,start,end)
        rows.append({
            "source":source,
            "period":name,
            "start":start.isoformat(),
            "end_exclusive":end.isoformat(),
            **p,
            "months":months,
            "month_bootstrap_mean_log_lo":lo,
            "month_bootstrap_mean_log_hi":hi,
            "active_day_pct":float((g["position"]!=0).mean()*100) if len(g) else math.nan,
            "long_day_pct":float((g["position"]>0).mean()*100) if len(g) else math.nan,
            "short_day_pct":float((g["position"]<0).mean()*100) if len(g) else math.nan,
        })
    return pd.DataFrame(rows)


def yearly_table(panel: pd.DataFrame, source: str) -> pd.DataFrame:
    x=panel.copy()
    x["year"]=x["datetime_utc"].dt.year
    rows=[]
    for year,g in x.groupby("year"):
        p=perf(g["net_pct"].to_numpy(dtype=float))
        rows.append({
            "source":source,
            "year":int(year),
            **p,
            "active_day_pct":float((g["position"]!=0).mean()*100),
            "long_day_pct":float((g["position"]>0).mean()*100),
            "short_day_pct":float((g["position"]<0).mean()*100),
        })
    return pd.DataFrame(rows)


def episode_table(panel: pd.DataFrame, source: str) -> pd.DataFrame:
    p=panel["position"].to_numpy(dtype=float)
    rows=[]
    start_i=None
    side=0.0
    for i,val in enumerate(p):
        if val != side:
            if side != 0 and start_i is not None:
                end_i=i-1
                vals=panel["net_pct"].iloc[start_i:end_i+1].to_numpy(dtype=float)
                wealth=np.prod(1.0+vals/100.0)
                rows.append({
                    "source":source,
                    "entry_ts":panel["datetime_utc"].iloc[start_i],
                    "exit_ts":panel["datetime_utc"].iloc[end_i],
                    "side":"LONG" if side>0 else "SHORT",
                    "days":end_i-start_i+1,
                    "return_pct":(wealth-1.0)*100.0,
                })
            start_i=i if val!=0 else None
            side=val
    return pd.DataFrame(rows)


def backward_episode_stats(episodes: pd.DataFrame) -> dict:
    if episodes.empty:
        return {}
    z=episodes[
        (episodes["entry_ts"]>=pd.Timestamp("2017-09-15",tz="UTC"))
        & (episodes["entry_ts"]<pd.Timestamp("2019-08-10",tz="UTC"))
    ]
    vals=z["return_pct"].to_numpy(dtype=float)
    if len(vals)<2:
        return {"episodes":len(vals)}
    rng=np.random.default_rng(88)
    means=np.empty(10000)
    for i in range(len(means)):
        means[i]=rng.choice(vals,size=len(vals),replace=True).mean()
    return {
        "episodes":len(vals),
        "win_pct":float((vals>0).mean()*100.0),
        "mean_episode_pct":float(vals.mean()),
        "median_episode_pct":float(np.median(vals)),
        "bootstrap_mean_lo":float(np.quantile(means,.025)),
        "bootstrap_mean_hi":float(np.quantile(means,.975)),
    }


def source_overlap_compare(binance: pd.DataFrame, bitget: pd.DataFrame) -> tuple[pd.DataFrame,pd.DataFrame]:
    # Daily strategy output agreement on exact shared dates.
    a=binance[["datetime_utc","position","net_pct","BTCUSDT_fwd","ETHUSDT_fwd"]].copy()
    b=bitget[["datetime_utc","position","net_pct","BTCUSDT_fwd","ETHUSDT_fwd"]].copy()
    z=a.merge(b,on="datetime_utc",suffixes=("_binance","_bitget"))
    z=z[z["datetime_utc"]>=pd.Timestamp("2019-08-10",tz="UTC")].copy()
    z["position_same"]=z["position_binance"]==z["position_bitget"]
    z["both_active"]=(z["position_binance"]!=0)&(z["position_bitget"]!=0)
    z["active_direction_same"]=np.where(
        z["both_active"],
        np.sign(z["position_binance"])==np.sign(z["position_bitget"]),
        np.nan,
    )
    summary=pd.DataFrame([{
        "shared_days":len(z),
        "position_exact_agreement_pct":float(z["position_same"].mean()*100.0),
        "binance_active_day_pct":float((z["position_binance"]!=0).mean()*100.0),
        "bitget_active_day_pct":float((z["position_bitget"]!=0).mean()*100.0),
        "both_active_days":int(z["both_active"].sum()),
        "active_direction_agreement_pct":float(pd.Series(z["active_direction_same"]).dropna().mean()*100.0) if z["both_active"].any() else math.nan,
        "btc_fwd_return_corr":float(z["BTCUSDT_fwd_binance"].corr(z["BTCUSDT_fwd_bitget"])),
        "eth_fwd_return_corr":float(z["ETHUSDT_fwd_binance"].corr(z["ETHUSDT_fwd_bitget"])),
        "strategy_daily_return_corr":float(z["net_pct_binance"].corr(z["net_pct_bitget"])),
    }])
    return summary,z



def direction_decomposition(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for mode in ("BOTH", "LONG_ONLY", "SHORT_ONLY"):
        x = panel.copy()
        if mode == "LONG_ONLY":
            x["position"] = x["position"].where(x["position"] > 0, 0.0)
        elif mode == "SHORT_ONLY":
            x["position"] = x["position"].where(x["position"] < 0, 0.0)

        gross = x["position"] * (x["BTCUSDT_fwd"].fillna(0.0) + x["ETHUSDT_fwd"].fillna(0.0)) / 2.0
        change = x["position"].diff().abs().fillna(x["position"].abs())
        x["mode_net_pct"] = gross - change * (ROUNDTRIP_COST_PCT / 2.0)

        for name, start, end in PERIODS:
            g = x[(x["datetime_utc"] >= start) & (x["datetime_utc"] < end)]
            rows.append({
                "mode": mode,
                "period": name,
                **perf(g["mode_net_pct"].to_numpy(dtype=float)),
                "active_day_pct": float((g["position"] != 0).mean() * 100.0) if len(g) else math.nan,
            })

        x["year"] = x["datetime_utc"].dt.year
        for year, g in x.groupby("year"):
            rows.append({
                "mode": mode,
                "period": f"YEAR_{int(year)}",
                **perf(g["mode_net_pct"].to_numpy(dtype=float)),
                "active_day_pct": float((g["position"] != 0).mean() * 100.0),
            })
    return pd.DataFrame(rows)

def main() -> None:
    OUT_ROOT.mkdir(parents=True,exist_ok=True)

    binance_data={s:download_binance_daily(s) for s in SYMBOLS}
    bitget_data={s:load_bitget_daily(s) for s in SYMBOLS}

    binance_panel=build_panel(binance_data)
    bitget_panel=build_panel(bitget_data)

    binance_panel.to_csv(OUT_ROOT/"binance_daily_strategy.csv",index=False)

    periods=pd.concat([
        period_table(binance_panel,"BINANCE_SPOT"),
        period_table(bitget_panel,"BITGET_PERP"),
    ],ignore_index=True)
    periods.to_csv(OUT_ROOT/"period_summary.csv",index=False)

    yearly=pd.concat([
        yearly_table(binance_panel,"BINANCE_SPOT"),
        yearly_table(bitget_panel,"BITGET_PERP"),
    ],ignore_index=True)
    yearly.to_csv(OUT_ROOT/"yearly_summary.csv",index=False)

    direction = direction_decomposition(binance_panel)
    direction.to_csv(OUT_ROOT/"direction_decomposition.csv",index=False)

    episodes=episode_table(binance_panel,"BINANCE_SPOT")
    episodes.to_csv(OUT_ROOT/"binance_episodes.csv",index=False)
    backward=backward_episode_stats(episodes)

    overlap, overlap_rows=source_overlap_compare(binance_panel,bitget_panel)
    overlap.to_csv(OUT_ROOT/"source_overlap_summary.csv",index=False)
    overlap_rows.to_csv(OUT_ROOT/"source_overlap_daily.csv",index=False)

    meta={
        "schema_version":1,
        "test":"Longer-history independent-source validation of frozen Stage3/4 regime rule",
        "binance_source":"Binance public spot monthly 1d kline archives",
        "binance_requested_start":START_MONTH.isoformat(),
        "binance_requested_end":END_MONTH.isoformat(),
        "binance_actual":{
            s:{
                "start":binance_data[s]["datetime_utc"].min().isoformat(),
                "end":binance_data[s]["datetime_utc"].max().isoformat(),
                "rows":len(binance_data[s]),
                "missing_month_files":binance_data[s].attrs.get("misses",[]),
            } for s in SYMBOLS
        },
        "frozen_rule":{
            "window_days":WINDOW_DAYS,
            "er_threshold":ER_THRESHOLD,
            "roundtrip_cost_pct":ROUNDTRIP_COST_PCT,
            "logic":"BTC and ETH 30d return signs must agree; take common direction only when average 30d efficiency ratio >= frozen threshold.",
        },
        "backward_oos_note":"2017-09-15 to 2019-08-10 predates the Bitget dataset used to discover the rule and is the key backward unseen test.",
        "backward_episode_stats":backward,
    }
    (OUT_ROOT/"meta.json").write_text(json.dumps(meta,indent=2),encoding="utf-8")

    print("=== STAGE5 META ===")
    print(json.dumps(meta,indent=2))
    print("\n=== PERIOD SUMMARY ===")
    print(periods.to_string(index=False))
    print("\n=== BINANCE YEARLY ===")
    print(yearly[yearly["source"]=="BINANCE_SPOT"].to_string(index=False))
    print("\n=== DIRECTION DECOMPOSITION PERIODS ===")
    print(direction[~direction["period"].str.startswith("YEAR_")].to_string(index=False))
    print("\n=== DIRECTION DECOMPOSITION YEARLY ===")
    print(direction[direction["period"].str.startswith("YEAR_")].to_string(index=False))
    print("\n=== BACKWARD OOS EPISODES ===")
    print(json.dumps(backward,indent=2))
    print("\n=== SOURCE OVERLAP ===")
    print(overlap.to_string(index=False))


if __name__=="__main__":
    main()
