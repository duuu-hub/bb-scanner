#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json, os
from pathlib import Path
import numpy as np, pandas as pd
import s2_current_rule_5y as bb
import open_outside_bb_first_touch as ft

BAR=bb.BAR
THRESHOLDS=(5,6)
EXTS=(0.5,1.0,2.0,3.0,4.0)
TARGETS=("near1","near2","near3")
SLS=(2.0,3.0,4.0,5.0)
HOLDS={"2h":8,"4h":16}
FILL_WINDOW=4

def fast_outcome_from_fill(raw,fill_i,entry,tp,sl_pct,hold_bars):
    ts=raw["ts"];op=raw["open"];hi=raw["high"];lo=raw["low"]
    sl=entry*(1+sl_pct/100); et=int(ts[fill_i])
    if not (0<tp<entry): return None
    # fill bar: if target or stop also touched, chronology unknown => loss
    if lo[fill_i]<=tp or hi[fill_i]>=sl:
        return ("SL",-sl_pct,et+BAR)
    for k in range(1,hold_bars):
        j=fill_i+k
        if j>=len(ts) or int(ts[j])!=et+k*BAR:return None
        th=lo[j]<=tp; sh=hi[j]>=sl
        if th and sh:return ("SL",-sl_pct,et+(k+1)*BAR)
        if sh:return ("SL",-sl_pct,et+(k+1)*BAR)
        if th:return ("TP",(entry-tp)/entry*100,et+(k+1)*BAR)
    j=fill_i+hold_bars
    if j>=len(ts) or int(ts[j])!=et+hold_bars*BAR:return None
    x=float(op[j])
    return ("TIME",(entry-x)/entry*100,et+hold_bars*BAR)

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
            evmap={(ext,tg):[] for ext in EXTS for tg in TARGETS}
            for i in setups:
                if i+FILL_WINDOW+16>=len(ts):continue
                bands=sorted([float(upp[name][i]) for name in bb.TF if ab[name][i] and np.isfinite(upp[name][i])],reverse=True)
                if len(bands)<3:continue
                tmap={"near1":bands[0],"near2":bands[1],"near3":bands[2]}
                setup_open=float(op[i])
                for ext in EXTS:
                    entry=setup_open*(1+ext/100)
                    fill_i=None
                    for off in range(FILL_WINDOW):
                        j=i+off
                        if int(ts[j])!=int(ts[i])+off*BAR:break
                        if float(hi[j])>=entry:
                            fill_i=j;break
                    if fill_i is None:continue
                    for tg_name,tg in tmap.items():
                        if float(np.min(lo[i:fill_i+1]))<=tg:continue
                        if not (0<tg<entry):continue
                        evmap[(ext,tg_name)].append((i,fill_i,entry,tg))
            for (ext,tg_name),events in evmap.items():
                meta["events"][f"{n}:{ext}:{tg_name}"]=meta["events"].get(f"{n}:{ext}:{tg_name}",0)+len(events)
                for sl in SLS:
                    for hn,hb in HOLDS.items():
                        busy=-1
                        for si,fi,entry,tg in events:
                            ftm=int(ts[fi])
                            if ftm<busy:continue
                            r=fast_outcome_from_fill(raw,fi,entry,tg,sl,hb)
                            if r is None:continue
                            st,gross,xt=r;busy=xt
                            rows.append({
                              "threshold":n,"extension_pct":ext,"target":tg_name,"sl_pct":sl,"hold":hn,
                              "symbol":sym,"setup_ts":int(ts[si]),"fill_ts":ftm,"signal_ts":ftm,"exit_ts":xt,
                              "status":st,"gross_pct":gross,
                              "target_dist_pct":(entry-tg)/entry*100,
                              "fill_lag_min":int((ftm-int(ts[si]))/60000)
                            })
        print("LIMITFADE_PROGRESS",pi,"/",len(files),sym,flush=True)
    pd.DataFrame(rows).to_csv(out/"trades.csv.gz",index=False,compression="gzip")
    (out/"meta.json").write_text(json.dumps(meta,indent=2))
    print("LIMITFADE_DONE",len(rows),flush=True)
if __name__=="__main__":main()
