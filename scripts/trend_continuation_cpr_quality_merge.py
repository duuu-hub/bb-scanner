#!/usr/bin/env python3
from __future__ import annotations
import glob, json
from pathlib import Path
import numpy as np
import pandas as pd

KEY=["filter","clv_min","vol_ratio_min","split","cost_bp","tp_pct","sl_pct","time_limit"]
SUM=["n","raw_signals","overlap_skips","excluded","tp_n","sl_n","time_n","win_n",
     "sum_net_pct","gross_profit_net_pct","gross_loss_abs_net_pct"]

def main():
    fs=sorted(glob.glob("shard_results/**/cells.csv",recursive=True))
    if len(fs)!=8:
        raise RuntimeError(f"expected 8 shard files, got {len(fs)}")
    z=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
    g=z.groupby(KEY,dropna=False,as_index=False)[SUM].sum()
    g["win_rate_net_pct"]=np.where(g.n>0,g.win_n/g.n*100,np.nan)
    g["avg_net_pct"]=np.where(g.n>0,g.sum_net_pct/g.n,np.nan)
    g["pf_net"]=np.where(g.gross_loss_abs_net_pct>0,g.gross_profit_net_pct/g.gross_loss_abs_net_pct,np.nan)
    g.to_csv("merged_cells.csv",index=False)

    rows=[]
    for f in g["filter"].unique():
        tr=g[(g["filter"]==f)&(g["split"]=="TRAIN")&(g["cost_bp"]==20)]
        ho=g[(g["filter"]==f)&(g["split"]=="HOLDOUT")&(g["cost_bp"]==20)]
        rows.append({"filter":f,
            "train":tr.iloc[0].to_dict() if len(tr) else None,
            "holdout":ho.iloc[0].to_dict() if len(ho) else None})
    positive=[x for x in rows if x["train"] and x["holdout"] and x["train"]["avg_net_pct"]>0 and x["holdout"]["avg_net_pct"]>0]
    summary={"shards":len(fs),"paired_20bp":rows,"train_holdout_positive_20bp":positive}
    Path("merged_summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("QUALITY_MERGE_JSON"); print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__": main()
