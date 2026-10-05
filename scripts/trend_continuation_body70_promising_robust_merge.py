#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np
import pandas as pd

COSTS=(0.20,0.30,0.40)
DAY=24*60*60*1000
VARS=("RANGE8","B80_90","B80_90_H12_17")
DELAYS=(0,1,2)

def max_ls(x):
    b=c=0
    for v in x:
        if v<0: c+=1; b=max(b,c)
        else: c=0
    return b

def mdd_sum(x):
    eq=peak=0.0; dd=0.0
    for v in x:
        eq+=v; peak=max(peak,eq); dd=min(dd,eq-peak)
    return dd

def perf(g,cost):
    y=g.gross_pct.astype(float).to_numpy()-cost
    if not len(y): return {"n":0}
    gp=y[y>0].sum(); gl=-y[y<0].sum()
    return {"n":int(len(y)),"wr_pct":float((y>0).mean()*100),"ev_pct":float(y.mean()),
            "pf":float(gp/gl) if gl>0 else None,"sum_net_pct":float(y.sum()),
            "max_ls":int(max_ls(y)),"mdd_pctsum":float(mdd_sum(y))}

def concurrency(g):
    pts=[]
    for r in g.itertuples(index=False):
        pts.append((int(r.entry_ts),1)); pts.append((int(r.exit_ts),-1))
    pts.sort(key=lambda x:(x[0],x[1]))
    cur=mx=0
    for _,d in pts:
        cur+=d; mx=max(mx,cur)
    return int(mx)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--partials",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/trades.csv.gz",recursive=True))
    if len(fs)!=8: raise RuntimeError(f"expected 8 partials, got {len(fs)}")
    z=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    z=z.sort_values(["variant","delay_min","entry_ts","symbol"]).reset_index(drop=True)
    z.to_csv(out/"trades_all.csv.gz",index=False,compression="gzip")
    max_ts=int(z.signal_ts.max()); cut120=max_ts-120*DAY
    cut2025=int(pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000)

    summary={"max_signal_ts":max_ts,"variants":{}}
    for v in VARS:
        summary["variants"][v]={}
        for d in DELAYS:
            g=z[(z.variant==v)&(z.delay_min==d)]
            rec={"all":{},"train":{},"holdout":{},"recent120":{},"max_concurrent":concurrency(g)}
            segs={"all":g,"train":g[g.signal_ts<cut2025],"holdout":g[g.signal_ts>=cut2025],"recent120":g[g.signal_ts>=cut120]}
            for sname,sg in segs.items():
                for c in COSTS:
                    rec[sname][f"{int(c*100)}bp"]=perf(sg,c)
            summary["variants"][v][f"delay{d}"]=rec
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("BODY70_PROMISING_ROBUST_JSON")
    print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__": main()
