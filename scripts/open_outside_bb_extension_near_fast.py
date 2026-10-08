#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json, os
from pathlib import Path
import numpy as np, pandas as pd
import s2_current_rule_5y as bb
import open_outside_bb_first_touch as ft

BAR=bb.BAR
THRESHOLDS=(5,6)
EXTS=(1.0,2.0,3.0)
TRIGGERS=("RED_AFTER_EXTENSION","BREAK_PREV_LOW_AFTER_EXTENSION")
TARGETS=("near1","near2","near3")
SLS=(2.0,3.0,4.0,5.0)
HOLDS={"2h":8,"4h":16}
WAIT_BARS=4

def fast_outcome(raw,i,tp,sl_pct,hold_bars):
    ts=raw["ts"];op=raw["open"];hi=raw["high"];lo=raw["low"]
    e=float(op[i]); sl=e*(1+sl_pct/100); et=int(ts[i])
    if not (0<tp<e): return {"status":"BAD_TARGET"}
    if lo[i]<=tp or hi[i]>=sl:
        return {"status":"SL","gross_pct":-sl_pct,"exit_ts":et+BAR}
    for k in range(1,hold_bars):
        j=i+k
        if j>=len(ts) or int(ts[j])!=et+k*BAR:return {"status":"DATA_GAP"}
        th=lo[j]<=tp;sh=hi[j]>=sl
        if th and sh:return {"status":"SL","gross_pct":-sl_pct,"exit_ts":et+(k+1)*BAR}
        if sh:return {"status":"SL","gross_pct":-sl_pct,"exit_ts":et+(k+1)*BAR}
        if th:return {"status":"TP","gross_pct":(e-tp)/e*100,"exit_ts":et+(k+1)*BAR}
    j=i+hold_bars
    if j>=len(ts) or int(ts[j])!=et+hold_bars*BAR:return {"status":"DATA_GAP"}
    x=float(op[j])
    return {"status":"TIME","gross_pct":(e-x)/e*100,"exit_ts":et+hold_bars*BAR}

def trigger_ok(raw,j,kind):
    op=raw["open"];lo=raw["low"];cl=raw["close"]
    if kind=="RED_AFTER_EXTENSION": return float(cl[j])<float(op[j])
    if kind=="BREAK_PREV_LOW_AFTER_EXTENSION": return j>0 and float(cl[j])<float(lo[j-1])
    raise ValueError(kind)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--raw",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    files=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    rows=[];meta={"events":{}}
    for pi,p in enumerate(files,1):
        sym=bb.sym_from_path(p);raw=bb.load_raw(p)
        ts=raw["ts"];op=raw["open"];hi=raw["high"];lo=raw["low"]
        valid,ab,upp,count=ft.states(raw)
        prev=np.roll(count,1);prev[0]=-1
        cont=np.zeros(len(ts),bool);cont[1:]=(ts[1:]-ts[:-1])==BAR
        for n in THRESHOLDS:
            setups=np.where(valid&cont&(count>=n)&(prev<n))[0]
            evmap={(ext,tr,tg):[] for ext in EXTS for tr in TRIGGERS for tg in TARGETS}
            for i in setups:
                if i+WAIT_BARS+1>=len(ts):continue
                bands=sorted([float(upp[name][i]) for name in bb.TF if ab[name][i] and np.isfinite(upp[name][i])],reverse=True)
                if len(bands)<3:continue
                tmap={"near1":bands[0],"near2":bands[1],"near3":bands[2]}
                setup_open=float(op[i])
                for ext in EXTS:
                    ext_px=setup_open*(1+ext/100)
                    extended=False
                    trig={tr:None for tr in TRIGGERS}
                    for off in range(WAIT_BARS):
                        j=i+off
                        if int(ts[j])!=int(ts[i])+off*BAR:break
                        if float(hi[j])>=ext_px:extended=True
                        if extended:
                            for tr in TRIGGERS:
                                if trig[tr] is None and trigger_ok(raw,j,tr):trig[tr]=j
                    for tr,j in trig.items():
                        if j is None:continue
                        ei=j+1
                        if int(ts[ei])!=int(ts[j])+BAR:continue
                        for tg_name,tg in tmap.items():
                            if float(np.min(lo[i:j+1]))<=tg:continue
                            if not (0<tg<float(op[ei])):continue
                            evmap[(ext,tr,tg_name)].append((i,j,ei,tg))
            for (ext,tr,tg_name),events in evmap.items():
                meta["events"][f"{n}:{ext}:{tr}:{tg_name}"]=meta["events"].get(f"{n}:{ext}:{tr}:{tg_name}",0)+len(events)
                for sl in SLS:
                    for hn,hb in HOLDS.items():
                        busy=-1
                        for si,ci,ei,tg in events:
                            et=int(ts[ei])
                            if et<busy:continue
                            r=fast_outcome(raw,ei,tg,sl,hb)
                            if r.get("gross_pct") is None:continue
                            busy=int(r["exit_ts"])
                            rows.append({
                              "threshold":n,"extension_pct":ext,"trigger":tr,"target":tg_name,
                              "sl_pct":sl,"hold":hn,"symbol":sym,"setup_ts":int(ts[si]),
                              "confirm_ts":int(ts[ci]),"signal_ts":et,"exit_ts":int(r["exit_ts"]),
                              "status":r["status"],"gross_pct":float(r["gross_pct"]),
                              "target_dist_pct":(float(op[ei])-tg)/float(op[ei])*100,
                              "entry_lag_min":int((et-int(ts[si]))/60000)
                            })
        print("EXTNEAR_PROGRESS",pi,"/",len(files),sym,flush=True)
    pd.DataFrame(rows).to_csv(out/"trades.csv.gz",index=False,compression="gzip")
    (out/"meta.json").write_text(json.dumps(meta,indent=2))
    print("EXTNEAR_DONE",len(rows),flush=True)
if __name__=="__main__":main()
