#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np, pandas as pd

COSTS=(20,40)
CUT=int(pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000)
DAY=24*60*60*1000

def pf(v):
    a=np.asarray(v,float);gp=a[a>0].sum();gl=-a[a<0].sum()
    return float(gp/gl) if gl>0 else (float("inf") if gp>0 else None)

def stats(g,cost_bp):
    if g.empty:return {"n":0}
    v=g.gross_pct.to_numpy(float)-cost_bp/100
    return {
      "n":int(len(v)),"symbols":int(g.symbol.nunique()),
      "wr_pct":float((v>0).mean()*100),"avg_net_pct":float(v.mean()),
      "median_net_pct":float(np.median(v)),"pf":pf(v),"sum_net_pct":float(v.sum()),
      "tp_pct":float((g.status=="TP").mean()*100),
      "sl_pct":float((g.status=="SL").mean()*100),
      "time_pct":float((g.status=="TIME").mean()*100),
      "target_dist_med_pct":float(g.target_dist_pct.median()),
      "fill_lag_med_min":float(g.fill_lag_min.median())
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--partials",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/trades.csv.gz",recursive=True))
    if len(fs)!=8:raise RuntimeError(f"expected 8 partials got {len(fs)}")
    z=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    z=z.sort_values(["threshold","extension_pct","target","sl_pct","hold","signal_ts","symbol"]).reset_index(drop=True)
    z.to_csv(out/"trades_all.csv.gz",index=False,compression="gzip")

    keys=["threshold","extension_pct","target","sl_pct","hold"]
    rows=[];summary={"configs":{}}
    for vals,g in z.groupby(keys):
        th,ext,tg,sl,hold=vals
        key=f"n{int(th)}_ext{ext:g}_{tg}_sl{sl:g}_{hold}"
        summary["configs"][key]={}
        for sp,gs in [("TRAIN",g[(g.signal_ts<CUT)&(g.exit_ts<CUT)]),("VALIDATION_SEEN",g[g.signal_ts>=CUT]),("ALL",g)]:
            summary["configs"][key][sp]={}
            for c in COSTS:
                s=stats(gs,c);summary["configs"][key][sp][f"{c}bp"]=s
                rows.append({"config":key,"split":sp,"cost_bp":c,**s})
    pd.DataFrame(rows).to_csv(out/"summary_table.csv",index=False)

    cand=[]
    for key,rec in summary["configs"].items():
        tr=rec["TRAIN"]["20bp"];va=rec["VALIDATION_SEEN"]["20bp"]
        if tr.get("n",0)>=100 and va.get("n",0)>=100 and tr.get("pf") is not None and va.get("pf") is not None:
            cand.append({
              "config":key,"train_n":tr["n"],"val_n":va["n"],
              "train_pf":tr["pf"],"val_pf":va["pf"],
              "train_ev":tr["avg_net_pct"],"val_ev":va["avg_net_pct"],
              "robust_pf":min(tr["pf"],va["pf"]),
              "avg_ev":(tr["avg_net_pct"]+va["avg_net_pct"])/2,
              "target_dist_med":(tr["target_dist_med_pct"]+va["target_dist_med_pct"])/2,
              "fill_lag_med":(tr["fill_lag_med_min"]+va["fill_lag_med_min"])/2
            })
    cand=sorted(cand,key=lambda x:(x["robust_pf"],x["avg_ev"]),reverse=True)
    pd.DataFrame(cand).to_csv(out/"candidate_ranking.csv",index=False)
    summary["top_candidates"]=cand[:20]

    maxsig=int(z.signal_ts.max())
    recent=[]
    for days in (120,180,365):
        q=z[z.signal_ts>=maxsig-days*DAY]
        for vals,g in q.groupby(keys):
            th,ext,tg,sl,hold=vals
            key=f"n{int(th)}_ext{ext:g}_{tg}_sl{sl:g}_{hold}"
            recent.append({"days":days,"config":key,**stats(g,20)})
    pd.DataFrame(recent).to_csv(out/"recent.csv",index=False)

    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("LIMITFADE_SUMMARY",json.dumps({"top":cand[:20]},default=str),flush=True)

if __name__=="__main__":main()
