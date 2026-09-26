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

import research.aoa_price_action_v1.price_action_v1 as v1
import research.aoa_price_action_v3.price_action_v3 as v3

OUT=ROOT/"research"/"aoa_price_action_v4"/"output"
EP=v1.EP
TEST_START=v1.TEST_START
TEST_END=v1.TEST_END

# Pure OHLC path + own position state. No volume / RSI / BB / EMA / ATR / OI / funding.
POLICY_FEATURES=[
    "sret_15m","sret_30m","sret_1h","sret_2h","sret_4h","sret_8h","sret_12h","sret_24h","sret_3d",
    "sbody_bps","supper_wick_bps","slower_wick_bps","sclose_loc",
    "sup_frac_1h","sup_frac_4h","sloc_4h","sloc_24h","sloc_3d","sloc_7d",
    "sdist_high_4h","sdist_low_4h","sdist_high_24h","sdist_low_24h",
    "sbreak_4h","sbreak_24h",
    "leg_bps","leg_pos_bps","leg_neg_bps",
    "duration_log","age_lt1h","age_1_6h","age_6_24h","age_24_72h","age_72h_plus",
    "range_4h_bps","range_24h_bps","range_3d_bps","side_long",
    *[f"entry_{x}" for x in [
        "sret_1h","sret_4h","sret_24h","sret_3d","sbody_bps","sclose_loc",
        "sloc_24h","sloc_3d","sdist_high_24h","sdist_low_24h","range_24h_bps"
    ]],
    "mfe_bps","mae_bps","path_bps","eff_since_entry","pullback_from_mfe_bps","rebound_from_mae_bps",
]

DUR_KEYS=["pct_lt1h","pct_1_6h","pct_6_24h","pct_24_72h","pct_72h_plus"]


def make_pipe(C=0.10):
    return Pipeline([
        ("imp",SimpleImputer(strategy="median")),
        ("sc",StandardScaler()),
        ("lr",LogisticRegression(max_iter=5000,class_weight="balanced",C=C))
    ])



def update_path_state(state,row,side,entry_px):
    cl=float(row["close"]); hi=float(row["high"]); lo=float(row["low"])
    state["path_bps"] += abs(cl/state["prev_close"]-1.0)*10000.0
    state["prev_close"]=cl
    if side>0:
        fav=(hi/entry_px-1.0)*10000.0
        adv=(lo/entry_px-1.0)*10000.0
    else:
        fav=(1.0-lo/entry_px)*10000.0
        adv=(1.0-hi/entry_px)*10000.0
    state["mfe_bps"]=max(state["mfe_bps"],fav)
    state["mae_bps"]=min(state["mae_bps"],adv)
    net=side*(cl/entry_px-1.0)*10000.0
    eff=abs(net)/state["path_bps"] if state["path_bps"]>1e-9 else 0.0
    return {
        "mfe_bps":state["mfe_bps"],
        "mae_bps":state["mae_bps"],
        "path_bps":state["path_bps"],
        "eff_since_entry":eff,
        "pullback_from_mfe_bps":max(0.0,state["mfe_bps"]-net),
        "rebound_from_mae_bps":max(0.0,net-state["mae_bps"]),
    }

def build_policy_rows(candles):
    ep=pd.read_csv(EP)
    ends=candles["bar_end_s"].to_numpy(dtype=np.int64)
    rows=[]
    for _,e in ep.iterrows():
        st=int(e["st"]); et=int(e["et"]); side=1 if e["d"]=="L" else -1
        sidx=v1.completed_index(ends,st); eidx=v1.completed_index(ends,et)
        if sidx<0 or eidx<=sidx or eidx>=len(candles):
            continue
        erow=candles.iloc[sidx]
        entry_px=float(erow["close"])
        ectx=v1.entry_context(erow,side)
        path_state={"prev_close":entry_px,"path_bps":0.0,"mfe_bps":0.0,"mae_bps":0.0}
        for i in range(sidx+1,eidx+1):
            r=candles.iloc[i]
            f=v1.hazard_state(r,side,entry_px,st,ectx)
            f.update(update_path_state(path_state,r,side,entry_px))
            bars_left=eidx-i
            f.update({
                "ts":int(r["bar_end_s"]),
                "year":int(r["bar_start"].year),
                "episode":int(e["episode"]),
                "flip_soon_1h":int(bool(e["flip_close"]) and bars_left<=4),
                "hold_next_6h":int(bars_left>=24),
                "bars_left":int(bars_left),
            })
            rows.append({k:f.get(k,np.nan) for k in POLICY_FEATURES+[
                "ts","year","episode","flip_soon_1h","hold_next_6h","bars_left"
            ]})
    return pd.DataFrame(rows)


