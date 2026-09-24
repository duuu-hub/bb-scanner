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
from sklearn.metrics import roc_auc_score, average_precision_score

import research.aoa_fidelity_v3.continuous_side_model as v3
import research.aoa_fidelity_v5.leg_state_flip_model as v5
import research.aoa_3way_oos.backtest_3way as b3

OUT=ROOT/"research"/"aoa_fidelity_v6"/"output"
HORIZONS=[1,4,8,16]  # 15m,1h,2h,4h; rerun after inherited simulator fix
THRESHOLDS=[.60,.70,.75,.80,.85,.90,.94,.97]
MINHOLDS=[4,8,16,24,32,48]


def future_flip_target(df,h):
    x=df["flip_target"].to_numpy(np.int8)
    n=len(x); y=np.zeros(n,np.int8)
    # H is small; explicit max is clearer and avoids alignment mistakes.
    for k in range(h):
        y[:n-k]=np.maximum(y[:n-k],x[k:])
    return y


def fit_for_horizon(panel,h):
    end=v5.TRAIN_END-pd.Timedelta(minutes=15*(h-1))
    tr=panel[(panel["bar_start"]>=v5.TRAIN_START)&(panel["bar_start"]<end)].copy().reset_index(drop=True)
    tr["target_h"]=future_flip_target(tr,h)
    X=v5.build_features(tr,tr["actual_side"],tr["hold_bars_actual"],
                        tr["leg_pnl_bps_actual"],tr["leg_mfe_bps_actual"],tr["leg_mae_bps_actual"])
    y=tr["target_h"].astype(int)
    m=Pipeline([
      ("imp",SimpleImputer(strategy="median")),
      ("sc",StandardScaler()),
      ("lr",LogisticRegression(max_iter=3000,class_weight="balanced",C=.30)),
    ])
    m.fit(X,y)
    return m,tr


def calibrate_all(panel):
    rows=[]; models={}; best=None
    for h in HORIZONS:
        model,tr=fit_for_horizon(panel,h); models[h]=(model,tr)
        actual=tr["actual_side"].to_numpy(np.int8)
        af=v3.flip_count(actual); ah=v3.median_run_hours(actual); init=int(actual[0])
        parts=v5.fast_parts(model,tr)
        for mh in MINHOLDS:
            for th in THRESHOLDS:
                pred,_=v5.simulate(model,tr,init,th,mh)
                acc=float((pred==actual).mean()); pf=v3.flip_count(pred); ph=v3.median_run_hours(pred)
                fr=(pf+1)/(af+1); hr=(ph+.25)/(ah+.25)
                score=acc-.16*abs(math.log(fr))-.12*abs(math.log(hr))
                rec={"horizon_bars":h,"horizon_min":h*15,"threshold":th,"min_hold_bars":mh,
                     "accuracy":acc,"pred_flips":pf,"actual_flips":af,"flip_ratio":fr,
                     "median_hold_h":ph,"actual_median_hold_h":ah,"hold_ratio":hr,"score":score}
                rows.append(rec)
                if best is None or score>best["score"]: best=rec
    return best,pd.DataFrame(rows),models


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    btc,ep,panel=v5.build_panel()
    best,grid,models=calibrate_all(panel)
    grid.to_csv(OUT/"calibration_grid.csv",index=False)
    h=int(best["horizon_bars"]); model,tr=models[h]

    va=panel[(panel["bar_start"]>=v5.VAL_START)&(panel["bar_start"]<v5.VAL_END)].copy().reset_index(drop=True)
    actual=va["actual_side"].to_numpy(np.int8)
    candles=b3.build_market_candles(btc)
    init_model,init_rows=v3.fit_initial_direction(candles)
    c0=candles.loc[candles["bar_start"]<v5.VAL_START].tail(1)
    p0=float(init_model.predict_proba(c0[v3.MARKET_FEATURES])[0,1]); init=1 if p0>=.5 else -1

    pred,probs=v5.simulate(model,va,init,float(best["threshold"]),int(best["min_hold_bars"]))
    va["pred_side"]=pred; va["p_flip_window"]=probs
    va.to_csv(OUT/"validation_2021.csv.gz",index=False,compression="gzip")

    # Diagnostic classification of "flip within H" using actual state only.
    vy=future_flip_target(va,h)
    Xv=v5.build_features(va,va["actual_side"],va["hold_bars_actual"],
                         va["leg_pnl_bps_actual"],va["leg_mfe_bps_actual"],va["leg_mae_bps_actual"])
    sp=model.predict_proba(Xv)[:,1]

    fidelity={
      "side_accuracy_pct":float((pred==actual).mean()*100),
      "actual_flips":v3.flip_count(actual),"pred_flips":v3.flip_count(pred),
      "actual_median_hold_h":v3.median_run_hours(actual),"pred_median_hold_h":v3.median_run_hours(pred),
      "window_target_roc_auc":float(roc_auc_score(vy,sp)),
      "window_target_pr_auc":float(average_precision_score(vy,sp)),
      "initial_p_long":p0,"initial_side":"LONG" if init>0 else "SHORT"
    }

    rows=[]
    for cname,cost in v3.SIDE_COSTS.items():
        rows.append({"series":"MODEL_V6","cost":cname,**v3.performance(va,pred,cost)})
        rows.append({"series":"ACTUAL_SIDE_ORACLE","cost":cname,**v3.performance(va,actual,cost)})
    summary=pd.DataFrame(rows); summary.to_csv(OUT/"summary.csv",index=False)

    meta={
      "training":"2019-07-16 through 2020-12-31 only",
      "validation":"2021 only",
      "candidate_horizons_min":[15,60,120,240],
      "selection":"horizon + threshold + minimum hold selected only by 2019-2020 path fidelity",
      "best_training_config":best,
      "validation_fidelity":fidelity,
      "note":"No 2022+ data used. V6 predicts an upcoming reversal window rather than the exact 15m reversal bar."
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print("=== META ===");print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== SUMMARY ===");print(summary.to_string(index=False))
    print("\n=== TOP TRAIN CONFIGS ===")
    print(grid.sort_values("score",ascending=False).head(20).to_string(index=False))


if __name__=="__main__":
    main()
