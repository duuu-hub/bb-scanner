from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

DATA_ROOT = Path("market_data_store/bitget/15m")
OUT_ROOT = Path("research_output/btc_eth_regime_stage4")
SYMBOLS = ("BTCUSDT", "ETHUSDT")
DISCOVERY_END = pd.Timestamp("2024-01-01", tz="UTC")
VALIDATION_END = pd.Timestamp("2025-07-01", tz="UTC")
BASE_WINDOW = 30
BASE_Q = 0.50
BASE_RT_COST = 0.25


def load_15m(symbol: str) -> pd.DataFrame:
    files = sorted((DATA_ROOT / symbol).glob("*.csv"))
    x = pd.concat([pd.read_csv(p) for p in files], ignore_index=True)
    x["datetime_utc"] = pd.to_datetime(x["datetime_utc"], utc=True)
    for col in ("open", "high", "low", "close"):
        x[col] = pd.to_numeric(x[col], errors="coerce")
    return x.drop_duplicates("timestamp_ms").dropna(subset=["open","high","low","close"]).sort_values("datetime_utc").reset_index(drop=True)


def daily(raw: pd.DataFrame) -> pd.DataFrame:
    return (
        raw.set_index("datetime_utc")
        .resample("1D", label="left", closed="left")
        .agg(open=("open","first"), close=("close","last"))
        .dropna()
        .reset_index()
    )


