#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd

COSTS=(20,40)
SPLIT=int(pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000)
SIDE=("SHORT","LONG")
N=(5,6)
CONF=("FIRST_BODY","BREAK_SETUP_EXTREME","RETURN_15M")
TARGS=("far2","far3")
STOPS=(3.,5.)

def pf(a):
    a=np.asarray(a,float)
    win=a[a>0].sum();loss=-a[a<0].sum()
    if loss<=0:return float("inf") if win>0 else None
    return float(win/loss)

def stats(z,c):
    if z.empty:return {"n":0}
    a=z.gross_pct.to_numpy(float)-c/100
    return {"n":int(len(a)),"symbols":int(z.symbol.nunique()),"pf":pf(a),"avg_net_pct":float(np.mean(a)),
            "win_pct":float((a>0).mean()*100),"tp_pct":float((z.status=="TP").mean()*100),
            "sl_pct":float((z.status=="SL").mean()*100),"time_pct":float((z.status=="TIME").mean()*100),
            "avg_hold_min":float(((z.exit_ts-z.entry_ts)/60000).mean())}

def diag(g):
    if g.empty:return {"n":0}
    return {"n":int(len(g)),"median_distance_pct":float(g.target_distance_pct.median()),
      "raw_hit_1h_pct":float(g.raw_hit_1h.mean()*100),
      "raw_hit_4h_pct":float(g.raw_hit_4h.mean()*100),
      "median_mae_4h_pct":float(g.max_adverse_4h_pct.median())}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--partials",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();p=Path(a.out);p.mkdir(parents=True,exist_ok=True)
    tfiles=sorted(glob.glob(a.partials+"/**/trades.csv.gz",recursive=True))
    dfiles=sorted(glob.glob(a.partials+"/**/diagnostics.csv.gz",recursive=True))
    mfiles=sorted(glob.glob(a.partials+"/**/meta.json",recursive=True))
    if not (len(tfiles)==len(dfiles)==len(mfiles)==8):raise RuntimeError(f"incomplete shards {len(tfiles)} {len(dfiles)} {len(mfiles)}")
    t=pd.concat([pd.read_csv(x,compression="gzip") for x in tfiles],ignore_index=True)
    d=pd.concat([pd.read_csv(x,compression="gzip") for x in dfiles],ignore_index=True)
    t.to_csv(p/"all_trades.csv.gz",compression="gzip",index=False)
    d.to_csv(p/"all_diagnostics.csv.gz",compression="gzip",index=False)
    counts=Counter(); excludes=Counter()
    for x in mfiles:
        j=json.loads(Path(x).read_text())
        counts.update(j["counts"]);excludes.update(j["exclusions"])
    diagrows=[]
    for (s,n,tg),g in d.groupby(["side","threshold","target"]):
        diagrows.append({"side":s,"threshold":int(n),"target":tg,**diag(g)})
    pd.DataFrame(diagrows).to_csv(p/"diagnostics_summary.csv",index=False)

    rows=[]
    for (side,n,confirm,tg,sl),g in t.groupby(["side","threshold","confirmation","target","sl_pct"]):
        for period in ("TRAIN","VALIDATION_SEEN","ALL"):
            sub=(g[(g.signal_ts<SPLIT)&(g.exit_ts<SPLIT)] if period=="TRAIN" else (g[g.signal_ts>=SPLIT] if period=="VALIDATION_SEEN" else g))
            for cost in COSTS:
                rows.append({"side":side,"threshold":int(n),"confirmation":confirm,"target":tg,"sl_pct":float(sl),
                             "period":period,"cost_bp":cost,**stats(sub,cost)})
    tbl=pd.DataFrame(rows);tbl.to_csv(p/"summary_table.csv",index=False)
    # Frozen equally weighted account simulation and full MTM audit still required: no promotion.
    top=[]
    for (s,n,confirm,tg,sl),g in tbl[tbl.cost_bp==40].groupby(["side","threshold","confirmation","target","sl_pct"]):
        tr=g[g.period=="TRAIN"];val=g[g.period=="VALIDATION_SEEN"]
        if tr.empty or val.empty:continue
        tr=tr.iloc[0];val=val.iloc[0]
        if tr.n<100 or val.n<100:continue
        top.append({"side":s,"threshold":int(n),"confirmation":confirm,"target":tg,"sl_pct":float(sl),
                    "train_n":int(tr.n),"val_n":int(val.n),"train_pf40":float(tr.pf),"val_pf40":float(val.pf),
                    "train_avg40":float(tr.avg_net_pct),"val_avg40":float(val.avg_net_pct),
                    "robust_pf":min(float(tr.pf),float(val.pf))})
    top.sort(key=lambda x:x["robust_pf"],reverse=True)
    pd.DataFrame(top).to_csv(p/"rank_40bp.csv",index=False)
    summary={"class":"EXPLORATORY_PROVISIONAL","raw_counts":dict(counts),"exclusions":dict(excludes),"top_40bp":top[:20],
             "diagnostic":diagrows,"periods":{"TRAIN":"2021-09 through 2024-12","VALIDATION_SEEN":"2025-01 through 2026-08"},
             "funding":"excluded","portfolio_sim":"not yet certified"}
    (p/"summary.json").write_text(json.dumps(summary,indent=2))
    print("BIDIR_FINAL_SUMMARY",json.dumps(summary),flush=True)
if __name__=="__main__": main()
