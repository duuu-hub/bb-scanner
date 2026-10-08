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

def stats(g,cost):
    if g.empty:return {"n":0}
    v=g.gross_pct.to_numpy(float)-cost/100
    return {
      "n":int(len(v)),"symbols":int(g.symbol.nunique()),
      "wr_pct":float((v>0).mean()*100),"avg_net_pct":float(v.mean()),
      "pf":pf(v),"sum_net_pct":float(v.sum()),
      "tp_pct":float((g.status=="TP").mean()*100),
      "sl_pct":float((g.status=="SL").mean()*100),
      "be_pct":float((g.status=="BE").mean()*100),
      "time_pct":float((g.status=="TIME").mean()*100),
      "target_dist_med_pct":float(g.target_dist_pct.median()),
      "near1_dist_med_pct":float(g.near1_dist_pct.median())
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--partials",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/trades.csv.gz",recursive=True))
    ms=sorted(glob.glob(a.partials+"/**/meta.json",recursive=True))
    if len(fs)!=8 or len(ms)!=8:raise RuntimeError(f"expected 8 partials got {len(fs)}/{len(ms)}")
    z=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    z=z.sort_values(["threshold","target","sl_pct","signal_ts","symbol"]).reset_index(drop=True)
    z["year"]=pd.to_datetime(z.signal_ts,unit="ms",utc=True).dt.year
    z.to_csv(out/"trades_all.csv.gz",index=False,compression="gzip")
    summary={"configs":{},"excluded":{}}
    for p in ms:
        j=json.loads(Path(p).read_text())
        for k,v in j.get("excluded",{}).items():summary["excluded"][k]=summary["excluded"].get(k,0)+int(v)
    rows=[]; yr=[]; recent=[]
    for vals,g in z.groupby(["threshold","target","sl_pct","hold"]):
        th,tg,sl,hold=vals; key=f"n{int(th)}_{tg}_sl{sl:g}_{hold}"
        summary["configs"][key]={}
        for sp,gs in [("TRAIN",g[(g.signal_ts<CUT)&(g.exit_ts<CUT)]),("VALIDATION_SEEN",g[g.signal_ts>=CUT]),("ALL",g)]:
            summary["configs"][key][sp]={}
            for c in COSTS:
                s=stats(gs,c);summary["configs"][key][sp][f"{c}bp"]=s
                rows.append({"config":key,"split":sp,"cost_bp":c,**s})
        for y,gy in g.groupby("year"):
            yr.append({"config":key,"year":int(y),**stats(gy,20)})
        maxsig=int(g.signal_ts.max())
        for days in (120,180,365):
            recent.append({"config":key,"days":days,**stats(g[g.signal_ts>=maxsig-days*DAY],20)})
    pd.DataFrame(rows).to_csv(out/"summary_table.csv",index=False)
    pd.DataFrame(yr).to_csv(out/"yearly.csv",index=False)
    pd.DataFrame(recent).to_csv(out/"recent.csv",index=False)
    ranking=[]
    for k,r in summary["configs"].items():
        a20=r["TRAIN"]["20bp"];b20=r["VALIDATION_SEEN"]["20bp"]
        ranking.append({
          "config":k,"train_n":a20["n"],"val_n":b20["n"],
          "train_pf20":a20["pf"],"val_pf20":b20["pf"],
          "train_ev20":a20["avg_net_pct"],"val_ev20":b20["avg_net_pct"],
          "train_pf40":r["TRAIN"]["40bp"]["pf"],"val_pf40":r["VALIDATION_SEEN"]["40bp"]["pf"],
          "train_ev40":r["TRAIN"]["40bp"]["avg_net_pct"],"val_ev40":r["VALIDATION_SEEN"]["40bp"]["avg_net_pct"]
        })
    ranking=sorted(ranking,key=lambda x:(min(x["train_pf20"],x["val_pf20"]),x["val_pf40"]),reverse=True)
    summary["ranking"]=ranking
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("BE_EXACT_SUMMARY",json.dumps(ranking),flush=True)
if __name__=="__main__":main()
