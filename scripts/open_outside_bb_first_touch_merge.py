#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np, pandas as pd

COSTS=(20,40)
TRAIN_CUT=int(pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000)

def pf(v):
    a=np.asarray(v,float);gp=a[a>0].sum();gl=-a[a<0].sum()
    return float(gp/gl) if gl>0 else (float("inf") if gp>0 else None)

def max_ls(v):
    b=c=0
    for x in v:
        if x<0:c+=1;b=max(b,c)
        else:c=0
    return b

def stats(g,cost_bp):
    if g.empty:return {"n":0}
    v=g.gross_pct.to_numpy(float)-cost_bp/100.0
    return {
      "n":int(len(v)),"symbols":int(g.symbol.nunique()),
      "wr_pct":float((v>0).mean()*100),"avg_net_pct":float(v.mean()),
      "median_net_pct":float(np.median(v)),"pf":pf(v),"sum_net_pct":float(v.sum()),
      "max_losing_streak":int(max_ls(v)),
      "tp_pct":float((g.status=="TP").mean()*100),
      "sl_pct":float((g.status=="SL").mean()*100),
      "time_pct":float((g.status=="TIME").mean()*100),
      "target_dist_med_pct":float(g.target_dist_pct.median())
    }

def split(g,name):
    if name=="TRAIN":return g[(g.signal_ts<TRAIN_CUT)&(g.exit_ts<TRAIN_CUT)]
    if name=="VALIDATION_SEEN":return g[g.signal_ts>=TRAIN_CUT]
    return g

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--partials",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/trades.csv.gz",recursive=True))
    ms=sorted(glob.glob(a.partials+"/**/meta.json",recursive=True))
    if len(fs)!=8 or len(ms)!=8:raise RuntimeError(f"expected 8 partials got {len(fs)}/{len(ms)}")
    z=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    z=z.sort_values(["threshold","target","sl_pct","hold","signal_ts","symbol"]).reset_index(drop=True)
    z.to_csv(out/"trades_all.csv.gz",index=False,compression="gzip")
    signal_counts={"5":0,"6":0}; exclusions={}
    for p in ms:
        j=json.loads(Path(p).read_text())
        for k,v in j.get("signals",{}).items():signal_counts[k]+=int(v)
        for k,v in j.get("excluded",{}).items():exclusions[k]=exclusions.get(k,0)+int(v)

    summary={"signal_counts":signal_counts,"excluded":exclusions,"configs":{}}
    rows=[]
    for (th,tg,sl,hold),g in z.groupby(["threshold","target","sl_pct","hold"]):
        key=f"n{int(th)}_{tg}_sl{sl:g}_{hold}"
        summary["configs"][key]={}
        for sp in ("TRAIN","VALIDATION_SEEN","ALL"):
            gs=split(g,sp)
            summary["configs"][key][sp]={}
            for c in COSTS:
                s=stats(gs,c);summary["configs"][key][sp][f"{c}bp"]=s
                rows.append({"config":key,"threshold":int(th),"target":tg,"sl_pct":sl,"hold":hold,"split":sp,"cost_bp":c,**s})
    tab=pd.DataFrame(rows)
    tab.to_csv(out/"summary_table.csv",index=False)

    # rank candidates by worst of train/validation PF at 20bp, then avg EV
    cand=[]
    for key,rec in summary["configs"].items():
        a20=rec["TRAIN"]["20bp"]; b20=rec["VALIDATION_SEEN"]["20bp"]
        if a20.get("n",0)>=100 and b20.get("n",0)>=100:
            cand.append({
              "config":key,"train_n":a20["n"],"val_n":b20["n"],
              "train_pf":a20["pf"],"val_pf":b20["pf"],
              "train_ev":a20["avg_net_pct"],"val_ev":b20["avg_net_pct"],
              "robust_pf":min(a20["pf"],b20["pf"]) if a20["pf"] is not None and b20["pf"] is not None else None,
              "avg_ev":(a20["avg_net_pct"]+b20["avg_net_pct"])/2
            })
    cand=sorted(cand,key=lambda x:((x["robust_pf"] or -999),x["avg_ev"]),reverse=True)
    pd.DataFrame(cand).to_csv(out/"candidate_ranking.csv",index=False)
    summary["top_candidates_20bp"]=cand[:12]
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("OPENBB_FT_SUMMARY",json.dumps({"signals":signal_counts,"top":cand[:12]},default=str),flush=True)

if __name__=="__main__":main()