def fit_binary(train,valid,target,C=0.10):
    m=make_pipe(C)
    m.fit(train[POLICY_FEATURES],train[target])
    p=m.predict_proba(valid[POLICY_FEATURES])[:,1]
    return m,{
        "auc":float(roc_auc_score(valid[target],p)),
        "ap":float(average_precision_score(valid[target],p)),
        "base":float(valid[target].mean()),
    },p


def compile_fast(pipe):
    imp=pipe.named_steps["imp"]; sc=pipe.named_steps["sc"]; lr=pipe.named_steps["lr"]
    return {
        "med":np.asarray(imp.statistics_,float),
        "mean":np.asarray(sc.mean_,float),
        "scale":np.asarray(sc.scale_,float),
        "coef":np.asarray(lr.coef_[0],float),
        "intercept":float(lr.intercept_[0]),
    }


def fast_prob(model,f):
    x=np.asarray([f[k] for k in POLICY_FEATURES],float)
    bad=~np.isfinite(x)
    if bad.any():
        x[bad]=model["med"][bad]
    z=(x-model["mean"])/np.where(model["scale"]==0,1.0,model["scale"])
    s=model["intercept"]+float(np.dot(model["coef"],z))
    s=max(-50,min(50,s))
    return 1.0/(1.0+math.exp(-s))


def policy_features(row,side,entry_px,entry_ts,ectx,path_state):
    f=v1.hazard_state(row,side,entry_px,entry_ts,ectx)
    f.update(update_path_state(path_state,row,side,entry_px))
    return f


def behavior_score(m,actual):
    def lr(a,b,eps=.25):
        return abs(math.log((float(a)+eps)/(float(b)+eps)))
    s=abs(math.log(max(m["legs"],1)/max(actual["legs"],1)))
    s+=lr(m["median_h"],actual["median_h"])
    s+=0.5*lr(m["q25_h"],actual["q25_h"])
    s+=0.5*lr(m["q75_h"],actual["q75_h"])
    s+=1.25*sum(abs(m[k]-actual[k]) for k in DUR_KEYS)/100.0
    return s


