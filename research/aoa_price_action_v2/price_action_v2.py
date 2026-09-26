#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, accuracy_score, f1_score, confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

import research.aoa_price_action_v1.price_action_v1 as v1

OUT=ROOT/"research"/"aoa_price_action_v2"/"output"
EP=v1.EP
TRAIN_END=v1.TRAIN_END
TEST_START=v1.TEST_START
TEST_END=v1.TEST_END

MODE_FEATURES=[
    "sret_15m","sret_30m","sret_1h","sret_2h","sret_4h","sret_8h","sret_12h","sret_24h","sret_3d",
    "sbody_bps","supper_wick_bps","slower_wick_bps","sclose_loc",
    "sup_frac_1h","sup_frac_4h",
    "sloc_4h","sloc_24h","sloc_3d","sloc_7d",
    "sdist_high_4h","sdist_low_4h","sdist_high_24h","sdist_low_24h",
    "sbreak_4h","sbreak_24h",
    "range_4h_bps","range_24h_bps","range_3d_bps",
    "side_long",
]
MODES=["SCALP","SWING","EXTENDED"]

def mode_label(hours):
    if hours < 6: return "SCALP"
    if hours < 72: return "SWING"
    return "EXTENDED"

def make_mode_pipe(C):
    return Pipeline([
        ("imp",SimpleImputer(strategy="median")),
        ("sc",StandardScaler()),
        ("lr",LogisticRegression(max_iter=5000,class_weight="balanced",C=C))
    ])

def build_mode_rows(candles):
    ep=pd.read_csv(EP)
    ends=candles["bar_end_s"].to_numpy(dtype=np.int64)
    rows=[]
    for _,e in ep.iterrows():
        i=v1.completed_index(ends,e["st"])
        if i<0 or i>=len(candles): continue
        side=1 if e["d"]=="L" else -1
        s=v1.signed_state(candles.iloc[i],side)
        s["side_long"]=float(side>0)
        r={k:s.get(k,np.nan) for k in MODE_FEATURES}
        hours=float(e["duration_sec"])/3600.0
        r.update({
            "episode":int(e["episode"]),
            "ts":int(e["st"]),
            "year":int(candles.iloc[i]["bar_start"].year),
            "mode":mode_label(hours),
            "duration_h":hours,
        })
        rows.append(r)
    return pd.DataFrame(rows)

def fit_mode_model(rows):
    tr=rows[rows["ts"]<int(TRAIN_END.timestamp())].copy()
    te=rows[(rows["ts"]>=int(TEST_START.timestamp()))&(rows["ts"]<int(TEST_END.timestamp()))].copy()
    inner_tr=tr[tr["year"]==2019]
    inner_va=tr[tr["year"]==2020]
    cand=[]
    for C in [0.03,0.05,0.10,0.25,0.50,1.0]:
        p=make_mode_pipe(C)
        p.fit(inner_tr[MODE_FEATURES],inner_tr["mode"])
        pred=p.predict(inner_va[MODE_FEATURES])
        cand.append({
            "C":C,
            "bal_acc_2020":float(balanced_accuracy_score(inner_va["mode"],pred)),
            "macro_f1_2020":float(f1_score(inner_va["mode"],pred,average="macro")),
        })
    best=float(pd.DataFrame(cand).sort_values(["macro_f1_2020","bal_acc_2020"],ascending=False).iloc[0]["C"])
    model=make_mode_pipe(best)
    model.fit(tr[MODE_FEATURES],tr["mode"])
    pred=model.predict(te[MODE_FEATURES])
    proba=model.predict_proba(te[MODE_FEATURES])
    classes=list(model.named_steps["lr"].classes_)
    cm=confusion_matrix(te["mode"],pred,labels=MODES).tolist()
    meta={
        "best_C":best,
        "n_train":int(len(tr)),
        "n_test":int(len(te)),
        "accuracy_2021":float(accuracy_score(te["mode"],pred)),
        "balanced_accuracy_2021":float(balanced_accuracy_score(te["mode"],pred)),
        "macro_f1_2021":float(f1_score(te["mode"],pred,average="macro")),
        "classes":classes,
        "confusion_matrix_labels":MODES,
        "confusion_matrix":cm,
        "actual_mode_share_2021":te["mode"].value_counts(normalize=True).mul(100).to_dict(),
        "pred_mode_share_2021":pd.Series(pred).value_counts(normalize=True).mul(100).to_dict(),
        "inner_selection":cand,
    }
    return model,tr,te,meta

