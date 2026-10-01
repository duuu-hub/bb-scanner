#!/usr/bin/env python3
"""TRAIN-only admission policy study for PSAR 1D SHORT practical max-6 portfolio."""
import argparse, hashlib, json
from pathlib import Path
import numpy as np
import pandas as pd
import psar_1d_live_portfolio as p

FIXED_STOP="PCT_10"
BASE_RISK=.0025
HASH_SEEDS=tuple(range(20))
POLICIES=("D0_DESC","D0_ASC","ATRPCT_DESC","ATRPCT_ASC","HASH_MEDIAN")

def save_json(path,obj): Path(path).write_text(json.dumps(obj,indent=2,allow_nan=False))
def find_one(root,name):
    m=sorted(Path(root).rglob(name))
    if len(m)!=1: raise ValueError(f"expected one {name}, found {len(m)}")
    return m[0]
def hash_score(sym,ts,seed):
    h=hashlib.sha256(f"{seed}|{sym}|{int(ts)}".encode()).digest()
    return int.from_bytes(h[:8],"big")/2**64
def remap(trades,mode,seed=0):
    out=[]
    for tr in trades:
        q=tr.copy()
        d=float(tr["d0"]); vol=float(tr["atr_open"]/tr["entry"])
        if mode=="D0_DESC": score=d
        elif mode=="D0_ASC": score=-d
        elif mode=="ATRPCT_DESC": score=vol
        elif mode=="ATRPCT_ASC": score=-vol
        elif mode=="HASH_MEDIAN": score=hash_score(tr["symbol"],tr["ts"],seed)
        else: raise ValueError(mode)
        q["d0"]=score
        out.append(q)
    return out
def slim(r): return {k:v for k,v in r.items() if k not in ("realized_trades","curve")}
def run_policy(trades,mode,seed=0,risk=BASE_RISK):
    return p.portfolio_sim(remap(trades,mode,seed),risk)
def yearly_policy(trades,mode,seed=0,risk=BASE_RISK):
    df=pd.DataFrame({"i":range(len(trades)),"ts":[x["ts"] for x in trades]})
    df["year"]=pd.to_datetime(df.ts,unit="ms",utc=True).dt.year
    rows=[]
    for y,g in df.groupby("year"):
        subset=[trades[int(i)] for i in g.i]
        if len(subset)<100: continue
        z=run_policy(subset,mode,seed,risk)
        rows.append({"year":int(y),"signals":len(subset),**slim(z)})
    return rows
def choose_hash_seed(trades):
    rows=[]
    for seed in HASH_SEEDS:
        z=run_policy(trades,"HASH_MEDIAN",seed)
        rows.append({"seed":seed,**slim(z)})
    tab=pd.DataFrame(rows)
    med=float(tab.total_return_pct.median())
    tab["distance_to_median"]=abs(tab.total_return_pct-med)
    ch=tab.sort_values(["distance_to_median","seed"]).iloc[0]
    return int(ch.seed),tab,med
def choose_policy(trades):
    hseed,htab,hmed=choose_hash_seed(trades)
    rows=[]; year_rows=[]
    for mode in POLICIES:
        seed=hseed if mode=="HASH_MEDIAN" else 0
        z=run_policy(trades,mode,seed)
        yr=yearly_policy(trades,mode,seed)
        robust=min((x["total_return_pct"] for x in yr),default=-1e9)
        positive_years=sum(x["total_return_pct"]>0 for x in yr)
        rows.append({"policy":mode,"seed":seed,"robust_worst_year_return_pct":robust,
                     "positive_years":positive_years,**slim(z)})
        for x in yr: year_rows.append({"policy":mode,"seed":seed,**x})
    tab=pd.DataFrame(rows)
    eligible=tab[(~tab.bankrupt)&(tab.ending_equity>1)]
    pool=eligible if len(eligible) else tab
    ch=pool.sort_values(["robust_worst_year_return_pct","positive_years","ending_equity"],
                        ascending=[False,False,False]).iloc[0]
    status="profitable_train_policy_found" if len(eligible) else "no_profitable_train_policy"
    freeze={"status":status,"chosen_policy":str(ch.policy),"chosen_seed":int(ch.seed),
            "selection_rule":"maximize worst annual return over TRAIN years with >=100 signals; tie positive years then total ending equity; require total TRAIN ending equity >1 if any policy qualifies",
            "hash_seed_rule":"20 deterministic seeds; choose seed nearest median TRAIN total return, never best seed",
            "hash_median_train_return_pct":hmed,
            "base_risk_fraction_pct":BASE_RISK*100}
    return freeze,tab,pd.DataFrame(year_rows),htab
