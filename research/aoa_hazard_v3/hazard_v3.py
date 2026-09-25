#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

import research.aoa_3way_oos.backtest_3way as b3
import research.aoa_era_validation.validate_era as era

EP=ROOT/"research"/"aoa_market_context"/"aoa_episodes_2019h2_2021_compact.csv"
OUT=ROOT/"research"/"aoa_hazard_v3"/"output"
TRAIN_END=pd.Timestamp("2021-01-01",tz="UTC")
TEST_START=pd.Timestamp("2021-01-01",tz="UTC")
TEST_END=pd.Timestamp("2022-01-01",tz="UTC")

FEATURES=[
    "signed_ret15m","signed_ret1h","signed_ret4h","signed_ret24h","signed_ret3d",
    "signed_bb_z","signed_rsi","signed_range24h",
    "leg_atr","leg_pos_atr","leg_neg_atr","duration_log",
    "rv24h","atr14_pct","bb_width20","vol_z96","er24h","high_vol"
]

def add_state_features(row,side,entry_px,entry_ts):
    atr=max(float(row["atr14_pct"]),1e-9)
    leg=side*(float(row["close"])/entry_px-1.0)*10000.0/atr
    hrs=max(0.0,(row["bar_end_s"]-entry_ts)/3600.0)
    return {
        "signed_ret15m":side*float(row["ret15m"]),
        "signed_ret1h":side*float(row["ret1h"]),
        "signed_ret4h":side*float(row["ret4h"]),
        "signed_ret24h":side*float(row["ret24h"]),
        "signed_ret3d":side*float(row["ret3d"]),
        "signed_bb_z":side*float(row["bb_z20"]),
        "signed_rsi":side*(float(row["rsi14"])-50.0),
        "signed_range24h":side*(float(row["range_pos24h"])-0.5),
        "leg_atr":leg,
        "leg_pos_atr":max(leg,0.0),
        "leg_neg_atr":max(-leg,0.0),
        "duration_log":math.log1p(hrs),
        "rv24h":float(row["rv24h"]),
        "atr14_pct":float(row["atr14_pct"]),
        "bb_width20":float(row["bb_width20"]),
        "vol_z96":float(row["vol_z96"]),
        "er24h":float(row["er24h"]),
        "high_vol":float(row["high_vol"]),
    }

def build_hazard_rows(candles):
    ep=pd.read_csv(EP)
    ep["st_dt"]=pd.to_datetime(ep["st"],unit="s",utc=True)
    ep["et_dt"]=pd.to_datetime(ep["et"],unit="s",utc=True)
    ends=candles["bar_end_s"].to_numpy(dtype=np.int64)
    rows=[]
    for _,e in ep.iterrows():
        st=int(e["st"]); et=int(e["et"]); side=1 if e["d"]=="L" else -1
        sidx=np.searchsorted(ends,st,side="right")-1
        eidx=np.searchsorted(ends,et,side="right")-1
        if sidx<0 or eidx<=sidx or eidx>=len(candles): continue
        entry_px=float(candles.iloc[sidx]["close"])
        # First tradable decision after position start through last completed bar before flip.
        for i in range(sidx+1,eidx+1):
            r=candles.iloc[i]
            f=add_state_features(r,side,entry_px,st)
            f.update({
                "bar_i":i,
                "ts":int(r["bar_end_s"]),
                "episode":int(e["episode"]),
                "side":side,
                "flip":int(i==eidx and bool(e["flip_close"])),
                "year":int(r["bar_start"].year),
            })
            rows.append(f)
    return pd.DataFrame(rows)

def fit_model(h):
    tr=h[h["ts"]<int(TRAIN_END.timestamp())].copy()
    te=h[(h["ts"]>=int(TEST_START.timestamp()))&(h["ts"]<int(TEST_END.timestamp()))].copy()
    pipe=Pipeline([
        ("imp",SimpleImputer(strategy="median")),
        ("sc",StandardScaler()),
        ("lr",LogisticRegression(max_iter=4000,class_weight="balanced",C=0.25))
    ])
    pipe.fit(tr[FEATURES],tr["flip"])
    for x in [tr,te]:
        x["p"]=pipe.predict_proba(x[FEATURES])[:,1]
    auc=float(roc_auc_score(te["flip"],te["p"]))
    ap=float(average_precision_score(te["flip"],te["p"]))
    base=float(te["flip"].mean())
    coef={f:float(v) for f,v in zip(FEATURES,pipe.named_steps["lr"].coef_[0])}
    return pipe,tr,te,auc,ap,base,dict(sorted(coef.items(),key=lambda kv:abs(kv[1]),reverse=True))

