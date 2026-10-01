#!/usr/bin/env python3
"""TRAIN-only minimum breadth study for PSAR 1D SHORT practical portfolio."""
import argparse, json
from pathlib import Path
import pandas as pd
import psar_1d_live_portfolio as p
import psar_1d_live_admission as a

FIXED_STOP="PCT_10"
BREADTH_GRID=(6,10,15,20,30,40,60)
BASE_RISK=.0025

def save_json(path,obj): Path(path).write_text(json.dumps(obj,indent=2,allow_nan=False))
def slim(r): return {k:v for k,v in r.items() if k not in ("realized_trades","curve")}
def run_with_breadth(trades,maxpos,risk,seed,rb=40,sb=10,fb=2):
    old=p.MAX_POSITIONS
    try:
        p.MAX_POSITIONS=maxpos
        return p.portfolio_sim(a.remap(trades,"HASH_MEDIAN",seed),risk,roundtrip_bps=rb,stop_slip_bps=sb,funding_bps_day=fb)
    finally:
        p.MAX_POSITIONS=old
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--data",required=True); ap.add_argument("--canonical",required=True); ap.add_argument("--out",required=True); x=ap.parse_args()
    out=Path(x.out); out.mkdir(parents=True,exist_ok=True)
    train,hold,cf=p.load_canonical(x.canonical); idx=p.index_raw_files(x.data)
    spec=[s for s in p.STOP_SPECS if s[0]==FIXED_STOP]
    _,ttr=p.scan_signals(train,idx,spec,keep=FIXED_STOP)
    seed,seedtab,seedmedian=a.choose_hash_seed(ttr)
    seedtab.to_csv(out/"train_hash_seed_distribution.csv",index=False)
    rows=[]
    for m in BREADTH_GRID:
        z=run_with_breadth(ttr,m,BASE_RISK,seed); rows.append({"max_positions":m,**slim(z)})
    tab=pd.DataFrame(rows); tab.to_csv(out/"train_breadth_grid.csv",index=False)
    eligible=tab[(~tab.bankrupt)&(tab.ending_equity>1)&(tab.mdd_pct<=p.TRAIN_MDD_CEILING_PCT)]
    if len(eligible):
        ch=eligible.sort_values(["max_positions","mdd_pct"],ascending=[True,True]).iloc[0]; status="minimum_profitable_breadth_found"
    else:
        ch=tab.sort_values(["ending_equity","mdd_pct"],ascending=[False,True]).iloc[0]; status="no_profitable_breadth_up_to_60"
    maxpos=int(ch.max_positions)
    bfreeze={"status":status,"chosen_max_positions":maxpos,"base_risk_fraction_pct":BASE_RISK*100,
             "neutral_hash_seed":int(seed),"hash_seed_selection":"seed nearest median TRAIN return among 20 deterministic seeds",
             "selection_rule":"smallest breadth with TRAIN ending equity >1 and MDD <=25%; otherwise best TRAIN ending equity"}
    save_json(out/"frozen_breadth_choice.json",bfreeze)
    risk_rows=[]
    for risk in p.RISK_GRID:
        z=run_with_breadth(ttr,maxpos,risk,seed); risk_rows.append(slim(z))
    rtab=pd.DataFrame(risk_rows); rtab.to_csv(out/"train_risk_grid.csv",index=False)
    elig=rtab[(~rtab.bankrupt)&(rtab.ending_equity>1)&(rtab.mdd_pct<=p.TRAIN_MDD_CEILING_PCT)]
    if len(elig):
        rc=elig.sort_values(["ending_equity","mdd_pct"],ascending=[False,True]).iloc[0]; rstatus="constraint_pass"
    else:
        rc=rtab.sort_values(["mdd_pct","risk_fraction_pct"]).iloc[0]; rstatus="constraint_failed"
    risk=float(rc.risk_fraction_pct)/100
    rfreeze={"status":rstatus,"chosen_risk_fraction_pct":risk*100,"mdd_ceiling_pct":p.TRAIN_MDD_CEILING_PCT}
    save_json(out/"frozen_risk_choice.json",rfreeze)
    train_final=run_with_breadth(ttr,maxpos,risk,seed)
    _,htr=p.scan_signals(hold,idx,spec,keep=FIXED_STOP)
    hold_final=run_with_breadth(htr,maxpos,risk,seed)
    stress=[]
    for name,rb,sb,fb in [("BASE",40,10,2),("NO_FUNDING",40,10,0),("MODERATE",60,25,5),("SEVERE",80,50,10)]:
        z=run_with_breadth(htr,maxpos,risk,seed,rb,sb,fb)
        stress.append({"scenario":name,"roundtrip_bps":rb,"stop_slip_bps":sb,"funding_bps_per_day":fb,**slim(z)})
    pd.DataFrame(stress).to_csv(out/"holdout_stress.csv",index=False)
    summary={"study":"PSAR 1D minimum portfolio breadth","fixed_stop":FIXED_STOP,"neutral_hash_seed":int(seed),
             "breadth_freeze":bfreeze,"risk_freeze":rfreeze,"train_final":slim(train_final),"holdout_final":slim(hold_final),
             "holdout_stress":stress,
             "honesty":["Breadth and risk are selected on TRAIN only.","Prior HOLDOUT results were already observed in earlier retrofit runs, so this HOLDOUT remains diagnostic rather than pristine OOS.","No new entry filter or HOLDOUT-based threshold was introduced."]}
    save_json(out/"summary.json",summary)
    (out/"REPORT.md").write_text(f"""# PSAR 1D minimum breadth study

Fixed stop: **{FIXED_STOP}**
Neutral admission: deterministic hash median seed **{seed}**
Chosen max positions: **{maxpos}**
Chosen stop-risk: **{risk*100:.3f}%**

TRAIN: {train_final['total_return_pct']:+.2f}% return, {train_final['mdd_pct']:.2f}% MDD.
HOLDOUT: {hold_final['total_return_pct']:+.2f}% return, {hold_final['mdd_pct']:.2f}% MDD.

Breadth and risk were selected on TRAIN only. Prior HOLDOUT was already observed in earlier runs, so the HOLDOUT result here is diagnostic, not pristine OOS.
""")
    print(json.dumps(summary,indent=2),flush=True)
if __name__=="__main__": main()
