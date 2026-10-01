#!/usr/bin/env python3
from __future__ import annotations
import glob, json, math
from pathlib import Path
import numpy as np
import pandas as pd

KEY=["lookback","tail","threshold_pct","sl_pct","time_limit","cost_bp","split"]
SUMCOLS=["n","raw_signals","overlap_skips","excluded","tp_n","sl_n","time_n","win_n",
         "sum_net_pct","gross_profit_net_pct","gross_loss_abs_net_pct"]

def main():
    fs=sorted(glob.glob("shard_results/**/cells.csv",recursive=True))
    if len(fs)!=8:
        raise RuntimeError(f"expected 8 shard cells files, got {len(fs)}")
    z=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
    g=z.groupby(KEY,dropna=False,as_index=False)[SUMCOLS].sum()
    g["tp_rate_pct"]=np.where(g.n>0,g.tp_n/g.n*100,np.nan)
    g["win_rate_net_pct"]=np.where(g.n>0,g.win_n/g.n*100,np.nan)
    g["avg_net_pct"]=np.where(g.n>0,g.sum_net_pct/g.n,np.nan)
    g["pf_net"]=np.where(g.gross_loss_abs_net_pct>0,g.gross_profit_net_pct/g.gross_loss_abs_net_pct,np.nan)
    g.to_csv("merged_cells.csv",index=False)

    k=["lookback","tail","threshold_pct","sl_pct","time_limit","cost_bp"]
    tr=g[(g.split=="TRAIN")&(g.n>=100)].copy()
    tr=tr.sort_values(["avg_net_pct","pf_net","n"],ascending=[False,False,False]).head(50)
    paired=[]
    for r in tr.to_dict("records"):
        h=g[g.split.eq("HOLDOUT")]
        for x in k:
            h=h[h[x].eq(r[x])]
        paired.append({"train":r,"holdout":h.iloc[0].to_dict() if len(h) else None})

    robust=[]
    for x in paired:
        a=x["train"]; b=x["holdout"]
        if not b: continue
        if a.get("avg_net_pct",float("nan"))>0 and b.get("avg_net_pct",float("nan"))>0:
            robust.append(x)
    summary={
        "shards":8,
        "cells":int(len(g)),
        "top_train_paired_holdout":paired[:30],
        "train_and_holdout_positive":robust[:30],
        "note":"PF/avg/win rate are exact merged aggregates; median and max losing streak require finalist chronological replay."
    }
    Path("merged_summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("MERGE_RESULT_JSON")
    print(json.dumps(summary,default=str))

if __name__=="__main__":
    main()
