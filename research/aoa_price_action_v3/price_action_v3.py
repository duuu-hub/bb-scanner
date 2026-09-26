#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, f1_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

import research.aoa_price_action_v1.price_action_v1 as v1

OUT=ROOT/"research"/"aoa_price_action_v3"/"output"
EP=v1.EP
TRAIN_END=v1.TRAIN_END
TEST_START=v1.TEST_START
TEST_END=v1.TEST_END

BASE_STATE=[
    "sret_15m","sret_30m","sret_1h","sret_2h","sret_4h","sret_8h","sret_12h","sret_24h","sret_3d",
    "sbody_bps","supper_wick_bps","slower_wick_bps","sclose_loc",
    "sup_frac_1h","sup_frac_4h","sloc_4h","sloc_24h","sloc_3d","sloc_7d",
    "sdist_high_4h","sdist_low_4h","sdist_high_24h","sdist_low_24h",
    "sbreak_4h","sbreak_24h",
    "leg_bps","leg_pos_bps","leg_neg_bps",
    "range_4h_bps","range_24h_bps","range_3d_bps","side_long",
]
ENTRY_KEYS=[f"entry_{x}" for x in [
    "sret_1h","sret_4h","sret_24h","sret_3d","sbody_bps","sclose_loc",
    "sloc_24h","sloc_3d","sdist_high_24h","sdist_low_24h","range_24h_bps"
]]
PATH_KEYS=["mfe_bps","mae_bps","path_bps","eff_since_entry","pullback_from_mfe_bps","rebound_from_mae_bps"]
GATE_FEATURES=BASE_STATE+ENTRY_KEYS+PATH_KEYS

def make_pipe(C):
    return Pipeline([
        ("imp",SimpleImputer(strategy="median")),
        ("sc",StandardScaler()),
        ("lr",LogisticRegression(max_iter=5000,class_weight="balanced",C=C))
    ])

def path_features(candles,sidx,i,side,entry_px):
    z=candles.iloc[sidx+1:i+1]
    if z.empty:
        return {k:0.0 for k in PATH_KEYS}
    hi=z["high"].astype(float).to_numpy()
    lo=z["low"].astype(float).to_numpy()
    cl=z["close"].astype(float).to_numpy()
    if side>0:
        fav=(hi.max()/entry_px-1.0)*10000.0
        adv=(lo.min()/entry_px-1.0)*10000.0
    else:
        fav=(1.0-lo.min()/entry_px)*10000.0
        adv=(1.0-hi.max()/entry_px)*10000.0
    prev=np.r_[entry_px,cl[:-1]]
    path=float(np.sum(np.abs(cl/prev-1.0))*10000.0)
    net=side*(cl[-1]/entry_px-1.0)*10000.0
    eff=abs(net)/path if path>1e-9 else 0.0
    pull=max(0.0,fav-net)
    rebound=max(0.0,net-adv)
    return {
        "mfe_bps":float(fav),"mae_bps":float(adv),"path_bps":path,
        "eff_since_entry":float(eff),"pullback_from_mfe_bps":float(pull),
        "rebound_from_mae_bps":float(rebound),
    }

def gate_features(candles,sidx,i,side,entry_px,entry_ts,ectx):
    r=candles.iloc[i]
    f=v1.hazard_state(r,side,entry_px,entry_ts,ectx)
    out={k:f.get(k,np.nan) for k in BASE_STATE+ENTRY_KEYS}
    out.update(path_features(candles,sidx,i,side,entry_px))
    return out

def build_gate_rows(candles,checkpoint_h,target_h):
    ep=pd.read_csv(EP)
    ends=candles["bar_end_s"].to_numpy(dtype=np.int64)
    rows=[]
    checkpoint_bars=int(round(checkpoint_h*4))
    for _,e in ep.iterrows():
        dur_h=float(e["duration_sec"])/3600.0
        if dur_h < checkpoint_h:
            continue
        st=int(e["st"]); side=1 if e["d"]=="L" else -1
        sidx=v1.completed_index(ends,st)
        i=sidx+checkpoint_bars
        if sidx<0 or i>=len(candles):
            continue
        entry_row=candles.iloc[sidx]
        entry_px=float(entry_row["close"])
        ectx=v1.entry_context(entry_row,side)
        f=gate_features(candles,sidx,i,side,entry_px,st,ectx)
        f.update({
            "episode":int(e["episode"]),"ts":st,
            "year":int(entry_row["bar_start"].year),
            "target":int(dur_h>=target_h),"duration_h":dur_h
        })
        rows.append(f)
    return pd.DataFrame(rows)

