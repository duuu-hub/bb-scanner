#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor

import research.aoa_3way_oos.backtest_3way as b3
import research.aoa_era_validation.validate_era as era

OUT=ROOT/"research"/"aoa_fidelity_v2"/"output"
POLICY=ROOT/"research"/"aoa_market_context"/"aoa_policy_2019h2_2021_compact.csv"
EPISODES=ROOT/"research"/"aoa_market_context"/"aoa_episodes_2019h2_2021_compact.csv"
START=pd.Timestamp("2020-01-01",tz="UTC")
END=pd.Timestamp("2022-01-01",tz="UTC")
COSTS={"ZERO":0.0,"LOW_RT_004":0.0004/2}


def fit_behavior_calibration():
    p=pd.read_csv(POLICY)
    p=p[p["t"] < int(pd.Timestamp("2021-01-01",tz="UTC").timestamp())].copy()
    p["year"]=pd.to_datetime(p["t"],unit="s",utc=True).dt.year\n    p=p[p["year"].isin([2019,2020])].copy()
    p["atr"]=pd.to_numeric(p["atr14_pct"],errors="coerce")
    p["fav_atr"]=p["fav"]/p["atr"]
    for h in ["signed_ret1h","signed_ret4h","signed_ret24h"]:
        p[h+"_atr"]=p[h]/p["atr"]

    adds=p[p["a"]=="A"]
    adv=adds[adds["fav"]<0]
    fav=adds[adds["fav"]>0]

    adv_first=float((-adv["fav_atr"]).median())
    fav_first=float(fav["fav_atr"].median())

    # Positive flip threshold: fitted only on 2019-2020 actual flip events.
    fx=p[(p["a"]=="FX") & (p["fav"]>0)].dropna(subset=["fav_atr","signed_ret1h_atr","signed_ret4h_atr","signed_ret24h_atr"]).copy()
    X=fx[["signed_ret4h_atr","signed_ret24h_atr","signed_ret1h_atr"]].clip(-10,10)
    y=fx["fav_atr"].clip(-5,8)
    hub=HuberRegressor().fit(X,y)

    # Negative flip: use robust 2019-2020 empirical location and holding-time median.
    nfx=p[(p["a"]=="FX") & (p["fav"]<0)].copy()
    loss_flip_atr=float((-nfx["fav_atr"]).median())
    loss_hold_bars=max(8,int(round(float(nfx["tb"].median())/900.0)))

    # Reduction threshold in ATR units, using only profitable reductions.
    red=p[(p["a"]=="R") & (p["fav"]>0)].copy()
    reduce_atr=float(red["fav_atr"].median())

    return {
        "adv_first_atr":adv_first,
        "fav_first_atr":fav_first,
        "reduce_atr":reduce_atr,
        "loss_flip_atr":loss_flip_atr,
        "loss_hold_bars":loss_hold_bars,
        "flip_model_intercept":float(hub.intercept_),
        "flip_model_coef":{k:float(v) for k,v in zip(X.columns,hub.coef_)},
        "_flip_model":hub,
    }


def dynamic_flip_atr(row, side, cal):
    atr=max(float(row["atr14_pct"]),1e-9)
    x=np.array([[
        float(side*row["ret4h"]/atr),
        float(side*row["ret24h"]/atr),
        float(side*row["ret1h"]/atr),
    ]])
    pred=float(cal["_flip_model"].predict(np.clip(x,-10,10))[0])
    return float(np.clip(pred,0.9,4.5))


