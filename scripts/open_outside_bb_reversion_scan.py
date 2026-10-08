#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json, os
from pathlib import Path
import numpy as np, pandas as pd
import s2_current_rule_5y as bb

BAR=bb.BAR
HORIZONS={"15m":1,"30m":2,"1h":4,"2h":8,"4h":16}
TARGETS=("nearest","median","deepest","m15")

def compute_states(raw):
    ts=raw["ts"]; live=raw["open"]; close=raw["close"]; n=len(ts)
    uppers={}; above={}; valid=np.ones(n,bool)
    for name,(dur,offset) in bb.TF.items():
        if name=="15M":
            ct=ts+BAR; cc=close
        else:
            ct,cc=bb.complete_tf(ts,close,dur,offset)
        v,a,d=bb.bb_state_vec(ts,live,ct,cc,dur,offset)
        valid &= v; above[name]=a
        u=np.full(n,np.nan)
        ok=v & np.isfinite(d) & (1.0+d/100.0>0)
        u[ok]=live[ok]/(1.0+d[ok]/100.0)
        uppers[name]=u
    count=np.zeros(n,np.int8)
    for name in bb.TF: count += above[name].astype(np.int8)
    return valid,above,uppers,count

def first_hit_bar(lo, start, end, target):
    for j in range(start,end):
        if lo[j] <= target:return j-start
    return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--raw",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    rows=[]; meta=[]
    for k,p in enumerate(fs,1):
        sym=bb.sym_from_path(p); raw=bb.load_raw(p)
        ts=raw["ts"];op=raw["open"];hi=raw["high"];lo=raw["low"]
        valid,above,uppers,count=compute_states(raw)
        prev_count=np.roll(count,1);prev_count[0]=0
        prev_cont=np.zeros(len(ts),bool);prev_cont[1:]=(ts[1:]-ts[:-1])==BAR
        ix=np.where(valid & (count>=2))[0]
        usable=0
        for i in ix:
            if i+16>len(ts):continue
            breached=[name for name in bb.TF if above[name][i] and np.isfinite(uppers[name][i])]
            if len(breached)<2:continue
            bands=np.array([uppers[name][i] for name in breached],float)
            targets={
                "nearest":float(np.max(bands)),
                "median":float(np.median(bands)),
                "deepest":float(np.min(bands)),
                "m15":float(uppers["15M"][i]) if above["15M"][i] and np.isfinite(uppers["15M"][i]) else np.nan,
            }
            rec={
                "symbol":sym,"signal_ts":int(ts[i]),"entry":float(op[i]),
                "outside_count":int(count[i]),"prev_outside_count":int(prev_count[i]) if prev_cont[i] else -1,
                "prev_contig":int(prev_cont[i]),"m15_outside":int(above["15M"][i]),
            }
            for tn,tg in targets.items():
                if not np.isfinite(tg) or tg<=0 or tg>=op[i]:
                    rec[f"{tn}_dist_pct"]=np.nan
                    for h in HORIZONS:
                        rec[f"{tn}_hit_{h}"]=np.nan
                        rec[f"{tn}_firstbar_{h}"]=np.nan
                    continue
                rec[f"{tn}_dist_pct"]=(op[i]-tg)/op[i]*100.0
                for h,bars in HORIZONS.items():
                    j=first_hit_bar(lo,i,i+bars,tg)
                    rec[f"{tn}_hit_{h}"]=int(j is not None)
                    rec[f"{tn}_firstbar_{h}"]=float(j) if j is not None else np.nan
            for h,bars in HORIZONS.items():
                rec[f"mae_up_{h}_pct"]=(float(np.max(hi[i:i+bars]))/op[i]-1.0)*100.0
                rec[f"close_{h}_ret_pct"]=(float(raw["close"][i+bars-1])/op[i]-1.0)*100.0
            rows.append(rec);usable+=1
        meta.append({"symbol":sym,"rows":usable,"raw_min":int(ts[0]) if len(ts) else None,"raw_max":int(ts[-1]) if len(ts) else None})
        print("OPENBB_PROGRESS",k,"/",len(fs),sym,"rows",usable,flush=True)
    pd.DataFrame(rows).to_csv(out/"events.csv.gz",index=False,compression="gzip")
    (out/"meta.json").write_text(json.dumps(meta,indent=2))
    print("OPENBB_DONE",len(rows),flush=True)
if __name__=="__main__":main()
