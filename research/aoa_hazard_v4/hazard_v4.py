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
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

import research.aoa_era_validation.validate_era as era

EP=ROOT/"research"/"aoa_market_context"/"aoa_episodes_2019h2_2021_compact.csv"
OUT=ROOT/"research"/"aoa_hazard_v4"/"output"
TRAIN_END=pd.Timestamp("2021-01-01",tz="UTC")
TEST_START=pd.Timestamp("2021-01-01",tz="UTC")
TEST_END=pd.Timestamp("2022-01-01",tz="UTC")

FEATURES=[
    "signed_ret15m","signed_ret1h","signed_ret4h","signed_ret24h","signed_ret3d",
    "signed_bb_z","signed_rsi","signed_range24h",
    "leg_atr","leg_pos_atr","leg_neg_atr","duration_log",
    "age_lt1h","age_1_6h","age_6_24h","age_24_72h","age_72h_plus",
    "rv24h","atr14_pct","bb_width20","vol_z96","er24h","high_vol",
    "side_long",
    "entry_signed_ret1h","entry_signed_ret4h","entry_signed_ret24h","entry_signed_ret7d",
    "entry_signed_bb_z","entry_signed_range24h",
    "entry_atr14","entry_rv24","entry_er24","entry_high_vol","entry_vol_z"
]

DUR_KEYS=["pct_lt1h","pct_1_6h","pct_6_24h","pct_24_72h","pct_72h_plus"]


def entry_context(row,side):
    return {
        "entry_signed_ret1h":side*float(row["ret1h"]),
        "entry_signed_ret4h":side*float(row["ret4h"]),
        "entry_signed_ret24h":side*float(row["ret24h"]),
        "entry_signed_ret7d":side*float(row["ret7d"]),
        "entry_signed_bb_z":side*float(row["bb_z20"]),
        "entry_signed_range24h":side*(float(row["range_pos24h"])-0.5),
        "entry_atr14":float(row["atr14_pct"]),
        "entry_rv24":float(row["rv24h"]),
        "entry_er24":float(row["er24h"]),
        "entry_high_vol":float(row["high_vol"]),
        "entry_vol_z":float(row["vol_z96"]),
    }


def state_features(row,side,entry_px,entry_ts,ectx):
    atr=max(float(row["atr14_pct"]),1e-9)
    leg=side*(float(row["close"])/entry_px-1.0)*10000.0/atr
    hrs=max(0.0,(float(row["bar_end_s"])-entry_ts)/3600.0)
    f={
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
        "age_lt1h":float(hrs<1),
        "age_1_6h":float(1<=hrs<6),
        "age_6_24h":float(6<=hrs<24),
        "age_24_72h":float(24<=hrs<72),
        "age_72h_plus":float(hrs>=72),
        "rv24h":float(row["rv24h"]),
        "atr14_pct":float(row["atr14_pct"]),
        "bb_width20":float(row["bb_width20"]),
        "vol_z96":float(row["vol_z96"]),
        "er24h":float(row["er24h"]),
        "high_vol":float(row["high_vol"]),
        "side_long":float(side>0),
    }
    f.update(ectx)
    return f


def build_hazard_rows(candles):
    ep=pd.read_csv(EP)
    ends=candles["bar_end_s"].to_numpy(dtype=np.int64)
    rows=[]
    for _,e in ep.iterrows():
        st=int(e["st"]); et=int(e["et"]); side=1 if e["d"]=="L" else -1
        sidx=np.searchsorted(ends,st,side="right")-1
        eidx=np.searchsorted(ends,et,side="right")-1
        if sidx<0 or eidx<=sidx or eidx>=len(candles):
            continue
        erow=candles.iloc[sidx]
        ectx=entry_context(erow,side)
        entry_px=float(erow["close"])
        for i in range(sidx+1,eidx+1):
            r=candles.iloc[i]
            f=state_features(r,side,entry_px,st,ectx)
            f.update({
                "ts":int(r["bar_end_s"]),
                "episode":int(e["episode"]),
                "side":side,
                "flip":int(i==eidx and bool(e["flip_close"])),
                "year":int(r["bar_start"].year),
            })
            rows.append(f)
    return pd.DataFrame(rows)


