#!/usr/bin/env python3
"""Deep BODY70 stop-path diagnostics on actually executed canonical trades.

Input executed trades comes from frozen BODY70 validation (delay_min=0), so same-symbol overlap
blocking is already applied. Candidate remains TP +3%, max 6h, 20bp context.

Diagnostics:
1) Canonical SL3/4/5/6/7/8 first-touch outcome with TP3.
2) For SL5 losses: whether price later reaches original TP3 before original 6h deadline.
3) Time from SL exit to later TP3.
4) Grace-stop variants: TP3 active immediately; SL5 inactive for first 15/30/60m, then active.
   If activation-bar open is already below SL5, exit at that open. No retuning.
5) PRIOR vs RECENT120, recent months.

Conservative collision rule inherited from trend_continuation_first_touch:
same 1m TP+SL => SL.
"""
from __future__ import annotations
import argparse, glob, json, os
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import trend_continuation_first_touch as base

BAR=base.BAR_MS
MIN=base.MIN_MS
DAY=base.DAY_MS
TP=3.0
SLS=(3.0,4.0,5.0,6.0,7.0,8.0)
GRACES=(15,30,60)
base.TP_PCT=TP

def scan_later_tp(symbol,raw,entry_ts,entry,stop_exit_ts,deadline):
    tp=entry*(1+TP/100.0)
    ts=raw["ts"]; hi=raw["high"]
    # If stop exit is inside a 15m bar due to 1m collision resolution, inspect remaining minutes.
    bar=(int(stop_exit_ts)//BAR)*BAR
    if int(stop_exit_ts)%BAR!=0:
        day=datetime.fromtimestamp(bar/1000,tz=timezone.utc).strftime("%Y-%m-%d")
        rows=base.load_1m_day(symbol,day)
        if rows is None: return {"later_tp":None,"later_tp_ts":None,"status":"DATA_GAP"}
        seg=[(t,h,l) for (t,h,l) in rows if int(stop_exit_ts)<=t<min(bar+BAR,deadline)]
        expected=max(0,int((min(bar+BAR,deadline)-int(stop_exit_ts))//MIN))
        if len(seg)!=expected:
            return {"later_tp":None,"later_tp_ts":None,"status":"DATA_GAP"}
        for t,h,l in seg:
            if h>=tp:
                return {"later_tp":1,"later_tp_ts":int(t+MIN),"status":"OK"}

    start=((int(stop_exit_ts)+BAR-1)//BAR)*BAR
    j=int(np.searchsorted(ts,start))
    expected=start
    while expected<deadline:
        if j>=len(ts) or int(ts[j])!=expected:
            return {"later_tp":None,"later_tp_ts":None,"status":"DATA_GAP"}
        if float(hi[j])>=tp:
            return {"later_tp":1,"later_tp_ts":int(expected+BAR),"status":"OK"}
        expected+=BAR; j+=1
    return {"later_tp":0,"later_tp_ts":None,"status":"OK"}

def grace_outcome(symbol,raw,entry_ts,entry,grace_min):
    ts=raw["ts"]; op=raw["open"]; hi=raw["high"]; lo=raw["low"]; cl=raw["close"]
    idx=int(np.searchsorted(ts,entry_ts))
    if idx>=len(ts) or int(ts[idx])!=entry_ts or abs(float(op[idx])/entry-1)>1e-9:
        return {"status":"ENTRY_MISMATCH"}
    tp=entry*(1+TP/100.0); sl=entry*(1-5.0/100.0)
    grace_bars=grace_min//15
    deadline=entry_ts+24*BAR

    for k in range(24):
        j=idx+k; bt=entry_ts+k*BAR
        if j>=len(ts) or int(ts[j])!=bt:
            return {"status":"DATA_GAP"}
        th=float(hi[j])>=tp
        active=(k>=grace_bars)

        if not active:
            if th:
                return {"status":"TP","gross_pct":TP,"exit_ts":bt+BAR}
            continue

        # At first activation bar, if open is already below stop, exit at open.
        if k==grace_bars and float(op[j])<=sl:
            gross=(float(op[j])/entry-1)*100.0
            return {"status":"SL_ACTIVATION_GAP","gross_pct":gross,"exit_ts":bt}

        sh=float(lo[j])<=sl
        if th and sh:
            r=base.resolve_collision_1m(symbol,bt,tp,sl)
            st=r.get("status")
            if st=="TP": return {"status":"TP","gross_pct":TP,"exit_ts":int(r["exit_ts"])}
            if st=="SL": return {"status":"SL","gross_pct":-5.0,"exit_ts":int(r["exit_ts"])}
            return {"status":st}
        if sh:
            return {"status":"SL","gross_pct":-5.0,"exit_ts":bt+BAR}
        if th:
            return {"status":"TP","gross_pct":TP,"exit_ts":bt+BAR}

    gross=(float(cl[idx+23])/entry-1)*100.0
    return {"status":"TIME","gross_pct":gross,"exit_ts":deadline}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--trades",required=True)
    ap.add_argument("--raw",required=True)
    ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)

    t=pd.read_csv(a.trades,compression="infer")
    t=t[t["delay_min"].eq(0)].copy()
    keep=set(zip(t["symbol"].astype(str),t["signal_ts"].astype("int64"),t["entry_ts"].astype("int64")))
    raw_paths={base.sym_from_path(p):p for p in glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True)}

    rows=[]
    for sym,g in t.sort_values(["symbol","entry_ts"]).groupby("symbol"):
        p=raw_paths.get(sym)
        if not p: continue
        raw=base.load_raw_symbol(p)
        for r in g.itertuples(index=False):
            et=int(r.entry_ts); entry_idx=int(np.searchsorted(raw["ts"],et))
            if entry_idx>=len(raw["ts"]) or int(raw["ts"][entry_idx])!=et: continue
            entry=float(raw["open"][entry_idx])
            rec={
                "symbol":sym,"signal_ts":int(r.signal_ts),"entry_ts":et,
                "entry":entry,"canonical_status":str(r.status),"canonical_gross_pct":float(r.gross_pct)
            }
            # SL-width first touch with same frozen TP3/6h
            for sl in SLS:
                rr=base.event_outcomes(sym,raw,et,entry,sl)["6h"]
                rec[f"sl{int(sl)}_status"]=rr.get("status")
                rec[f"sl{int(sl)}_gross"]=rr.get("gross_pct")
                rec[f"sl{int(sl)}_exit_ts"]=rr.get("exit_ts")
            # SL5 stop then later TP3 before original deadline
            r5=base.event_outcomes(sym,raw,et,entry,5.0)["6h"]
            if r5.get("status")=="SL":
                q=scan_later_tp(sym,raw,et,entry,int(r5["exit_ts"]),et+24*BAR)
                rec["sl5_later_tp"]=q["later_tp"]
                rec["sl5_later_tp_ts"]=q["later_tp_ts"]
                rec["sl5_later_tp_status"]=q["status"]
                rec["sl5_to_later_tp_min"]=((q["later_tp_ts"]-int(r5["exit_ts"]))/MIN if q["later_tp_ts"] is not None else np.nan)
            else:
                rec["sl5_later_tp"]=np.nan; rec["sl5_later_tp_ts"]=np.nan
                rec["sl5_later_tp_status"]="NA"; rec["sl5_to_later_tp_min"]=np.nan
            for gm in GRACES:
                gr=grace_outcome(sym,raw,et,entry,gm)
                rec[f"grace{gm}_status"]=gr.get("status")
                rec[f"grace{gm}_gross"]=gr.get("gross_pct")
                rec[f"grace{gm}_exit_ts"]=gr.get("exit_ts")
            rows.append(rec)
    z=pd.DataFrame(rows)
    z.to_csv(out/"stop_path.csv.gz",index=False,compression="gzip")
    print("STOP_PATH_ROWS",len(z),"symbols",z.symbol.nunique() if len(z) else 0,flush=True)

if __name__=="__main__": main()