def decide_adaptive(st:b3.St,row,cal):
    if st.side==0:
        return {"type":"ENTER","dir":1 if row["aoa_p_long"]>=0.5 else -1}

    px=float(row["close"])
    atr=max(float(row["atr14_pct"]),1e-9)
    pnl_bps=st.pnl_bps(px)
    pnl_atr=pnl_bps/atr
    s1=st.side*float(row["ret1h"])/atr
    s4=st.side*float(row["ret4h"])/atr
    ex=st.exposure(px)
    tr=st.tranche_frac()

    # Fidelity-first damage flip: loss threshold from 2019-2020 actual negative flips.
    # In stronger persistent against-trend conditions, widen modestly instead of panic-flipping.
    loss_thr=cal["loss_flip_atr"] + 0.35*max(0.0,-s4)
    loss_thr=float(np.clip(loss_thr,2.5,5.0))
    loss_hold=max(cal["loss_hold_bars"], int(round(32 + 8*max(0.0,-s4))))
    if pnl_atr <= -loss_thr and st.hold_bars>=loss_hold:
        return {"type":"FLIP","reason":"ADAPTIVE_LOSS_FLIP"}

    # Profitable flip: threshold is a learned function of market momentum,
    # calibrated only on 2019-2020 Wonyotti flips.
    flip_thr=dynamic_flip_atr(row,st.side,cal)
    min_hold=int(round(16 + 20*max(0.0,flip_thr-1.0)))  # 4h base, longer when trend is strong.
    if pnl_atr>=flip_thr and st.hold_bars>=min_hold:
        return {"type":"FLIP","reason":"ADAPTIVE_PROFIT_FLIP"}

    # Equal-tranche adverse ladder. Thresholds are ATR-normalized and finite.
    adv_levels=[
        -cal["adv_first_atr"],
        -(cal["adv_first_atr"]+0.55),
        -(cal["adv_first_atr"]+1.10),
        -(cal["adv_first_atr"]+1.65),
    ]
    if st.adverse_n<len(adv_levels) and pnl_atr<=adv_levels[st.adverse_n] and ex<0.99:
        # Suppress additional averaging when current 1h move is already a large adverse shock.
        if s1>-1.75 or st.adverse_n==0:
            return {"type":"ADD","frac":tr,"add_type":"ADV"}

    # Favorable pyramid only if the market is actually moving with the position.
    fav_levels=[cal["fav_first_atr"],cal["fav_first_atr"]+0.55,cal["fav_first_atr"]+1.10]
    if st.fav_n<len(fav_levels) and pnl_atr>=fav_levels[st.fav_n] and s1>0.35 and s4>0 and ex<0.99:
        return {"type":"ADD","frac":tr,"add_type":"FAV"}

    # Partial distribution. In strong trend, allow a wider threshold and at most 2 reductions.
    reduce_thr=cal["reduce_atr"] + 0.35*max(0.0,s4)
    reduce_thr=float(np.clip(reduce_thr,0.5,2.0))
    if st.reduce_n<2 and pnl_atr>=reduce_thr*(st.reduce_n+1) and ex>tr+0.05:
        return {"type":"REDUCE","frac":tr}

    return None


def simulate(df,cost,cal):
    st=b3.St(name="AOA_CLONE",side_cost=cost)
    oos=df[(df["datetime_utc"]>=START)&(df["datetime_utc"]<END)].copy().reset_index(drop=True)
    prev=df[df["datetime_utc"]<START].iloc[-1]
    st.pending={"type":"ENTER","dir":1 if prev["aoa_p_long"]>=0.5 else -1}
    st.last_mark=float(prev["close"])
    for _,r in oos.iterrows():
        op=float(r["open"]); cl=float(r["close"])
        st.mark(op); st.exec_pending(op)
        st.mark(cl)
        if st.side!=0: st.hold_bars+=1
        st.curve.append({"ts":r["datetime_utc"],"equity":st.equity,"exposure":st.exposure(cl),"side":st.side})
        st.pending=decide_adaptive(st,r,cal)
    if st.side!=0:
        px=float(oos.iloc[-1]["close"]); st.close_leg(px,"FINAL_CLOSE"); st.actions["EXIT"]+=1
        st.curve[-1]["equity"]=st.equity; st.curve[-1]["exposure"]=0.0; st.curve[-1]["side"]=0
    return st


