#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

import research.aoa_price_action_v1.price_action_v1 as v1
import research.aoa_price_action_v4.price_action_v4 as v4

OUT=ROOT/"research"/"aoa_price_action_v41"/"output"
TEST_START=v1.TEST_START
TEST_END=v1.TEST_END
FEATURES=v4.POLICY_FEATURES
DUR_KEYS=v4.DUR_KEYS


def build_competing_rows(candles):
    rows=v4.build_policy_rows(candles)
    d=rows[(rows["flip_soon_1h"]==1)|(rows["hold_next_6h"]==1)].copy()
    d["flip_vs_hold"]=(d["flip_soon_1h"]==1).astype(int)
    return rows,d


def fit_competing(train,valid):
    model=v4.make_pipe(0.10)
    model.fit(train[FEATURES],train["flip_vs_hold"])
    p=model.predict_proba(valid[FEATURES])[:,1]
    m={
        "auc":float(roc_auc_score(valid["flip_vs_hold"],p)),
        "ap":float(average_precision_score(valid["flip_vs_hold"],p)),
        "base":float(valid["flip_vs_hold"].mean()),
        "n":int(len(valid)),
        "positive_n":int(valid["flip_vs_hold"].sum())
    }
    return model,m,p


def fast_prob(m,f):
    return v4.fast_prob(m,f)


def simulate(candles,fast,start,end,threshold,min_hold,confirm,initial_side,cost_side=0.0):
    df=candles[(candles["bar_start"]>=start-pd.Timedelta(minutes=15))&(candles["bar_start"]<end)].copy().reset_index(drop=True)
    prev_idx=df.index[df["bar_start"]<start][-1]
    prev=df.loc[prev_idx]
    side=int(initial_side)
    first_idx=df.index[df["bar_start"]>=start][0]
    first=df.loc[first_idx]

    entry_px=float(first["open"])
    entry_ts=int(start.timestamp())
    ectx=v1.entry_context(prev,side)
    path_state={"prev_close":entry_px,"path_bps":0.0,"mfe_bps":0.0,"mae_bps":0.0}

    equity=1.0; qty=side*equity/entry_px; last_px=entry_px; leg_eq=equity
    bars=0; streak=0; pending=False; pending_ctx=None
    equity-=equity*cost_side
    legs=[]; curve=[]

    for _,r in df[df["bar_start"]>=start].iterrows():
        op=float(r["open"]); cl=float(r["close"])
        equity += qty*(op-last_px); last_px=op

        if pending:
            equity-=abs(qty)*op*cost_side
            legs.append({"return_pct":(equity/leg_eq-1.0)*100,"bars":bars,"direction":"LONG" if side>0 else "SHORT"})
            side=-side
            equity-=equity*cost_side
            qty=side*equity/op
            entry_px=op
            entry_ts=int(r["bar_end_s"]-900)
            ectx=pending_ctx
            path_state={"prev_close":entry_px,"path_bps":0.0,"mfe_bps":0.0,"mae_bps":0.0}
            leg_eq=equity; bars=0; streak=0; pending=False

        equity += qty*(cl-last_px); last_px=cl
        bars+=1
        f=v1.hazard_state(r,side,entry_px,entry_ts,ectx)
        f.update(v4.update_path_state(path_state,r,side,entry_px))
        p=fast_prob(fast,f)

        trigger=(bars>=min_hold and p>=threshold)
        streak=streak+1 if trigger else 0
        if streak>=confirm:
            pending=True
            pending_ctx=v1.entry_context(r,-side)

        curve.append((r["bar_start"],equity,p,side,bars))

    equity-=abs(qty)*last_px*cost_side
    legs.append({"return_pct":(equity/leg_eq-1.0)*100,"bars":bars,"direction":"LONG" if side>0 else "SHORT"})

    l=pd.DataFrame(legs)
    c=pd.DataFrame(curve,columns=["ts","equity","p_flip_vs_hold","side","bars"])
    eq=c["equity"].to_numpy(); dd=eq/np.maximum.accumulate(eq)-1
    gp=l.loc[l.return_pct>0,"return_pct"].sum(); gl=-l.loc[l.return_pct<0,"return_pct"].sum()
    return {
        "return_pct":float((equity-1)*100),
        "mdd_pct":float(dd.min()*100),
        "legs":int(len(l)),
        "win_rate":float((l.return_pct>0).mean()*100),
        "pf":float(gp/max(gl,1e-12)),
        **v1.duration_stats(l["bars"]*.25)
    },l,c


