#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json, os
from pathlib import Path
import numpy as np, pandas as pd
import s2_current_rule_5y as bb
import open_outside_bb_first_touch as ft

BAR=bb.BAR
THRESHOLDS=(5,6)
TARGETS=("far2","far3")
SLS=(4.0,5.0,6.0,8.0)
HOLDS={"2h":8,"4h":16}

def outcome(raw,i,near1,tp,sl_pct,hold_bars):
    ts=raw["ts"];op=raw["open"];hi=raw["high"];lo=raw["low"]
    entry=float(op[i]); init_sl=entry*(1+sl_pct/100); et=int(ts[i])
    if not (0<tp<near1<entry): return None
    be=False
    # entry bar conservative
    th=lo[i]<=tp; nh=lo[i]<=near1; sh=hi[i]>=init_sl
    if sh and (nh or th): return ("SL",-sl_pct,et+BAR)
    if sh:return ("SL",-sl_pct,et+BAR)
    if th:return ("TP",(entry-tp)/entry*100,et+BAR)
    if nh:be=True

    for k in range(1,hold_bars):
        j=i+k
        if j>=len(ts) or int(ts[j])!=et+k*BAR:return None
        if not be:
            th=lo[j]<=tp; nh=lo[j]<=near1; sh=hi[j]>=init_sl
            if sh and (nh or th):return ("SL",-sl_pct,et+(k+1)*BAR)
            if sh:return ("SL",-sl_pct,et+(k+1)*BAR)
            if th:return ("TP",(entry-tp)/entry*100,et+(k+1)*BAR)
            if nh:
                be=True
                # activation next bar; no BE check in activation bar
                continue
        else:
            th=lo[j]<=tp; bh=hi[j]>=entry
            if th and bh:return ("BE",0.0,et+(k+1)*BAR)
            if bh:return ("BE",0.0,et+(k+1)*BAR)
            if th:return ("TP",(entry-tp)/entry*100,et+(k+1)*BAR)

    j=i+hold_bars
    if j>=len(ts) or int(ts[j])!=et+hold_bars*BAR:return None
    x=float(op[j])
    return ("TIME",(entry-x)/entry*100,et+hold_bars*BAR)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--raw",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    files=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    rows=[]
    for pi,p in enumerate(files,1):
        sym=bb.sym_from_path(p);raw=bb.load_raw(p)
        ts=raw["ts"];op=raw["open"]
        valid,ab,upp,count=ft.states(raw)
        prev=np.roll(count,1);prev[0]=-1
        cont=np.zeros(len(ts),bool);cont[1:]=(ts[1:]-ts[:-1])==BAR
        for n in THRESHOLDS:
            sig=np.where(valid&cont&(count>=n)&(prev<n))[0]
            events=[]
            for i in sig:
                if i+16>=len(ts):continue
                bands=sorted([float(upp[name][i]) for name in bb.TF if ab[name][i] and np.isfinite(upp[name][i])],reverse=True)
                if len(bands)<3:continue
                events.append((i,bands[0],{"far2":bands[-2],"far3":bands[-3]}))
            for tg_name in TARGETS:
                for sl in SLS:
                    for hn,hb in HOLDS.items():
                        busy=-1
                        for i,near1,tmap in events:
                            et=int(ts[i])
                            if et<busy:continue
                            r=outcome(raw,i,near1,float(tmap[tg_name]),sl,hb)
                            if r is None:continue
                            st,gross,xt=r;busy=xt
                            rows.append({
                              "threshold":n,"target":tg_name,"sl_pct":sl,"hold":hn,
                              "symbol":sym,"signal_ts":et,"exit_ts":xt,"status":st,
                              "gross_pct":gross,"target_dist_pct":(float(op[i])-float(tmap[tg_name]))/float(op[i])*100,
                              "near1_dist_pct":(float(op[i])-near1)/float(op[i])*100
                            })
        print("BE_PROGRESS",pi,"/",len(files),sym,flush=True)
    pd.DataFrame(rows).to_csv(out/"trades.csv.gz",index=False,compression="gzip")
    print("BE_DONE",len(rows),flush=True)
if __name__=="__main__":main()
