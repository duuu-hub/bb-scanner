#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, os
from pathlib import Path
import numpy as np, pandas as pd
import s2_current_rule_5y as base

BAR=base.BAR; MIN=base.MIN
VARIANTS=("S2_CURRENT","ALL7_FRESH","S2_TO_7","S1_CURRENT")
DELAYS=(0,1,2)

def masks(raw):
    ts=raw["ts"]; live=raw["open"]; close=raw["close"]; n=len(ts)
    states={}; valid=np.ones(n,bool); exact=np.zeros(n,np.int8)
    for name,(dur,offset) in base.TF.items():
        if name=="15M":
            ct=ts+BAR; cc=close
        else:
            ct,cc=base.complete_tf(ts,close,dur,offset)
        v,a,d=base.bb_state_vec(ts,live,ct,cc,dur,offset)
        states[name]=(v,a,d); valid &= v; exact += a.astype(np.int8)
    a15=states["15M"][1]; d15=states["15M"][2]
    s2=valid&(exact==6)&(~a15)&(d15>=-1.0)
    all7=valid&(exact==7)
    ret4=np.full(n,np.nan)
    if n>16:
        cont=(ts[16:]-ts[:-16])==16*BAR
        idx=np.where(cont)[0]+16
        ret4[idx]=(live[idx]/live[idx-16]-1.0)*100.0
    s1=all7&(ret4>=10.0)

    prev_cont=np.zeros(n,bool); prev_cont[1:]=(ts[1:]-ts[:-1])==BAR
    def fresh(x):
        p=np.zeros(n,bool); p[1:]=x[:-1]
        return x & prev_cont & ~p
    s2_prev=np.zeros(n,bool); s2_prev[1:]=s2[:-1]
    return {
      "S2_CURRENT":fresh(s2),
      "ALL7_FRESH":fresh(all7),
      "S2_TO_7":all7 & prev_cont & s2_prev,
      "S1_CURRENT":fresh(s1),
    }, d15, ret4

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--raw",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    files=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    rows=[]; counts={v:0 for v in VARIANTS}
    for k,p in enumerate(files,1):
        sym=base.sym_from_path(p); raw=base.load_raw(p)
        mm,d15,ret4=masks(raw)
        ts=raw["ts"]
        for v in VARIANTS:
            sig=np.where(mm[v])[0]; counts[v]+=len(sig)
            for delay in DELAYS:
                busy=-1
                for i in sig:
                    st=int(ts[i]); aet=st+delay*MIN
                    if aet<busy: continue
                    r=base.short_outcome(sym,raw,st,delay)
                    status=r.get("status")
                    if status in {"DATA_GAP","ENTRY_MISMATCH","EXIT_MISMATCH"} or r.get("gross_pct") is None:
                        continue
                    busy=int(r["exit_ts"])
                    rows.append({
                      "variant":v,"symbol":sym,"signal_ts":st,"entry_ts":int(r["entry_ts"]),
                      "exit_ts":int(r["exit_ts"]),"delay_min":delay,"status":status,
                      "gross_pct":float(r["gross_pct"]),"d15":float(d15[i]) if np.isfinite(d15[i]) else np.nan,
                      "ret4h_pct":float(ret4[i]) if np.isfinite(ret4[i]) else np.nan,
                    })
        print("VAR_PROGRESS",k,"/",len(files),sym,flush=True)
    pd.DataFrame(rows).to_csv(out/"variant_trades.csv.gz",index=False,compression="gzip")
    Path(out/"variant_meta.json").write_text(__import__("json").dumps({"raw_signals":counts},indent=2))
    print("VAR_DONE",counts,"rows",len(rows),flush=True)
if __name__=="__main__":main()