def panel(raw15: dict[str, pd.DataFrame], window: int, q: float) -> tuple[pd.DataFrame, float]:
    parts = {}
    for symbol in SYMBOLS:
        d = daily(raw15[symbol])[["datetime_utc","close"]].copy()
        c = d["close"]
        d[f"{symbol}_fwd"] = c.pct_change().shift(-1) * 100.0
        d[f"{symbol}_ret"] = c / c.shift(window) - 1.0
        path = c.diff().abs().rolling(window, min_periods=max(10, window//2)).sum()
        d[f"{symbol}_er"] = (c-c.shift(window)).abs() / path.replace(0,np.nan)
        parts[symbol] = d
    x = parts["BTCUSDT"].merge(parts["ETHUSDT"], on="datetime_utc", how="inner")
    x["btc_sign"] = np.sign(x["BTCUSDT_ret"])
    x["eth_sign"] = np.sign(x["ETHUSDT_ret"])
    x["agree"] = (x["btc_sign"] == x["eth_sign"]) & (x["btc_sign"] != 0)
    x["signal"] = x["btc_sign"].where(x["agree"],0.0).fillna(0.0)
    x["market_er"] = (x["BTCUSDT_er"] + x["ETHUSDT_er"]) / 2.0
    threshold = float(x.loc[x["datetime_utc"] < DISCOVERY_END, "market_er"].quantile(q))
    x["position"] = x["signal"].where(x["market_er"] >= threshold,0.0).fillna(0.0)
    x["gross_portfolio"] = x["position"] * (x["BTCUSDT_fwd"].fillna(0)+x["ETHUSDT_fwd"].fillna(0))/2.0
    return x, threshold


def apply_cost(x: pd.DataFrame, roundtrip_cost: float) -> pd.Series:
    one_way = roundtrip_cost/2.0
    change = x["position"].diff().abs().fillna(x["position"].abs())
    return x["gross_portfolio"] - change*one_way


def perf(vals: np.ndarray) -> dict:
    vals = np.asarray(vals,dtype=float)
    vals = vals[np.isfinite(vals)]
    if len(vals)==0:
        return {"days":0,"total_return_pct":math.nan,"cagr_pct":math.nan,"sharpe":math.nan,"mdd_pct":math.nan}
    wealth=np.prod(1+vals/100)
    years=len(vals)/365.25
    curve=np.cumprod(1+vals/100)
    full=np.r_[1.0,curve]
    peaks=np.maximum.accumulate(full)
    dd=(full/peaks-1)*100
    std=np.std(vals)
    return {
        "days":len(vals),
        "total_return_pct":float((wealth-1)*100),
        "cagr_pct":float((wealth**(1/years)-1)*100) if wealth>0 else math.nan,
        "sharpe":float(np.mean(vals)/std*math.sqrt(365.25)) if std>0 else math.nan,
        "mdd_pct":float(dd.min()),
    }


def split_label(ts: pd.Timestamp) -> str:
    if ts < DISCOVERY_END:
        return "DISCOVERY"
    if ts < VALIDATION_END:
        return "VALIDATION"
    return "HOLDOUT"


def episode_table(x: pd.DataFrame, roundtrip_cost: float) -> pd.DataFrame:
    p=x["position"].to_numpy(dtype=float)
    rows=[]
    start=None
    side=0.0
    for i,val in enumerate(p):
        if val != side:
            if side != 0 and start is not None:
                end=i-1
                gross_daily=x["gross_portfolio"].iloc[start:end+1].to_numpy(dtype=float)
                wealth=np.prod(1+gross_daily/100)
                # entry + exit cost, each one-way; exact multiplicative approximation
                wealth *= (1-roundtrip_cost/200.0)**2
                rows.append({
                    "entry_ts":x["datetime_utc"].iloc[start],
                    "exit_ts":x["datetime_utc"].iloc[end],
                    "side":"LONG" if side>0 else "SHORT",
                    "days":end-start+1,
                    "return_pct":(wealth-1)*100,
                })
            start=i if val!=0 else None
            side=val
    if side!=0 and start is not None:
        end=len(x)-2
        if end>=start:
            gross_daily=x["gross_portfolio"].iloc[start:end+1].to_numpy(dtype=float)
            wealth=np.prod(1+gross_daily/100)*(1-roundtrip_cost/200.0)**2
            rows.append({
                "entry_ts":x["datetime_utc"].iloc[start],
                "exit_ts":x["datetime_utc"].iloc[end],
                "side":"LONG" if side>0 else "SHORT",
                "days":end-start+1,
                "return_pct":(wealth-1)*100,
            })
    e=pd.DataFrame(rows)
    if not e.empty:
        e["split"]=e["entry_ts"].map(split_label)
    return e


def episode_bootstrap(e: pd.DataFrame, split: str, reps: int=10000, seed: int=91) -> dict:
    vals=e.loc[e["split"]==split,"return_pct"].to_numpy(dtype=float)
    if len(vals)<2:
        return {"split":split,"episodes":len(vals),"win_pct":math.nan,"median_pct":math.nan,"mean_pct":math.nan,"bootstrap_mean_lo":math.nan,"bootstrap_mean_hi":math.nan}
    rng=np.random.default_rng(seed)
    means=np.empty(reps)
    for i in range(reps):
        means[i]=rng.choice(vals,size=len(vals),replace=True).mean()
    return {
        "split":split,
        "episodes":len(vals),
        "win_pct":float((vals>0).mean()*100),
        "median_pct":float(np.median(vals)),
        "mean_pct":float(vals.mean()),
        "bootstrap_mean_lo":float(np.quantile(means,.025)),
        "bootstrap_mean_hi":float(np.quantile(means,.975)),
    }


def remove_top_episodes(e: pd.DataFrame, split: str) -> list[dict]:
    z=e[e["split"]==split].sort_values("return_pct",ascending=False).copy()
    out=[]
    for k in (0,1,2,3,5):
        vals=z.iloc[k:]["return_pct"].to_numpy(dtype=float)
        # Episode returns are sequential non-overlapping chunks; compound directly.
        wealth=np.prod(1+vals/100) if len(vals) else math.nan
        out.append({
            "split":split,
            "remove_top_episodes":k,
            "episodes_left":len(vals),
            "compounded_episode_return_pct":(wealth-1)*100 if len(vals) else math.nan,
            "mean_episode_pct":vals.mean() if len(vals) else math.nan,
            "win_pct":(vals>0).mean()*100 if len(vals) else math.nan,
        })
    return out


def local_window_map(raw15: dict[str,pd.DataFrame]) -> pd.DataFrame:
    rows=[]
    # Post-hoc local stability map around 30d. It is diagnostic only.
    for window in (24,27,30,33,36,40,45):
        for q in (0.45,0.50,0.55):
            x,thr=panel(raw15,window,q)
            net=apply_cost(x,BASE_RT_COST)
            for split,start,end in (
                ("VALIDATION",DISCOVERY_END,VALIDATION_END),
                ("HOLDOUT",VALIDATION_END,None),
            ):
                mask=x["datetime_utc"]>=start
                if end is not None:
                    mask &= x["datetime_utc"]<end
                p=perf(net[mask].to_numpy(dtype=float))
                rows.append({"window_days":window,"er_quantile":q,"threshold":thr,"split":split,**p})
    return pd.DataFrame(rows)


def halfyear_table(x: pd.DataFrame, net: pd.Series) -> pd.DataFrame:
    z=pd.DataFrame({"date":x["datetime_utc"],"ret":net,"position":x["position"]}).copy()
    z=z[z["date"]>=DISCOVERY_END]
    z["half"]=z["date"].dt.year.astype(str)+"H"+np.where(z["date"].dt.month<=6,"1","2")
    rows=[]
    for half,g in z.groupby("half"):
        p=perf(g["ret"].to_numpy(dtype=float))
        rows.append({
            "half":half,
            **p,
            "active_day_pct":float((g["position"]!=0).mean()*100),
            "long_day_pct":float((g["position"]>0).mean()*100),
            "short_day_pct":float((g["position"]<0).mean()*100),
        })
    return pd.DataFrame(rows)


def cost_stress(x: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    for cost in (0.0,0.12,0.25,0.50,1.00):
        net=apply_cost(x,cost)
        for split,start,end in (
            ("VALIDATION",DISCOVERY_END,VALIDATION_END),
            ("HOLDOUT",VALIDATION_END,None),
        ):
            mask=x["datetime_utc"]>=start
            if end is not None:
                mask &= x["datetime_utc"]<end
            rows.append({"roundtrip_cost_pct":cost,"split":split,**perf(net[mask].to_numpy(dtype=float))})
    return pd.DataFrame(rows)


def main() -> None:
    OUT_ROOT.mkdir(parents=True,exist_ok=True)
    raw15={s:load_15m(s) for s in SYMBOLS}
    x,threshold=panel(raw15,BASE_WINDOW,BASE_Q)
    net=apply_cost(x,BASE_RT_COST)

    episodes=episode_table(x,BASE_RT_COST)
    episodes.to_csv(OUT_ROOT/"episodes.csv",index=False)

    ep_boot=pd.DataFrame([episode_bootstrap(episodes,s) for s in ("DISCOVERY","VALIDATION","HOLDOUT")])
    ep_boot.to_csv(OUT_ROOT/"episode_bootstrap.csv",index=False)

    ep_conc=pd.DataFrame(remove_top_episodes(episodes,"VALIDATION")+remove_top_episodes(episodes,"HOLDOUT"))
    ep_conc.to_csv(OUT_ROOT/"episode_concentration.csv",index=False)

    local=local_window_map(raw15)
    local.to_csv(OUT_ROOT/"local_window_map.csv",index=False)

    halves=halfyear_table(x,net)
    halves.to_csv(OUT_ROOT/"halfyear.csv",index=False)

    costs=cost_stress(x)
    costs.to_csv(OUT_ROOT/"cost_stress.csv",index=False)

    meta={
        "schema_version":1,
        "base_rule":"BTC and ETH 30d return signs agree; trade that direction only while average 30d efficiency ratio >= discovery median",
        "base_er_threshold":threshold,
        "base_roundtrip_cost_pct":BASE_RT_COST,
        "stage4_focus":[
            "episode-level bootstrap/concentration",
            "post-hoc local 24-45d neighborhood stability",
            "half-year temporal stability",
            "0-1% roundtrip cost stress",
        ],
        "warning":"Local window/quantile map is post-hoc robustness only. Do not promote a new best parameter from it.",
    }
    (OUT_ROOT/"meta.json").write_text(json.dumps(meta,indent=2),encoding="utf-8")

    print("=== STAGE4 META ===")
    print(json.dumps(meta,indent=2))
    print("\n=== EPISODE BOOTSTRAP ===")
    print(ep_boot.to_string(index=False))
    print("\n=== EPISODE CONCENTRATION ===")
    print(ep_conc.to_string(index=False))
    print("\n=== HALF-YEAR STABILITY ===")
    print(halves.to_string(index=False))
    print("\n=== COST STRESS ===")
    print(costs.to_string(index=False))
    print("\n=== LOCAL WINDOW MAP VALIDATION ===")
    print(local[local["split"]=="VALIDATION"].to_string(index=False))
    print("\n=== LOCAL WINDOW MAP HOLDOUT ===")
    print(local[local["split"]=="HOLDOUT"].to_string(index=False))


if __name__=="__main__":
    main()
