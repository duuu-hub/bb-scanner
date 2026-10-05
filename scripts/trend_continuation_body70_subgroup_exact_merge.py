#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np
import pandas as pd

COSTS=(0.20,0.30,0.40)
DAY=24*60*60*1000
VARS=("BODY70","BODY80_90","BODY90_100","BODY80_PLUS","BODY70_H12_17","BODY70_RANGE8","BODY80_90_H12_17")

def perf(g,cost):
    y=g.gross_pct.astype(float).to_numpy()-cost
    if not len(y): return {"n":0}
    gp=y[y>0].sum(); gl=-y[y<0].sum()
    return {"n":int(len(y)),"wr_pct":float((y>0).mean()*100),"ev_pct":float(y.mean()),
            "pf":float(gp/gl) if gl>0 else None,"sum_net_pct":float(y.sum())}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--partials",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/trades.csv.gz",recursive=True))
    if len(fs)!=8: raise RuntimeError(f"expected 8 partials, got {len(fs)}")
    z=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    z=z.sort_values(["variant","signal_ts","symbol"]).reset_index(drop=True)
    z.to_csv(out/"trades_all.csv.gz",index=False,compression="gzip")
    z["year"]=pd.to_datetime(z.signal_ts,unit="ms",utc=True).dt.year
    max_ts=int(z.signal_ts.max()); cut120=max_ts-120*DAY; cut180=max_ts-180*DAY

    summary={"max_signal_ts":max_ts,"variants":{}}
    yr=[]
    for v in VARS:
        g=z[z.variant.eq(v)]
        rec={"all":{},"train":{},"holdout":{},"recent120":{},"recent180":{}}
        train=g[g.signal_ts<pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000]
        hold=g[g.signal_ts>=pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000]
        r120=g[g.signal_ts>=cut120]; r180=g[g.signal_ts>=cut180]
        for c in COSTS:
            tag=f"{int(c*100)}bp"
            rec["all"][tag]=perf(g,c); rec["train"][tag]=perf(train,c); rec["holdout"][tag]=perf(hold,c)
            rec["recent120"][tag]=perf(r120,c); rec["recent180"][tag]=perf(r180,c)
        for y,gy in g.groupby("year"):
            m=perf(gy,.20); yr.append({"variant":v,"year":int(y),**m})
        summary["variants"][v]=rec
    pd.DataFrame(yr).to_csv(out/"yearly.csv",index=False)
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("BODY70_SUBGROUP_EXACT_JSON")
    print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__": main()
