#!/usr/bin/env python3
"""TRAIN-only grid: max positions x total exposure x stop-risk for PSAR 1D SHORT.

Frozen before this study:
- canonical Age=3 / D0 / 7d-or-reflip signal from run 36817230406
- PCT_10 protective stop selected on TRAIN in run 36819434601
- ATRPCT_DESC admission ranking selected on TRAIN in run 36819874770

This study changes only portfolio construction. HOLDOUT is evaluated after a
single TRAIN-only configuration freeze and is not used for retuning.
"""
import argparse, json
from pathlib import Path
import pandas as pd
import psar_1d_live_portfolio as p
import psar_1d_live_admission as a

FIXED_STOP="PCT_10"
FIXED_ADMISSION="ATRPCT_DESC"
POSITION_GRID=(2,4,6,8,10,15,20,30)
EXPOSURE_GRID=(1.0,1.5,2.0)
RISK_GRID=(.0025,.005,.0075,.01,.015,.02)
MDD_CEILING=25.0

def save_json(path,obj):
    Path(path).write_text(json.dumps(obj,indent=2,allow_nan=False))

def slim(r):
    return {k:v for k,v in r.items() if k not in ("realized_trades","curve")}

def run_cfg(trades,maxpos,exposure,risk,rb=40,sb=10,fb=2,record_curve=False):
    old_pos,old_exp=p.MAX_POSITIONS,p.TOTAL_EXPOSURE_CAP
    try:
        p.MAX_POSITIONS=int(maxpos)
        p.TOTAL_EXPOSURE_CAP=float(exposure)
        ranked=a.remap(trades,FIXED_ADMISSION,0)
        return p.portfolio_sim(ranked,risk,roundtrip_bps=rb,stop_slip_bps=sb,
                               funding_bps_day=fb,record_curve=record_curve)
    finally:
        p.MAX_POSITIONS, p.TOTAL_EXPOSURE_CAP = old_pos, old_exp

def yearly(trades,maxpos,exposure,risk):
    df=pd.DataFrame({"i":range(len(trades)),"ts":[x["ts"] for x in trades]})
    df["year"]=pd.to_datetime(df.ts,unit="ms",utc=True).dt.year
    rows=[]
    for y,g in df.groupby("year"):
        subset=[trades[int(i)] for i in g.i]
        if len(subset)<100:
            continue
        z=run_cfg(subset,maxpos,exposure,risk)
        rows.append({"year":int(y),"signals":len(subset),**slim(z)})
    return rows

