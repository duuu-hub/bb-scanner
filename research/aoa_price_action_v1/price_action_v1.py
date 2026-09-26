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

import research.aoa_market_context.analyze_market_context as mc

EP=ROOT/"research"/"aoa_market_context"/"aoa_episodes_2019h2_2021_compact.csv"
OUT=ROOT/"research"/"aoa_price_action_v1"/"output"
TRAIN_END=pd.Timestamp("2021-01-01",tz="UTC")
TEST_START=pd.Timestamp("2021-01-01",tz="UTC")
TEST_END=pd.Timestamp("2022-01-01",tz="UTC")

RET_WINDOWS={1:"15m",2:"30m",4:"1h",8:"2h",16:"4h",32:"8h",48:"12h",96:"24h",288:"3d",672:"7d"}
RANGE_WINDOWS={4:"1h",16:"4h",96:"24h",288:"3d",672:"7d"}

DIR_FEATURES=[
    *[f"ret_{name}" for name in RET_WINDOWS.values()],
    "body_bps","range_bps","upper_wick_bps","lower_wick_bps","close_loc",
    "up_frac_1h","up_frac_4h","eff_4h","eff_24h",
    "loc_4h","loc_24h","loc_3d","loc_7d",
    "dist_prev_high_4h","dist_prev_low_4h","dist_prev_high_24h","dist_prev_low_24h",
    "break_up_4h","break_dn_4h","break_up_24h","break_dn_24h",
    "reversal_15m_vs_4h","reversal_1h_vs_24h",
    "range_4h_bps","range_24h_bps","range_3d_bps",
]

HAZARD_FEATURES=[
    *[f"sret_{name}" for name in ["15m","30m","1h","2h","4h","8h","12h","24h","3d"]],
    "sbody_bps","supper_wick_bps","slower_wick_bps","sclose_loc",
    "sup_frac_1h","sup_frac_4h",
    "sloc_4h","sloc_24h","sloc_3d","sloc_7d",
    "sdist_high_4h","sdist_low_4h","sdist_high_24h","sdist_low_24h",
    "sbreak_4h","sbreak_24h",
    "leg_bps","leg_pos_bps","leg_neg_bps",
    "duration_log","age_lt1h","age_1_6h","age_6_24h","age_24_72h","age_72h_plus",
    "range_4h_bps","range_24h_bps","range_3d_bps",
    "side_long",
    *[f"entry_{x}" for x in [
        "sret_1h","sret_4h","sret_24h","sret_3d",
        "sbody_bps","sclose_loc","sloc_24h","sloc_3d",
        "sdist_high_24h","sdist_low_24h","range_24h_bps"
    ]]
]

DUR_KEYS=["pct_lt1h","pct_1_6h","pct_6_24h","pct_24_72h","pct_72h_plus"]


