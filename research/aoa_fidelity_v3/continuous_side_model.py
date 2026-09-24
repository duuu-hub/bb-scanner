#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

import research.aoa_3way_oos.backtest_3way as b3
from research.aoa_market_context.analyze_market_context import MARKET_FEATURES, attach

OUT=ROOT/"research"/"aoa_fidelity_v3"/"output"
EP=ROOT/"research"/"aoa_market_context"/"aoa_episodes_2019h2_2021_compact.csv"
POL=ROOT/"research"/"aoa_market_context"/"aoa_policy_2019h2_2021_compact.csv"
TRAIN_START=pd.Timestamp("2019-07-16",tz="UTC")
TRAIN_END=pd.Timestamp("2021-01-01",tz="UTC")
VAL_START=pd.Timestamp("2021-01-01",tz="UTC")
VAL_END=pd.Timestamp("2022-01-01",tz="UTC")
SIDE_COSTS={"ZERO":0.0,"LOW_RT_004":0.0004/2,"BASE_RT_012":0.0012/2}
EXPOSURE=0.75

RAW_STATE=["ret15m","ret1h","ret4h","ret24h","ret3d","ret7d","rv4h","rv24h","atr14_pct",
           "bb_z20","bb_width20","rsi14","vol_z96","range_pos24h","dd7d","er24h","ema20_80","trend_z24h"]
SIGNED_BASE=["ret15m","ret1h","ret4h","ret24h","ret3d","ret7d","bb_z20","ema20_80","trend_z24h"]
MODEL_FEATURES=RAW_STATE+["state_side","log_hold_bars"]+["signed_"+x for x in SIGNED_BASE]+[
    "signed_rsi_center","signed_range_center"
]


def side_at(times_s, episodes):
    st=episodes["st"].to_numpy(np.int64)
    et=episodes["et"].to_numpy(np.int64)
    ds=episodes["d"].map({"L":1,"S":-1}).to_numpy(np.int8)
    arr=np.asarray(times_s,dtype=np.int64)
    idx=np.searchsorted(st,arr,side="right")-1
    out=np.zeros(len(arr),dtype=np.int8)
    hold=np.zeros(len(arr),dtype=float)
    ok=idx>=0
    ii=idx[ok]
    good=arr[ok] < et[ii]
    pos=np.where(ok)[0][good]
    jj=ii[good]
    out[pos]=ds[jj]
    hold[pos]=(arr[pos]-st[jj])/900.0
    return out,hold


