#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json, os
from pathlib import Path
import numpy as np, pandas as pd
import s2_current_rule_5y as bb

BAR=bb.BAR
HORIZONS={"1h":4,"2h":8,"4h":16}
RANKS=7

def states(raw):
    ts=raw["ts"]; live=raw["open"]; close=raw["close"]; n=len(ts)
    upp={}; ab={}; valid=np.ones(n,bool)
    for name,(dur,off) in bb.TF.items():
        if name=="15M": ct=ts+BAR; cc=close
        else: ct,cc=bb.complete_tf(ts,close,dur,off)
        v,a,d=bb.bb_state_vec(ts,live,ct,cc,dur,off)
        valid &= v; ab[name]=a
        u=np.full(n,np.nan)
        ok=v & np.isfinite(d) & (1+d/100>0)
        u[ok]=live[ok]/(1+d[ok]/100)
        upp[name]=u
    count=np.zeros(n,np.int8)
    for name in bb.TF: count+=ab[name].astype(np.int8)
    return valid,ab,upp,count

def first_hit(lo,start,end,target):
    for j in range(start,end):
        if lo[j] <= target: return j-start
    return None

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--raw",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    rows=[]
    files=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    for k,p in enumerate(files,1):
        sym=bb.sym_from_path(p); raw=bb.load_raw(p)
        ts=raw["ts"];op=raw["open"];lo=raw["low"];hi=raw["high"]
        valid,ab,upp,count=states(raw)
        prev=np.roll(count,1);prev[0]=-1
        cont=np.zeros(len(ts),bool);cont[1:]=(ts[1:]-ts[:-1])==BAR
        ix=np.where(valid&(count>=2))[0]
        for i in ix:
            if i+16>len(ts): continue
            bands=sorted([float(upp[n][i]) for n in bb.TF if ab[n][i] and np.isfinite(upp[n][i])], reverse=True)
            if len(bands)<2: continue
            rec={"symbol":sym,"signal_ts":int(ts[i]),"entry":float(op[i]),"outside_count":int(count[i]),
                 "prev_outside_count":int(prev[i]) if cont[i] else -1,"prev_contig":int(cont[i])}
            m=len(bands)
            for r in range(1,RANKS+1):
                # nearest rank: 1 is highest band (smallest pullback)
                if r<=m:
                    tg=bands[r-1]
                    rec[f"near{r}_dist_pct"]=(op[i]-tg)/op[i]*100
                    for h,bars in HORIZONS.items():
                        hit=first_hit(lo,i,i+bars,tg)
                        rec[f"near{r}_hit_{h}"]=int(hit is not None)
                else:
                    rec[f"near{r}_dist_pct"]=np.nan
                    for h in HORIZONS: rec[f"near{r}_hit_{h}"]=np.nan
            for fr in range(1,RANKS+1):
                # far1 = lowest/farthest breached band
                if fr<=m:
                    tg=bands[-fr]
                    rec[f"far{fr}_dist_pct"]=(op[i]-tg)/op[i]*100
                    for h,bars in HORIZONS.items():
                        hit=first_hit(lo,i,i+bars,tg)
                        rec[f"far{fr}_hit_{h}"]=int(hit is not None)
                else:
                    rec[f"far{fr}_dist_pct"]=np.nan
                    for h in HORIZONS: rec[f"far{fr}_hit_{h}"]=np.nan
            rec["mae_up_4h_pct"]=(float(np.max(hi[i:i+16]))/op[i]-1)*100
            rows.append(rec)
        print("RANKBB_PROGRESS",k,"/",len(files),sym,flush=True)
    pd.DataFrame(rows).to_csv(out/"rank_events.csv.gz",index=False,compression="gzip")
    print("RANKBB_DONE",len(rows),flush=True)

if __name__=="__main__": main()
