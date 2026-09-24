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
from sklearn.metrics import roc_auc_score, average_precision_score, precision_score, recall_score

import research.aoa_fidelity_v3.continuous_side_model as v3

OUT=ROOT/"research"/"aoa_fidelity_v4"/"output"

SIGNED=["ret15m","ret1h","ret4h","ret24h","ret3d","ret7d","bb_z20","ema20_80","trend_z24h","dd7d"]
ABS=["rv4h","rv24h","atr14_pct","bb_width20","vol_z96","er24h"]
FEATURES=["state_side","log_hold_bars"]+ABS+["signed_"+x for x in SIGNED]+["signed_rsi_center","signed_range_center"]


def features(df,side,hold):
    z=pd.DataFrame(index=df.index)
    s=np.asarray(side,float); h=np.asarray(hold,float)
    z["state_side"]=s
    z["log_hold_bars"]=np.log1p(np.maximum(h,0))
    for c in ABS: z[c]=df[c].to_numpy(float)
    for c in SIGNED: z["signed_"+c]=s*df[c].to_numpy(float)
    z["signed_rsi_center"]=s*(df["rsi14"].to_numpy(float)-50)
    z["signed_range_center"]=s*(df["range_pos24h"].to_numpy(float)-0.5)
    return z[FEATURES]


def fit_flip(panel):
    tr=panel[(panel["bar_start"]>=v3.TRAIN_START)&(panel["bar_start"]<v3.TRAIN_END)].copy()
    tr["flip"]=(tr["target_side"]!=tr["actual_side"]).astype(int)
    X=features(tr,tr["actual_side"],tr["hold_bars_actual"])
    m=Pipeline([
        ("imp",SimpleImputer(strategy="median")),
        ("sc",StandardScaler()),
        ("lr",LogisticRegression(max_iter=3000,class_weight="balanced",C=0.35)),
    ])
    m.fit(X,tr["flip"])
    return m,tr


def fast_parts(model,df):
    imp=model.named_steps["imp"]; sc=model.named_steps["sc"]; lr=model.named_steps["lr"]
    stats=np.asarray(imp.statistics_,float)
    mean=np.asarray(sc.mean_,float); scale=np.asarray(sc.scale_,float)
    coef=np.asarray(lr.coef_[0],float); w=coef/scale
    intercept=float(lr.intercept_[0]-np.sum(coef*mean/scale))
    ix={f:i for i,f in enumerate(FEATURES)}
    n=len(df)

    static=np.zeros(n,float)
    for c in ABS:
        v=df[c].to_numpy(float); j=ix[c]
        v=np.where(np.isfinite(v),v,stats[j]); static+=w[j]*v

    def directional(side):
        out=np.zeros(n,float)
        for c in SIGNED:
            src=df[c].to_numpy(float); j=ix["signed_"+c]
            val=side*src; val=np.where(np.isfinite(val),val,stats[j]); out+=w[j]*val
        src=df["rsi14"].to_numpy(float)-50; j=ix["signed_rsi_center"]
        val=side*src; val=np.where(np.isfinite(val),val,stats[j]); out+=w[j]*val
        src=df["range_pos24h"].to_numpy(float)-.5; j=ix["signed_range_center"]
        val=side*src; val=np.where(np.isfinite(val),val,stats[j]); out+=w[j]*val
        return out

    return dict(intercept=intercept,static=static,pos=directional(1),neg=directional(-1),
                w_side=w[ix["state_side"]],w_hold=w[ix["log_hold_bars"]])


def sigmoid(x):
    if x>=0: return 1/(1+math.exp(-min(x,700)))
    e=math.exp(max(x,-700)); return e/(1+e)


def simulate(model,df,initial_side,threshold):
    parts=fast_parts(model,df)
    side=int(initial_side); hold=0; n=len(df)
    pred=np.empty(n,np.int8); prob=np.empty(n,float)
    for i in range(n):
        pred[i]=side
        logit=(parts["intercept"]+parts["static"][i]+
               (parts["pos"][i] if side>0 else parts["neg"][i])+
               parts["w_side"]*side+parts["w_hold"]*math.log1p(hold))
        p=sigmoid(logit); prob[i]=p
        new=-side if p>=threshold else side
        if new==side: hold+=1
        else: side=new; hold=0
    return pred,prob