def choose_train(grid):
    ok=grid[(~grid.bankrupt)&(grid.mdd_pct<=MDD_CEILING)&(grid.ending_equity>1.0)].copy()
    if len(ok):
        # Primary objective is actually making money. Tie-breaks favor less risk/capacity.
        ch=ok.sort_values(
            ["total_return_pct","mdd_pct","risk_fraction_pct","total_exposure_pct","max_positions"],
            ascending=[False,True,True,True,True]
        ).iloc[0]
        status="profitable_train_configuration_found"
    else:
        ch=grid.sort_values(
            ["total_return_pct","mdd_pct","risk_fraction_pct"],
            ascending=[False,True,True]
        ).iloc[0]
        status="no_profitable_train_configuration"
    return ch,status

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data",required=True)
    ap.add_argument("--canonical",required=True)
    ap.add_argument("--out",required=True)
    x=ap.parse_args()
    out=Path(x.out); out.mkdir(parents=True,exist_ok=True)

    # Re-run existing 30 x 10 synthetic/invariant audit before portfolio grid.
    save_json(out/"base_audit_30_by_10.json",p.audit())

    train,hold,cf=p.load_canonical(x.canonical)
    idx=p.index_raw_files(x.data)
    spec=[s for s in p.STOP_SPECS if s[0]==FIXED_STOP]
    if len(spec)!=1:
        raise ValueError("fixed stop missing")

    _,ttr=p.scan_signals(train,idx,spec,keep=FIXED_STOP)

    rows=[]
    total=len(POSITION_GRID)*len(EXPOSURE_GRID)*len(RISK_GRID)
    done=0
    for maxpos in POSITION_GRID:
        for exposure in EXPOSURE_GRID:
            for risk in RISK_GRID:
                z=run_cfg(ttr,maxpos,exposure,risk)
                rows.append({
                    "max_positions":maxpos,
                    "total_exposure_pct":exposure*100,
                    **slim(z)
                })
                done+=1
                if done%12==0 or done==total:
                    print(f"TRAIN_GRID {done}/{total}",flush=True)
    grid=pd.DataFrame(rows)
    grid.to_csv(out/"train_portfolio_grid.csv",index=False)

    ch,status=choose_train(grid)
    maxpos=int(ch.max_positions)
    exposure=float(ch.total_exposure_pct)/100.0
    risk=float(ch.risk_fraction_pct)/100.0
    freeze={
        "status":status,
        "fixed_stop":FIXED_STOP,
        "fixed_admission":FIXED_ADMISSION,
        "position_grid":list(POSITION_GRID),
        "exposure_grid_pct":[x*100 for x in EXPOSURE_GRID],
        "risk_grid_pct":[x*100 for x in RISK_GRID],
        "mdd_ceiling_pct":MDD_CEILING,
        "selected_max_positions":maxpos,
        "selected_total_exposure_pct":exposure*100,
        "selected_risk_fraction_pct":risk*100,
        "selection_rule":"highest TRAIN total return among non-bankrupt configurations with ending equity >1 and MDD <=25%; tie lower MDD, risk, exposure, positions",
        "holdout_used_for_selection":False
    }
    save_json(out/"frozen_portfolio_choice.json",freeze)
    print("TRAIN_FROZEN",json.dumps(freeze),flush=True)

    # Useful slices around the selected configuration.
    by_positions=[]
    for mp,g in grid.groupby("max_positions"):
        elig=g[(~g.bankrupt)&(g.mdd_pct<=MDD_CEILING)]
        q=(elig if len(elig) else g).sort_values(["total_return_pct","mdd_pct"],ascending=[False,True]).iloc[0]
        by_positions.append(q.to_dict())
    pd.DataFrame(by_positions).to_csv(out/"train_best_by_position_count.csv",index=False)

    ytrain=yearly(ttr,maxpos,exposure,risk)
    pd.DataFrame(ytrain).to_csv(out/"train_selected_yearly.csv",index=False)

    train_final=run_cfg(ttr,maxpos,exposure,risk,record_curve=True)
    train_curve=train_final.pop("curve")
    train_trades=train_final.pop("realized_trades")
    pd.DataFrame(train_curve).to_csv(out/"train_equity_curve_daily.csv",index=False)
    pd.DataFrame(train_trades).to_csv(out/"train_realized_trades.csv",index=False)

    # HOLDOUT only after all portfolio choices are frozen.
    _,htr=p.scan_signals(hold,idx,spec,keep=FIXED_STOP)
    hold_final=run_cfg(htr,maxpos,exposure,risk,record_curve=True)
    hold_curve=hold_final.pop("curve")
    hold_trades=hold_final.pop("realized_trades")
    pd.DataFrame(hold_curve).to_csv(out/"holdout_equity_curve_daily.csv",index=False)
    pd.DataFrame(hold_trades).to_csv(out/"holdout_realized_trades.csv",index=False)

    yhold=yearly(htr,maxpos,exposure,risk)
    pd.DataFrame(yhold).to_csv(out/"holdout_yearly.csv",index=False)

    stress=[]
    for name,rb,sb,fb in [
        ("BASE",40,10,2),
        ("NO_FUNDING",40,10,0),
        ("MODERATE",60,25,5),
        ("SEVERE",80,50,10),
    ]:
        z=run_cfg(htr,maxpos,exposure,risk,rb,sb,fb)
        stress.append({
            "scenario":name,"roundtrip_bps":rb,"stop_slip_bps":sb,
            "funding_bps_per_day":fb,**slim(z)
        })
    pd.DataFrame(stress).to_csv(out/"holdout_cost_stress.csv",index=False)

    summary={
        "study":"PSAR 1D portfolio construction grid",
        "canonical_run":p.CANONICAL_RUN,
        "fixed_stop":FIXED_STOP,
        "fixed_admission":FIXED_ADMISSION,
        "freeze":freeze,
        "train_final":slim(train_final),
        "train_yearly":ytrain,
        "holdout_final":slim(hold_final),
        "holdout_yearly":yhold,
        "holdout_stress":stress,
        "audit":{"base_distinct":30,"base_consecutive_clean":10,"grid_rows":len(grid)},
        "honesty":[
            "Only portfolio construction is tuned here; canonical entry, stop and admission ranking are already frozen.",
            "The 144 portfolio combinations are evaluated on TRAIN only before HOLDOUT.",
            "Prior research has already exposed HOLDOUT aggregate behavior, so this HOLDOUT is diagnostic and not pristine OOS.",
            "No HOLDOUT result is used to alter the frozen portfolio choice."
        ]
    }
    save_json(out/"summary.json",summary)

    (out/"REPORT.md").write_text(f"""# PSAR 1D portfolio construction grid

Fixed signal: canonical Age=3 / D0 / 7d-or-reflip.
Fixed stop: **{FIXED_STOP}**
Fixed admission: **{FIXED_ADMISSION}**

TRAIN grid: {len(grid)} combinations
- Positions: {POSITION_GRID}
- Total exposure: 100% / 150% / 200%
- Stop-risk per position: 0.25% / 0.50% / 0.75% / 1.00% / 1.50% / 2.00%

## Frozen TRAIN choice
- max positions: **{maxpos}**
- total exposure: **{exposure*100:.0f}%**
- stop-risk per position: **{risk*100:.2f}%**
- TRAIN return: **{train_final['total_return_pct']:+.2f}%**
- TRAIN MDD: **{train_final['mdd_pct']:.2f}%**

## Diagnostic HOLDOUT
- HOLDOUT return: **{hold_final['total_return_pct']:+.2f}%**
- HOLDOUT MDD: **{hold_final['mdd_pct']:.2f}%**
- HOLDOUT ending equity: **{hold_final['ending_equity']:.4f}**

Selection used TRAIN only. HOLDOUT was already observed in prior research, so it remains diagnostic rather than pristine OOS.
""")
    print(json.dumps(summary,indent=2),flush=True)

if __name__=="__main__":
    main()
