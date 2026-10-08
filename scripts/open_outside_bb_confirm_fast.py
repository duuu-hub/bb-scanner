#!/usr/bin/env python3
from __future__ import annotations

import argparse, glob, json, os
from pathlib import Path
import numpy as np, pandas as pd

import s2_current_rule_5y as bb
import open_outside_bb_first_touch as ft


BAR=bb.BAR
THRESHOLDS=(5,6)
CONFIRMS=("RED_CLOSE","BREAK_PREV_LOW","REENTER_NEAREST","REENTER_AND_BREAK_LOW")
WINDOWS=(1,2,4)
TARGETS=("far2","far3")
SLS=(3.0,4.0,5.0)
HOLDS={"2h":8,"4h":16}

def confirm_hit(raw,j,kind,nearest):
    op=raw["open"]; hi=raw["high"]; lo=raw["low"]; cl=raw["close"]
    if kind=="RED_CLOSE":
        return float(cl[j]) < float(op[j])
    if kind=="BREAK_PREV_LOW":
        return j>0 and float(cl[j]) < float(lo[j-1])
    if kind=="REENTER_NEAREST":
        return float(cl[j]) <= nearest
    if kind=="REENTER_AND_BREAK_LOW":
        return j>0 and float(cl[j]) <= nearest and float(cl[j]) < float(lo[j-1])
    raise ValueError(kind)

def fast_outcome(raw,i,tp,sl_pct,hold_bars):
    ts=raw["ts"];op=raw["open"];hi=raw["high"];lo=raw["low"]
    e=float(op[i]); sl=e*(1+sl_pct/100); et=int(ts[i])
    if not (0<tp<e): return {"status":"BAD_TARGET"}
    # ultra-conservative screen: any exit-level touch in entry 15m => LOSS
    if lo[i] <= tp or hi[i] >= sl:
        return {"status":"SL","gross_pct":-sl_pct,"exit_ts":et+BAR}
    for k in range(1,hold_bars):
        j=i+k
        if j>=len(ts) or int(ts[j])!=et+k*BAR:return {"status":"DATA_GAP"}
        th=lo[j]<=tp; sh=hi[j]>=sl
        if th and sh:return {"status":"SL","gross_pct":-sl_pct,"exit_ts":et+(k+1)*BAR}
        if sh:return {"status":"SL","gross_pct":-sl_pct,"exit_ts":et+(k+1)*BAR}
        if th:return {"status":"TP","gross_pct":(e-tp)/e*100,"exit_ts":et+(k+1)*BAR}
    j=i+hold_bars
    if j>=len(ts) or int(ts[j])!=et+hold_bars*BAR:return {"status":"DATA_GAP"}
    x=float(op[j])
    return {"status":"TIME","gross_pct":(e-x)/e*100,"exit_ts":et+hold_bars*BAR}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--raw",required=True)
    ap.add_argument("--out",required=True)
    a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)

    files=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    rows=[]
    meta={"raw_setups":{"5":0,"6":0},"confirm_events":{},"excluded":{}}

    for pidx,p in enumerate(files,1):
        sym=bb.sym_from_path(p)
        raw=bb.load_raw(p)
        ts=raw["ts"]; op=raw["open"]; lo=raw["low"]
        valid,ab,upp,count=ft.states(raw)
        prev=np.roll(count,1); prev[0]=-1
        cont=np.zeros(len(ts),bool); cont[1:]=(ts[1:]-ts[:-1])==BAR

        for n in THRESHOLDS:
            setup_ix=np.where(valid & cont & (count>=n) & (prev<n))[0]
            meta["raw_setups"][str(n)] += int(len(setup_ix))

            # Pre-build confirmation events independently of stop/hold.
            event_map={(kind,w,tg):[] for kind in CONFIRMS for w in WINDOWS for tg in TARGETS}
            for i in setup_ix:
                if i+5>=len(ts):
                    continue
                bands=sorted(
                    [float(upp[name][i]) for name in bb.TF if ab[name][i] and np.isfinite(upp[name][i])],
                    reverse=True
                )
                if len(bands)<3:
                    continue
                nearest=bands[0]
                tmap={"far2":bands[-2],"far3":bands[-3]}

                for kind in CONFIRMS:
                    # Find earliest confirmation for each max window.
                    first=None
                    for off in range(4):
                        j=i+off
                        if j>=len(ts) or int(ts[j]) != int(ts[i])+off*BAR:
                            break
                        if confirm_hit(raw,j,kind,nearest):
                            first=j
                            break
                    if first is None:
                        continue

                    bars_to_confirm=first-i+1
                    for w in WINDOWS:
                        if bars_to_confirm>w:
                            continue
                        entry_i=first+1
                        if entry_i>=len(ts) or int(ts[entry_i]) != int(ts[first])+BAR:
                            continue
                        for tg_name,tg in tmap.items():
                            # Target must not already have been consumed before entry.
                            if float(np.min(lo[i:first+1])) <= float(tg):
                                key=f"{n}:{kind}:w{w}:{tg_name}:TARGET_ALREADY_TOUCHED"
                                meta["excluded"][key]=meta["excluded"].get(key,0)+1
                                continue
                            if not (0<float(tg)<float(op[entry_i])):
                                key=f"{n}:{kind}:w{w}:{tg_name}:TARGET_NOT_BELOW_ENTRY"
                                meta["excluded"][key]=meta["excluded"].get(key,0)+1
                                continue
                            event_map[(kind,w,tg_name)].append(
                                (i,first,entry_i,float(tg),float(nearest))
                            )

            for key,events in event_map.items():
                kind,w,tg_name=key
                meta_key=f"{n}:{kind}:w{w}:{tg_name}"
                meta["confirm_events"][meta_key]=meta["confirm_events"].get(meta_key,0)+len(events)

                for sl in SLS:
                    for hold_name,hold_bars in HOLDS.items():
                        busy=-1
                        for setup_i,conf_i,entry_i,tg,nearest in events:
                            et=int(ts[entry_i])
                            if et<busy:
                                continue
                            r=fast_outcome(raw,entry_i,tg,sl,hold_bars)
                            st=r.get("status")
                            if st in {"DATA_GAP","EXIT_MISMATCH","BAD_TARGET"} or r.get("gross_pct") is None:
                                ex=f"{meta_key}:sl{sl:g}:{hold_name}:{st}"
                                meta["excluded"][ex]=meta["excluded"].get(ex,0)+1
                                continue
                            busy=int(r["exit_ts"])
                            rows.append({
                                "threshold":n,
                                "confirm":kind,
                                "window_bars":w,
                                "target":tg_name,
                                "sl_pct":sl,
                                "hold":hold_name,
                                "symbol":sym,
                                "setup_ts":int(ts[setup_i]),
                                "confirm_ts":int(ts[conf_i]),
                                "signal_ts":et,
                                "exit_ts":int(r["exit_ts"]),
                                "entry":float(op[entry_i]),
                                "nearest_setup_bb":nearest,
                                "target_px":tg,
                                "target_dist_pct":(float(op[entry_i])-tg)/float(op[entry_i])*100.0,
                                "confirm_delay_min":int((et-int(ts[setup_i]))/60_000),
                                "status":st,
                                "gross_pct":float(r["gross_pct"]),
                            })
        print("CONFIRM_PROGRESS",pidx,"/",len(files),sym,flush=True)

    pd.DataFrame(rows).to_csv(out/"trades.csv.gz",index=False,compression="gzip")
    (out/"meta.json").write_text(json.dumps(meta,indent=2))
    print("CONFIRM_DONE rows",len(rows),flush=True)

if __name__=="__main__":
    main()
