#!/usr/bin/env python3
"""Exact executable replay of BODY70 diagnostic subgroups.

All variants keep frozen entry/exit:
- base signal 8h>=20%, top10%, fresh
- next 15m open
- TP3 / SL5 / max6h
- same-symbol overlap blocked AFTER each variant filter
- costs reported later by merge

Variants are diagnostic, not promoted:
BODY70, BODY80_90, BODY90_100, BODY80_PLUS,
BODY70_H12_17, BODY70_RANGE8, BODY80_90_H12_17.
"""
from __future__ import annotations
import argparse, glob, os
from pathlib import Path
import numpy as np
import pandas as pd
import trend_continuation_first_touch as base

base.TP_PCT=3.0
SL=5.0
VARIANTS=("BODY70","BODY80_90","BODY90_100","BODY80_PLUS","BODY70_H12_17","BODY70_RANGE8","BODY80_90_H12_17")

def pass_variant(name,body,hour,range_pct):
    if name=="BODY70": return body>=.70
    if name=="BODY80_90": return body>=.80 and body<.90
    if name=="BODY90_100": return body>=.90
    if name=="BODY80_PLUS": return body>=.80
    if name=="BODY70_H12_17": return body>=.70 and 12<=hour<=17
    if name=="BODY70_RANGE8": return body>=.70 and range_pct>=8.0
    if name=="BODY80_90_H12_17": return body>=.80 and body<.90 and 12<=hour<=17
    raise ValueError(name)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--events-in",required=True); ap.add_argument("--raw",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    e=pd.read_csv(a.events_in,compression="infer")
    e=e[e["lookback"].eq("8h") & np.isclose(e["tail"].astype(float),.10) & np.isclose(e["threshold_pct"].astype(float),20.0)].copy()
    e["ts"]=e.ts.astype("int64"); e["entry_ts"]=e.entry_ts.astype("int64")
    raw_paths={base.sym_from_path(p):p for p in glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True)}
    e=e[e.symbol.isin(set(raw_paths))].copy()

    rows=[]
    for sym,g in e.sort_values(["symbol","entry_ts"]).groupby("symbol",sort=True):
        p=raw_paths.get(sym)
        if not p: continue
        raw=base.load_raw_symbol(p)
        ts=raw["ts"]; op=raw["open"]; hi=raw["high"]; lo=raw["low"]; cl=raw["close"]
        # Build qualifying records first.
        recs=[]
        for r in g.itertuples(index=False):
            st=int(r.ts); et=int(r.entry_ts)
            si=int(np.searchsorted(ts,st))
            if si>=len(ts) or int(ts[si])!=st: continue
            rng=float(hi[si]-lo[si])
            body=max(float(cl[si]-op[si]),0.0)/rng if rng>0 else 0.0
            if not (float(cl[si])>float(op[si])): body=0.0
            range_pct=(rng/float(op[si])*100.0) if op[si]>0 else np.nan
            hour=int(pd.Timestamp(st,unit="ms",tz="UTC").hour)
            recs.append((st,et,float(r.entry),body,range_pct,hour))
        for v in VARIANTS:
            busy=-1
            for st,et,entry,body,range_pct,hour in recs:
                if not pass_variant(v,body,hour,range_pct): continue
                if et<busy: continue
                rr=base.event_outcomes(sym,raw,et,entry,SL)["6h"]
                status=rr.get("status")
                if status in ("DATA_GAP","ENTRY_MISMATCH","EXIT_MISMATCH") or "gross_pct" not in rr:
                    continue
                busy=int(rr["exit_ts"])
                rows.append({"variant":v,"symbol":sym,"signal_ts":st,"entry_ts":et,
                             "exit_ts":int(rr["exit_ts"]),"status":status,"gross_pct":float(rr["gross_pct"]),
                             "body_ratio":body,"range_pct":range_pct,"hour_utc":hour})
    pd.DataFrame(rows).to_csv(out/"trades.csv.gz",index=False,compression="gzip")
    print("EXACT_SUBGROUP_ROWS",len(rows),flush=True)

if __name__=="__main__": main()
