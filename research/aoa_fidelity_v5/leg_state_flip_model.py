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

import research.aoa_3way_oos.backtest_3way as b3
import research.aoa_fidelity_v3.continuous_side_model as v3

OUT=ROOT/"research"/"aoa_fidelity_v5"/"output"
EP=v3.EP
TRAIN_START=v3.TRAIN_START
TRAIN_END=v3.TRAIN_END
VAL_START=v3.VAL_START
VAL_END=v3.VAL_END
EXPOSURE=v3.EXPOSURE
SIDE_COSTS=v3.SIDE_COSTS

ABS=["rv4h","rv24h","atr14_pct","bb_width20","vol_z96","er24h"]
SIGNED=["ret15m","ret1h","ret4h","ret24h","ret3d","ret7d","bb_z20","ema20_80","trend_z24h","dd7d"]
FEATURES=["state_side","log_hold_bars"]+ABS+["signed_"+x for x in SIGNED]+[
    "signed_rsi_center","signed_range_center",
    "leg_pnl_atr","leg_mfe_atr","leg_mae_atr","leg_giveback_atr"
]


def episode_idx_at(times_s, episodes):
    st=episodes["st"].to_numpy(np.int64)
    et=episodes["et"].to_numpy(np.int64)
    arr=np.asarray(times_s,dtype=np.int64)
    idx=np.searchsorted(st,arr,side="right")-1
    good=(idx>=0)
    safe=np.maximum(idx,0)
    good &= arr < et[safe]
    out=np.where(good,idx,-1)
    return out.astype(np.int32)