def mode_features_from_row(row,side):
    s=v1.signed_state(row,side)
    s["side_long"]=float(side>0)
    return {k:s.get(k,np.nan) for k in MODE_FEATURES}

def predict_mode(model,row,side):
    X=pd.DataFrame([mode_features_from_row(row,side)])
    pred=model.predict(X)[0]
    p=model.predict_proba(X)[0]
    cls=model.named_steps["lr"].classes_
    return pred,{c:float(v) for c,v in zip(cls,p)}

def train_mode_policy(mode_rows):
    tr=mode_rows[mode_rows["ts"]<int(TRAIN_END.timestamp())].copy()
    out={}
    for m in MODES:
        g=tr[tr["mode"]==m]["duration_h"]
        out[m]={
            "n":int(len(g)),
            "q10_h":float(g.quantile(.10)),
            "q25_h":float(g.quantile(.25)),
            "median_h":float(g.median()),
            "q75_h":float(g.quantile(.75)),
        }
    # Enforce only behavior definitions, not PnL. SCALP can flip after training q10,
    # SWING cannot flip before 6h, EXTENDED cannot flip before 72h.
    min_hold={
        "SCALP":max(1,int(math.ceil(out["SCALP"]["q10_h"]*4))),
        "SWING":24,
        "EXTENDED":288,
    }
    confirm={"SCALP":2,"SWING":2,"EXTENDED":3}
    return out,min_hold,confirm

def simulate_mode(candles,hfast,mode_model,start,end,threshold,initial_side,min_hold_map,confirm_map,cost_side=0.0):
    df=candles[(candles["bar_start"]>=start-pd.Timedelta(minutes=15))&(candles["bar_start"]<end)].copy().reset_index(drop=True)
    prev=df[df["bar_start"]<start].iloc[-1]
    side=int(initial_side)
    first=df[df["bar_start"]>=start].iloc[0]
    entry_px=float(first["open"]); entry_ts=int(start.timestamp()); ectx=v1.entry_context(prev,side)
    mode,modep=predict_mode(mode_model,prev,side)

    equity=1.0; qty=side*equity/entry_px; last_px=entry_px; leg_eq=equity
    bars=0; streak=0; pending=False; pending_ctx=None; pending_mode=None; pending_modep=None
    equity-=equity*cost_side; legs=[]; curve=[]

    for _,r in df[df["bar_start"]>=start].iterrows():
        op=float(r["open"]); cl=float(r["close"])
        equity += qty*(op-last_px); last_px=op
        if pending:
            equity-=abs(qty)*op*cost_side
            legs.append({
                "return_pct":(equity/leg_eq-1)*100,"bars":bars,
                "direction":"LONG" if side>0 else "SHORT","mode":mode,
                "mode_conf":max(modep.values())
            })
            side=-side
            equity-=equity*cost_side
            qty=side*equity/op
            entry_px=op; entry_ts=int(r["bar_end_s"]-900); ectx=pending_ctx
            mode=pending_mode; modep=pending_modep
            leg_eq=equity; bars=0; streak=0; pending=False

        equity += qty*(cl-last_px); last_px=cl; bars+=1
        f=v1.hazard_state(r,side,entry_px,entry_ts,ectx)
        p=v1.fast_prob(hfast,f)
        mh=min_hold_map[mode]; cb=confirm_map[mode]
        if bars>=mh and p>=threshold: streak+=1
        else: streak=0
        if streak>=cb:
            pending=True
            pending_ctx=v1.entry_context(r,-side)
            pending_mode,pending_modep=predict_mode(mode_model,r,-side)
        curve.append((r["bar_start"],equity,p,side,bars,mode,max(modep.values())))

    equity-=abs(qty)*last_px*cost_side
    legs.append({
        "return_pct":(equity/leg_eq-1)*100,"bars":bars,
        "direction":"LONG" if side>0 else "SHORT","mode":mode,
        "mode_conf":max(modep.values())
    })
    l=pd.DataFrame(legs)
    c=pd.DataFrame(curve,columns=["ts","equity","hazard_p","side","bars","mode","mode_conf"])
    eq=c["equity"].to_numpy(); dd=eq/np.maximum.accumulate(eq)-1
    gp=l.loc[l.return_pct>0,"return_pct"].sum(); gl=-l.loc[l.return_pct<0,"return_pct"].sum()
    m={
        "return_pct":float((equity-1)*100),"mdd_pct":float(dd.min()*100),
        "legs":int(len(l)),"win_rate":float((l.return_pct>0).mean()*100),
        "pf":float(gp/max(gl,1e-12)),
        **v1.duration_stats(l["bars"]*.25),
        "mode_share":l["mode"].value_counts(normalize=True).mul(100).to_dict()
    }
    return m,l,c

