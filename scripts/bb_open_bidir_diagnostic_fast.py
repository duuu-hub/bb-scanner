#!/usr/bin/env python3
"""15m-OHLC raw band revisit diagnostics, NOT executable first-touch backtest."""
import argparse, glob, json
from pathlib import Path
import numpy as np
import pandas as pd
from bb_open_bidir_reversal_v1 import build_states
import s2_current_rule_5y as base

BAR=base.BAR
def main():
    p=argparse.ArgumentParser();p.add_argument("--raw",required=True);p.add_argument("--out",required=True);args=p.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(args.raw+"/**/*USDT.csv.gz",recursive=True))
    if not fs:raise RuntimeError("missing raw")
    rows=[]
    for seq,path in enumerate(fs,1):
        sym=base.sym_from_path(path);r=base.load_raw(path);ts=r["ts"];op=r["open"];hi=r["high"];lo=r["low"]
        valid,vprev,cont,upper,lower,upp,dn=build_states(r)
        for side in ("SHORT","LONG"):
            arr=upper if side=="SHORT" else lower
            bounds=upp if side=="SHORT" else dn
            prev=np.roll(arr,1);prev[0]=-1
            for threshold in (5,6):
                ix=np.where(valid &vprev&cont &(arr>=threshold)&(prev<threshold))[0]
                for i in ix:
                    if i+16>=len(ts):continue
                    levels=[float(bounds[k][i]) for k in base.TF
                            if (op[i]>upp[k][i] if side=="SHORT" else op[i]<dn[k][i])]
                    levels.sort(reverse=(side=="SHORT"))
                    if len(levels)<3:continue
                    for tg in ("far2","far3"):
                        px=levels[-2] if tg=="far2" else levels[-3]
                        raw1=(np.any(lo[i:i+4]<=px) if side=="SHORT" else np.any(hi[i:i+4]>=px))
                        raw4=(np.any(lo[i:i+16]<=px) if side=="SHORT" else np.any(hi[i:i+16]>=px))
                        mae=((np.max(hi[i:i+16])/op[i]-1)*100 if side=="SHORT" else (1-np.min(lo[i:i+16])/op[i])*100)
                        rows.append((sym,side,threshold,tg,int(ts[i]),abs(op[i]-px)/op[i]*100,int(raw1),int(raw4),mae))
        print("DIAGNOSTIC_PROGRESS",seq,len(fs),sym,flush=True)
    cols=["symbol","side","threshold","target","signal_ts","distance_pct","raw_hit_1h","raw_hit_4h","adverse_4h_pct"]
    pd.DataFrame(rows,columns=cols).to_csv(out/"diagnostics.csv.gz",index=False,compression="gzip")
    print("DIAGNOSTIC_DONE",len(rows))
if __name__=="__main__":main()