def actual_targets():
    e=pd.read_csv(EPISODES)
    e["dt"]=pd.to_datetime(e["st"],unit="s",utc=True)
    e["year"]=e["dt"].dt.year
    rows=[]
    for yr in [2020,2021]:
        g=e[e["year"]==yr]
        rows.append({
            "year":yr,
            "legs":int(len(g)),
            "win_rate_pct":float((g["ret_bps"]>0).mean()*100),
            "median_duration_h":float(g["duration_sec"].median()/3600),
            "median_ret_bps":float(g["ret_bps"].median()),
            "median_adverse_adds":float(g["adverse_add_orders"].median()),
            "median_favorable_adds":float(g["favorable_add_orders"].median()),
            "median_leverage":float(g["approx_max_leverage"].median()),
        })
    return pd.DataFrame(rows)


def model_years(st):
    legs=pd.DataFrame(st.legs)
    curve=pd.DataFrame(st.curve)
    # infer leg year by cumulative closing order from curve is unavailable; use durations partition by
    # approximate close timestamps reconstructed from curve action transitions is expensive.
    # Instead compute year performance plus overall leg duration distribution separately.
    yr=[]
    curve["r"]=curve["equity"].pct_change().fillna(curve["equity"].iloc[0]-1)
    curve["year"]=curve["ts"].dt.year
    for y,g in curve.groupby("year"):
        w=float(np.prod(1+g["r"].to_numpy()))
        eq=np.cumprod(1+g["r"].to_numpy()); dd=eq/np.maximum.accumulate(eq)-1
        yr.append({"year":int(y),"return_pct":(w-1)*100,"mdd_pct":float(dd.min()*100),
                   "active_pct":float((g["exposure"]>1e-9).mean()*100)})
    return pd.DataFrame(yr),legs


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    btc,eth,candles,model_meta=era.prep()
    cal=fit_behavior_calibration()

    # Remove non-serializable model from metadata.
    meta_cal={k:v for k,v in cal.items() if k!="_flip_model"}
    targets=actual_targets()
    targets.to_csv(OUT/"actual_targets.csv",index=False)

    rows=[]; yall=[]; lall=[]; curves=[]
    for cname,cost in COSTS.items():
        st=simulate(candles,cost,cal)
        m=b3.metrics(st)
        legs=pd.DataFrame(st.legs)
        rows.append({"variant":"AOA_FIDELITY_V2","cost":cname,**m,
                     "median_leg_duration_h":float(legs["bars"].median()*0.25) if len(legs) else math.nan,
                     "median_leg_return_pct":float(legs["return_pct"].median()) if len(legs) else math.nan})
        yy,ll=model_years(st); yy["variant"]="AOA_FIDELITY_V2"; yy["cost"]=cname; yall.append(yy)
        if not ll.empty:
            ll["variant"]="AOA_FIDELITY_V2"; ll["cost"]=cname; lall.append(ll)
        cc=pd.DataFrame(st.curve);cc["variant"]="AOA_FIDELITY_V2";cc["cost"]=cname;curves.append(cc)

    summary=pd.DataFrame(rows)
    yearly=pd.concat(yall,ignore_index=True)
    summary.to_csv(OUT/"summary.csv",index=False)
    yearly.to_csv(OUT/"yearly.csv",index=False)
    if lall: pd.concat(lall,ignore_index=True).to_csv(OUT/"legs.csv",index=False)
    pd.concat(curves,ignore_index=True).to_csv(OUT/"equity_curves.csv.gz",index=False,compression="gzip")

    meta={
        "calibration_period":"2019-07 through 2020-12 only",
        "validation_emphasis":"2021 behavior/market regime; 2020 is calibration-era fidelity",
        "behavior_calibration":meta_cal,
        "direction_models":model_meta,
        "actual_targets":targets.to_dict(orient="records"),
        "note":"This is imitation-policy tuning, not alpha validation. 2022+ data are not read by this workflow.",
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print("=== META ===");print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== ACTUAL TARGETS ===");print(targets.to_string(index=False))
    print("\n=== FIDELITY V2 SUMMARY ===");print(summary.to_string(index=False))
    print("\n=== YEARLY ===");print(yearly.to_string(index=False))


if __name__=="__main__":
    main()