def make_pipe(C):
    return Pipeline([
        ("imp",SimpleImputer(strategy="median")),
        ("sc",StandardScaler()),
        ("lr",LogisticRegression(max_iter=5000,class_weight="balanced",C=C))
    ])


def fit_model(h):
    inner_tr=h[h["year"]<=2019].copy()
    inner_va=h[h["year"]==2020].copy()
    candidates=[]
    for C in [0.05,0.10,0.25,0.50,1.00]:
        p=make_pipe(C)
        p.fit(inner_tr[FEATURES],inner_tr["flip"])
        pr=p.predict_proba(inner_va[FEATURES])[:,1]
        candidates.append({
            "C":C,
            "auc_2020":float(roc_auc_score(inner_va["flip"],pr)),
            "ap_2020":float(average_precision_score(inner_va["flip"],pr)),
            "base_2020":float(inner_va["flip"].mean()),
        })
    sel=pd.DataFrame(candidates).sort_values(["ap_2020","auc_2020"],ascending=False).iloc[0]
    best_C=float(sel["C"])

    tr=h[h["ts"]<int(TRAIN_END.timestamp())].copy()
    te=h[(h["ts"]>=int(TEST_START.timestamp()))&(h["ts"]<int(TEST_END.timestamp()))].copy()
    pipe=make_pipe(best_C)
    pipe.fit(tr[FEATURES],tr["flip"])
    tr["p"]=pipe.predict_proba(tr[FEATURES])[:,1]
    te["p"]=pipe.predict_proba(te[FEATURES])[:,1]
    test={
        "auc":float(roc_auc_score(te["flip"],te["p"])),
        "ap":float(average_precision_score(te["flip"],te["p"])),
        "base":float(te["flip"].mean()),
    }
    coef={f:float(v) for f,v in zip(FEATURES,pipe.named_steps["lr"].coef_[0])}
    return pipe,tr,te,pd.DataFrame(candidates),best_C,test,dict(sorted(coef.items(),key=lambda kv:abs(kv[1]),reverse=True))


def compile_fast(pipe):
    imp=pipe.named_steps["imp"]; sc=pipe.named_steps["sc"]; lr=pipe.named_steps["lr"]
    return {
        "med":np.asarray(imp.statistics_,float),
        "mean":np.asarray(sc.mean_,float),
        "scale":np.asarray(sc.scale_,float),
        "coef":np.asarray(lr.coef_[0],float),
        "intercept":float(lr.intercept_[0]),
    }


def fast_prob(m,f):
    x=np.asarray([f[k] for k in FEATURES],float)
    bad=~np.isfinite(x)
    if bad.any():
        x[bad]=m["med"][bad]
    z=(x-m["mean"])/np.where(m["scale"]==0,1.0,m["scale"])
    s=m["intercept"]+float(np.dot(m["coef"],z))
    if s>=0:
        e=math.exp(-s); return 1.0/(1.0+e)
    e=math.exp(s); return e/(1.0+e)


def duration_stats(hours):
    a=np.asarray(hours,float)
    n=len(a)
    if n==0:
        return {k:math.nan for k in ["q25_duration_h","median_duration_h","q75_duration_h"]+DUR_KEYS}
    return {
        "q25_duration_h":float(np.quantile(a,.25)),
        "median_duration_h":float(np.median(a)),
        "q75_duration_h":float(np.quantile(a,.75)),
        "pct_lt1h":float(np.mean(a<1)*100),
        "pct_1_6h":float(np.mean((a>=1)&(a<6))*100),
        "pct_6_24h":float(np.mean((a>=6)&(a<24))*100),
        "pct_24_72h":float(np.mean((a>=24)&(a<72))*100),
        "pct_72h_plus":float(np.mean(a>=72)*100),
    }


