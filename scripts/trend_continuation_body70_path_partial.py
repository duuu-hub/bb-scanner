#!/usr/bin/env python3
"""BODY70 path diagnostics at intraday resolution.

Frozen candidate:
- 8h >= +20%, cross-sectional top10%, fresh transition
- bullish signal-body ratio >= 0.70
- entry next 15m open
- canonical trade TP3/SL5/max6h remains unchanged

Diagnostics only:
- forward close return at 15/30/60/120/240/360m
- MFE/MAE over 15m bars to each horizon
- full-6h MFE/MAE and time-to-MFE/MAE
- touch rates for +1/+2/+2.5/+3 and -1/-2/-3/-5
- near-miss: MFE >=2.5 but <3.0
- early weakness: MAE <=-1/-2 within first 60m
- compare PRIOR vs RECENT120, plus month and year
No parameter retuning.
"""
from __future__ import annotations
import argparse, glob, os, json
from pathlib import Path
import numpy as np
import pandas as pd
import trend_continuation_first_touch as base

BAR=base.BAR_MS
DAY=base.DAY_MS
HORIZONS=[15,30,60,120,240,360]
UPS=[1.0,2.0,2.5,3.0]
DNS=[1.0,2.0,3.0,5.0]

def load_body70(events, raw_paths):
    rows=[]
    for sym,g in events.sort_values(["symbol","entry_ts"]).groupby("symbol",sort=True):
        p=raw_paths.get(sym)
        if not p: continue
        d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"],
                      dtype={"open_time":"int64","open":"float64","high":"float64","low":"float64","close":"float64"})
        d=d.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
        ts=d.open_time.to_numpy(np.int64); op=d.open.to_numpy(float); hi=d.high.to_numpy(float); lo=d.low.to_numpy(float); cl=d.close.to_numpy(float)
        for r in g.itertuples(index=False):
            si=int(np.searchsorted(ts,int(r.ts)))
            ei=int(np.searchsorted(ts,int(r.entry_ts)))
            if si>=len(ts) or ei>=len(ts) or int(ts[si])!=int(r.ts) or int(ts[ei])!=int(r.entry_ts): continue
            rng=hi[si]-lo[si]
            body=max(cl[si]-op[si],0.0)
            if not (cl[si]>op[si] and rng>0 and body/rng>=0.70): continue
            # require continuous 6h parent bars
            if ei+24>len(ts): continue
            exp=int(r.entry_ts)+np.arange(24,dtype=np.int64)*BAR
            if not np.array_equal(ts[ei:ei+24],exp): continue
            entry=float(op[ei])
            rec={"symbol":sym,"signal_ts":int(r.ts),"entry_ts":int(r.entry_ts),"entry":entry}
            h6=hi[ei:ei+24]; l6=lo[ei:ei+24]; c6=cl[ei:ei+24]
            reth=(h6/entry-1)*100.0
            retl=(l6/entry-1)*100.0
            rec["mfe_360"]=float(np.max(reth)); rec["mae_360"]=float(np.min(retl))
            rec["t_mfe_min"]=int((np.argmax(reth)+1)*15); rec["t_mae_min"]=int((np.argmin(retl)+1)*15)
            for m in HORIZONS:
                n=m//15
                rec[f"close_{m}"]=float((c6[n-1]/entry-1)*100.0)
                rec[f"mfe_{m}"]=float(np.max(reth[:n]))
                rec[f"mae_{m}"]=float(np.min(retl[:n]))
            for u in UPS:
                rec[f"touch_up_{str(u).replace('.','_')}"]=int(np.any(reth>=u))
            for dn in DNS:
                rec[f"touch_dn_{str(dn).replace('.','_')}"]=int(np.any(retl<=-dn))
            rec["near_miss_2_5_to_3"]=int(rec["mfe_360"]>=2.5 and rec["mfe_360"]<3.0)
            rec["early_mae1_60"]=int(rec["mae_60"]<=-1.0)
            rec["early_mae2_60"]=int(rec["mae_60"]<=-2.0)
            rows.append(rec)
    return pd.DataFrame(rows)

def summarize(g,label):
    o={"segment":label,"n":int(len(g))}
    if not len(g): return o
    for m in HORIZONS:
        for k in ("close","mfe","mae"):
            x=g[f"{k}_{m}"].astype(float)
            o[f"{k}_{m}_mean"]=float(x.mean())
            o[f"{k}_{m}_median"]=float(x.median())
    for u in UPS:
        col=f"touch_up_{str(u).replace('.','_')}"
        o[f"{col}_pct"]=float(g[col].mean()*100)
    for dn in DNS:
        col=f"touch_dn_{str(dn).replace('.','_')}"
        o[f"{col}_pct"]=float(g[col].mean()*100)
    o["near_miss_2_5_to_3_pct"]=float(g.near_miss_2_5_to_3.mean()*100)
    o["early_mae1_60_pct"]=float(g.early_mae1_60.mean()*100)
    o["early_mae2_60_pct"]=float(g.early_mae2_60.mean()*100)
    o["t_mfe_min_mean"]=float(g.t_mfe_min.mean())
    o["t_mfe_min_median"]=float(g.t_mfe_min.median())
    o["t_mae_min_mean"]=float(g.t_mae_min.mean())
    o["t_mae_min_median"]=float(g.t_mae_min.median())
    return o

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--events-in",required=True); ap.add_argument("--raw",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    e=pd.read_csv(a.events_in,compression="infer")
    e=e[e["lookback"].eq("8h") & np.isclose(e["tail"].astype(float),0.10) & np.isclose(e["threshold_pct"].astype(float),20.0)].copy()
    e["ts"]=e["ts"].astype("int64"); e["entry_ts"]=e["entry_ts"].astype("int64")
    raw_paths={base.sym_from_path(p):p for p in glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True)}
    e=e[e.symbol.isin(set(raw_paths))].copy()
    z=load_body70(e,raw_paths)
    z.to_csv(out/"path_diag.csv.gz",index=False,compression="gzip")
    print("PATH_ROWS",len(z),"symbols",z.symbol.nunique() if len(z) else 0,flush=True)

if __name__=="__main__": main()
