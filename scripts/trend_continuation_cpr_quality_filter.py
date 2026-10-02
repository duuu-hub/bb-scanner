#!/usr/bin/env python3
"""Continuation CPR v3: keep immediate entry; filter signal quality.

Frozen baseline:
- 8h return >= +20%, cross-sectional top 10%, fresh transition.
- entry = next 15m open.
- TP +3%, SL -5%, max hold 6h.
- costs = 20/40bp.
- Train <=2024, Holdout >=2025.

Only added information is known at signal close:
1) close location (CLV) = (close-low)/(high-low) of the completed signal 15m bar.
2) quote-volume surge = signal quote_volume / median(previous 32 completed 15m bars), excluding signal bar.

Pre-registered filters:
- BASE
- CL >= 0.50 / 0.70 / 0.80 / 0.90
- VOL >= 1.5x / 2.0x / 3.0x
- combos: CL70+VOL1.5, CL70+VOL2, CL80+VOL1.5, CL80+VOL2, CL90+VOL2

No exit retuning. Same-symbol overlaps blocked after filtering.
"""
from __future__ import annotations
import argparse, glob, json, os
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
import trend_continuation_first_touch as base

BAR_MS=base.BAR_MS
DAY_MS=base.DAY_MS
TP_PCT=3.0
SL_PCT=5.0
TL="6h"
COSTS=(20,40)
base.TP_PCT=TP_PCT

FILTERS=[
    ("BASE",None,None),
    ("CL50",0.50,None),("CL70",0.70,None),("CL80",0.80,None),("CL90",0.90,None),
    ("VOL1.5",None,1.5),("VOL2",None,2.0),("VOL3",None,3.0),
    ("CL70_VOL1.5",0.70,1.5),("CL70_VOL2",0.70,2.0),
    ("CL80_VOL1.5",0.80,1.5),("CL80_VOL2",0.80,2.0),
    ("CL90_VOL2",0.90,2.0),
]

def load_raw_metrics(path):
    d=pd.read_csv(path,compression="gzip",
        usecols=["open_time","open","high","low","close","quote_volume"],
        dtype={"open_time":"int64","open":"float64","high":"float64","low":"float64","close":"float64","quote_volume":"float64"})
    d=d.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    qv=d.quote_volume.astype(float)
    prev_med=qv.shift(1).rolling(32,min_periods=32).median()
    rng=d.high-d.low
    clv=np.where(rng>0,(d.close-d.low)/rng,0.5)
    vr=np.where(prev_med>0,qv/prev_med,np.nan)
    return d, np.asarray(clv,dtype=float), np.asarray(vr,dtype=float)

def event_metrics_and_outcomes(events, raw_paths):
    recs=[]
    outcomes={}
    for sym,g in events.sort_values(["symbol","entry_ts"]).groupby("symbol",sort=True):
        p=raw_paths.get(sym)
        if not p:
            continue
        d,clv,vr=load_raw_metrics(p)
        raw={
            "ts":d.open_time.to_numpy(dtype=np.int64),
            "open":d.open.to_numpy(dtype=float),
            "high":d.high.to_numpy(dtype=float),
            "low":d.low.to_numpy(dtype=float),
            "close":d.close.to_numpy(dtype=float),
        }
        ts=raw["ts"]
        for row in g.itertuples(index=False):
            sig_ts=int(row.ts); et=int(row.entry_ts)
            si=int(np.searchsorted(ts,sig_ts))
            if si>=len(ts) or int(ts[si])!=sig_ts:
                recs.append((sym,et,np.nan,np.nan,"DATA_GAP")); continue
            mcl=float(clv[si]); mvr=float(vr[si]) if np.isfinite(vr[si]) else np.nan
            rs=base.event_outcomes(sym,raw,et,float(row.entry),SL_PCT)
            outcomes[(sym,et)]=rs[TL]
            recs.append((sym,et,mcl,mvr,"OK"))
    m=pd.DataFrame(recs,columns=["symbol","entry_ts","clv","vol_ratio","metric_status"])
    return m,outcomes

