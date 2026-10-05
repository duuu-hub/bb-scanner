#!/usr/bin/env python3
"""BODY70 signal-context diagnostics on executed canonical trades."""
from __future__ import annotations
import argparse, glob, os
from pathlib import Path
import numpy as np
import pandas as pd
import trend_continuation_first_touch as base

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--trades",required=True); ap.add_argument("--events",required=True)
    ap.add_argument("--raw",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)

    t=pd.read_csv(a.trades,compression="infer")
    t=t[t.delay_min.eq(0)].copy()
    raw_paths={base.sym_from_path(p):p for p in glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True)}
    t=t[t.symbol.isin(set(raw_paths))].copy()
    keys=set(zip(t.symbol.astype(str),t.signal_ts.astype("int64")))

    e=pd.read_csv(a.events,compression="infer",
                  usecols=["ts","symbol","lookback","tail","threshold_pct","strength_ret_pct","pct"])
    e=e[e["lookback"].eq("8h") & np.isclose(e["tail"].astype(float),0.10) & np.isclose(e["threshold_pct"].astype(float),20.0)].copy()
    e["ts"]=e.ts.astype("int64")
    e=e[e.apply(lambda r:(str(r.symbol),int(r.ts)) in keys,axis=1)]
    attrs={(str(r.symbol),int(r.ts)):(float(r.strength_ret_pct),float(r.pct)) for r in e.itertuples(index=False)}

    rows=[]
    for sym,g in t.groupby("symbol"):
        p=raw_paths.get(sym)
        if not p: continue
        d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"],
                      dtype={"open_time":"int64","open":"float64","high":"float64","low":"float64","close":"float64"})
        d=d.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
        ts=d.open_time.to_numpy(np.int64); op=d.open.to_numpy(float); hi=d.high.to_numpy(float); lo=d.low.to_numpy(float); cl=d.close.to_numpy(float)
        for r in g.itertuples(index=False):
            st=int(r.signal_ts); i=int(np.searchsorted(ts,st))
            if i>=len(ts) or int(ts[i])!=st: continue
            attr=attrs.get((sym,st))
            if attr is None: continue
            rng=hi[i]-lo[i]
            body=max(cl[i]-op[i],0.0)
            body_ratio=body/rng if rng>0 else np.nan
            close_loc=(cl[i]-lo[i])/rng if rng>0 else np.nan
            range_pct=rng/op[i]*100 if op[i]>0 else np.nan
            bar_ret=(cl[i]/op[i]-1)*100 if op[i]>0 else np.nan
            prev15=(op[i]/cl[i-1]-1)*100 if i>=1 and cl[i-1]>0 else np.nan
            rows.append({
                "symbol":sym,"signal_ts":st,"entry_ts":int(r.entry_ts),
                "gross_pct":float(r.gross_pct),"status":str(r.status),
                "strength8h_pct":attr[0],"xsec_pct":attr[1],
                "body_ratio":body_ratio,"close_loc":close_loc,
                "signal_range_pct":range_pct,"signal_bar_ret_pct":bar_ret,
                "gap_vs_prev_close_pct":prev15,
            })
    z=pd.DataFrame(rows)
    z.to_csv(out/"context_partial.csv.gz",index=False,compression="gzip")
    print("CONTEXT_ROWS",len(z),"symbols",z.symbol.nunique() if len(z) else 0,flush=True)

if __name__=="__main__": main()
