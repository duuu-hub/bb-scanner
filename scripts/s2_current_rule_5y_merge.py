#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json, math
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd

COSTS=(20,40)
DELAYS=(0,1,2)
TRAIN_START=int(pd.Timestamp("2021-01-01",tz="UTC").timestamp()*1000)
CUT=int(pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000)
DAY=24*60*60*1000

def pf(vals):
    a=np.asarray(vals,float)
    gp=a[a>0].sum(); gl=-a[a<0].sum()
    return float(gp/gl) if gl>0 else (float("inf") if gp>0 else None)

def max_ls(vals):
    best=cur=0
    for x in vals:
        if x<0: cur+=1; best=max(best,cur)
        else: cur=0
    return best

def stats(g,cost_bp):
    if g.empty:return {"n":0}
    v=g.gross_pct.to_numpy(float)-cost_bp/100.0
    return {
        "n":int(len(v)),
        "symbols":int(g.symbol.nunique()),
        "win_rate_pct":float((v>0).mean()*100),
        "avg_net_pct":float(v.mean()),
        "median_net_pct":float(np.median(v)),
        "pf":pf(v),
        "sum_net_pct":float(v.sum()),
        "max_losing_streak":int(max_ls(v)),
        "tp_pct":float((g.status=="TP").mean()*100),
        "sl_pct":float((g.status=="SL").mean()*100),
        "time_pct":float((g.status=="TIME").mean()*100),
    }

def split_name(r):
    s=int(r.signal_ts); x=int(r.exit_ts)
    if s>=TRAIN_START and s<CUT and x<CUT:return "TRAIN"
    if s>=CUT:return "VALIDATION_SEEN"
    return "EXCLUDED_SPLIT_BOUNDARY"

def event_counts(g):
    if g.empty:return {"unique_15m_boundaries":0,"one_hour_clusters":0}
    times=np.sort(g.signal_ts.unique().astype(np.int64))
    clusters=0; last=None
    for t in times:
        if last is None or t-last>60*60*1000:
            clusters+=1
        last=t
    return {"unique_15m_boundaries":int(len(times)),"one_hour_clusters":int(clusters)}

