#!/usr/bin/env python3
"""Continuation CPR v4: signal-candle structure filters.

Frozen baseline:
- 8h return >= +20%, cross-sectional top 10%, fresh transition.
- immediate entry at next 15m open.
- TP +3%, SL -5%, max hold 6h.
- 20/40bp round-trip costs.
- Train <= 2024, Holdout >= 2025.
- Same-symbol overlap blocked after filtering.

All added features use only the completed signal candle and prior completed candles:
- CLV = (close-low)/(high-low)
- upper wick ratio = (high-max(open,close))/(high-low)
- bullish body ratio = max(close-open,0)/(high-low)
- range expansion = signal true range / median(previous 32 true ranges)
- CLOSE_BREAK4 = signal close > max(high of prior 4 completed 15m bars)

Pre-registered filters are deliberately small. No exit retuning and no Holdout-driven thresholds.
"""
from __future__ import annotations
import argparse, glob, json, os
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
import trend_continuation_first_touch as base

DAY_MS=base.DAY_MS
TP_PCT=3.0
SL_PCT=5.0
TL="6h"
COSTS=(20,40)
base.TP_PCT=TP_PCT

FILTER_NAMES=[
    "BASE","CL80",
    "UPPER10","UPPER20",
    "BULL","BODY50","BODY70",
    "RANGE1.5","RANGE2",
    "BREAK4",
    "CL80_UPPER20","CL80_BODY50","CL80_RANGE1.5","CL80_BREAK4",
]

def load_metrics(path):
    d=pd.read_csv(
        path,compression="gzip",
        usecols=["open_time","open","high","low","close"],
        dtype={"open_time":"int64","open":"float64","high":"float64","low":"float64","close":"float64"},
    ).sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)

    op=d.open.astype(float); hi=d.high.astype(float); lo=d.low.astype(float); cl=d.close.astype(float)
    rng=(hi-lo).astype(float)
    safe_rng=rng.where(rng>0,np.nan)

    clv=((cl-lo)/safe_rng).fillna(0.5)
    upper=((hi-np.maximum(op,cl))/safe_rng).fillna(0.0)
    bull=(cl>op)
    body=(np.maximum(cl-op,0.0)/safe_rng).fillna(0.0)

    prev_close=cl.shift(1)
    tr=pd.concat([(hi-lo).abs(),(hi-prev_close).abs(),(lo-prev_close).abs()],axis=1).max(axis=1)
    prev_tr_med=tr.shift(1).rolling(32,min_periods=32).median()
    range_ratio=(tr/prev_tr_med).replace([np.inf,-np.inf],np.nan)

    prev4_high=hi.shift(1).rolling(4,min_periods=4).max()
    break4=(cl>prev4_high)

    return d,{
        "clv":clv.to_numpy(dtype=float),
        "upper":upper.to_numpy(dtype=float),
        "bull":bull.to_numpy(dtype=bool),
        "body":body.to_numpy(dtype=float),
        "range_ratio":range_ratio.to_numpy(dtype=float),
        "break4":break4.to_numpy(dtype=bool),
    }

def event_metrics_and_outcomes(events,raw_paths):
    recs=[]; outcomes={}
    for sym,g in events.sort_values(["symbol","entry_ts"]).groupby("symbol",sort=True):
        p=raw_paths.get(sym)
        if not p: continue
        d,m=load_metrics(p)
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
                recs.append((sym,et,np.nan,np.nan,False,np.nan,np.nan,False,"DATA_GAP"))
                continue
            rr=float(m["range_ratio"][si]) if np.isfinite(m["range_ratio"][si]) else np.nan
            recs.append((
                sym,et,float(m["clv"][si]),float(m["upper"][si]),bool(m["bull"][si]),
                float(m["body"][si]),rr,bool(m["break4"][si]),"OK"
            ))
            rs=base.event_outcomes(sym,raw,et,float(row.entry),SL_PCT)
            outcomes[(sym,et)]=rs[TL]
    cols=["symbol","entry_ts","clv","upper_wick_ratio","bull","bull_body_ratio","range_atr_ratio","break4","metric_status"]
    return pd.DataFrame(recs,columns=cols),outcomes

def apply_filter(e,name):
    if name=="BASE": return e
    if name=="CL80": return e[e.clv>=0.80]
    if name=="UPPER10": return e[e.upper_wick_ratio<=0.10]
    if name=="UPPER20": return e[e.upper_wick_ratio<=0.20]
    if name=="BULL": return e[e.bull]
    if name=="BODY50": return e[e.bull_body_ratio>=0.50]
    if name=="BODY70": return e[e.bull_body_ratio>=0.70]
    if name=="RANGE1.5": return e[e.range_atr_ratio>=1.50]
    if name=="RANGE2": return e[e.range_atr_ratio>=2.00]
    if name=="BREAK4": return e[e.break4]
    if name=="CL80_UPPER20": return e[(e.clv>=0.80)&(e.upper_wick_ratio<=0.20)]
    if name=="CL80_BODY50": return e[(e.clv>=0.80)&(e.bull_body_ratio>=0.50)]
    if name=="CL80_RANGE1.5": return e[(e.clv>=0.80)&(e.range_atr_ratio>=1.50)]
    if name=="CL80_BREAK4": return e[(e.clv>=0.80)&(e.break4)]
    raise ValueError(name)

def summarize(e,outcomes,split,cost):
    a=e[e["split"].eq(split)].sort_values("entry_ts")
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
    e["ts"]=e["ts"].astype("int64"); e["entry_ts"]=e["entry_ts"].astype("int64")
    if "split" not in e: e["split"]=e["ts"].map(base.split_name)

    raw_paths={base.sym_from_path(p):p for p in glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True)}
    e=e[e["symbol"].isin(set(raw_paths))].copy()
    metrics,outcomes=event_metrics_and_outcomes(e,raw_paths)
    e=e.merge(metrics,on=["symbol","entry_ts"],how="left")
    print("STRUCT_EVENTS",len(e),"symbols",e.symbol.nunique(),flush=True)

    rows=[]
    for name in FILTER_NAMES:
        z=apply_filter(e,name)
        for split in ("TRAIN","HOLDOUT"):
            for cost in COSTS:
                s=summarize(z,outcomes,split,cost)
                s.update({"filter":name,"split":split,"cost_bp":cost,"tp_pct":TP_PCT,"sl_pct":SL_PCT,"time_limit":TL})
                rows.append(s)
        print("STRUCT_FILTER_DONE",name,"rows",len(z),flush=True)

    pd.DataFrame(rows).to_csv(out/"cells.csv",index=False)
    summary={
        "design":{
            "baseline":"8h>=20% top10 fresh transition","entry":"next15m open","tp":3.0,"sl":5.0,"hold":"6h",
            "features":"signal candle only + prior completed bars",
            "filters":FILTER_NAMES,"costs":COSTS,
        },
        "integrity_stats":dict(base.STATS),"rows":rows,
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("STRUCT_RESULT_JSON"); print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__": main()