def simulate(candles,flip_fast,hold_fast,start,end,flip_th,hold_th,min_hold,confirm,initial_side,cost_side=0.0):
    df=candles[(candles["bar_start"]>=start-pd.Timedelta(minutes=15))&(candles["bar_start"]<end)].copy().reset_index(drop=True)
    prev_idx=df.index[df["bar_start"]<start][-1]
    prev=df.loc[prev_idx]
    side=int(initial_side)
    first_idx=df.index[df["bar_start"]>=start][0]
    first=df.loc[first_idx]
    entry_px=float(first["open"])
    entry_ts=int(start.timestamp())
    ectx=v1.entry_context(prev,side)
    entry_i=first_idx-1

    equity=1.0; qty=side*equity/entry_px; last_px=entry_px; leg_eq=equity
    bars=0; streak=0; pending=False; pending_ctx=None
    path_state={"prev_close":entry_px,"path_bps":0.0,"mfe_bps":0.0,"mae_bps":0.0}
    equity-=equity*cost_side
    legs=[]; curve=[]

    for i,r in df[df["bar_start"]>=start].iterrows():
        op=float(r["open"]); cl=float(r["close"])
        equity += qty*(op-last_px); last_px=op

        if pending:
            equity-=abs(qty)*op*cost_side
            legs.append({
                "return_pct":(equity/leg_eq-1.0)*100,
                "bars":bars,
                "direction":"LONG" if side>0 else "SHORT"
            })
            side=-side
            equity-=equity*cost_side
            qty=side*equity/op
            entry_px=op
            entry_ts=int(r["bar_end_s"]-900)
            ectx=pending_ctx
            entry_i=i-1
            leg_eq=equity
            bars=0; streak=0; pending=False
            path_state={"prev_close":entry_px,"path_bps":0.0,"mfe_bps":0.0,"mae_bps":0.0}

        equity += qty*(cl-last_px); last_px=cl
        bars+=1

        f=policy_features(r,side,entry_px,entry_ts,ectx,path_state)
        pflip=fast_prob(flip_fast,f)
        phold=fast_prob(hold_fast,f)

        trigger=(bars>=min_hold and pflip>=flip_th and phold<=hold_th)
        streak=streak+1 if trigger else 0
        if streak>=confirm:
            pending=True
            pending_ctx=v1.entry_context(r,-side)

        curve.append((r["bar_start"],equity,pflip,phold,pflip-phold,side,bars))

    equity-=abs(qty)*last_px*cost_side
    legs.append({
        "return_pct":(equity/leg_eq-1.0)*100,
        "bars":bars,
        "direction":"LONG" if side>0 else "SHORT"
    })

    l=pd.DataFrame(legs)
    c=pd.DataFrame(curve,columns=["ts","equity","p_flip_1h","p_hold_6h","policy_gap","side","bars"])
    eq=c["equity"].to_numpy()
    dd=eq/np.maximum.accumulate(eq)-1
    gp=l.loc[l.return_pct>0,"return_pct"].sum()
    gl=-l.loc[l.return_pct<0,"return_pct"].sum()
    return {
        "return_pct":float((equity-1)*100),
        "mdd_pct":float(dd.min()*100),
        "legs":int(len(l)),
        "win_rate":float((l.return_pct>0).mean()*100),
        "pf":float(gp/max(gl,1e-12)),
        **v1.duration_stats(l["bars"]*.25),
    },l,c


