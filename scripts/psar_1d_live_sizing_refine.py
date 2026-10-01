#!/usr/bin/env python3
"""Fine TRAIN-only stop-risk sizing sweep for frozen PSAR 1D live portfolio."""
import argparse, json
from pathlib import Path
import pandas as pd
import psar_1d_live_portfolio as p
import psar_1d_live_admission as a

FIXED_STOP="PCT_10"
FIXED_POLICY="ATRPCT_DESC"
FIXED_SEED=0
RISK_GRID_PCT=(0.025,0.05,0.075,0.10,0.125,0.15,0.175,0.20,0.225,0.25,0.275,0.30,0.35,0.40,0.50)
MDD_CEILING=25.0

def slim(z): return {k:v for k,v in z.items() if k not in ("realized_trades","curve")}
def save(path,obj): Path(path).write_text(json.dumps(obj,indent=2,allow_nan=False))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data",required=True)
    ap.add_argument("--canonical",required=True)
    ap.add_argument("--prior-admission",required=True)
    ap.add_argument("--out",required=True)
    q=ap.parse_args()
    out=Path(q.out); out.mkdir(parents=True,exist_ok=True)

    prior=json.loads(next(Path(q.prior_admission).rglob("summary.json")).read_text())
    assert prior["fixed_stop"]==FIXED_STOP
    assert prior["admission_freeze"]["chosen_policy"]==FIXED_POLICY
    assert int(prior["admission_freeze"]["chosen_seed"])==FIXED_SEED

    train,hold,_=p.load_canonical(q.canonical)
    idx=p.index_raw_files(q.data)
    spec=[x for x in p.STOP_SPECS if x[0]==FIXED_STOP]

    _,ttr=p.scan_signals(train,idx,spec,keep=FIXED_STOP)
    ttr=a.remap(ttr,FIXED_POLICY,FIXED_SEED)
    train_rows=[]
    for rpct in RISK_GRID_PCT:
        z=p.portfolio_sim(ttr,rpct/100.0)
        train_rows.append(slim(z))
    tt=pd.DataFrame(train_rows)
    tt.to_csv(out/"train_fine_risk_grid.csv",index=False)

    eligible=tt[(~tt.bankrupt)&(tt.ending_equity>1)&(tt.mdd_pct<=MDD_CEILING)]
    if len(eligible):
        ch=eligible.sort_values(["ending_equity","mdd_pct"],ascending=[False,True]).iloc[0]
        status="constraint_pass"
    else:
        ch=tt.sort_values(["ending_equity","mdd_pct"],ascending=[False,True]).iloc[0]
        status="no_profitable_under_ceiling"
    chosen_pct=float(ch.risk_fraction_pct)
    freeze={
      "status":status,
      "fixed_stop":FIXED_STOP,
      "fixed_policy":FIXED_POLICY,
      "selection_rule":"TRAIN only: highest ending equity among non-bankrupt, ending_equity>1, MDD<=25%; tie lower MDD",
      "risk_grid_pct":list(RISK_GRID_PCT),
      "chosen_risk_fraction_pct":chosen_pct,
      "equivalent_notional_pct_for_10pct_stop":chosen_pct/10*100,
      "mdd_ceiling_pct":MDD_CEILING
    }
    save(out/"frozen_fine_sizing.json",freeze)

    _,htr=p.scan_signals(hold,idx,spec,keep=FIXED_STOP)
    htr=a.remap(htr,FIXED_POLICY,FIXED_SEED)
    hold=p.portfolio_sim(htr,chosen_pct/100.0)
    train_final=p.portfolio_sim(ttr,chosen_pct/100.0)
    stress=[]
    for name,rb,sb,fb in [("BASE",40,10,2),("NO_FUNDING",40,10,0),("MODERATE",60,25,5),("SEVERE",80,50,10)]:
        z=p.portfolio_sim(htr,chosen_pct/100.0,roundtrip_bps=rb,stop_slip_bps=sb,funding_bps_day=fb)
        stress.append({"scenario":name,**slim(z)})
    pd.DataFrame(stress).to_csv(out/"holdout_stress.csv",index=False)
    summary={
      "study":"PSAR 1D fine risk sizing after frozen 10% stop + ATRPCT_DESC admission",
      "freeze":freeze,
      "train":slim(train_final),
      "holdout":slim(hold),
      "holdout_stress":stress,
      "honesty":["Risk selection uses TRAIN only.","HOLDOUT has been observed previously and is not pristine OOS.","This refines sizing only; signal, stop and admission rule are fixed."]
    }
    save(out/"summary.json",summary)
    (out/"REPORT.md").write_text(
      f"# PSAR 1D fine sizing\n\nChosen stop-risk: **{chosen_pct:.3f}%** of equity per position.\n"
      f"Equivalent notional at fixed 10% stop: **{chosen_pct*10:.3f}%** of equity.\n\n"
      f"TRAIN return {train_final['total_return_pct']:+.3f}%, MDD {train_final['mdd_pct']:.3f}%.\n"
      f"HOLDOUT return {hold['total_return_pct']:+.3f}%, MDD {hold['mdd_pct']:.3f}%.\n"
    )
    print(json.dumps(summary,indent=2),flush=True)

if __name__=="__main__": main()