def fit_gate(rows,name):
    tr=rows[rows["ts"]<int(TRAIN_END.timestamp())].copy()
    te=rows[(rows["ts"]>=int(TEST_START.timestamp()))&(rows["ts"]<int(TEST_END.timestamp()))].copy()
    inner_tr=tr[tr["year"]==2019]
    inner_va=tr[tr["year"]==2020]
    grid=[]
    for C in [0.03,0.05,0.10,0.25,0.50,1.0]:
        m=make_pipe(C);m.fit(inner_tr[GATE_FEATURES],inner_tr["target"])
        p=m.predict_proba(inner_va[GATE_FEATURES])[:,1]
        for th in [0.35,0.40,0.45,0.50,0.55,0.60,0.65]:
            pred=(p>=th).astype(int)
            grid.append({
                "C":C,"threshold":th,
                "bal_acc_2020":float(balanced_accuracy_score(inner_va["target"],pred)),
                "f1_2020":float(f1_score(inner_va["target"],pred)),
            })
    gd=pd.DataFrame(grid).sort_values(["f1_2020","bal_acc_2020"],ascending=False).reset_index(drop=True)
    best=gd.iloc[0]; C=float(best.C); th=float(best.threshold)
    model=make_pipe(C);model.fit(tr[GATE_FEATURES],tr["target"])
    p=model.predict_proba(te[GATE_FEATURES])[:,1]
    pred=(p>=th).astype(int)
    meta={
        "name":name,"best_C":C,"threshold":th,
        "n_train":int(len(tr)),"n_test":int(len(te)),
        "base_rate_2021":float(te["target"].mean()),
        "balanced_accuracy_2021":float(balanced_accuracy_score(te["target"],pred)),
        "f1_2021":float(f1_score(te["target"],pred)),
        "auc_2021":float(roc_auc_score(te["target"],p)),
        "pred_positive_2021":float(pred.mean()),
        "inner_top":gd.head(10).to_dict(orient="records")
    }
    return model,th,meta,gd

def predict_gate(model,row):
    return float(model.predict_proba(pd.DataFrame([row])[GATE_FEATURES])[:,1][0])