def calibrate_2020(candles,flip_fast,hold_fast,valid2020):
    actual=v1.actual_stats(2020)

    # Score-scaled thresholds from the real 2020 state distribution.
    fp=valid2020["p_flip"].dropna()
    hp=valid2020["p_hold"].dropna()
    flip_candidates=sorted(set(float(fp.quantile(q)) for q in [.95,.975,.99]))
    hold_candidates=sorted(set(float(hp.quantile(q)) for q in [.35,.50,.65]))
    rows=[]

    for fth in flip_candidates:
        for hth in hold_candidates:
            for mh in [4]:
                for cb in [1,2]:
                    m,_,_=simulate(
                        candles,flip_fast,hold_fast,
                        pd.Timestamp("2020-01-01",tz="UTC"),
                        pd.Timestamp("2021-01-01",tz="UTC"),
                        fth,hth,mh,cb,
                        v1.actual_side_at(pd.Timestamp("2020-01-01",tz="UTC")),
                        0.0
                    )
                    rows.append({
                        "flip_threshold":fth,"hold_threshold":hth,
                        "min_hold_bars":mh,"confirm_bars":cb,
                        "behavior_score":behavior_score(m,actual),
                        **m
                    })
    tab=pd.DataFrame(rows).sort_values(["behavior_score","flip_threshold","hold_threshold"]).reset_index(drop=True)
    b=tab.iloc[0]
    return float(b.flip_threshold),float(b.hold_threshold),int(b.min_hold_bars),int(b.confirm_bars),tab


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    candles=v1.load_pa_candles()
    rows=build_policy_rows(candles)

    train2019=rows[rows["year"]==2019].copy()
    valid2020=rows[rows["year"]==2020].copy()
    test2021=rows[rows["year"]==2021].copy()

    # Clean walk-forward: fit on 2019 only, calibrate policy on 2020 behavior, test on 2021.
    flip_model,flip20,valid2020["p_flip"]=fit_binary(train2019,valid2020,"flip_soon_1h",0.10)
    hold_model,hold20,valid2020["p_hold"]=fit_binary(train2019,valid2020,"hold_next_6h",0.10)

    flip21=test2021.copy()
    hold21=test2021.copy()
    pflip21=flip_model.predict_proba(test2021[POLICY_FEATURES])[:,1]
    phold21=hold_model.predict_proba(test2021[POLICY_FEATURES])[:,1]
    flip21m={
        "auc":float(roc_auc_score(test2021["flip_soon_1h"],pflip21)),
        "ap":float(average_precision_score(test2021["flip_soon_1h"],pflip21)),
        "base":float(test2021["flip_soon_1h"].mean()),
    }
    hold21m={
        "auc":float(roc_auc_score(test2021["hold_next_6h"],phold21)),
        "ap":float(average_precision_score(test2021["hold_next_6h"],phold21)),
        "base":float(test2021["hold_next_6h"].mean()),
    }

    flip_fast=compile_fast(flip_model)
    hold_fast=compile_fast(hold_model)
    fth,hth,mh,cb,cal=calibrate_2020(candles,flip_fast,hold_fast,valid2020)
    cal.to_csv(OUT/"behavior_calibration_2020.csv",index=False)

    # Direction model is retained from PA-V1 only to make the fully autonomous start variant.
    dmodel,_,_,dmeta=v1.fit_direction(candles)
    prev=candles[candles["bar_start"]<TEST_START].iloc[-1]
    auto_side=v1.direction_at(dmodel,prev)
    actual_side=v1.actual_side_at(TEST_START)

    summary=[]; alllegs=[]
    for sm,side in [("CONDITIONAL_ACTUAL_START",actual_side),("FULLY_AUTONOMOUS",auto_side)]:
        for cname,cost in [("ZERO",0.0),("RT_004",0.0004/2)]:
            m,l,c=simulate(candles,flip_fast,hold_fast,TEST_START,TEST_END,fth,hth,mh,cb,side,cost)
            summary.append({
                "start_mode":sm,"cost":cname,
                "initial_side":"LONG" if side>0 else "SHORT",**m
            })
            l["start_mode"]=sm;l["cost"]=cname;alllegs.append(l)
            c.to_csv(OUT/f"curve_{sm}_{cname}.csv.gz",index=False,compression="gzip")

    sdf=pd.DataFrame(summary)
    sdf.to_csv(OUT/"summary_2021.csv",index=False)
    pd.concat(alllegs,ignore_index=True).to_csv(OUT/"legs_2021.csv",index=False)

    meta={
        "price_action_only":True,
        "walk_forward":"2019 model fit -> 2020 behavior-only threshold calibration -> 2021 untouched OOS",
        "flip_target":"actual flip occurs within next 1 hour",
        "hold_target":"actual position survives at least next 6 hours",
        "flip_2020":flip20,"hold_2020":hold20,
        "flip_2021":flip21m,"hold_2021":hold21m,
        "selected_flip_threshold":fth,
        "selected_hold_threshold":hth,
        "selected_min_hold_bars":mh,
        "selected_confirm_bars":cb,
        "actual_2020":v1.actual_stats(2020),
        "actual_2021":v1.actual_stats(2021),
        "direction_model_2021":dmeta,
        "excluded":["volume","RSI","Bollinger Bands","EMA","ATR","OI","funding","orderbook"],
        "note":"No PnL is used in threshold calibration. Policy flips only when flip-soon probability is high AND hold-next-6h probability is low."
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")

    print("=== META ===");print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== TOP 2020 BEHAVIOR CALIBRATION ===");print(cal.head(12).to_string(index=False))
    print("\n=== 2021 SUMMARY ===");print(sdf.to_string(index=False))


if __name__=="__main__":
    main()
