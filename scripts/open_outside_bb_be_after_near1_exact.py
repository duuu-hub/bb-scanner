#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json, os
from pathlib import Path
import numpy as np, pandas as pd
import s2_current_rule_5y as bb
import open_outside_bb_first_touch as ft

BAR=bb.BAR; MIN=bb.MIN
CONFIGS=(
    (6,"far2",8.0,16),
    (6,"far2",6.0,16),
    (6,"far2",5.0,16),
    (5,"far2",8.0,16),
    (6,"far3",8.0,16),
)

def minute_bar(symbol,parent_ts):
    day=pd.Timestamp(parent_ts,unit="ms",tz="UTC").strftime("%Y-%m-%d")
    rows=ft.load_1m_day(symbol,day)
    if rows is None:return None
    seg=[r for r in rows if parent_ts<=r[0]<parent_ts+BAR]
    if len(seg)!=15 or seg[0][0]!=parent_ts:return None
    if any(seg[k][0]-seg[k-1][0]!=MIN for k in range(1,15)):return None
    return seg

def exact_outcome(symbol,raw,i,near1,tp,sl_pct,hold_bars):
    ts=raw["ts"];op=raw["open"];hi=raw["high"];lo=raw["low"]
    entry=float(op[i]); init_sl=entry*(1+sl_pct/100); et=int(ts[i])
    if not (0<tp<near1<entry):return {"status":"BAD_TARGET"}
    state="INIT"

    for k in range(hold_bars):
        j=i+k; pt=et+k*BAR
        if j>=len(ts) or int(ts[j])!=pt:return {"status":"DATA_GAP"}

        need=False
        if state=="INIT":
            need=(float(lo[j])<=near1) or (float(hi[j])>=init_sl)
        else:
            need=(float(lo[j])<=tp) or (float(hi[j])>=entry)

        if not need:
            continue

        seg=minute_bar(symbol,pt)
        if seg is None:return {"status":"DATA_GAP"}
        for mi,(mt,o,h,l,c) in enumerate(seg):
            if state=="INIT":
                th=float(l)<=tp
                sh=float(h)>=init_sl
                nh=float(l)<=near1
                if k==0 and mi==0 and (th or sh):
                    return {"status":"SL","gross_pct":-sl_pct,"exit_ts":mt+MIN,"via":"entry_minute_conservative_loss"}
                if th and sh:
                    return {"status":"SL","gross_pct":-sl_pct,"exit_ts":mt+MIN,"via":"same_1m_tp_sl_loss"}
                if sh:
                    return {"status":"SL","gross_pct":-sl_pct,"exit_ts":mt+MIN,"via":"1m_initial_sl"}
                if th:
                    return {"status":"TP","gross_pct":(entry-tp)/entry*100,"exit_ts":mt+MIN,"via":"1m_target"}
                if nh:
                    # Activate BE from the next minute. This is conservative for intraminute order.
                    state="BE"
                    continue
            else:
                th=float(l)<=tp
                bh=float(h)>=entry
                if th and bh:
                    return {"status":"BE","gross_pct":0.0,"exit_ts":mt+MIN,"via":"same_1m_target_be_be"}
                if bh:
                    return {"status":"BE","gross_pct":0.0,"exit_ts":mt+MIN,"via":"1m_be"}
                if th:
                    return {"status":"TP","gross_pct":(entry-tp)/entry*100,"exit_ts":mt+MIN,"via":"1m_target"}

    deadline=et+hold_bars*BAR
    j=i+hold_bars
    if j>=len(ts) or int(ts[j])!=deadline:return {"status":"DATA_GAP"}
    exit_px=float(op[j])
    return {"status":"TIME","gross_pct":(entry-exit_px)/entry*100,"exit_ts":deadline,"via":"deadline_open"}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--raw",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    files=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    rows=[]; meta={"excluded":{}}
    for pi,p in enumerate(files,1):
        sym=bb.sym_from_path(p);raw=bb.load_raw(p)
        ts=raw["ts"];op=raw["open"]
        valid,ab,upp,count=ft.states(raw)
        prev=np.roll(count,1);prev[0]=-1
        cont=np.zeros(len(ts),bool);cont[1:]=(ts[1:]-ts[:-1])==BAR

        for n,tg_name,sl,hb in CONFIGS:
            sig=np.where(valid&cont&(count>=n)&(prev<n))[0]
            busy=-1
            for i in sig:
                if i+hb>=len(ts):continue
                et=int(ts[i])
                if et<busy:continue
                bands=sorted([float(upp[name][i]) for name in bb.TF if ab[name][i] and np.isfinite(upp[name][i])],reverse=True)
                if len(bands)<3:continue
                near1=bands[0];tg=bands[-2] if tg_name=="far2" else bands[-3]
                r=exact_outcome(sym,raw,i,near1,tg,sl,hb)
                st=r.get("status")
                if st in {"DATA_GAP","BAD_TARGET"} or r.get("gross_pct") is None:
                    key=f"{n}:{tg_name}:{sl}:{st}"
                    meta["excluded"][key]=meta["excluded"].get(key,0)+1
                    continue
                busy=int(r["exit_ts"])
                rows.append({
                  "threshold":n,"target":tg_name,"sl_pct":sl,"hold":"4h",
                  "symbol":sym,"signal_ts":et,"exit_ts":int(r["exit_ts"]),
                  "status":st,"gross_pct":float(r["gross_pct"]),"via":r.get("via"),
                  "target_dist_pct":(float(op[i])-tg)/float(op[i])*100,
                  "near1_dist_pct":(float(op[i])-near1)/float(op[i])*100
                })
        print("BE_EXACT_PROGRESS",pi,"/",len(files),sym,flush=True)

    pd.DataFrame(rows).to_csv(out/"trades.csv.gz",index=False,compression="gzip")
    (out/"meta.json").write_text(json.dumps(meta,indent=2))
    print("BE_EXACT_DONE",len(rows),json.dumps(meta),flush=True)

if __name__=="__main__":main()
