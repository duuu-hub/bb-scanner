#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np
import pandas as pd

DAY=24*60*60*1000
HORIZONS=[15,30,60,120,240,360]
UPS=[1.0,2.0,2.5,3.0]
DNS=[1.0,2.0,3.0,5.0]

def summarize(g,label):
    o={"segment":label,"n":int(len(g))}
    if not len(g): return o
    for m in HORIZONS:
        for k in ("close","mfe","mae"):
            x=g[f"{k}_{m}"].astype(float)
            o[f"{k}_{m}_mean"]=float(x.mean())
            o[f"{k}_{m}_median"]=float(x.median())
    for u in UPS:
        c=f"touch_up_{str(u).replace('.','_')}"
        o[f"{c}_pct"]=float(g[c].mean()*100)
    for d in DNS:
        c=f"touch_dn_{str(d).replace('.','_')}"
        o[f"{c}_pct"]=float(g[c].mean()*100)
    for c in ("near_miss_2_5_to_3","early_mae1_60","early_mae2_60"):
        o[f"{c}_pct"]=float(g[c].mean()*100)
    for c in ("t_mfe_min","t_mae_min"):
        o[f"{c}_mean"]=float(g[c].mean()); o[f"{c}_median"]=float(g[c].median())
    return o

def delta(prior,recent):
    keys=[
      *[f"close_{m}_mean" for m in HORIZONS],
      *[f"mfe_{m}_mean" for m in HORIZONS],
      *[f"mae_{m}_mean" for m in HORIZONS],
      *[f"touch_up_{str(u).replace('.','_')}_pct" for u in UPS],
      *[f"touch_dn_{str(d).replace('.','_')}_pct" for d in DNS],
      "near_miss_2_5_to_3_pct","early_mae1_60_pct","early_mae2_60_pct",
      "t_mfe_min_mean","t_mae_min_mean",
    ]
    return {k:(recent.get(k)-prior.get(k)) for k in keys if prior.get(k) is not None and recent.get(k) is not None}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--partials",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/path_diag.csv.gz",recursive=True))
    if len(fs)!=8: raise RuntimeError(f"expected 8 path partials, got {len(fs)}")
    z=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    z=z.sort_values(["signal_ts","symbol"]).drop_duplicates(["symbol","signal_ts"]).reset_index(drop=True)
    z.to_csv(out/"body70_path_all.csv.gz",index=False,compression="gzip")

    max_ts=int(z.signal_ts.max()); cut=max_ts-120*DAY; cut180=max_ts-180*DAY
    z["period"]=np.where(z.signal_ts>=cut,"RECENT120","PRIOR")
    z["month"]=pd.to_datetime(z.signal_ts,unit="ms",utc=True).dt.strftime("%Y-%m")
    z["year"]=pd.to_datetime(z.signal_ts,unit="ms",utc=True).dt.year

    prior=summarize(z[z.period=="PRIOR"],"PRIOR")
    recent=summarize(z[z.period=="RECENT120"],"RECENT120")
    recent180=summarize(z[z.signal_ts>=cut180],"RECENT180")
    overall=summarize(z,"ALL")

    monthly=[summarize(g,str(m)) for m,g in z.groupby("month")]
    yearly=[summarize(g,str(int(y))) for y,g in z.groupby("year")]
    pd.DataFrame(monthly).to_csv(out/"monthly_path.csv",index=False)
    pd.DataFrame(yearly).to_csv(out/"yearly_path.csv",index=False)

    # Compact diagnosis flags.
    diag={
      "first_15m_close_delta_pct":recent["close_15_mean"]-prior["close_15_mean"],
      "first_60m_close_delta_pct":recent["close_60_mean"]-prior["close_60_mean"],
      "mfe_60_delta_pct":recent["mfe_60_mean"]-prior["mfe_60_mean"],
      "mae_60_delta_pct":recent["mae_60_mean"]-prior["mae_60_mean"],
      "mfe_360_delta_pct":recent["mfe_360_mean"]-prior["mfe_360_mean"],
      "mae_360_delta_pct":recent["mae_360_mean"]-prior["mae_360_mean"],
      "tp3_touch_delta_pp":recent["touch_up_3_0_pct"]-prior["touch_up_3_0_pct"],
      "tp2_5_touch_delta_pp":recent["touch_up_2_5_pct"]-prior["touch_up_2_5_pct"],
      "near_miss_delta_pp":recent["near_miss_2_5_to_3_pct"]-prior["near_miss_2_5_to_3_pct"],
      "early_mae1_60_delta_pp":recent["early_mae1_60_pct"]-prior["early_mae1_60_pct"],
      "early_mae2_60_delta_pp":recent["early_mae2_60_pct"]-prior["early_mae2_60_pct"],
    }

    # Heuristic label: whether decay shows before exits could matter.
    entry_score=sum([
      diag["first_15m_close_delta_pct"]<0,
      diag["first_60m_close_delta_pct"]<0,
      diag["mfe_60_delta_pct"]<0,
      diag["mae_60_delta_pct"]<0,
      diag["early_mae1_60_delta_pp"]>0,
    ])
    exit_score=sum([
      diag["tp2_5_touch_delta_pp"]>=-2 and diag["tp3_touch_delta_pp"]<-3,
      diag["near_miss_delta_pp"]>2,
      diag["mfe_360_delta_pct"]>=-0.15 and diag["tp3_touch_delta_pp"]<-3,
    ])
    diagnosis="ENTRY_SIGNAL_DECAY" if entry_score>=3 and entry_score>exit_score else ("EXIT_NEAR_MISS_DECAY" if exit_score>=2 else "MIXED")

    summary={
      "definition":{"candidate":"BODY70 immediate next-15m-open; path only, no retuning","horizons_min":HORIZONS,
                    "recent120_anchor_max_signal_ts":max_ts,"recent120_cut_ts":cut},
      "overall":overall,"prior":prior,"recent120":recent,"recent180":recent180,
      "recent_minus_prior":delta(prior,recent),
      "diagnostic_core":diag,
      "heuristic_diagnosis":diagnosis,
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("BODY70_PATH_MERGE_JSON")
    print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__": main()