def actual_mode_stats(year):
    ep=pd.read_csv(EP); dt=pd.to_datetime(ep["st"],unit="s",utc=True); g=ep[dt.dt.year==year].copy()
    g["mode"]=(g["duration_sec"]/3600.0).map(mode_label)
    return {
        "mode_share":g["mode"].value_counts(normalize=True).mul(100).to_dict(),
        "by_mode":{
            m:{
                "n":int(len(q)),
                "win_rate":float((q["ret_bps"]>0).mean()*100) if len(q) else math.nan,
                "median_h":float((q["duration_sec"]/3600.0).median()) if len(q) else math.nan,
            } for m,q in g.groupby("mode")
        }
    }

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    candles=v1.load_pa_candles()

    dmodel,_,_,dmeta=v1.fit_direction(candles)
    hazard=v1.build_hazard(candles)
    hmodel,htr,hte,hmeta=v1.fit_hazard(hazard)
    hfast=v1.compile_fast(hmodel,v1.HAZARD_FEATURES)

    # Reuse V1 global hazard calibration derived from 2020 behavior only.
    th,_,_,grid=v1.calibrate_2020(candles,hfast,htr)
    grid.to_csv(OUT/"base_hazard_calibration_2020.csv",index=False)

    mode_rows=build_mode_rows(candles)
    mode_model,mtr,mte,mm=v1_mode=fit_mode_model(mode_rows)
    policy,min_hold,confirm=train_mode_policy(mode_rows)

    prev=candles[candles["bar_start"]<TEST_START].iloc[-1]
    auto_side=v1.direction_at(dmodel,prev)
    actual_side=v1.actual_side_at(TEST_START)

    rows=[]; legs=[]
    for start_mode,side in [("CONDITIONAL_ACTUAL_START",actual_side),("FULLY_AUTONOMOUS",auto_side)]:
        for cname,cost in [("ZERO",0.0),("RT_004",0.0004/2)]:
            m,l,c=simulate_mode(candles,hfast,mode_model,TEST_START,TEST_END,th,side,min_hold,confirm,cost)
            flat={k:v for k,v in m.items() if k!="mode_share"}
            rows.append({
                "start_mode":start_mode,"cost":cname,
                "initial_side":"LONG" if side>0 else "SHORT",
                **flat,
                "mode_share_json":json.dumps(m["mode_share"],sort_keys=True)
            })
            l["start_mode"]=start_mode;l["cost"]=cname;legs.append(l)
            c.to_csv(OUT/f"curve_{start_mode}_{cname}.csv.gz",index=False,compression="gzip")

    summary=pd.DataFrame(rows)
    summary.to_csv(OUT/"summary_2021.csv",index=False)
    pd.concat(legs,ignore_index=True).to_csv(OUT/"legs_2021.csv",index=False)
    mode_rows.to_csv(OUT/"mode_dataset.csv",index=False)

    meta={
        "price_action_only":True,
        "mode_definition":{"SCALP":"<6h","SWING":"6-72h","EXTENDED":">=72h"},
        "mode_model":mm,
        "mode_policy_from_2019_2020":policy,
        "min_hold_bars":min_hold,
        "confirm_bars":confirm,
        "direction_model_2021":dmeta,
        "hazard_model_2021":hmeta,
        "hazard_threshold_from_2020_behavior":th,
        "actual_2021":v1.actual_stats(2021),
        "actual_mode_2021":actual_mode_stats(2021),
        "note":"Only OHLC-derived price action plus own entry price/time are used. Mode classifier is trained on 2019-2020 and tested on 2021. No 2021 PnL or behavior tunes any parameter."
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print("=== META ===");print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== 2021 SUMMARY ===");print(summary.to_string(index=False))

if __name__=="__main__":
    main()
