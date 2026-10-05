#!/usr/bin/env python3
"""Execution robustness for promising BODY70 subgroups.

Variants:
- RANGE8: BODY>=70% and signal range>=8% of open
- B80_90: body in [80%,90%)
- B80_90_H12_17: body in [80%,90%) and signal UTC hour 12..17

Frozen exits TP3/SL5/6h. Entry delays 0/+1m/+2m. Same-symbol overlap recalculated per
variant+delay. Costs applied in merge.
"""
from __future__ import annotations
import argparse, glob, os
from pathlib import Path
import numpy as np
import pandas as pd
import trend_continuation_first_touch as base
import trend_continuation_body70_validate as val

base.TP_PCT=3.0
SL=5.0
VARIANTS=("RANGE8","B80_90","B80_90_H12_17")
DELAYS=(0,1,2)

def passed(v,body,rng,hour):
    if v=="RANGE8": return body>=.70 and rng>=8.0
    if v=="B80_90": return body>=.80 and body<.90
    if v=="B80_90_H12_17": return body>=.80 and body<.90 and 12<=hour<=17
    raise ValueError(v)

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
        sigs=[]
        for r in g.itertuples(index=False):
            st=int(r.ts); et=int(r.entry_ts)
            si=int(np.searchsorted(ts,st))
            if si>=len(ts) or int(ts[si])!=st: continue
            rg=float(hi[si]-lo[si])
            body=max(float(cl[si]-op[si]),0.0)/rg if rg>0 else 0.0
            if float(cl[si])<=float(op[si]): body=0.0
            rp=rg/float(op[si])*100.0 if op[si]>0 else np.nan
            hr=int(pd.Timestamp(st,unit="ms",tz="UTC").hour)
            sigs.append((st,et,float(r.entry),body,rp,hr))
        for v in VARIANTS:
            vs=[x for x in sigs if passed(v,x[3],x[4],x[5])]
            for delay in DELAYS:
                busy=-1
                for st,et,entry,body,rp,hr in vs:
                    aet=et+delay*base.MIN_MS
                    if aet<busy: continue
                    if delay==0:
                        rr=base.event_outcomes(sym,raw,et,entry,SL)["6h"].copy()
                        rr["entry_ts"]=et
                    else:
                        rr=val.delayed_outcome(sym,raw,et,delay)
                    status=rr.get("status")
                    if status in ("DATA_GAP","ENTRY_MISMATCH","EXIT_MISMATCH") or "gross_pct" not in rr: continue
                    busy=int(rr["exit_ts"])
                    rows.append({"variant":v,"delay_min":delay,"symbol":sym,"signal_ts":st,
                                 "entry_ts":int(rr.get("entry_ts",aet)),"exit_ts":int(rr["exit_ts"]),
                                 "status":status,"gross_pct":float(rr["gross_pct"])})
    pd.DataFrame(rows).to_csv(out/"trades.csv.gz",index=False,compression="gzip")
    print("ROBUST_TRADES",len(rows),flush=True)

if __name__=="__main__": main()
