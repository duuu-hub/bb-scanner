#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np, pandas as pd

COSTS=(20,40)
CUT=int(pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000)

def pf(v):
    a=np.asarray(v,float);gp=a[a>0].sum();gl=-a[a<0].sum()
    return float(gp/gl) if gl>0 else (float("inf") if gp>0 else None)

def stats(g,cost):
    if g.empty:return {"n":0}
    v=g.gross_pct.to_numpy(float)-cost/100
    return {
      "n":int(len(v)),"symbols":int(g.symbol.nunique()),
      "wr_pct":float((v>0).mean()*100),"avg_net_pct":float(v.mean()),"pf":pf(v),
      "sum_net_pct":float(v.sum()),"tp_pct":float((g.status=="TP").mean()*100),
      "sl_pct":float((g.status=="SL").mean()*100),"be_pct":float((g.status=="BE").mean()*100),
      "time_pct":float((g.status=="TIME").mean()*100),
      "target_dist_med_pct":float(g.target_dist_pct.median()),
      "near1_dist_med_pct":float(g.near1_dist_pct.median())
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--partials",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/trades.csv.gz",recursive=True))
    if len(fs)!=8:raise RuntimeError(f"expected 8 partials got {len(fs)}")
    z=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    rows=[];summary={"configs":{}}
    for vals,g in z.groupby(["threshold","target","sl_pct","hold"]):
        th,tg,sl,hold=vals; key=f"n{int(th)}_{tg}_sl{sl:g}_{hold}"
        summary["configs"][key]={}
        for sp,gs in [("TRAIN",g[(g.signal_ts<CUT)&(g.exit_ts<CUT)]),("VALIDATION_SEEN",g[g.signal_ts>=CUT]),("ALL",g)]:
            summary["configs"][key][sp]={}
            for c in COSTS:
                s=stats(gs,c); summary["configs"][key][sp][f"{c}bp"]=s
                rows.append({"config":key,"split":sp,"cost_bp":c,**s})
    pd.DataFrame(rows).to_csv(out/"summary_table.csv",index=False)
    cand=[]
    for k,r in summary["configs"].items():
        a20=r["TRAIN"]["20bp"];b20=r["VALIDATION_SEEN"]["20bp"]
        if a20.get("n",0)>=100 and b20.get("n",0)>=100:
            cand.append({"config":k,"train_n":a20["n"],"val_n":b20["n"],
                         "train_pf":a20["pf"],"val_pf":b20["pf"],
                         "train_ev":a20["avg_net_pct"],"val_ev":b20["avg_net_pct"],
                         "robust_pf":min(a20["pf"],b20["pf"]),
                         "avg_ev":(a20["avg_net_pct"]+b20["avg_net_pct"])/2,
                         "be_pct":(a20["be_pct"]+b20["be_pct"])/2})
    cand=sorted(cand,key=lambda x:(x["robust_pf"],x["avg_ev"]),reverse=True)
    pd.DataFrame(cand).to_csv(out/"candidate_ranking.csv",index=False)
    summary["top_candidates"]=cand[:20]
    (out/"summary.json").write_text(json.dumps(summary,indent=2))
    print("BE_SUMMARY",json.dumps({"top":cand[:20]}),flush=True)
if __name__=="__main__":main()