def choose_risk(trades,policy,seed):
    rows=[]
    for risk in p.RISK_GRID:
        z=run_policy(trades,policy,seed,risk)
        rows.append(slim(z))
    tab=pd.DataFrame(rows)
    eligible=tab[(~tab.bankrupt)&(tab.ending_equity>1)&(tab.mdd_pct<=p.TRAIN_MDD_CEILING_PCT)]
    if len(eligible):
        ch=eligible.sort_values(["ending_equity","mdd_pct"],ascending=[False,True]).iloc[0]; status="constraint_pass"
    else:
        ch=tab.sort_values(["mdd_pct","risk_fraction_pct"]).iloc[0]; status="constraint_failed"
    risk=float(ch.risk_fraction_pct)/100
    return {"status":status,"chosen_risk_fraction_pct":risk*100,"mdd_ceiling_pct":p.TRAIN_MDD_CEILING_PCT},tab,risk
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--data",required=True); ap.add_argument("--canonical",required=True); ap.add_argument("--prior",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    prior=json.loads(find_one(a.prior,"frozen_stop_choice.json").read_text())
    if prior["chosen_spec"]!=FIXED_STOP: raise ValueError(f"prior stop drift {prior}")
    train,hold,cf=p.load_canonical(a.canonical); idx=p.index_raw_files(a.data)
    spec=[x for x in p.STOP_SPECS if x[0]==FIXED_STOP]
    _,ttr=p.scan_signals(train,idx,spec,keep=FIXED_STOP)
    freeze,ptab,ytab,htab=choose_policy(ttr)
    ptab.to_csv(out/"train_admission_policies.csv",index=False); ytab.to_csv(out/"train_admission_yearly.csv",index=False); htab.to_csv(out/"train_hash_seed_distribution.csv",index=False); save_json(out/"frozen_admission_choice.json",freeze)
    policy=freeze["chosen_policy"]; seed=int(freeze["chosen_seed"])
    rfreeze,rtab,risk=choose_risk(ttr,policy,seed); rtab.to_csv(out/"train_risk_grid_after_admission.csv",index=False); save_json(out/"frozen_risk_after_admission.json",rfreeze)
    train_final=run_policy(ttr,policy,seed,risk)
    _,htr=p.scan_signals(hold,idx,spec,keep=FIXED_STOP)
    hold_final=run_policy(htr,policy,seed,risk)
    stress=[]
    for name,rb,sb,fb in [("BASE",40,10,2),("NO_FUNDING",40,10,0),("MODERATE",60,25,5),("SEVERE",80,50,10)]:
        z=p.portfolio_sim(remap(htr,policy,seed),risk,roundtrip_bps=rb,stop_slip_bps=sb,funding_bps_day=fb)
        stress.append({"scenario":name,"roundtrip_bps":rb,"stop_slip_bps":sb,"funding_bps_per_day":fb,**slim(z)})
    pd.DataFrame(stress).to_csv(out/"holdout_stress.csv",index=False)
    yearly=yearly_policy(htr,policy,seed,risk); pd.DataFrame(yearly).to_csv(out/"holdout_yearly.csv",index=False)
    summary={"study":"PSAR 1D max-6 admission evolution","fixed_stop":FIXED_STOP,"prior_run":36819434601,
             "admission_freeze":freeze,"risk_freeze":rfreeze,"train_final":slim(train_final),"holdout_final":slim(hold_final),
             "holdout_stress":stress,"holdout_yearly":yearly,
             "honesty":["Admission redesign was triggered by TRAIN failure of D0_DESC max-6 portfolio, not by HOLDOUT performance.",
                        "However prior run 36819434601 already exposed HOLDOUT aggregate portfolio results, so this follow-up HOLDOUT is not pristine OOS.",
                        "No HOLDOUT outcome is used by admission or risk selection."]}
    save_json(out/"summary.json",summary)
    (out/"REPORT.md").write_text(f"""# PSAR 1D max-6 admission evolution
Fixed TRAIN-selected stop: **{FIXED_STOP}**

Chosen TRAIN admission: **{policy}** (seed {seed})
Chosen TRAIN risk: **{risk*100:.3f}% stop-risk per position**

TRAIN: return {train_final['total_return_pct']:+.2f}%, MDD {train_final['mdd_pct']:.2f}%, ending equity {train_final['ending_equity']:.4f}
HOLDOUT: return {hold_final['total_return_pct']:+.2f}%, MDD {hold_final['mdd_pct']:.2f}%, ending equity {hold_final['ending_equity']:.4f}

The redesign reason existed in TRAIN: D0-desc max-6 was unprofitable there. Prior HOLDOUT results had already been observed, so this is not pristine OOS. Selection still uses TRAIN only.
""")
    print(json.dumps(summary,indent=2),flush=True)
if __name__=="__main__": main()
