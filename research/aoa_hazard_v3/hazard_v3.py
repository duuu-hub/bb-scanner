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

def sim_one(candles,model,start,end,threshold,min_hold_bars,cost_side=0.0):
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

def choose_threshold(candles,model,train_probs,min_hold_bars):
    actual=actual_stats(2020)
    # Candidate thresholds are probability quantiles, not PnL optimized.
    qs=[0.90,0.93,0.95,0.96,0.97,0.98,0.985,0.99,0.9925,0.995,0.9975]
    vals=sorted(set(float(train_probs.quantile(q)) for q in qs))
    rows=[]
    for th in vals:
        m,_,_=sim_one(candles,model,pd.Timestamp("2020-01-01",tz="UTC"),pd.Timestamp("2021-01-01",tz="UTC"),th,min_hold_bars,0.0)
        count_err=abs(m["legs"]-actual["legs"])/max(1,actual["legs"])
        dur_err=abs(math.log(max(m["median_duration_h"],0.25)/max(actual["median_duration_h"],0.25)))
        score=count_err+dur_err
        rows.append({"threshold":th,"fidelity_score":score,**m})
    tab=pd.DataFrame(rows).sort_values(["fidelity_score","threshold"])
    return float(tab.iloc[0]["threshold"]),tab

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    btc,eth,candles,model_meta=era.prep()
    h=build_hazard_rows(candles)
    model,tr,te,auc,ap,base,coef=fit_model(h)

    ep=pd.read_csv(EP)
    dt=pd.to_datetime(ep["st"],unit="s",utc=True)
    train_ep=ep[dt<TRAIN_END]
    q10=float(np.quantile(train_ep["duration_sec"]/900.0,0.10))
    min_hold=max(1,int(round(q10)))

    th,cal=choose_threshold(candles,model,tr["p"],min_hold)
    cal.to_csv(OUT/"threshold_fidelity_2020.csv",index=False)

    results=[]
    leg_out=[]
    for cname,cost in [("ZERO",0.0),("RT_004",0.0004/2)]:
        m,l,c=sim_one(candles,model,TEST_START,TEST_END,th,min_hold,cost)
        results.append({"cost":cname,**m})
        l["cost"]=cname; leg_out.append(l)
        c.to_csv(OUT/f"curve_2021_{cname}.csv.gz",index=False,compression="gzip")
    pd.DataFrame(results).to_csv(OUT/"summary_2021.csv",index=False)
    pd.concat(leg_out,ignore_index=True).to_csv(OUT/"legs_2021.csv",index=False)

    # Event-level ranking diagnostics on actual 2021 state path.
    pos=te[te.flip==1]["p"]
    neg=te[te.flip==0]["p"]
    diag={
        "test_2021_auc":auc,
        "test_2021_average_precision":ap,
        "test_2021_base_flip_rate":base,
        "ap_lift_vs_base":float(ap/base) if base>0 else None,
        "train_rows":int(len(tr)),
        "train_flips":int(tr.flip.sum()),
        "test_rows":int(len(te)),
        "test_flips":int(te.flip.sum()),
        "threshold_from_2020_fidelity":th,
        "min_hold_bars_from_train_q10":min_hold,
        "actual_2020":actual_stats(2020),
        "actual_2021":actual_stats(2021),
        "median_p_at_actual_2021_flip":float(pos.median()),
        "median_p_at_nonflip_2021":float(neg.median()),
        "top_coefficients":coef,
        "note":"Threshold chosen ONLY to match 2020 leg count/median duration, never PnL. 2021 is untouched validation."
    }
    (OUT/"meta.json").write_text(json.dumps(diag,ensure_ascii=False,indent=2),encoding="utf-8")
    print("=== META ===");print(json.dumps(diag,ensure_ascii=False,indent=2))
    print("\n=== 2020 THRESHOLD FIDELITY ===");print(cal.to_string(index=False))
    print("\n=== 2021 AUTONOMOUS ===");print(pd.DataFrame(results).to_string(index=False))

if __name__=="__main__":
    main()