def portfolio(g,cost_bp):
    # Fixed current operating constraints, deterministic neutral tie-break:
    # entry_ts then symbol. Entries at a timestamp are considered before exits
    # carrying the same timestamp (conservative capacity treatment).
    if g.empty:return {"trades_taken":0}
    z=g.sort_values(["entry_ts","symbol"]).copy()
    entries={}
    exits={}
    for i,r in z.iterrows():
        entries.setdefault(int(r.entry_ts),[]).append((i,r))
        exits.setdefault(int(r.exit_ts),[]).append(i)
    times=sorted(set(entries)|set(exits))
    balance=1.0; peak=1.0; mdd=0.0
    openpos={}; accepted=[]; skipped=0; maxopen=0; maxgross=0.0
    for t in times:
        # entries first if same timestamp as existing exits: conservative
        for i,r in entries.get(t,[]):
            if len(openpos)>=6:
                skipped+=1; continue
            notional=balance*0.30
            gross=sum(p["notional"] for p in openpos.values())
            if balance<=0 or gross+notional>balance*2.0+1e-12:
                skipped+=1; continue
            openpos[i]={"notional":notional,"ret":float(r.gross_pct)-cost_bp/100.0}
            accepted.append(i)
            maxopen=max(maxopen,len(openpos))
            maxgross=max(maxgross,(gross+notional)/balance if balance>0 else 0)
        for i in exits.get(t,[]):
            p=openpos.pop(i,None)
            if p is None:continue
            balance += p["notional"]*(p["ret"]/100.0)
            peak=max(peak,balance)
            if peak>0:mdd=max(mdd,(peak-balance)/peak)
    return {
        "signals_available":int(len(g)),
        "trades_taken":int(len(accepted)),
        "skipped_capacity":int(skipped),
        "return_pct":float((balance-1)*100),
        "realized_close_mdd_pct":float(mdd*100),
        "max_open_positions":int(maxopen),
        "max_gross_exposure_pct":float(maxgross*100),
        "note":"Realized-close equity simulation; not full intratrade MTM MDD."
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--partials",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    tfiles=sorted(glob.glob(a.partials+"/**/trades.csv.gz",recursive=True))
    mfiles=sorted(glob.glob(a.partials+"/**/meta.json",recursive=True))
    if len(tfiles)!=8 or len(mfiles)!=8:
        raise RuntimeError(f"expected 8 trades/meta, got {len(tfiles)}/{len(mfiles)}")
    frames=[pd.read_csv(p,compression="gzip") for p in tfiles]
    z=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
    if z.empty:raise RuntimeError("no resolved trades")
    z=z.sort_values(["delay_min","entry_ts","symbol"]).reset_index(drop=True)
    z["split"]=z.apply(split_name,axis=1)
    z["year"]=pd.to_datetime(z.signal_ts,unit="ms",utc=True).dt.year
    z.to_csv(out/"s2_trades_all.csv.gz",index=False,compression="gzip")

    metas=[json.loads(Path(p).read_text()) for p in mfiles]
    raw_signal_n=sum(sum(int(s.get("signals",0)) for s in m.get("symbols",[])) for m in metas)
    exclusions=Counter()
    raw_min=[];raw_max=[]
    for m in metas:
        exclusions.update(m.get("stats",{}))
        for s in m.get("symbols",[]):
            if s.get("min_ts") is not None:raw_min.append(int(s["min_ts"]))
            if s.get("max_ts") is not None:raw_max.append(int(s["max_ts"]))

    summary={
      "experiment_id":"S2_CURRENT_RULE_5Y_V1",
      "raw_signal_n_per_delay_base":int(raw_signal_n),
      "raw_data_min_ts":min(raw_min) if raw_min else None,
      "raw_data_max_ts":max(raw_max) if raw_max else None,
      "resolved_rows_total_all_delays":int(len(z)),
      "exclusion_stats":dict(exclusions),
      "results":{},
      "portfolio":{},
    }
    rows=[]
    for d in DELAYS:
        gd=z[z.delay_min==d]
        summary["results"][f"delay{d}"]={}
        summary["portfolio"][f"delay{d}"]={}
        for split in ("TRAIN","VALIDATION_SEEN","ALL"):
            gs=gd if split=="ALL" else gd[gd.split==split]
            rr={"events":event_counts(gs)}
            pp={}
            for cost in COSTS:
                rr[f"{cost}bp"]=stats(gs,cost)
                pp[f"{cost}bp"]=portfolio(gs,cost)
                rows.append({"delay_min":d,"split":split,"cost_bp":cost,**stats(gs,cost)})
            summary["results"][f"delay{d}"][split]=rr
            summary["portfolio"][f"delay{d}"][split]=pp

    pd.DataFrame(rows).to_csv(out/"summary_table.csv",index=False)

    # Year stability at 20/40bp.
    yr=[]
    for (d,y),g in z[z.split!="EXCLUDED_SPLIT_BOUNDARY"].groupby(["delay_min","year"]):
        for c in COSTS:
            yr.append({"delay_min":int(d),"year":int(y),"cost_bp":c,**stats(g,c)})
    pd.DataFrame(yr).to_csv(out/"yearly.csv",index=False)

    # Recent 120/180d within frozen historical data.
    maxsig=int(z.signal_ts.max())
    rec=[]
    for days in (120,180,365):
        cut=maxsig-days*DAY
        for d in DELAYS:
            g=z[(z.delay_min==d)&(z.signal_ts>=cut)]
            for c in COSTS:
                rec.append({"days":days,"delay_min":d,"cost_bp":c,**stats(g,c)})
    pd.DataFrame(rec).to_csv(out/"recent.csv",index=False)

    # Symbol concentration at primary delay0 / 20bp.
    g=z[(z.delay_min==0)&(z.split!="EXCLUDED_SPLIT_BOUNDARY")].copy()
    g["net20"]=g.gross_pct-.20
    ss=g.groupby("symbol").agg(n=("symbol","size"),sum_net20=("net20","sum"),avg_net20=("net20","mean")).reset_index()
    ss=ss.sort_values("sum_net20",ascending=False)
    ss.to_csv(out/"symbol_stats.csv",index=False)
    pos=ss[ss.sum_net20>0]
    gp=float(pos.sum_net20.sum())
    summary["concentration_delay0_20bp"]={
      "symbols":int(len(ss)),
      "top1_share_positive_pct":float(pos.head(1).sum_net20.sum()/gp*100) if gp>0 else None,
      "top5_share_positive_pct":float(pos.head(5).sum_net20.sum()/gp*100) if gp>0 else None,
      "top10_share_positive_pct":float(pos.head(10).sum_net20.sum()/gp*100) if gp>0 else None,
    }

    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("S2_MERGED_JSON")
    print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__":main()