def simulate(candles,hfast,g6,t6,g72,t72,start,end,threshold,min_hold,confirm,initial_side,cost_side=0.0):
    df=candles[(candles["bar_start"]>=start-pd.Timedelta(minutes=15))&(candles["bar_start"]<end)].copy().reset_index(drop=True)
    prev_idx=df.index[df["bar_start"]<start][-1]
    prev=df.loc[prev_idx]
    side=int(initial_side)
    first_idx=df.index[df["bar_start"]>=start][0]
    first=df.loc[first_idx]
    entry_px=float(first["open"]); entry_ts=int(start.timestamp()); ectx=v1.entry_context(prev,side)
    entry_i=first_idx-1  # state before first test bar
    equity=1.0;qty=side*equity/entry_px;last_px=entry_px;leg_eq=equity
    bars=0;streak=0;pending=False;pending_ctx=None
    protect6=False; protect72=False; gate6_p=np.nan; gate72_p=np.nan
    equity-=equity*cost_side
    legs=[];curve=[]

    for i,r in df[df["bar_start"]>=start].iterrows():
        op=float(r["open"]);cl=float(r["close"])
        equity += qty*(op-last_px);last_px=op

        if pending:
            equity-=abs(qty)*op*cost_side
            legs.append({
                "return_pct":(equity/leg_eq-1)*100,"bars":bars,
                "direction":"LONG" if side>0 else "SHORT",
                "protect6":protect6,"protect72":protect72,
                "gate6_p":gate6_p,"gate72_p":gate72_p
            })
            side=-side
            equity-=equity*cost_side
            qty=side*equity/op
            entry_px=op;entry_ts=int(r["bar_end_s"]-900);ectx=pending_ctx
            entry_i=i-1
            leg_eq=equity;bars=0;streak=0;pending=False
            protect6=False;protect72=False;gate6_p=np.nan;gate72_p=np.nan

        equity += qty*(cl-last_px);last_px=cl;bars+=1

        # Dynamic promotion: do not decide the final horizon at entry.
        if bars==4:
            gf=gate_features(df,entry_i,i,side,entry_px,entry_ts,ectx)
            gate6_p=predict_gate(g6,gf);protect6=gate6_p>=t6
        if bars==24:
            gf=gate_features(df,entry_i,i,side,entry_px,entry_ts,ectx)
            gate72_p=predict_gate(g72,gf);protect72=gate72_p>=t72

        protected=(protect6 and bars<24) or (protect72 and bars<288)
        f=v1.hazard_state(r,side,entry_px,entry_ts,ectx)
        p=v1.fast_prob(hfast,f)
        if (not protected) and bars>=min_hold and p>=threshold:
            streak+=1
        else:
            streak=0
        if streak>=confirm:
            pending=True;pending_ctx=v1.entry_context(r,-side)

        phase="EXTENDED" if protect72 and bars>=24 else ("SWING" if protect6 and bars>=4 else "SCALP")
        curve.append((r["bar_start"],equity,p,side,bars,phase,protect6,protect72,gate6_p,gate72_p))

    equity-=abs(qty)*last_px*cost_side
    legs.append({
        "return_pct":(equity/leg_eq-1)*100,"bars":bars,
        "direction":"LONG" if side>0 else "SHORT",
        "protect6":protect6,"protect72":protect72,
        "gate6_p":gate6_p,"gate72_p":gate72_p
    })
    l=pd.DataFrame(legs)
    c=pd.DataFrame(curve,columns=["ts","equity","hazard_p","side","bars","phase","protect6","protect72","gate6_p","gate72_p"])
    eq=c["equity"].to_numpy();dd=eq/np.maximum.accumulate(eq)-1
    gp=l.loc[l.return_pct>0,"return_pct"].sum();gl=-l.loc[l.return_pct<0,"return_pct"].sum()
    return {
        "return_pct":float((equity-1)*100),"mdd_pct":float(dd.min()*100),
        "legs":int(len(l)),"win_rate":float((l.return_pct>0).mean()*100),
        "pf":float(gp/max(gl,1e-12)),
        **v1.duration_stats(l["bars"]*.25),
        "protect6_leg_pct":float(l["protect6"].mean()*100),
        "protect72_leg_pct":float(l["protect72"].mean()*100),
    },l,c

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    candles=v1.load_pa_candles()

    dmodel,_,_,dmeta=v1.fit_direction(candles)
    hazard=v1.build_hazard(candles)
    hmodel,htr,_,hmeta=v1.fit_hazard(hazard)
    hfast=v1.compile_fast(hmodel,v1.HAZARD_FEATURES)
    th,mh,cb,cal=v1.calibrate_2020(candles,hfast,htr)
    cal.to_csv(OUT/"hazard_calibration_2020.csv",index=False)

    gate6_rows=build_gate_rows(candles,1,6)
    gate72_rows=build_gate_rows(candles,6,72)
    g6,t6,m6,grid6=fit_gate(gate6_rows,"survive_1h_to_6h")
    g72,t72,m72,grid72=fit_gate(gate72_rows,"survive_6h_to_72h")
    grid6.to_csv(OUT/"gate6_grid.csv",index=False)
    grid72.to_csv(OUT/"gate72_grid.csv",index=False)
    gate6_rows.to_csv(OUT/"gate6_dataset.csv",index=False)
    gate72_rows.to_csv(OUT/"gate72_dataset.csv",index=False)

    prev=candles[candles["bar_start"]<TEST_START].iloc[-1]
    auto_side=v1.direction_at(dmodel,prev)
    actual_side=v1.actual_side_at(TEST_START)

    rows=[];legs=[]
    for sm,side in [("CONDITIONAL_ACTUAL_START",actual_side),("FULLY_AUTONOMOUS",auto_side)]:
        for cname,cost in [("ZERO",0.0),("RT_004",0.0004/2)]:
            m,l,c=simulate(candles,hfast,g6,t6,g72,t72,TEST_START,TEST_END,th,mh,cb,side,cost)
            rows.append({"start_mode":sm,"cost":cname,"initial_side":"LONG" if side>0 else "SHORT",**m})
            l["start_mode"]=sm;l["cost"]=cname;legs.append(l)
            c.to_csv(OUT/f"curve_{sm}_{cname}.csv.gz",index=False,compression="gzip")

    summary=pd.DataFrame(rows);summary.to_csv(OUT/"summary_2021.csv",index=False)
    pd.concat(legs,ignore_index=True).to_csv(OUT/"legs_2021.csv",index=False)

    meta={
        "price_action_only":True,
        "design":"dynamic horizon promotion: evaluate >=6h at +1h, evaluate >=72h at +6h",
        "gate6":m6,"gate72":m72,
        "direction_model":dmeta,"hazard_model":hmeta,
        "hazard_threshold":th,"hazard_min_hold":mh,"hazard_confirm":cb,
        "actual_2021":v1.actual_stats(2021),
        "note":"All predictors are OHLC-derived plus own entry price/time. Gate and hazard hyperparameters use only 2019->2020 validation; 2021 is untouched."
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print("=== META ===");print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== 2021 SUMMARY ===");print(summary.to_string(index=False))

if __name__=="__main__":
    main()