def actual_stats(year):
    ep=pd.read_csv(EP)
    dt=pd.to_datetime(ep["st"],unit="s",utc=True)
    g=ep[dt.dt.year==year].copy()
    return {
        "legs":int(len(g)),
        "win_rate_pct":float((g["ret_bps"]>0).mean()*100),
        "median_duration_h":float(g["duration_sec"].median()/3600),
        "median_ret_bps":float(g["ret_bps"].median()),
    }

def sim_one(candles,fast_model,start,end,threshold,min_hold_bars,cost_side=0.0):
    df=candles[(candles["bar_start"]>=start-pd.Timedelta(minutes=15))&(candles["bar_start"]<end)].copy().reset_index(drop=True)
    prev=df[df["bar_start"]<start].iloc[-1]
    side=1 if float(prev["aoa_p_long"])>=0.5 else -1
    entry_px=float(df[df["bar_start"]>=start].iloc[0]["open"])
    entry_ts=int(start.timestamp())
    equity=1.0
    qty=side*equity/entry_px
    last_px=entry_px
    leg_start_eq=equity
    leg_bars=0
    turnover=1.0
    equity-=equity*cost_side
    legs=[]
    curve=[]
    pending_flip=False

    for _,r in df[df["bar_start"]>=start].iterrows():
        op=float(r["open"]); cl=float(r["close"])
        equity += qty*(op-last_px); last_px=op
        if pending_flip:
            # close then reopen opposite at same next-open; 2x turnover.
            close_notional=abs(qty)*op
            equity-=close_notional*cost_side
            ret=(equity/leg_start_eq-1.0)*100
            legs.append({"return_pct":ret,"bars":leg_bars,"direction":"LONG" if side>0 else "SHORT"})
            side=-side
            open_notional=equity
            equity-=open_notional*cost_side
            qty=side*equity/op
            entry_px=op; entry_ts=int(r["bar_end_s"]-900)
            leg_start_eq=equity; leg_bars=0
            turnover += 2.0
            pending_flip=False
        equity += qty*(cl-last_px); last_px=cl
        leg_bars+=1
        f=add_state_features(r,side,entry_px,entry_ts)
        X=pd.DataFrame([f])[FEATURES]
        p=float(model.predict_proba(X)[:,1][0])
        if leg_bars>=min_hold_bars and p>=threshold:
            pending_flip=True
        curve.append((r["bar_start"],equity,p,side,leg_bars))

    # final close
    close_notional=abs(qty)*last_px
    equity-=close_notional*cost_side
    ret=(equity/leg_start_eq-1.0)*100
    legs.append({"return_pct":ret,"bars":leg_bars,"direction":"LONG" if side>0 else "SHORT"})
    turnover+=1.0
    l=pd.DataFrame(legs)
    c=pd.DataFrame(curve,columns=["ts","equity","hazard_p","side","leg_bars"])
    eq=c["equity"].to_numpy()
    dd=eq/np.maximum.accumulate(eq)-1
    return {
        "return_pct":float((equity-1)*100),
        "mdd_pct":float(dd.min()*100),
        "legs":int(len(l)),
        "win_rate_pct":float((l["return_pct"]>0).mean()*100),
        "median_duration_h":float(l["bars"].median()*0.25),
        "median_leg_return_pct":float(l["return_pct"].median()),
        "pf":float(l.loc[l.return_pct>0,"return_pct"].sum()/max(1e-12,-l.loc[l.return_pct<0,"return_pct"].sum())),
        "turnover_units":float(turnover),
    },l,c

def choose_threshold(candles,fast_model,train_df,min_hold_bars):
    # No PnL tuning and no threshold grid. Match only the observed per-bar flip frequency
    # in the 2019H2-2020 training state path.
    flip_rate=float(train_df["flip"].mean())
    q=max(0.0,min(1.0,1.0-flip_rate))
    th=float(train_df["p"].quantile(q))
    actual=actual_stats(2020)
    m,_,_=sim_one(
        candles,fast_model,
        pd.Timestamp("2020-01-01",tz="UTC"),
        pd.Timestamp("2021-01-01",tz="UTC"),
        th,min_hold_bars,0.0
    )
    tab=pd.DataFrame([{
        "threshold":th,
        "train_bar_flip_rate":flip_rate,
        "actual_2020_legs":actual["legs"],
        "actual_2020_median_duration_h":actual["median_duration_h"],
        **m
    }])
    return th,tab

