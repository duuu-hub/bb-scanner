#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json, os
from pathlib import Path
import numpy as np, pandas as pd
import s2_current_rule_5y as bb

BAR=bb.BAR
THRESHOLDS=(5,6)
TARGETS=("far2","far3")
SLS=(2.0,3.0,4.0,5.0)
HOLDS={"1h":4,"2h":8,"4h":16}

def states(raw):
    ts=raw["ts"]; live=raw["open"]; close=raw["close"]; n=len(ts)
    upp={}; ab={}; valid=np.ones(n,bool)
    for name,(dur,off) in bb.TF.items():
        if name=="15M": ct=ts+BAR; cc=close
        else: ct,cc=bb.complete_tf(ts,close,dur,off)
        v,a,d=bb.bb_state_vec(ts,live,ct,cc,dur,off)
        valid &= v; ab[name]=a
        u=np.full(n,np.nan); ok=v & np.isfinite(d) & (1+d/100>0)
        u[ok]=live[ok]/(1+d[ok]/100); upp[name]=u
    count=np.zeros(n,np.int8)
    for name in bb.TF: count += ab[name].astype(np.int8)
    return valid,ab,upp,count

def outcome(raw,i,tp,sl_pct,hold_bars):
    ts=raw["ts"];op=raw["open"];hi=raw["high"];lo=raw["low"]
    e=float(op[i]); sl=e*(1+sl_pct/100); et=int(ts[i])
    if not (0<tp<e): return None
    # Ultra-conservative: any exit-level touch in entry 15m bar => SL.
    if lo[i]<=tp or hi[i]>=sl:
        return ("SL",-sl_pct,et+BAR)
    for k in range(1,hold_bars):
        j=i+k
        if j>=len(ts) or int(ts[j])!=et+k*BAR:return None
        th=lo[j]<=tp;sh=hi[j]>=sl
        if th and sh:return ("SL",-sl_pct,et+(k+1)*BAR)
        if sh:return ("SL",-sl_pct,et+(k+1)*BAR)
        if th:return ("TP",(e-tp)/e*100,et+(k+1)*BAR)
    j=i+hold_bars
    if j>=len(ts) or int(ts[j])!=et+hold_bars*BAR:return None
    x=float(op[j]);return ("TIME",(e-x)/e*100,et+hold_bars*BAR)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--raw",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    files=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    rows=[]
    for pi,p in enumerate(files,1):
        sym=bb.sym_from_path(p);raw=bb.load_raw(p);ts=raw["ts"];op=raw["open"]
        valid,ab,upp,count=states(raw)
        prev=np.roll(count,1);prev[0]=-1
        cont=np.zeros(len(ts),bool);cont[1:]=(ts[1:]-ts[:-1])==BAR
        for n in THRESHOLDS:
            sig=np.where(valid&cont&(count>=n)&(prev<n))[0]
            ev=[]
            for i in sig:
                if i+16>=len(ts):continue
                bands=sorted([float(upp[name][i]) for name in bb.TF if ab[name][i] and np.isfinite(upp[name][i])],reverse=True)
                if len(bands)<3:continue
                ev.append((i,{"far2":bands[-2],"far3":bands[-3]}))
            for tg in TARGETS:
                for sl in SLS:
                    for hn,hb in HOLDS.items():
                        busy=-1
                        for i,tm in ev:
                            et=int(ts[i])
                            if et<busy:continue
                            r=outcome(raw,i,float(tm[tg]),sl,hb)
                            if r is None:continue
                            st,gross,xt=r;busy=xt
                            rows.append({"threshold":n,"target":tg,"sl_pct":sl,"hold":hn,"symbol":sym,
                                         "signal_ts":et,"exit_ts":xt,"status":st,"gross_pct":gross,
                                         "target_dist_pct":(float(op[i])-float(tm[tg]))/float(op[i])*100})
        print("FAST_PROGRESS",pi,"/",len(files),sym,flush=True)
    pd.DataFrame(rows).to_csv(out/"trades.csv.gz",index=False,compression="gzip")
    print("FAST_DONE",len(rows),flush=True)
if __name__=="__main__":main()