def actual_stats(year):
    ep=pd.read_csv(EP)
    dt=pd.to_datetime(ep["st"],unit="s",utc=True)
    g=ep[dt.dt.year==year].copy()
    ds=duration_stats(g["duration_sec"].to_numpy()/3600.0)
    return {
        "legs":int(len(g)),
        "win_rate_pct":float((g["ret_bps"]>0).mean()*100),
        "median_ret_bps":float(g["ret_bps"].median()),
        **ds
    }


def sim_one(candles,fast_model,start,end,threshold,min_hold_bars,confirm_bars,cost_side=0.0):
    df=candles[(candles["bar_start"]>=start-pd.Timedelta(minutes=15))&(candles["bar_start"]<end)].copy().reset_index(drop=True)
    prev=df[df["bar_start"]<start].iloc[-1]
    side=1 if float(prev["aoa_p_long"])>=0.5 else -1
    entry_px=float(df[df["bar_start"]>=start].iloc[0]["open"])
    entry_ts=int(start.timestamp())
    ectx=entry_context(prev,side)

    equity=1.0
    qty=side*equity/entry_px
    last_px=entry_px
    leg_start_eq=equity
    leg_bars=0
    equity-=equity*cost_side
    turnover=1.0
    legs=[]; curve=[]
    pending=False; pending_ctx=None; streak=0

    for _,r in df[df["bar_start"]>=start].iterrows():
        op=float(r["open"]); cl=float(r["close"])
        equity += qty*(op-last_px); last_px=op

        if pending:
            notional=abs(qty)*op
            equity-=notional*cost_side
            ret=(equity/leg_start_eq-1.0)*100
            legs.append({"return_pct":ret,"bars":leg_bars,"direction":"LONG" if side>0 else "SHORT"})
            side=-side
            equity-=equity*cost_side
            qty=side*equity/op
            entry_px=op
            entry_ts=int(r["bar_end_s"]-900)
            ectx=pending_ctx
            leg_start_eq=equity
            leg_bars=0
            turnover+=2.0
            pending=False; pending_ctx=None; streak=0

        equity += qty*(cl-last_px); last_px=cl
        leg_bars+=1
        f=state_features(r,side,entry_px,entry_ts,ectx)
        p=fast_prob(fast_model,f)

        if leg_bars>=min_hold_bars and p>=threshold:
            streak+=1
        else:
            streak=0
        if streak>=confirm_bars:
            pending=True
            pending_ctx=entry_context(r,-side)

        curve.append((r["bar_start"],equity,p,side,leg_bars))

    notional=abs(qty)*last_px
    equity-=notional*cost_side
    ret=(equity/leg_start_eq-1.0)*100
    legs.append({"return_pct":ret,"bars":leg_bars,"direction":"LONG" if side>0 else "SHORT"})
    turnover+=1.0

    l=pd.DataFrame(legs)
    c=pd.DataFrame(curve,columns=["ts","equity","hazard_p","side","leg_bars"])
    eq=c["equity"].to_numpy()
    dd=eq/np.maximum.accumulate(eq)-1
    ds=duration_stats(l["bars"].to_numpy()*0.25)
    gp=l.loc[l.return_pct>0,"return_pct"].sum()
    gl=-l.loc[l.return_pct<0,"return_pct"].sum()
    return {
        "return_pct":float((equity-1)*100),
        "mdd_pct":float(dd.min()*100),
        "legs":int(len(l)),
        "win_rate_pct":float((l["return_pct"]>0).mean()*100),
        "median_leg_return_pct":float(l["return_pct"].median()),
        "pf":float(gp/max(gl,1e-12)),
        "turnover_units":float(turnover),
        **ds
    },l,c