def summarize(e,outcomes,split,cost):
    a=e[e.split.eq(split)].sort_values("entry_ts")
    busy={}; vals=[]; st=Counter(); excl=Counter(); overlap=0; ats=[]
    for row in a.itertuples(index=False):
        sym=row.symbol; et=int(row.entry_ts)
        if et<busy.get(sym,-1):
            overlap+=1; continue
        rec=outcomes.get((sym,et),{"status":"ENTRY_MISMATCH"})
        rs=rec.get("status")
        if rs in ("DATA_GAP","ENTRY_MISMATCH","EXIT_MISMATCH") or "gross_pct" not in rec:
            excl[rs]+=1; continue
        busy[sym]=int(rec["exit_ts"])
        net=float(rec["gross_pct"])-cost/100.0
        vals.append(net); st[rs]+=1; ats.append(et)
    n=len(vals); pos=sum(x for x in vals if x>0); neg=-sum(x for x in vals if x<0)
    span=max(1.0,(max(ats)-min(ats))/DAY_MS+1.0) if ats else None
    return {
        "n":n,"raw_signals":int(len(a)),"overlap_skips":int(overlap),"excluded":int(sum(excl.values())),
        "excluded_detail":json.dumps(dict(excl),sort_keys=True),"tp_n":int(st["TP"]),"sl_n":int(st["SL"]),"time_n":int(st["TIME"]),
        "win_n":int(sum(x>0 for x in vals)),"sum_net_pct":float(sum(vals)),
        "gross_profit_net_pct":float(pos),"gross_loss_abs_net_pct":float(neg),
        "win_rate_net_pct":(sum(x>0 for x in vals)/n*100 if n else None),
        "avg_net_pct":(sum(vals)/n if n else None),"pf_net":(pos/neg if neg>0 else None),
        "trades_per_day":(n/span if n and span else None),
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--events-in",required=True); ap.add_argument("--raw",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)

    e=pd.read_csv(a.events_in,compression="infer")
    e=e[e["lookback"].eq("8h") & np.isclose(e["tail"].astype(float),0.10) & np.isclose(e["threshold_pct"].astype(float),20.0)].copy()
    e["ts"]=e.ts.astype("int64"); e["entry_ts"]=e.entry_ts.astype("int64")
    if "split" not in e: e["split"]=e.ts.map(base.split_name)

    raw_paths={base.sym_from_path(p):p for p in glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True)}
    e=e[e.symbol.isin(set(raw_paths))].copy()
    metrics,outcomes=event_metrics_and_outcomes(e,raw_paths)
    e=e.merge(metrics,on=["symbol","entry_ts"],how="left")
    print("FILTER_EVENTS",len(e),"symbols",e.symbol.nunique(),flush=True)

    rows=[]
    for name,clmin,vmin in FILTERS:
        z=e.copy()
        if clmin is not None: z=z[z.clv>=clmin]
        if vmin is not None: z=z[z.vol_ratio>=vmin]
        for split in ("TRAIN","HOLDOUT"):
            for cost in COSTS:
                s=summarize(z,outcomes,split,cost)
                s.update({"filter":name,"clv_min":clmin,"vol_ratio_min":vmin,"split":split,"cost_bp":cost,
                          "tp_pct":TP_PCT,"sl_pct":SL_PCT,"time_limit":TL})
                rows.append(s)
        print("FILTER_DONE",name,"rows",len(z),flush=True)

    cells=pd.DataFrame(rows); cells.to_csv(out/"cells.csv",index=False)
    summary={"design":{"baseline":"8h>=20% top10 fresh transition","entry":"next15m open","tp":3.0,"sl":5.0,"hold":"6h",
                       "clv":"(signal close-low)/(high-low)","vol_ratio":"signal quote_volume / median(previous 32 bars), signal excluded",
                       "filters":[x[0] for x in FILTERS],"costs":COSTS},
             "integrity_stats":dict(base.STATS),"rows":rows}
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("FILTER_RESULT_JSON"); print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__": main()