def add_price_action_features(df):
    x=df.copy().sort_values("bar_start").reset_index(drop=True)
    c=x["close"].astype(float); o=x["open"].astype(float); h=x["high"].astype(float); l=x["low"].astype(float)
    prev=c.shift(1)
    for n,name in RET_WINDOWS.items():
        x[f"ret_{name}"]=(c/c.shift(n)-1.0)*10000.0

    x["body_bps"]=(c/o-1.0)*10000.0
    x["range_bps"]=(h/l-1.0)*10000.0
    mx=pd.concat([o,c],axis=1).max(axis=1)
    mn=pd.concat([o,c],axis=1).min(axis=1)
    x["upper_wick_bps"]=(h/mx-1.0)*10000.0
    x["lower_wick_bps"]=(mn/l-1.0)*10000.0
    x["close_loc"]=(c-l)/(h-l).replace(0,np.nan)

    r1=(c/prev-1.0)
    x["up_frac_1h"]=(r1>0).rolling(4,min_periods=2).mean()
    x["up_frac_4h"]=(r1>0).rolling(16,min_periods=8).mean()

    path=c.pct_change().abs()
    x["eff_4h"]=(c/c.shift(16)-1).abs()/path.rolling(16,min_periods=8).sum().replace(0,np.nan)
    x["eff_24h"]=(c/c.shift(96)-1).abs()/path.rolling(96,min_periods=48).sum().replace(0,np.nan)

    for n,name in RANGE_WINDOWS.items():
        rh=h.rolling(n,min_periods=max(2,n//2)).max()
        rl=l.rolling(n,min_periods=max(2,n//2)).min()
        x[f"loc_{name}"]=(c-rl)/(rh-rl).replace(0,np.nan)
        x[f"range_{name}_bps"]=(rh/rl-1.0)*10000.0

        ph=h.shift(1).rolling(n,min_periods=max(2,n//2)).max()
        pl=l.shift(1).rolling(n,min_periods=max(2,n//2)).min()
        x[f"dist_prev_high_{name}"]=(c/ph-1.0)*10000.0
        x[f"dist_prev_low_{name}"]=(c/pl-1.0)*10000.0
        x[f"break_up_{name}"]=(c>ph).astype(float)
        x[f"break_dn_{name}"]=(c<pl).astype(float)

    x["reversal_15m_vs_4h"]=((np.sign(x["ret_15m"])!=np.sign(x["ret_4h"])) & x["ret_15m"].notna() & x["ret_4h"].notna()).astype(float)
    x["reversal_1h_vs_24h"]=((np.sign(x["ret_1h"])!=np.sign(x["ret_24h"])) & x["ret_1h"].notna() & x["ret_24h"].notna()).astype(float)
    return x


def load_pa_candles():
    raw=mc.load_candles()
    keep=["timestamp_ms","open","high","low","close","bar_start","bar_end_s"]
    return add_price_action_features(raw[keep].copy())


def completed_index(ends,ts):
    return np.searchsorted(ends,int(ts),side="right")-1


def make_pipe(C=0.25):
    return Pipeline([
        ("imp",SimpleImputer(strategy="median")),
        ("sc",StandardScaler()),
        ("lr",LogisticRegression(max_iter=5000,class_weight="balanced",C=C))
    ])


def fit_direction(candles):
    ep=pd.read_csv(EP)
    ends=candles["bar_end_s"].to_numpy(dtype=np.int64)
    rows=[]
    for _,e in ep.iterrows():
        i=completed_index(ends,e["st"])
        if i<0 or i>=len(candles):
            continue
        r={f:float(candles.iloc[i][f]) if pd.notna(candles.iloc[i][f]) else np.nan for f in DIR_FEATURES}
        r.update({"ts":int(e["st"]),"year":int(candles.iloc[i]["bar_start"].year),"is_long":int(e["d"]=="L")})
        rows.append(r)
    d=pd.DataFrame(rows)
    tr=d[d["ts"]<int(TRAIN_END.timestamp())].copy()
    te=d[(d["ts"]>=int(TEST_START.timestamp()))&(d["ts"]<int(TEST_END.timestamp()))].copy()

    candidates=[]
    inner_tr=tr[tr["year"]==2019]
    inner_va=tr[tr["year"]==2020]
    Cs=[0.05,0.10,0.25,0.50,1.0]
    if len(inner_tr)>20 and inner_tr["is_long"].nunique()==2 and len(inner_va)>20 and inner_va["is_long"].nunique()==2:
        for C in Cs:
            p=make_pipe(C); p.fit(inner_tr[DIR_FEATURES],inner_tr["is_long"])
            pr=p.predict_proba(inner_va[DIR_FEATURES])[:,1]
            candidates.append({"C":C,"auc_2020":float(roc_auc_score(inner_va["is_long"],pr))})
        best_C=float(pd.DataFrame(candidates).sort_values("auc_2020",ascending=False).iloc[0]["C"])
    else:
        best_C=0.25

    model=make_pipe(best_C); model.fit(tr[DIR_FEATURES],tr["is_long"])
    p=model.predict_proba(te[DIR_FEATURES])[:,1]
    auc=float(roc_auc_score(te["is_long"],p)) if te["is_long"].nunique()==2 else math.nan
    acc=float(((p>=.5).astype(int)==te["is_long"].to_numpy()).mean())
    coef={f:float(v) for f,v in zip(DIR_FEATURES,model.named_steps["lr"].coef_[0])}
    return model,tr,te,{"best_C":best_C,"auc_2021":auc,"accuracy_2021":acc,"n_train":len(tr),"n_test":len(te),"candidates":candidates,"coef":dict(sorted(coef.items(),key=lambda kv:abs(kv[1]),reverse=True))}


def signed_state(row,side):
    def sval(name):
        v=float(row[name]) if pd.notna(row[name]) else np.nan
        return side*v if np.isfinite(v) else np.nan
    f={}
    for name in ["15m","30m","1h","2h","4h","8h","12h","24h","3d"]:
        f[f"sret_{name}"]=sval(f"ret_{name}")
    f["sbody_bps"]=sval("body_bps")
    # Upper/lower wick are geometric, flip roles by side.
    up=float(row["upper_wick_bps"]); lo=float(row["lower_wick_bps"])
    f["supper_wick_bps"]=up if side>0 else lo
    f["slower_wick_bps"]=lo if side>0 else up
    loc=float(row["close_loc"])
    f["sclose_loc"]=(loc-.5)*side if np.isfinite(loc) else np.nan
    f["sup_frac_1h"]=(float(row["up_frac_1h"])-.5)*side
    f["sup_frac_4h"]=(float(row["up_frac_4h"])-.5)*side
    for name in ["4h","24h","3d","7d"]:
        locv=float(row[f"loc_{name}"])
        f[f"sloc_{name}"]=(locv-.5)*side if np.isfinite(locv) else np.nan
    # Distances are transformed so positive means more room / breakout in current direction.
    if side>0:
        f["sdist_high_4h"]=float(row["dist_prev_high_4h"]); f["sdist_low_4h"]=float(row["dist_prev_low_4h"])
        f["sdist_high_24h"]=float(row["dist_prev_high_24h"]); f["sdist_low_24h"]=float(row["dist_prev_low_24h"])
        f["sbreak_4h"]=float(row["break_up_4h"]-row["break_dn_4h"])
        f["sbreak_24h"]=float(row["break_up_24h"]-row["break_dn_24h"])
    else:
        f["sdist_high_4h"]=-float(row["dist_prev_low_4h"]); f["sdist_low_4h"]=-float(row["dist_prev_high_4h"])
        f["sdist_high_24h"]=-float(row["dist_prev_low_24h"]); f["sdist_low_24h"]=-float(row["dist_prev_high_24h"])
        f["sbreak_4h"]=float(row["break_dn_4h"]-row["break_up_4h"])
        f["sbreak_24h"]=float(row["break_dn_24h"]-row["break_up_24h"])
    for name in ["4h","24h","3d"]:
        f[f"range_{name}_bps"]=float(row[f"range_{name}_bps"])
    return f


def entry_context(row,side):
    s=signed_state(row,side)
    keys=["sret_1h","sret_4h","sret_24h","sret_3d","sbody_bps","sclose_loc","sloc_24h","sloc_3d","sdist_high_24h","sdist_low_24h","range_24h_bps"]
    return {f"entry_{k}":s[k] for k in keys}


def hazard_state(row,side,entry_px,entry_ts,ectx):
    f=signed_state(row,side)
    leg=side*(float(row["close"])/entry_px-1.0)*10000.0
    hrs=max(0.0,(float(row["bar_end_s"])-entry_ts)/3600.0)
    f.update({
        "leg_bps":leg,"leg_pos_bps":max(leg,0.0),"leg_neg_bps":max(-leg,0.0),
        "duration_log":math.log1p(hrs),
        "age_lt1h":float(hrs<1),"age_1_6h":float(1<=hrs<6),"age_6_24h":float(6<=hrs<24),
        "age_24_72h":float(24<=hrs<72),"age_72h_plus":float(hrs>=72),
        "side_long":float(side>0),
    })
    f.update(ectx)
    return f


def build_hazard(candles):
    ep=pd.read_csv(EP)
    ends=candles["bar_end_s"].to_numpy(dtype=np.int64)
    rows=[]
    for _,e in ep.iterrows():
        st=int(e["st"]); et=int(e["et"]); side=1 if e["d"]=="L" else -1
        sidx=completed_index(ends,st); eidx=completed_index(ends,et)
        if sidx<0 or eidx<=sidx or eidx>=len(candles):
            continue
        erow=candles.iloc[sidx]
        ectx=entry_context(erow,side)
        entry_px=float(erow["close"])
        for i in range(sidx+1,eidx+1):
            r=candles.iloc[i]
            f=hazard_state(r,side,entry_px,st,ectx)
            f.update({"ts":int(r["bar_end_s"]),"year":int(r["bar_start"].year),"flip":int(i==eidx and bool(e["flip_close"]))})
            rows.append(f)
    return pd.DataFrame(rows)


def fit_hazard(h):
    tr=h[h["ts"]<int(TRAIN_END.timestamp())].copy()
    te=h[(h["ts"]>=int(TEST_START.timestamp()))&(h["ts"]<int(TEST_END.timestamp()))].copy()
    candidates=[]
    inner_tr=tr[tr["year"]==2019]; inner_va=tr[tr["year"]==2020]
    Cs=[0.05,0.10,0.25,0.50,1.0]
    if len(inner_tr)>50 and inner_tr["flip"].nunique()==2 and len(inner_va)>50 and inner_va["flip"].nunique()==2:
        for C in Cs:
            p=make_pipe(C); p.fit(inner_tr[HAZARD_FEATURES],inner_tr["flip"])
            pr=p.predict_proba(inner_va[HAZARD_FEATURES])[:,1]
            candidates.append({"C":C,"auc_2020":float(roc_auc_score(inner_va["flip"],pr)),"ap_2020":float(average_precision_score(inner_va["flip"],pr))})
        best_C=float(pd.DataFrame(candidates).sort_values(["ap_2020","auc_2020"],ascending=False).iloc[0]["C"])
    else:
        best_C=0.25

    model=make_pipe(best_C); model.fit(tr[HAZARD_FEATURES],tr["flip"])
    tr["p"]=model.predict_proba(tr[HAZARD_FEATURES])[:,1]
    te["p"]=model.predict_proba(te[HAZARD_FEATURES])[:,1]
    auc=float(roc_auc_score(te["flip"],te["p"]))
    ap=float(average_precision_score(te["flip"],te["p"]))
    base=float(te["flip"].mean())
    coef={f:float(v) for f,v in zip(HAZARD_FEATURES,model.named_steps["lr"].coef_[0])}
    return model,tr,te,{"best_C":best_C,"auc_2021":auc,"ap_2021":ap,"base_2021":base,"ap_lift":ap/base if base>0 else None,"n_train":len(tr),"n_test":len(te),"candidates":candidates,"coef":dict(sorted(coef.items(),key=lambda kv:abs(kv[1]),reverse=True))}


def compile_fast(pipe,features):
    imp=pipe.named_steps["imp"]; sc=pipe.named_steps["sc"]; lr=pipe.named_steps["lr"]
    return {"med":np.asarray(imp.statistics_,float),"mean":np.asarray(sc.mean_,float),"scale":np.asarray(sc.scale_,float),"coef":np.asarray(lr.coef_[0],float),"intercept":float(lr.intercept_[0]),"features":features}


def fast_prob(m,f):
    x=np.asarray([f[k] for k in m["features"]],float)
    bad=~np.isfinite(x)
    if bad.any(): x[bad]=m["med"][bad]
    z=(x-m["mean"])/np.where(m["scale"]==0,1.0,m["scale"])
    s=m["intercept"]+float(np.dot(m["coef"],z))
    return 1/(1+math.exp(-max(-50,min(50,s))))


def duration_stats(hours):
    a=np.asarray(hours,float)
    return {
        "q25_h":float(np.quantile(a,.25)),"median_h":float(np.median(a)),"q75_h":float(np.quantile(a,.75)),
        "pct_lt1h":float(np.mean(a<1)*100),"pct_1_6h":float(np.mean((a>=1)&(a<6))*100),
        "pct_6_24h":float(np.mean((a>=6)&(a<24))*100),"pct_24_72h":float(np.mean((a>=24)&(a<72))*100),
        "pct_72h_plus":float(np.mean(a>=72)*100)
    }


def actual_stats(year):
    ep=pd.read_csv(EP); dt=pd.to_datetime(ep["st"],unit="s",utc=True); g=ep[dt.dt.year==year].copy()
    return {"legs":len(g),"win_rate":float((g["ret_bps"]>0).mean()*100),**duration_stats(g["duration_sec"]/3600.0)}


def actual_side_at(ts):
    ep=pd.read_csv(EP); t=int(pd.Timestamp(ts).timestamp())
    g=ep[(ep["st"]<=t)&(ep["et"]>t)]
    if g.empty: g=ep[ep["st"]<=t].sort_values("st").tail(1)
    return 1 if g.iloc[0]["d"]=="L" else -1


def simulate(candles,hfast,start,end,threshold,min_hold,confirm,initial_side,cost_side=0.0):
    df=candles[(candles["bar_start"]>=start-pd.Timedelta(minutes=15))&(candles["bar_start"]<end)].copy().reset_index(drop=True)
    prev=df[df["bar_start"]<start].iloc[-1]
    side=int(initial_side)
    first=df[df["bar_start"]>=start].iloc[0]
    entry_px=float(first["open"]); entry_ts=int(start.timestamp()); ectx=entry_context(prev,side)
    equity=1.0; qty=side*equity/entry_px; last_px=entry_px; leg_eq=equity; bars=0; streak=0; pending=False; pending_ctx=None
    equity-=equity*cost_side; legs=[]; curve=[]

    for _,r in df[df["bar_start"]>=start].iterrows():
        op=float(r["open"]); cl=float(r["close"])
        equity += qty*(op-last_px); last_px=op
        if pending:
            equity-=abs(qty)*op*cost_side
            legs.append({"return_pct":(equity/leg_eq-1)*100,"bars":bars,"direction":"LONG" if side>0 else "SHORT"})
            side=-side
            equity-=equity*cost_side
            qty=side*equity/op
            entry_px=op; entry_ts=int(r["bar_end_s"]-900); ectx=pending_ctx; leg_eq=equity; bars=0; streak=0; pending=False
        equity += qty*(cl-last_px); last_px=cl; bars+=1
        f=hazard_state(r,side,entry_px,entry_ts,ectx); p=fast_prob(hfast,f)
        if bars>=min_hold and p>=threshold: streak+=1
        else: streak=0
        if streak>=confirm:
            pending=True; pending_ctx=entry_context(r,-side)
        curve.append((r["bar_start"],equity,p,side,bars))

    equity-=abs(qty)*last_px*cost_side
    legs.append({"return_pct":(equity/leg_eq-1)*100,"bars":bars,"direction":"LONG" if side>0 else "SHORT"})
    l=pd.DataFrame(legs); c=pd.DataFrame(curve,columns=["ts","equity","hazard_p","side","bars"])
    eq=c["equity"].to_numpy(); dd=eq/np.maximum.accumulate(eq)-1
    gp=l.loc[l.return_pct>0,"return_pct"].sum(); gl=-l.loc[l.return_pct<0,"return_pct"].sum()
    return {"return_pct":float((equity-1)*100),"mdd_pct":float(dd.min()*100),"legs":len(l),"win_rate":float((l.return_pct>0).mean()*100),"pf":float(gp/max(gl,1e-12)),**duration_stats(l["bars"]*.25)},l,c


def calibrate_2020(candles,hfast,tr):
    actual=actual_stats(2020)
    flip_scores=tr.loc[tr.flip==1,"p"].dropna()
    thresholds=sorted(set(float(flip_scores.quantile(q)) for q in [.75,.85,.90,.95]))
    ep=pd.read_csv(EP); dt=pd.to_datetime(ep["st"],unit="s",utc=True); dur=ep.loc[dt<TRAIN_END,"duration_sec"]/900
    mins=sorted(set(max(1,int(math.ceil(float(dur.quantile(q))))) for q in [.25,.33,.50]))
    rows=[]
    for th in thresholds:
        for mh in mins:
            for cb in [2,3]:
                m,_,_=simulate(candles,hfast,pd.Timestamp("2020-01-01",tz="UTC"),pd.Timestamp("2021-01-01",tz="UTC"),th,mh,cb,actual_side_at("2020-01-01"),0.0)
                score=abs(math.log(max(m["legs"],1)/actual["legs"])) + abs(math.log((m["median_h"]+.25)/(actual["median_h"]+.25)))
                score += .5*abs(math.log((m["q75_h"]+.25)/(actual["q75_h"]+.25)))
                for k in ["pct_lt1h","pct_1_6h","pct_6_24h","pct_24_72h","pct_72h_plus"]:
                    score += .01*abs(m[k]-actual[k])
                rows.append({"threshold":th,"min_hold":mh,"confirm":cb,"score":score,**m})
    tab=pd.DataFrame(rows).sort_values("score").reset_index(drop=True); b=tab.iloc[0]
    return float(b.threshold),int(b.min_hold),int(b.confirm),tab


def direction_at(model,row):
    X=pd.DataFrame([{f:row[f] for f in DIR_FEATURES}])
    return 1 if float(model.predict_proba(X)[0,1])>=.5 else -1


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    candles=load_pa_candles()
    dmodel,dtr,dte,dmeta=fit_direction(candles)
    hazard=build_hazard(candles)
    hmodel,htr,hte,hmeta=fit_hazard(hazard)
    hfast=compile_fast(hmodel,HAZARD_FEATURES)
    th,mh,cb,grid=calibrate_2020(candles,hfast,htr)
    grid.to_csv(OUT/"calibration_2020.csv",index=False)

    prev=candles[candles["bar_start"]<TEST_START].iloc[-1]
    autonomous_side=direction_at(dmodel,prev)
    actual_side=actual_side_at(TEST_START)

    rows=[]; alllegs=[]
    for mode,side in [("CONDITIONAL_ACTUAL_START",actual_side),("FULLY_AUTONOMOUS",autonomous_side)]:
        for cname,cost in [("ZERO",0.0),("RT_004",0.0004/2)]:
            m,l,c=simulate(candles,hfast,TEST_START,TEST_END,th,mh,cb,side,cost)
            rows.append({"mode":mode,"cost":cname,"initial_side":"LONG" if side>0 else "SHORT",**m})
            l["mode"]=mode; l["cost"]=cname; alllegs.append(l)
            c.to_csv(OUT/f"curve_{mode}_{cname}.csv.gz",index=False,compression="gzip")

    summary=pd.DataFrame(rows); summary.to_csv(OUT/"summary_2021.csv",index=False)
    pd.concat(alllegs,ignore_index=True).to_csv(OUT/"legs_2021.csv",index=False)

    meta={
        "price_action_only":True,
        "excluded":["volume","RSI","Bollinger Bands","EMA","ATR","OI","funding","orderbook"],
        "direction_model":dmeta,
        "hazard_model":hmeta,
        "selected_threshold":th,"selected_min_hold":mh,"selected_confirm":cb,
        "actual_2020":actual_stats(2020),"actual_2021":actual_stats(2021),
        "autonomous_initial_side_2021":"LONG" if autonomous_side>0 else "SHORT",
        "actual_initial_side_2021":"LONG" if actual_side>0 else "SHORT",
        "note":"All model predictors are derived only from OHLC price path and the agent's own entry price/time. Trigger calibration uses 2020 behavior fidelity only, never PnL. 2021 is validation."
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print("=== META ==="); print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== 2021 ==="); print(summary.to_string(index=False))
    print("\n=== TOP CALIBRATION ==="); print(grid.head(12).to_string(index=False))

if __name__=="__main__":
    main()
