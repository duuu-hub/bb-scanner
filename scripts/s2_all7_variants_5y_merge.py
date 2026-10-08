#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np, pandas as pd

COSTS=(20,40); DELAYS=(0,1,2)
VARS=("S2_CURRENT","ALL7_FRESH","S2_TO_7","S1_CURRENT")
TRAIN_START=int(pd.Timestamp("2021-01-01",tz="UTC").timestamp()*1000)
CUT=int(pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000)

def pf(v):
    a=np.asarray(v,float);gp=a[a>0].sum();gl=-a[a<0].sum()
    return float(gp/gl) if gl>0 else (float("inf") if gp>0 else None)

def st(g,c):
    if g.empty:return {"n":0}
    v=g.gross_pct.to_numpy(float)-c/100.0
    return {"n":int(len(v)),"symbols":int(g.symbol.nunique()),"wr":float((v>0).mean()*100),
            "avg":float(v.mean()),"pf":pf(v),"sum":float(v.sum()),
            "tp":float((g.status=="TP").mean()*100),"sl":float((g.status=="SL").mean()*100),
            "time":float((g.status=="TIME").mean()*100)}

def split(g,name):
    if name=="TRAIN":return g[(g.signal_ts>=TRAIN_START)&(g.signal_ts<CUT)&(g.exit_ts<CUT)]
    if name=="VALIDATION_SEEN":return g[g.signal_ts>=CUT]
    return g[g.signal_ts>=TRAIN_START]

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--partials",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/variant_trades.csv.gz",recursive=True))
    ms=sorted(glob.glob(a.partials+"/**/variant_meta.json",recursive=True))
    if len(fs)!=8 or len(ms)!=8:raise RuntimeError(f"expected 8 partials {len(fs)}/{len(ms)}")
    z=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    z["year"]=pd.to_datetime(z.signal_ts,unit="ms",utc=True).dt.year
    z.to_csv(out/"variant_trades_all.csv.gz",index=False,compression="gzip")
    raw={v:0 for v in VARS}
    for p in ms:
        j=json.loads(Path(p).read_text())
        for v,n in j["raw_signals"].items():raw[v]+=int(n)
    summary={"raw_signals":raw,"variants":{}}
    rows=[];yr=[]
    for v in VARS:
        gv=z[z.variant==v]
        summary["variants"][v]={}
        for d in DELAYS:
            gd=gv[gv.delay_min==d]
            summary["variants"][v][f"delay{d}"]={}
            for sp in ("TRAIN","VALIDATION_SEEN","ALL"):
                gs=split(gd,sp)
                rec={}
                for c in COSTS:
                    rec[f"{c}bp"]=st(gs,c)
                    rows.append({"variant":v,"delay":d,"split":sp,"cost_bp":c,**st(gs,c)})
                summary["variants"][v][f"delay{d}"][sp]=rec
        g0=gv[gv.delay_min==0]
        for y,gy in g0.groupby("year"):
            for c in COSTS:yr.append({"variant":v,"year":int(y),"cost_bp":c,**st(gy,c)})
    pd.DataFrame(rows).to_csv(out/"summary_table.csv",index=False)
    pd.DataFrame(yr).to_csv(out/"yearly.csv",index=False)
    (out/"summary.json").write_text(json.dumps(summary,indent=2))
    print("S2_ALL7_SUMMARY",json.dumps(summary),flush=True)
if __name__=="__main__":main()
