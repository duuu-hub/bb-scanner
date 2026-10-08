#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np, pandas as pd

H=("15m","30m","1h","2h","4h")
TARGETS=("nearest","median","deepest","m15")
COSTS=(0.20,0.40)

def q(x,p):
    x=pd.to_numeric(x,errors="coerce").dropna()
    return float(x.quantile(p)) if len(x) else None

def summarize(g):
    out={"n":int(len(g))}
    if not len(g):return out
    for t in TARGETS:
        d=pd.to_numeric(g[f"{t}_dist_pct"],errors="coerce")
        out[f"{t}_distance"]={
            "n":int(d.notna().sum()),"median_pct":q(d,.5),"p25_pct":q(d,.25),"p75_pct":q(d,.75)
        }
        for h in H:
            x=pd.to_numeric(g[f"{t}_hit_{h}"],errors="coerce").dropna()
            out[f"{t}_hit_{h}_pct"]=float(x.mean()*100) if len(x) else None
    for h in H:
        out[f"mae_up_{h}_median_pct"]=q(g[f"mae_up_{h}_pct"],.5)
        out[f"mae_up_{h}_p75_pct"]=q(g[f"mae_up_{h}_pct"],.75)
        out[f"close_{h}_mean_pct"]=float(pd.to_numeric(g[f"close_{h}_ret_pct"],errors="coerce").mean())
    return out

def econ_proxy(g,target,h):
    d=pd.to_numeric(g[f"{target}_dist_pct"],errors="coerce")
    hit=pd.to_numeric(g[f"{target}_hit_{h}"],errors="coerce")
    valid=d.notna()&hit.notna()
    if not valid.any():return {}
    out={}
    for c in COSTS:
        # Lower-bound/simple potential proxy: if boundary touched, gross capture = entry->frozen boundary distance.
        # Non-hit is NOT assigned a loss; this is not an executable strategy PnL.
        cap=(d[valid]-c)
        hh=hit[valid].astype(bool)
        out[f"{int(c*100)}bp"]={
            "hit_n":int(hh.sum()),
            "hit_rate_pct":float(hh.mean()*100),
            "median_net_capture_if_hit_pct":float(cap[hh].median()) if hh.any() else None,
            "pct_hits_covering_cost":float((cap[hh]>0).mean()*100) if hh.any() else None,
        }
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--partials",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/events.csv.gz",recursive=True))
    if len(fs)!=8:raise RuntimeError(f"expected 8 partials got {len(fs)}")
    z=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    z=z.sort_values(["signal_ts","symbol"]).reset_index(drop=True)
    z.to_csv(out/"events_all.csv.gz",index=False,compression="gzip")

    summary={"overall":summarize(z),"exact_count":{},"fresh_threshold":{},"m15_split":{},"econ_proxy":{}}
    for n in range(2,8):
        summary["exact_count"][str(n)]=summarize(z[z.outside_count.eq(n)])
        fresh=z[(z.prev_contig.eq(1))&(z.outside_count.ge(n))&(z.prev_outside_count.lt(n))]
        summary["fresh_threshold"][f"atleast{n}"]=summarize(fresh)
    summary["m15_split"]["m15_inside"]=summarize(z[z.m15_outside.eq(0)])
    summary["m15_split"]["m15_outside"]=summarize(z[z.m15_outside.eq(1)])

    for n in range(2,8):
        g=z[(z.prev_contig.eq(1))&(z.outside_count.ge(n))&(z.prev_outside_count.lt(n))]
        summary["econ_proxy"][f"fresh_atleast{n}"]={}
        for t in TARGETS:
            summary["econ_proxy"][f"fresh_atleast{n}"][t]={h:econ_proxy(g,t,h) for h in ("1h","2h","4h")}

    # compact comparison table
    rows=[]
    for n in range(2,8):
        g=z[(z.prev_contig.eq(1))&(z.outside_count.ge(n))&(z.prev_outside_count.lt(n))]
        s=summarize(g)
        rows.append({
            "fresh_atleast":n,"n":len(g),
            "nearest_dist_med_pct":s.get("nearest_distance",{}).get("median_pct"),
            "nearest_hit_1h_pct":s.get("nearest_hit_1h_pct"),
            "nearest_hit_2h_pct":s.get("nearest_hit_2h_pct"),
            "nearest_hit_4h_pct":s.get("nearest_hit_4h_pct"),
            "median_dist_med_pct":s.get("median_distance",{}).get("median_pct"),
            "median_hit_4h_pct":s.get("median_hit_4h_pct"),
            "deepest_dist_med_pct":s.get("deepest_distance",{}).get("median_pct"),
            "deepest_hit_4h_pct":s.get("deepest_hit_4h_pct"),
            "mae_up_1h_med_pct":s.get("mae_up_1h_median_pct"),
            "mae_up_4h_med_pct":s.get("mae_up_4h_median_pct"),
        })
    pd.DataFrame(rows).to_csv(out/"fresh_thresholds.csv",index=False)
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("OPENBB_MERGED",json.dumps({"rows":len(z),"fresh_rows":rows},default=str),flush=True)
if __name__=="__main__":main()