def calibrate(model,tr):
    actual=tr["actual_side"].to_numpy(np.int8)
    af=v3.flip_count(actual); ah=v3.median_run_hours(actual)
    init=int(actual[0])
    rows=[]; best=None
    for th in [0.50,0.60,0.70,0.75,0.80,0.85,0.88,0.90,0.92,0.94,0.96,0.97,0.98,0.99,0.995]:
        pred,p=simulate(model,tr,init,th)
        acc=float((pred==actual).mean()); pf=v3.flip_count(pred); ph=v3.median_run_hours(pred)
        fr=(pf+1)/(af+1); hr=(ph+.25)/(ah+.25)
        score=acc-0.12*abs(math.log(fr))-0.06*abs(math.log(hr))
        r=dict(threshold=th,accuracy=acc,pred_flips=pf,actual_flips=af,flip_ratio=fr,
               median_hold_h=ph,actual_median_hold_h=ah,hold_ratio=hr,score=score)
        rows.append(r)
        if best is None or score>best["score"]: best=r
    return best,pd.DataFrame(rows)


def perf(df,side,cost):
    return v3.performance(df,side,cost)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    btc,candles,episodes,panel=v3.make_panel()
    model,tr=fit_flip(panel)
    init_model,init_rows=v3.fit_initial_direction(candles)
    best,grid=calibrate(model,tr)
    grid.to_csv(OUT/"threshold_calibration.csv",index=False)

    va=panel[(panel["bar_start"]>=v3.VAL_START)&(panel["bar_start"]<v3.VAL_END)].copy().reset_index(drop=True)
    actual=va["actual_side"].to_numpy(np.int8)
    actual_flip=(va["target_side"]!=va["actual_side"]).astype(int).to_numpy()

    first=va.iloc[[0]]
    p0=float(init_model.predict_proba(first[v3.MARKET_FEATURES])[0,1])
    init=1 if p0>=.5 else -1
    pred,pp=simulate(model,va,init,best["threshold"])
    va["pred_side"]=pred; va["p_flip_recursive"]=pp
    va.to_csv(OUT/"validation_2021.csv.gz",index=False,compression="gzip")

    static_p=model.predict_proba(features(va,va["actual_side"],va["hold_bars_actual"]))[:,1]
    static_pred=(static_p>=best["threshold"]).astype(int)
    fidelity={
        "side_accuracy_pct":float((pred==actual).mean()*100),
        "actual_flips":v3.flip_count(actual),
        "pred_flips":v3.flip_count(pred),
        "actual_median_hold_h":v3.median_run_hours(actual),
        "pred_median_hold_h":v3.median_run_hours(pred),
        "flip_static_roc_auc":float(roc_auc_score(actual_flip,static_p)),
        "flip_static_pr_auc":float(average_precision_score(actual_flip,static_p)),
        "flip_static_precision":float(precision_score(actual_flip,static_pred,zero_division=0)),
        "flip_static_recall":float(recall_score(actual_flip,static_pred,zero_division=0)),
        "initial_p_long":p0,
        "initial_side":"LONG" if init>0 else "SHORT",
    }

    rows=[]
    for cname,cost in v3.SIDE_COSTS.items():
        rows.append({"series":"MODEL_V4","cost":cname,**perf(va,pred,cost)})
        rows.append({"series":"ACTUAL_SIDE_ORACLE","cost":cname,**perf(va,actual,cost)})
    summary=pd.DataFrame(rows)
    summary.to_csv(OUT/"summary.csv",index=False)

    coef=model.named_steps["lr"].coef_[0]
    meta={
        "training":"2019-07-16 through 2020-12-31",
        "validation":"2021 only",
        "target":"explicit next-15m flip hazard, not next-side class",
        "training_flip_rate_pct":float(tr["flip"].mean()*100),
        "training_rows":int(len(tr)),
        "training_actual_flips":int(v3.flip_count(tr["actual_side"].to_numpy())),
        "threshold_selected_training_only":best,
        "validation_fidelity":fidelity,
        "top_coefficients":dict(sorted(zip(FEATURES,map(float,coef)),key=lambda kv:abs(kv[1]),reverse=True)[:15]),
        "note":"2022+ data not used. ACTUAL_SIDE_ORACLE is not tradable; it diagnoses how much directional state alone explains."
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print("=== META ==="); print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== SUMMARY ==="); print(summary.to_string(index=False))
    print("\n=== THRESHOLD GRID ==="); print(grid.to_string(index=False))


if __name__=="__main__":
    main()