def make_panel():
    btc=b3.load_raw(b3.BTC_DIR)
    candles=b3.build_market_candles(btc)
    episodes=pd.read_csv(EP).sort_values("st").reset_index(drop=True)
    cur_s=(candles["timestamp_ms"]//1000).astype(np.int64).to_numpy()
    nxt_s=cur_s+900
    current,hold=side_at(cur_s,episodes)
    nxt,_=side_at(nxt_s,episodes)
    x=candles.copy()
    x["actual_side"]=current
    x["target_side"]=nxt
    x["hold_bars_actual"]=hold
    x=x[(x["bar_start"]>=TRAIN_START)&(x["bar_start"]<VAL_END)].copy()
    x=x[(x["actual_side"]!=0)&(x["target_side"]!=0)].copy()
    return btc,candles,episodes,x


def build_features(df,state_side,hold_bars):
    z=df.copy()
    s=np.asarray(state_side,float)
    h=np.asarray(hold_bars,float)
    z["state_side"]=s
    z["log_hold_bars"]=np.log1p(np.maximum(h,0))
    for col in SIGNED_BASE:
        z["signed_"+col]=s*z[col].to_numpy(float)
    z["signed_rsi_center"]=s*(z["rsi14"].to_numpy(float)-50.0)
    z["signed_range_center"]=s*(z["range_pos24h"].to_numpy(float)-0.5)
    return z[MODEL_FEATURES]


def fit_transition(panel):
    tr=panel[(panel["bar_start"]>=TRAIN_START)&(panel["bar_start"]<TRAIN_END)].copy()
    X=build_features(tr,tr["actual_side"],tr["hold_bars_actual"])
    y=(tr["target_side"]>0).astype(int)
    pipe=Pipeline([
        ("imp",SimpleImputer(strategy="median")),
        ("sc",StandardScaler()),
        ("lr",LogisticRegression(max_iter=3000,class_weight="balanced",C=0.35)),
    ])
    pipe.fit(X,y)
    return pipe,tr


def fit_initial_direction(candles):
    p=pd.read_csv(POL)
    ent=p[p["a"].isin(["E","FE"])].copy()
    j=attach(ent,candles,"t")
    j["event_dt"]=pd.to_datetime(j["t"],unit="s",utc=True)
    j=j[j["event_dt"]<VAL_START].copy()
    j["y"]=(j["d"]=="L").astype(int)
    pipe=Pipeline([
        ("imp",SimpleImputer(strategy="median")),
        ("sc",StandardScaler()),
        ("lr",LogisticRegression(max_iter=3000,class_weight="balanced",C=0.5)),
    ])
    pipe.fit(j[MARKET_FEATURES],j["y"])
    return pipe,j


def simulate_sides(model,df,initial_side,hyst):
    side=int(initial_side)
    hold=0
    pred=[]
    probs=[]
    for _,r in df.iterrows():
        # This side is carried during the current bar.
        pred.append(side)
        X=build_features(pd.DataFrame([r]),np.array([side]),np.array([hold]))
        p=float(model.predict_proba(X)[0,1])
        probs.append(p)
        if side>0:
            new_side=-1 if p < 0.5-hyst else 1
        else:
            new_side=1 if p > 0.5+hyst else -1
        if new_side==side:
            hold+=1
        else:
            side=new_side
            hold=0
    return np.asarray(pred,np.int8),np.asarray(probs,float)


def flip_count(side):
    s=np.asarray(side)
    return int(np.sum(s[1:]!=s[:-1])) if len(s)>1 else 0


def median_run_hours(side):
    s=np.asarray(side)
    if len(s)==0:return math.nan
    runs=[]; n=1
    for i in range(1,len(s)):
        if s[i]==s[i-1]: n+=1
        else: runs.append(n); n=1
    runs.append(n)
    return float(np.median(runs)*0.25)


def calibrate_hysteresis(model,tr):
    actual=tr["actual_side"].to_numpy(np.int8)
    init=int(actual[0])
    actual_flips=flip_count(actual)
    rows=[]
    best=None
    for h in [0.00,0.03,0.05,0.08,0.10,0.12,0.15,0.18,0.20,0.25,0.30,0.35]:
        pred,p=simulate_sides(model,tr,init,h)
        acc=float((pred==actual).mean())
        fc=flip_count(pred)
        ratio=(fc+1)/(actual_flips+1)
        score=acc-0.18*abs(math.log(ratio))
        rec={"hyst":h,"accuracy":acc,"pred_flips":fc,"actual_flips":actual_flips,
             "flip_ratio":ratio,"median_run_h":median_run_hours(pred),"score":score}
        rows.append(rec)
        if best is None or score>best["score"]: best=rec
    return best,pd.DataFrame(rows)


def performance(df,side,cost):
    z=df.copy().reset_index(drop=True)
    s=np.asarray(side,float)
    intr=s*(z["close"].to_numpy(float)/z["open"].to_numpy(float)-1.0)
    turn=np.zeros(len(s))
    turn[0]=1.0
    turn[1:]=(s[1:]!=s[:-1]).astype(float)*2.0
    r=EXPOSURE*intr-turn*EXPOSURE*cost
    eq=np.cumprod(1+r)
    dd=eq/np.maximum.accumulate(eq)-1
    sd=np.std(r)
    years=(z["bar_start"].iloc[-1]-z["bar_start"].iloc[0]).total_seconds()/86400/365.25
    return {
        "return_pct":float((eq[-1]-1)*100),
        "cagr_pct":float((eq[-1]**(1/years)-1)*100) if eq[-1]>0 and years>0 else math.nan,
        "sharpe":float(np.mean(r)/sd*np.sqrt(365.25*96)) if sd>0 else math.nan,
        "mdd_pct":float(dd.min()*100),
        "flips":flip_count(s),
        "median_hold_h":median_run_hours(s),
        "turnover_units":float(turn.sum()*EXPOSURE),
    }


def yearly_perf(df,side,cost):
    z=df.copy().reset_index(drop=True)
    s=np.asarray(side,float)
    intr=s*(z["close"].to_numpy(float)/z["open"].to_numpy(float)-1.0)
    turn=np.zeros(len(s)); turn[0]=1
    turn[1:]=(s[1:]!=s[:-1]).astype(float)*2
    z["r"]=EXPOSURE*intr-turn*EXPOSURE*cost
    z["side_pred"]=s
    z["year"]=z["bar_start"].dt.year
    out=[]
    for y,g in z.groupby("year"):
        rr=g["r"].to_numpy(); eq=np.cumprod(1+rr); dd=eq/np.maximum.accumulate(eq)-1
        out.append({"year":int(y),"return_pct":float((eq[-1]-1)*100),"mdd_pct":float(dd.min()*100),
                    "bars":len(g)})
    return out


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    btc,candles,episodes,panel=make_panel()
    model,tr=fit_transition(panel)
    init_model,init_rows=fit_initial_direction(candles)

    best,grid=calibrate_hysteresis(model,tr)
    grid.to_csv(OUT/"hysteresis_calibration.csv",index=False)

    va=panel[(panel["bar_start"]>=VAL_START)&(panel["bar_start"]<VAL_END)].copy().reset_index(drop=True)
    actual=va["actual_side"].to_numpy(np.int8)

    # Initial side is obtained from the separate pre-2021 market-only direction model.
    first=va.iloc[[0]]
    p0=float(init_model.predict_proba(first[MARKET_FEATURES])[0,1])
    init=1 if p0>=0.5 else -1

    pred,probs=simulate_sides(model,va,init,best["hyst"])
    va["pred_side"]=pred
    va["p_long"]=probs
    va.to_csv(OUT/"validation_2021_sides.csv.gz",index=False,compression="gzip")

    next_y=(va["target_side"]>0).astype(int)
    # Static AUC with actual state features: measures conditional model information, not simulation accuracy.
    p_static=model.predict_proba(build_features(va,va["actual_side"],va["hold_bars_actual"]))[:,1]
    auc=float(roc_auc_score(next_y,p_static))

    fidelity={
        "2021_side_accuracy_pct":float((pred==actual).mean()*100),
        "2021_actual_flips_15m":flip_count(actual),
        "2021_pred_flips":flip_count(pred),
        "2021_actual_median_hold_h_15m":median_run_hours(actual),
        "2021_pred_median_hold_h":median_run_hours(pred),
        "2021_conditional_next_side_auc":auc,
        "initial_p_long":p0,
        "initial_side":"LONG" if init>0 else "SHORT",
        "hysteresis":best["hyst"],
    }

    perf=[]
    yearly=[]
    for cname,cost in SIDE_COSTS.items():
        pp=performance(va,pred,cost)
        perf.append({"variant":"AOA_FIDELITY_V3_SIDE","cost":cname,**pp})
        for r in yearly_perf(va,pred,cost):
            yearly.append({"variant":"AOA_FIDELITY_V3_SIDE","cost":cname,**r})

    pd.DataFrame(perf).to_csv(OUT/"summary.csv",index=False)
    pd.DataFrame(yearly).to_csv(OUT/"yearly.csv",index=False)

    coef=model.named_steps["lr"].coef_[0]
    meta={
        "training":"2019-07-16 through 2020-12-31 continuous 15m Wonyotti position state",
        "validation":"2021 only",
        "hysteresis_selected_on_training_only":best,
        "fidelity_2021":fidelity,
        "transition_train_rows":int(len(tr)),
        "initial_direction_train_events":int(len(init_rows)),
        "top_transition_coefficients":dict(sorted(zip(MODEL_FEATURES,map(float,coef)),key=lambda kv:abs(kv[1]),reverse=True)[:15]),
        "important":"This is an imitation/fidelity model. 2022+ data are not used.",
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print("=== META ===");print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== PERFORMANCE ===");print(pd.DataFrame(perf).to_string(index=False))
    print("\n=== HYSTERESIS GRID ===");print(grid.to_string(index=False))


if __name__=="__main__":
    main()
