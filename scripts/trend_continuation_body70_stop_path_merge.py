#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np
import pandas as pd

DAY=24*60*60*1000
COST=0.20
SLS=(3,4,5,6,7,8)
GRACES=(15,30,60)

def perf_from_gross(gross):
    x=pd.to_numeric(gross,errors="coerce").dropna().to_numpy(float)-COST
    if not len(x): return {"n":0}
    gp=x[x>0].sum(); gl=-x[x<0].sum()
    return {
        "n":int(len(x)),"wr_pct":float((x>0).mean()*100),"ev_pct":float(x.mean()),
        "pf":float(gp/gl) if gl>0 else None,"sum_net_pct":float(x.sum())
    }

def status_counts(s):
    return {str(k):int(v) for k,v in s.fillna("NA").value_counts().to_dict().items()}

def segment_stats(g):
    out={"n":int(len(g)),"canonical":perf_from_gross(g.canonical_gross_pct),
         "canonical_status":status_counts(g.canonical_status)}
    for sl in SLS:
        out[f"sl{sl}"]={**perf_from_gross(g[f"sl{sl}_gross"]), "status":status_counts(g[f"sl{sl}_status"])}
    for m in GRACES:
        out[f"grace{m}"]={**perf_from_gross(g[f"grace{m}_gross"]), "status":status_counts(g[f"grace{m}_status"])}

    sl5=g[g.sl5_status.eq("SL")].copy()
    valid=sl5[sl5.sl5_later_tp_status.eq("OK") & sl5.sl5_later_tp.notna()]
    rescued=valid[valid.sl5_later_tp.eq(1)]
    out["sl5_loss_recovery"]={
        "sl5_loss_n":int(len(sl5)),
        "valid_followup_n":int(len(valid)),
        "later_tp3_n":int(len(rescued)),
        "later_tp3_pct_of_valid_sl5":float(len(rescued)/len(valid)*100) if len(valid) else None,
        "minutes_to_later_tp_mean":float(rescued.sl5_to_later_tp_min.mean()) if len(rescued) else None,
        "minutes_to_later_tp_median":float(rescued.sl5_to_later_tp_min.median()) if len(rescued) else None,
    }
    # Transition table from SL5 losses under wider stops.
    trans={}
    for sl in (6,7,8):
        q=sl5[f"sl{sl}_status"].fillna("NA").value_counts()
        trans[f"SL5_to_SL{sl}"]={str(k):int(v) for k,v in q.items()}
        tp=int((sl5[f"sl{sl}_status"]=="TP").sum())
        trans[f"SL5_to_SL{sl}_tp_rescue_pct"]=float(tp/len(sl5)*100) if len(sl5) else None
    out["sl5_wider_stop_transitions"]=trans
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--partials",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/stop_path.csv.gz",recursive=True))
    if len(fs)!=8: raise RuntimeError(f"expected 8 partials, got {len(fs)}")
    z=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    z=z.sort_values(["signal_ts","symbol"]).drop_duplicates(["symbol","signal_ts"]).reset_index(drop=True)
    z.to_csv(out/"stop_path_all.csv.gz",index=False,compression="gzip")
    max_ts=int(z.signal_ts.max()); cut=max_ts-120*DAY; cut180=max_ts-180*DAY
    z["period"]=np.where(z.signal_ts>=cut,"RECENT120","PRIOR")
    z["month"]=pd.to_datetime(z.signal_ts,unit="ms",utc=True).dt.strftime("%Y-%m")
    z["year"]=pd.to_datetime(z.signal_ts,unit="ms",utc=True).dt.year

    prior=segment_stats(z[z.period=="PRIOR"])
    recent=segment_stats(z[z.period=="RECENT120"])
    recent180=segment_stats(z[z.signal_ts>=cut180])
    overall=segment_stats(z)

    monthly=[]
    for m,g in z.groupby("month"):
        s=segment_stats(g)
        monthly.append({
            "month":m,"n":s["n"],
            "canon_pf":s["canonical"].get("pf"),"canon_ev":s["canonical"].get("ev_pct"),
            "sl5_recover_pct":s["sl5_loss_recovery"].get("later_tp3_pct_of_valid_sl5"),
            "sl6_pf":s["sl6"].get("pf"),"sl6_ev":s["sl6"].get("ev_pct"),
            "sl7_pf":s["sl7"].get("pf"),"sl7_ev":s["sl7"].get("ev_pct"),
            "sl8_pf":s["sl8"].get("pf"),"sl8_ev":s["sl8"].get("ev_pct"),
            "grace15_pf":s["grace15"].get("pf"),"grace15_ev":s["grace15"].get("ev_pct"),
            "grace30_pf":s["grace30"].get("pf"),"grace30_ev":s["grace30"].get("ev_pct"),
            "grace60_pf":s["grace60"].get("pf"),"grace60_ev":s["grace60"].get("ev_pct"),
        })
    pd.DataFrame(monthly).to_csv(out/"monthly_stop_path.csv",index=False)

    core={
        "sl5_recovery_prior_pct":prior["sl5_loss_recovery"]["later_tp3_pct_of_valid_sl5"],
        "sl5_recovery_recent_pct":recent["sl5_loss_recovery"]["later_tp3_pct_of_valid_sl5"],
        "sl5_recovery_delta_pp":(
            recent["sl5_loss_recovery"]["later_tp3_pct_of_valid_sl5"]-
            prior["sl5_loss_recovery"]["later_tp3_pct_of_valid_sl5"]
            if prior["sl5_loss_recovery"]["later_tp3_pct_of_valid_sl5"] is not None and recent["sl5_loss_recovery"]["later_tp3_pct_of_valid_sl5"] is not None else None
        ),
        "recent_canonical_ev":recent["canonical"].get("ev_pct"),
        "recent_sl6_ev":recent["sl6"].get("ev_pct"),
        "recent_sl7_ev":recent["sl7"].get("ev_pct"),
        "recent_sl8_ev":recent["sl8"].get("ev_pct"),
        "recent_grace15_ev":recent["grace15"].get("ev_pct"),
        "recent_grace30_ev":recent["grace30"].get("ev_pct"),
        "recent_grace60_ev":recent["grace60"].get("ev_pct"),
    }
    summary={
        "definition":{
            "cohort":"actually executed frozen BODY70 immediate trades from validation run; same cohort for diagnostics",
            "tp_pct":3.0,"sl_sweep_pct":list(SLS),"grace_min":list(GRACES),"cost_pct":COST,
            "recent120_anchor_max_signal_ts":max_ts,"recent120_cut_ts":cut,
            "warning":"SL sweep/grace are diagnostic on fixed canonical cohort; promising variants require separate executable overlap replay."
        },
        "overall":overall,"prior":prior,"recent120":recent,"recent180":recent180,
        "diagnostic_core":core,
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("BODY70_STOP_PATH_JSON")
    print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__": main()