def behavior_score(m,actual):
    def lr(a,b,eps=.25):
        return abs(math.log((float(a)+eps)/(float(b)+eps)))
    s=abs(math.log(max(m["legs"],1)/max(actual["legs"],1)))
    s+=lr(m["median_h"],actual["median_h"])
    s+=0.5*lr(m["q25_h"],actual["q25_h"])
    s+=0.5*lr(m["q75_h"],actual["q75_h"])
    s+=1.25*sum(abs(m[k]-actual[k]) for k in DUR_KEYS)/100.0
    return s


def calibrate_2020(candles,fast,score_2020_all):
    actual=v1.actual_stats(2020)
    q=score_2020_all.dropna()
    thresholds=sorted(set(float(q.quantile(x)) for x in [.90,.95,.975,.99,.995]))
    rows=[]
    for th in thresholds:
        for mh in [2,4,8]:
            for cb in [1,2]:
                m,_,_=simulate(
                    candles,fast,
                    pd.Timestamp("2020-01-01",tz="UTC"),
                    pd.Timestamp("2021-01-01",tz="UTC"),
                    th,mh,cb,
                    v1.actual_side_at(pd.Timestamp("2020-01-01",tz="UTC")),
                    0.0
                )
                rows.append({"threshold":th,"min_hold_bars":mh,"confirm_bars":cb,"behavior_score":behavior_score(m,actual),**m})
    tab=pd.DataFrame(rows).sort_values(["behavior_score","threshold"]).reset_index(drop=True)
    b=tab.iloc[0]
    return float(b.threshold),int(b.min_hold_bars),int(b.confirm_bars),tab


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    candles=v1.load_pa_candles()
    all_rows,decisive=build_competing_rows(candles)

    tr=decisive[decisive["year"]==2019].copy()
    va=decisive[decisive["year"]==2020].copy()
    te=decisive[decisive["year"]==2021].copy()

    model,m20,p20=fit_competing(tr,va)
    p21=model.predict_proba(te[FEATURES])[:,1]
    m21={
        "auc":float(roc_auc_score(te["flip_vs_hold"],p21)),
        "ap":float(average_precision_score(te["flip_vs_hold"],p21)),
        "base":float(te["flip_vs_hold"].mean()),
        "n":int(len(te)),
        "positive_n":int(te["flip_vs_hold"].sum())
    }

    # Score every real 2020 state only to set behavior threshold quantiles; no PnL.
    all20=all_rows[all_rows["year"]==2020].copy()
    all20["score"]=model.predict_proba(all20[FEATURES])[:,1]

    fast=v4.compile_fast(model)
    th,mh,cb,cal=calibrate_2020(candles,fast,all20["score"])
    cal.to_csv(OUT/"behavior_calibration_2020.csv",index=False)

    dmodel,_,_,dmeta=v1.fit_direction(candles)
    prev=candles[candles["bar_start"]<TEST_START].iloc[-1]
    auto_side=v1.direction_at(dmodel,prev)
    actual_side=v1.actual_side_at(TEST_START)

    rows=[]; legs=[]
    for sm,side in [("CONDITIONAL_ACTUAL_START",actual_side),("FULLY_AUTONOMOUS",auto_side)]:
        for cname,cost in [("ZERO",0.0),("RT_004",0.0004/2)]:
            m,l,c=simulate(candles,fast,TEST_START,TEST_END,th,mh,cb,side,cost)
            rows.append({"start_mode":sm,"cost":cname,"initial_side":"LONG" if side>0 else "SHORT",**m})
            l["start_mode"]=sm;l["cost"]=cname;legs.append(l)
            c.to_csv(OUT/f"curve_{sm}_{cname}.csv.gz",index=False,compression="gzip")

    summary=pd.DataFrame(rows)
    summary.to_csv(OUT/"summary_2021.csv",index=False)
    pd.concat(legs,ignore_index=True).to_csv(OUT/"legs_2021.csv",index=False)

    meta={
        "price_action_only":True,
        "policy":"direct competing-state classifier: FLIP within 1h vs HOLD at least 6h",
        "walk_forward":"2019 model fit -> 2020 behavior-only threshold calibration -> 2021 untouched OOS",
        "competing_2020":m20,
        "competing_2021":m21,
        "selected_threshold":th,
        "selected_min_hold_bars":mh,
        "selected_confirm_bars":cb,
        "actual_2020":v1.actual_stats(2020),
        "actual_2021":v1.actual_stats(2021),
        "direction_model_2021":dmeta,
        "note":"Ambiguous 1-6h-to-exit states are excluded from classifier fitting. No PnL is used in calibration."
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")

    print("=== META ===");print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== TOP 2020 CALIBRATION ===");print(cal.head(12).to_string(index=False))
    print("\n=== 2021 SUMMARY ===");print(summary.to_string(index=False))


if __name__=="__main__":
    main()