def calibrate_2020(candles,fast_model,train_df):
    actual=actual_stats(2020)
    flip_scores=train_df.loc[train_df["flip"]==1,"p"].dropna()
    thresholds=sorted(set(float(flip_scores.quantile(q)) for q in [.50,.60,.70,.75,.80,.85,.90]))
    ep=pd.read_csv(EP)
    dt=pd.to_datetime(ep["st"],unit="s",utc=True)
    dur_bars=ep.loc[dt<TRAIN_END,"duration_sec"]/900.0
    min_holds=sorted(set(max(1,int(math.ceil(float(dur_bars.quantile(q))))) for q in [.10,.20,.25,.33]))
    confirms=[1,2,3]
    rows=[]

    for th in thresholds:
        for mh in min_holds:
            for cb in confirms:
                m,_,_=sim_one(
                    candles,fast_model,
                    pd.Timestamp("2020-01-01",tz="UTC"),
                    pd.Timestamp("2021-01-01",tz="UTC"),
                    th,mh,cb,0.0
                )
                count_err=abs(math.log(max(m["legs"],1)/max(actual["legs"],1)))
                med_err=abs(math.log((m["median_duration_h"]+.25)/(actual["median_duration_h"]+.25)))
                q75_err=abs(math.log((m["q75_duration_h"]+.25)/(actual["q75_duration_h"]+.25)))
                dist=sum(abs(m[k]-actual[k]) for k in DUR_KEYS)/100.0
                score=count_err+med_err+0.3*q75_err+1.5*dist
                rows.append({
                    "threshold":th,"min_hold_bars":mh,"confirm_bars":cb,
                    "fidelity_score":score,
                    **{f"actual_{k}":v for k,v in actual.items() if k!="win_rate_pct" and k!="median_ret_bps"},
                    **m
                })
    tab=pd.DataFrame(rows).sort_values(["fidelity_score","threshold"]).reset_index(drop=True)
    b=tab.iloc[0]
    return float(b["threshold"]),int(b["min_hold_bars"]),int(b["confirm_bars"]),tab


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    _,_,candles,_=era.prep()
    h=build_hazard_rows(candles)
    model,tr,te,model_grid,best_C,test,coef=fit_model(h)
    model_grid.to_csv(OUT/"inner_model_selection.csv",index=False)
    fast=compile_fast(model)

    th,mh,cb,grid=calibrate_2020(candles,fast,tr)
    grid.to_csv(OUT/"fidelity_grid_2020.csv",index=False)

    results=[]; legs=[] 
    for cname,cost in [("ZERO",0.0),("RT_004",0.0004/2)]:
        m,l,c=sim_one(candles,fast,TEST_START,TEST_END,th,mh,cb,cost)
        results.append({"cost":cname,**m})
        l["cost"]=cname; legs.append(l)
        c.to_csv(OUT/f"curve_2021_{cname}.csv.gz",index=False,compression="gzip")
    pd.DataFrame(results).to_csv(OUT/"summary_2021.csv",index=False)
    pd.concat(legs,ignore_index=True).to_csv(OUT/"legs_2021.csv",index=False)

    pos=te.loc[te.flip==1,"p"]; neg=te.loc[te.flip==0,"p"]
    meta={
        "inner_model_selection":model_grid.to_dict(orient="records"),
        "selected_C":best_C,
        "test_2021_auc":test["auc"],
        "test_2021_average_precision":test["ap"],
        "test_2021_base_flip_rate":test["base"],
        "ap_lift_vs_base":float(test["ap"]/test["base"]) if test["base"]>0 else None,
        "selected_threshold":th,
        "selected_min_hold_bars":mh,
        "selected_confirm_bars":cb,
        "actual_2020":actual_stats(2020),
        "actual_2021":actual_stats(2021),
        "median_p_actual_2021_flip":float(pos.median()),
        "median_p_2021_nonflip":float(neg.median()),
        "top_coefficients":coef,
        "note":"Model hyperparameter selected on 2019->2020 event ranking. Trigger parameters selected only on 2020 behavior fidelity, excluding PnL. 2021 is validation."
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")

    print("=== META ===")
    print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== TOP 2020 FIDELITY ===")
    print(grid.head(15).to_string(index=False))
    print("\n=== 2021 VALIDATION ===")
    print(pd.DataFrame(results).to_string(index=False))


if __name__=="__main__":
    main()