def build_panel():
    btc=b3.load_raw(b3.BTC_DIR)
    c=b3.build_market_candles(btc).copy()
    ep=pd.read_csv(EP).sort_values("st").reset_index(drop=True)
    ep_side=ep["d"].map({"L":1,"S":-1}).to_numpy(np.int8)
    start_s=(c["timestamp_ms"]//1000).astype(np.int64).to_numpy()
    idx=episode_idx_at(start_s,ep)
    idx_next=episode_idx_at(start_s+900,ep)
    side=np.where(idx>=0,ep_side[np.maximum(idx,0)],0).astype(np.int8)
    side_next=np.where(idx_next>=0,ep_side[np.maximum(idx_next,0)],0).astype(np.int8)

    # Entry reference: last fully completed BTC 15m close before actual episode starts.
    ends=c["bar_end_s"].to_numpy(np.int64)
    closes=c["close"].to_numpy(float)
    ep_st=ep["st"].to_numpy(np.int64)
    ref_i=np.searchsorted(ends,ep_st,side="right")-1
    ref_i=np.clip(ref_i,0,len(c)-1)
    ep_ref=closes[ref_i]

    c["episode_idx"]=idx
    c["actual_side"]=side
    c["next_side"]=side_next
    c["flip_target"]=((side!=0)&(side_next!=0)&(side_next!=side)).astype(np.int8)
    ref=np.full(len(c),np.nan)
    good=idx>=0
    ref[good]=ep_ref[idx[good]]
    c["entry_ref"]=ref
    hold=np.zeros(len(c),float)
    hold[good]=(start_s[good]-ep_st[idx[good]])/900.0
    c["hold_bars_actual"]=np.maximum(hold,0)

    # Causal market inputs known before current 15m bar begins.
    c["prev_close"]=c["close"].shift(1)
    for col in set(ABS+SIGNED+["rsi14","range_pos24h"]):
        c["p_"+col]=c[col].shift(1)

    # Actual leg state as of previous completed bar.
    s=c["actual_side"].to_numpy(float)
    entry=c["entry_ref"].to_numpy(float)
    prev=c["prev_close"].to_numpy(float)
    pnl=s*(prev/entry-1.0)*10000.0
    c["leg_pnl_bps_actual"]=pnl

    # Excursions through each completed bar, shifted so current decision sees only prior bars.
    fav=np.full(len(c),np.nan); adv=np.full(len(c),np.nan)
    long=s>0; short=s<0
    fav[long]=(c.loc[long,"high"].to_numpy(float)/entry[long]-1)*10000
    adv[long]=(c.loc[long,"low"].to_numpy(float)/entry[long]-1)*10000
    fav[short]=(1-c.loc[short,"low"].to_numpy(float)/entry[short])*10000
    adv[short]=(1-c.loc[short,"high"].to_numpy(float)/entry[short])*10000
    tmp=pd.DataFrame({"ep":idx,"fav":fav,"adv":adv})
    tmp.loc[tmp["ep"]<0,["fav","adv"]]=np.nan
    mfe=tmp.groupby("ep",sort=False)["fav"].cummax().groupby(tmp["ep"],sort=False).shift(1)
    mae=tmp.groupby("ep",sort=False)["adv"].cummin().groupby(tmp["ep"],sort=False).shift(1)
    c["leg_mfe_bps_actual"]=mfe.fillna(0).to_numpy()
    c["leg_mae_bps_actual"]=mae.fillna(0).to_numpy()

    c=c[(c["bar_start"]>=TRAIN_START)&(c["bar_start"]<VAL_END)].copy()
    c=c[(c["actual_side"]!=0)&(c["next_side"]!=0)].reset_index(drop=True)
    return btc,ep,c


def build_features(df,side,hold,pnl_bps,mfe_bps,mae_bps):
    z=pd.DataFrame(index=df.index)
    s=np.asarray(side,float); h=np.asarray(hold,float)
    atr=df["p_atr14_pct"].to_numpy(float)
    atr=np.where(np.isfinite(atr)&(atr>1e-9),atr,np.nan)
    z["state_side"]=s
    z["log_hold_bars"]=np.log1p(np.maximum(h,0))
    for col in ABS: z[col]=df["p_"+col].to_numpy(float)
    for col in SIGNED: z["signed_"+col]=s*df["p_"+col].to_numpy(float)
    z["signed_rsi_center"]=s*(df["p_rsi14"].to_numpy(float)-50)
    z["signed_range_center"]=s*(df["p_range_pos24h"].to_numpy(float)-.5)
    pnl=np.asarray(pnl_bps,float); mfe=np.asarray(mfe_bps,float); mae=np.asarray(mae_bps,float)
    z["leg_pnl_atr"]=pnl/atr
    z["leg_mfe_atr"]=mfe/atr
    z["leg_mae_atr"]=mae/atr
    z["leg_giveback_atr"]=(mfe-pnl)/atr
    return z[FEATURES]


def fit_model(panel):
    tr=panel[(panel["bar_start"]>=TRAIN_START)&(panel["bar_start"]<TRAIN_END)].copy()
    X=build_features(tr,tr["actual_side"],tr["hold_bars_actual"],
                     tr["leg_pnl_bps_actual"],tr["leg_mfe_bps_actual"],tr["leg_mae_bps_actual"])
    y=tr["flip_target"].astype(int)
    model=Pipeline([
        ("imp",SimpleImputer(strategy="median")),
        ("sc",StandardScaler()),
        ("lr",LogisticRegression(max_iter=3000,class_weight="balanced",C=0.30)),
    ])
    model.fit(X,y)
    return model,tr


def fast_parts(model,df):
    imp=model.named_steps["imp"]; sc=model.named_steps["sc"]; lr=model.named_steps["lr"]
    stats=np.asarray(imp.statistics_,float)
    mean=np.asarray(sc.mean_,float); scale=np.asarray(sc.scale_,float)
    coef=np.asarray(lr.coef_[0],float); w=coef/scale
    intercept=float(lr.intercept_[0]-np.sum(coef*mean/scale))
    ix={f:i for i,f in enumerate(FEATURES)}
    n=len(df)
    static=np.zeros(n,float)
    for col in ABS:
        v=df["p_"+col].to_numpy(float); j=ix[col]
        v=np.where(np.isfinite(v),v,stats[j]); static+=w[j]*v

    def directional(side):
        out=np.zeros(n,float)
        for col in SIGNED:
            src=df["p_"+col].to_numpy(float); j=ix["signed_"+col]
            val=side*src; val=np.where(np.isfinite(val),val,stats[j]); out+=w[j]*val
        src=df["p_rsi14"].to_numpy(float)-50; j=ix["signed_rsi_center"]
        val=side*src; val=np.where(np.isfinite(val),val,stats[j]); out+=w[j]*val
        src=df["p_range_pos24h"].to_numpy(float)-.5; j=ix["signed_range_center"]
        val=side*src; val=np.where(np.isfinite(val),val,stats[j]); out+=w[j]*val
        return out
    dyn={k:w[ix[k]] for k in ["state_side","log_hold_bars","leg_pnl_atr","leg_mfe_atr","leg_mae_atr","leg_giveback_atr"]}
    dyn["stats"]={k:stats[ix[k]] for k in ["leg_pnl_atr","leg_mfe_atr","leg_mae_atr","leg_giveback_atr"]}
    return dict(intercept=intercept,static=static,pos=directional(1),neg=directional(-1),dyn=dyn)


def sigmoid(x):
    if x>=0:return 1/(1+math.exp(-min(x,700)))
    e=math.exp(max(x,-700));return e/(1+e)


def simulate(model,df,initial_side,threshold,min_hold_bars):
    p=fast_parts(model,df)
    n=len(df)
    side=int(initial_side)
    hold=0
    entry=float(df["open"].iloc[0])
    mfe=0.0; mae=0.0
    pred=np.empty(n,np.int8); probs=np.empty(n,float)
    for i,r in enumerate(df.itertuples(index=False)):
        prev_close=float(r.prev_close) if np.isfinite(r.prev_close) else float(r.open)
        atr=float(r.p_atr14_pct) if np.isfinite(r.p_atr14_pct) and r.p_atr14_pct>1e-9 else math.nan
        pnl=side*(prev_close/entry-1)*10000
        if np.isfinite(atr):
            vals={
                "leg_pnl_atr":pnl/atr,
                "leg_mfe_atr":mfe/atr,
                "leg_mae_atr":mae/atr,
                "leg_giveback_atr":(mfe-pnl)/atr,
            }
        else:
            vals={k:p["dyn"]["stats"][k] for k in p["dyn"]["stats"]}
        logit=(p["intercept"]+p["static"][i]+(p["pos"][i] if side>0 else p["neg"][i])+
               p["dyn"]["state_side"]*side+p["dyn"]["log_hold_bars"]*math.log1p(hold))
        for k,v in vals.items():
            if not np.isfinite(v):v=p["dyn"]["stats"][k]
            logit+=p["dyn"][k]*v
        prob=sigmoid(logit)
        probs[i]=prob

        if hold>=min_hold_bars and prob>=threshold:
            side=-side
            hold=0
            entry=float(r.open)
            mfe=0.0; mae=0.0

        pred[i]=side
        if side>0:
            fav=(float(r.high)/entry-1)*10000
            adv=(float(r.low)/entry-1)*10000
        else:
            fav=(1-float(r.low)/entry)*10000
            adv=(1-float(r.high)/entry)*10000
        mfe=max(mfe,fav); mae=min(mae,adv)
        hold+=1
    return pred,probs


def calibrate(model,tr):
    actual=tr["actual_side"].to_numpy(np.int8)
    actual_flips=v3.flip_count(actual)
    actual_hold=v3.median_run_hours(actual)
    init=int(actual[0])
    thresholds=[.80,.85,.88,.90,.92,.94,.96,.97,.98,.99,.995]
    minholds=[0,2,4,8,16,24,32]
    rows=[];best=None
    for mh in minholds:
        for th in thresholds:
            pred,_=simulate(model,tr,init,th,mh)
            acc=float((pred==actual).mean())
            fc=v3.flip_count(pred); med=v3.median_run_hours(pred)
            fr=(fc+1)/(actual_flips+1); hr=(med+.25)/(actual_hold+.25)
            score=acc-0.12*abs(math.log(fr))-0.10*abs(math.log(hr))
            rec={"threshold":th,"min_hold_bars":mh,"accuracy":acc,"pred_flips":fc,
                 "actual_flips":actual_flips,"flip_ratio":fr,"median_hold_h":med,
                 "actual_median_hold_h":actual_hold,"hold_ratio":hr,"score":score}
            rows.append(rec)
            if best is None or score>best["score"]:best=rec
    return best,pd.DataFrame(rows)


def performance(df,side,cost):
    return v3.performance(df,side,cost)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    btc,ep,panel=build_panel()
    model,tr=fit_model(panel)
    best,grid=calibrate(model,tr)
    grid.to_csv(OUT/"calibration_grid.csv",index=False)

    va=panel[(panel["bar_start"]>=VAL_START)&(panel["bar_start"]<VAL_END)].copy().reset_index(drop=True)
    actual=va["actual_side"].to_numpy(np.int8)
    # Starting side is predicted only from pre-2021 market context, never actual 2021 state.
    candles=b3.build_market_candles(btc)
    init_model,init_rows=v3.fit_initial_direction(candles)
    first=va.iloc[[0]]
    # Initial model expects unshifted market feature names from the current candle dataset.
    c0=candles.loc[candles["bar_start"]<VAL_START].tail(1)
    p0=float(init_model.predict_proba(c0[v3.MARKET_FEATURES])[0,1])
    init=1 if p0>=.5 else -1

    pred,probs=simulate(model,va,init,best["threshold"],int(best["min_hold_bars"]))
    va["pred_side"]=pred;va["p_flip"]=probs
    va.to_csv(OUT/"validation_2021.csv.gz",index=False,compression="gzip")

    # Static event classification with actual state, for diagnostic only.
    Xv=build_features(va,va["actual_side"],va["hold_bars_actual"],
                      va["leg_pnl_bps_actual"],va["leg_mfe_bps_actual"],va["leg_mae_bps_actual"])
    sp=model.predict_proba(Xv)[:,1]
    y=va["flip_target"].to_numpy(int)
    static=(sp>=best["threshold"]).astype(int)
    fidelity={
        "side_accuracy_pct":float((pred==actual).mean()*100),
        "actual_flips":v3.flip_count(actual),
        "pred_flips":v3.flip_count(pred),
        "actual_median_hold_h":v3.median_run_hours(actual),
        "pred_median_hold_h":v3.median_run_hours(pred),
        "flip_static_roc_auc":float(roc_auc_score(y,sp)),
        "flip_static_pr_auc":float(average_precision_score(y,sp)),
        "flip_static_precision":float(precision_score(y,static,zero_division=0)),
        "flip_static_recall":float(recall_score(y,static,zero_division=0)),
        "initial_p_long":p0,"initial_side":"LONG" if init>0 else "SHORT",
    }

    rows=[]
    for cname,cost in SIDE_COSTS.items():
        rows.append({"series":"MODEL_V5","cost":cname,**performance(va,pred,cost)})
        rows.append({"series":"ACTUAL_SIDE_ORACLE","cost":cname,**performance(va,actual,cost)})
    summary=pd.DataFrame(rows);summary.to_csv(OUT/"summary.csv",index=False)

    coef=model.named_steps["lr"].coef_[0]
    meta={
        "training":"2019-07-16 through 2020-12-31 only",
        "validation":"2021 only",
        "target":"flip within next 15m from causal previous-bar market data",
        "state_features":"predicted/actual leg hold, signed PnL from leg reference, MFE, MAE, giveback in ATR units",
        "training_rows":int(len(tr)),
        "training_flip_rate_pct":float(tr["flip_target"].mean()*100),
        "calibration_training_only":best,
        "validation_fidelity":fidelity,
        "top_coefficients":dict(sorted(zip(FEATURES,map(float,coef)),key=lambda kv:abs(kv[1]),reverse=True)[:18]),
        "note":"No 2022+ data used. Entry reference is a 15m BTC market-price proxy, not exact BitMEX weighted average entry."
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print("=== META ===");print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== SUMMARY ===");print(summary.to_string(index=False))
    print("\n=== BEST TRAINING CALIBRATION ===");print(best)


if __name__=="__main__":
    main()
