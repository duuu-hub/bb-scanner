#!/usr/bin/env python3
from __future__ import annotations

import json, math, sys
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

import research.aoa_price_action_v1.price_action_v1 as v1
import research.aoa_price_action_v3.price_action_v3 as v3

OUT=ROOT/"research"/"aoa_price_action_v31"/"output"
TEST_START=v1.TEST_START
TEST_END=v1.TEST_END

DUR_KEYS=["pct_lt1h","pct_1_6h","pct_6_24h","pct_24_72h","pct_72h_plus"]

def behavior_score(model,actual):
    def lr(a,b,eps=.25):
        return abs(math.log((float(a)+eps)/(float(b)+eps)))
    score=abs(math.log(max(model["legs"],1)/max(actual["legs"],1)))
    score+=lr(model["median_h"],actual["median_h"])
    score+=0.5*lr(model["q25_h"],actual["q25_h"])
    score+=0.5*lr(model["q75_h"],actual["q75_h"])
    score+=1.25*sum(abs(model[k]-actual[k]) for k in DUR_KEYS)/100.0
    return score

def calibrate_2020(candles,hfast,g6,g72,hth,hmh,hcb):
    actual=v1.actual_stats(2020)
    rows=[]
    # Behavior-only threshold grid. No PnL appears in the objective.
    for t6 in [0.40,0.50,0.60,0.70,0.80]:
        for t72 in [0.40,0.50,0.60,0.70,0.80]:
            m,_,_=v3.simulate(
                candles,hfast,g6,t6,g72,t72,
                pd.Timestamp("2020-01-01",tz="UTC"),
                pd.Timestamp("2021-01-01",tz="UTC"),
                hth,hmh,hcb,v1.actual_side_at(pd.Timestamp("2020-01-01",tz="UTC")),0.0
            )
            rows.append({
                "gate6_threshold":t6,
                "gate72_threshold":t72,
                "behavior_score":behavior_score(m,actual),
                **m
            })
    tab=pd.DataFrame(rows).sort_values(["behavior_score","gate6_threshold","gate72_threshold"]).reset_index(drop=True)
    b=tab.iloc[0]
    return float(b.gate6_threshold),float(b.gate72_threshold),tab

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    candles=v1.load_pa_candles()

    dmodel,_,_,dmeta=v1.fit_direction(candles)
    hazard=v1.build_hazard(candles)
    hmodel,htr,_,hmeta=v1.fit_hazard(hazard)
    hfast=v1.compile_fast(hmodel,v1.HAZARD_FEATURES)
    hth,hmh,hcb,hcal=v1.calibrate_2020(candles,hfast,htr)
    hcal.to_csv(OUT/"hazard_calibration_2020.csv",index=False)

    gate6_rows=v3.build_gate_rows(candles,1,6)
    gate72_rows=v3.build_gate_rows(candles,6,72)
    g6,_,m6,_=v3.fit_gate(gate6_rows,"survive_1h_to_6h")
    g72,_,m72,_=v3.fit_gate(gate72_rows,"survive_6h_to_72h")

    t6,t72,grid=calibrate_2020(candles,hfast,g6,g72,hth,hmh,hcb)
    grid.to_csv(OUT/"behavior_gate_calibration_2020.csv",index=False)

    prev=candles[candles["bar_start"]<TEST_START].iloc[-1]
    auto_side=v1.direction_at(dmodel,prev)
    actual_side=v1.actual_side_at(TEST_START)

    rows=[];legs=[]
    for sm,side in [("CONDITIONAL_ACTUAL_START",actual_side),("FULLY_AUTONOMOUS",auto_side)]:
        for cname,cost in [("ZERO",0.0),("RT_004",0.0004/2)]:
            m,l,c=v3.simulate(candles,hfast,g6,t6,g72,t72,TEST_START,TEST_END,hth,hmh,hcb,side,cost)
            rows.append({"start_mode":sm,"cost":cname,"initial_side":"LONG" if side>0 else "SHORT",**m})
            l["start_mode"]=sm;l["cost"]=cname;legs.append(l)
            c.to_csv(OUT/f"curve_{sm}_{cname}.csv.gz",index=False,compression="gzip")

    summary=pd.DataFrame(rows)
    summary.to_csv(OUT/"summary_2021.csv",index=False)
    pd.concat(legs,ignore_index=True).to_csv(OUT/"legs_2021.csv",index=False)

    meta={
        "price_action_only":True,
        "design":"dynamic horizon promotion with gate thresholds calibrated ONLY on 2020 behavior fidelity",
        "selected_gate6_threshold":t6,
        "selected_gate72_threshold":t72,
        "gate6_model_oos_2021":m6,
        "gate72_model_oos_2021":m72,
        "hazard_threshold":hth,
        "hazard_min_hold":hmh,
        "hazard_confirm":hcb,
        "direction_model":dmeta,
        "hazard_model":hmeta,
        "actual_2020":v1.actual_stats(2020),
        "actual_2021":v1.actual_stats(2021),
        "note":"No 2020 PnL enters calibration. Gate thresholds minimize only 2020 leg-count and holding-time-distribution mismatch. 2021 remains untouched validation."
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print("=== META ===");print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== TOP 2020 BEHAVIOR CALIBRATION ===");print(grid.head(12).to_string(index=False))
    print("\n=== 2021 SUMMARY ===");print(summary.to_string(index=False))

if __name__=="__main__":
    main()
