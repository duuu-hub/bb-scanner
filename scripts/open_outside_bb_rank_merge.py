#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np, pandas as pd

def stat(g, prefix):
    d=pd.to_numeric(g[f"{prefix}_dist_pct"],errors="coerce")
    out={"n":int(d.notna().sum())}
    if out["n"]==0:return out
    out["dist_med_pct"]=float(d.median())
    for h in ("1h","2h","4h"):
        x=pd.to_numeric(g[f"{prefix}_hit_{h}"],errors="coerce").dropna()
        out[f"hit_{h}_pct"]=float(x.mean()*100) if len(x) else None
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--partials",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/rank_events.csv.gz",recursive=True))
    if len(fs)!=8: raise RuntimeError(f"expected 8 partials got {len(fs)}")
    z=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    z=z.sort_values(["signal_ts","symbol"]).reset_index(drop=True)
    summary={}
    rows=[]
    for n in (3,4,5,6,7):
        g=z[(z.prev_contig.eq(1))&(z.outside_count.ge(n))&(z.prev_outside_count.lt(n))]
        rec={"n":int(len(g)),"mae4h_med_pct":float(pd.to_numeric(g.mae_up_4h_pct,errors="coerce").median()) if len(g) else None}
        for p in ("near1","near2","near3","far1","far2","far3"):
            rec[p]=stat(g,p)
            x=rec[p]
            rows.append({"fresh_atleast":n,"target":p,"signals":len(g),**x,"mae4h_med_pct":rec["mae4h_med_pct"]})
        summary[f"fresh_atleast{n}"]=rec
    pd.DataFrame(rows).to_csv(out/"rank_summary.csv",index=False)
    (out/"summary.json").write_text(json.dumps(summary,indent=2))
    print("RANKBB_SUMMARY",json.dumps(summary),flush=True)
if __name__=="__main__":main()
