#!/usr/bin/env python3
from __future__ import annotations
import glob, json
from pathlib import Path
import numpy as np
import pandas as pd

KEY=["pullback_pct","split","cost_bp","tp_pct","sl_pct","time_limit","setup_window","reclaim"]
SUM=["signal_n","n","overlap_skips","no_setup","excluded","tp_n","sl_n","time_n","win_n",
     "sum_net_pct","gross_profit_net_pct","gross_loss_abs_net_pct"]

def main():
    fs=sorted(glob.glob("shard_results/**/cells.csv",recursive=True))
    if len(fs)!=8:
        raise RuntimeError(f"expected 8 CPR shard files, got {len(fs)}")
    z=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
    z["bars_weight"]=z["avg_bars_to_entry"].fillna(0)*z["n"]
    g=z.groupby(KEY,as_index=False,dropna=False)[SUM+["bars_weight"]].sum()
    g["setup_fill_pct"]=np.where(g.signal_n>0,g.n/g.signal_n*100,np.nan)
    g["win_rate_net_pct"]=np.where(g.n>0,g.win_n/g.n*100,np.nan)
    g["avg_net_pct"]=np.where(g.n>0,g.sum_net_pct/g.n,np.nan)
    g["pf_net"]=np.where(g.gross_loss_abs_net_pct>0,g.gross_profit_net_pct/g.gross_loss_abs_net_pct,np.nan)
    g["avg_bars_to_entry"]=np.where(g.n>0,g.bars_weight/g.n,np.nan)
    g=g.drop(columns=["bars_weight"])
    g.to_csv("merged_cells.csv",index=False)

    paired=[]
    for d in sorted(g.pullback_pct.unique()):
        tr=g[(g.pullback_pct==d)&(g.split=="TRAIN")&(g.cost_bp==20)]
        ho=g[(g.pullback_pct==d)&(g.split=="HOLDOUT")&(g.cost_bp==20)]
        paired.append({
            "pullback_pct":float(d),
            "train":tr.iloc[0].to_dict() if len(tr) else None,
            "holdout":ho.iloc[0].to_dict() if len(ho) else None,
        })
    summary={
        "shard_files":len(fs),
        "design":"8h>=20% top10 -> pullback/reclaim -> next 15m open; TP3 SL5 6h",
        "paired_20bp":paired,
        "train_holdout_positive_20bp":[x for x in paired if x["train"] and x["holdout"] and x["train"]["avg_net_pct"]>0 and x["holdout"]["avg_net_pct"]>0],
    }
    Path("merged_summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("CPR_MERGE_JSON")
    print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__":
    main()
