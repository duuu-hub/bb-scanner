#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

import research.aoa_3way_oos.backtest_3way as b3
from research.aoa_market_context.analyze_market_context import attach, MARKET_FEATURES

OUT=ROOT/"research"/"aoa_era_validation"/"output"
POLICY=ROOT/"research"/"aoa_market_context"/"aoa_policy_2019h2_2021_compact.csv"
ERA_START=pd.Timestamp("2020-01-01",tz="UTC")
ERA_END=pd.Timestamp("2022-01-01",tz="UTC")
COSTS={"ZERO":0.0,"LOW_RT_004":0.0004/2,"BASE_RT_012":0.0012/2}


def fit_model(events,candles,cutoff):
    tr=events[events["event_dt"]<cutoff].copy()
    if len(tr)<30 or tr["y"].nunique()<2:
        raise RuntimeError(f"insufficient AOA direction training rows before {cutoff}: {len(tr)}")
    pipe=Pipeline([
        ("imp",SimpleImputer(strategy="median")),
        ("sc",StandardScaler()),
        ("lr",LogisticRegression(max_iter=3000,class_weight="balanced",C=0.5)),
    ])
    pipe.fit(tr[MARKET_FEATURES],tr["y"])
    return pipe,tr


def prep():
    btc=b3.load_raw(b3.BTC_DIR)
    eth=b3.load_raw(b3.ETH_DIR)
    candles=b3.build_market_candles(btc)

    pol=pd.read_csv(POLICY)
    ent=pol[pol["a"].isin(["E","FE"])].copy()
    ev=attach(ent,candles,"t")
    ev["event_dt"]=pd.to_datetime(ev["t"],unit="s",utc=True)
    ev["y"]=(ev["d"]=="L").astype(int)

    candles["aoa_p_long"]=np.nan
    model_meta=[]
    for year in [2020,2021]:
        start=pd.Timestamp(f"{year}-01-01",tz="UTC")
        end=pd.Timestamp(f"{year+1}-01-01",tz="UTC")
        model,tr=fit_model(ev,candles,start)
        m=(candles["bar_start"]>=start)&(candles["bar_start"]<end)
        candles.loc[m,"aoa_p_long"]=model.predict_proba(candles.loc[m,MARKET_FEATURES])[:,1]
        coef={k:float(v) for k,v in zip(MARKET_FEATURES,model.named_steps["lr"].coef_[0])}
        model_meta.append({
            "test_year":year,
            "train_end_exclusive":start.isoformat(),
            "train_n":int(len(tr)),
            "train_start":str(tr["event_dt"].min()),
            "train_end":str(tr["event_dt"].max()),
            "top_abs_coefficients":dict(sorted(coef.items(),key=lambda kv:abs(kv[1]),reverse=True)[:8]),
        })

    hsig=b3.hybrid_daily_signal(btc,eth)
    hmap=dict(zip(btc["datetime_utc"],hsig))
    candles["hybrid_dir"]=candles["bar_start"].map(hmap).fillna(0).astype(int)
    candles["datetime_utc"]=candles["bar_start"]

    # Include one prior bar for next-open initialization, and no post-era bars.
    era=candles[(candles["datetime_utc"]>=ERA_START-pd.Timedelta(minutes=15))&
                (candles["datetime_utc"]<ERA_END)].copy().reset_index(drop=True)
    return btc,eth,era,model_meta


def run_strategy(name,df,cost):
    old=b3.OOS_START
    b3.OOS_START=ERA_START
    try:
        return b3.simulate(name,df,cost)
    finally:
        b3.OOS_START=old


def period_metrics(st,start,end):
    c=pd.DataFrame(st.curve).copy()
    c=c[(c["ts"]>=start)&(c["ts"]<end)].copy()
    if c.empty:return {}
    c["r"]=c["equity"].pct_change().fillna(0.0)
    # Rebase within period using returns, not level.
    wealth=float(np.prod(1+c["r"].to_numpy()))
    eq=np.cumprod(1+c["r"].to_numpy())
    dd=eq/np.maximum.accumulate(eq)-1
    sd=float(c["r"].std(ddof=0))
    sharpe=float(c["r"].mean()/sd*np.sqrt(365.25*96)) if sd>0 else math.nan
    return {
        "return_pct":(wealth-1)*100,
        "mdd_pct":float(dd.min()*100),
        "sharpe":sharpe,
        "active_pct":float((c["exposure"]>1e-9).mean()*100),
        "avg_exposure_pct":float(c["exposure"].mean()*100),
    }


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    btc,eth,candles,model_meta=prep()
    rows=[]; years=[]; legs=[]; curves=[]
    for cname,cost in COSTS.items():
        for name in ["AOA_CLONE","AOA_CORE","AOA_HYBRID"]:
            st=run_strategy(name,candles,cost)
            overall=b3.metrics(st)
            rows.append({"strategy":name,"cost":cname,**overall})
            for yr in [2020,2021]:
                p=period_metrics(st,pd.Timestamp(f"{yr}-01-01",tz="UTC"),pd.Timestamp(f"{yr+1}-01-01",tz="UTC"))
                years.append({"strategy":name,"cost":cname,"year":yr,**p})
            for q in st.legs:
                legs.append({"strategy":name,"cost":cname,**q})
            cc=pd.DataFrame(st.curve); cc["strategy"]=name; cc["cost"]=cname; curves.append(cc)

    summary=pd.DataFrame(rows)
    yearly=pd.DataFrame(years)
    pd.DataFrame(legs).to_csv(OUT/"legs.csv",index=False)
    pd.concat(curves,ignore_index=True).to_csv(OUT/"equity_curves.csv.gz",index=False,compression="gzip")
    summary.to_csv(OUT/"summary.csv",index=False)
    yearly.to_csv(OUT/"yearly.csv",index=False)

    # Benchmarks for context.
    b=btc[(btc["datetime_utc"]>=ERA_START)&(btc["datetime_utc"]<ERA_END)]
    bh=(b["close"].iloc[-1]/b["open"].iloc[0]-1)*100
    meta={
        "era_start":ERA_START.isoformat(),
        "era_end_exclusive":ERA_END.isoformat(),
        "validation_type":"expanding historical reconstruction; 2020 direction model uses only pre-2020 AOA decisions, 2021 uses only pre-2021 decisions",
        "model_training":model_meta,
        "btc_buy_hold_pct":float(bh),
        "costs":COSTS,
        "important_limit":"Management thresholds were discovered from 2019-2021 AOA behavior, so this is a behavior-reconstruction/fidelity test, not an untouched alpha OOS. Direction choice is time-causal expanding OOS.",
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print("=== META ===");print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== SUMMARY ===");print(summary.to_string(index=False))
    print("\n=== YEARLY ===");print(yearly.to_string(index=False))


if __name__=="__main__":
    main()
