#!/usr/bin/env python3
"""Build shard-local market breadth features at frozen BODY70 trade signal timestamps."""
from __future__ import annotations
import argparse, glob, os
from pathlib import Path
import numpy as np
import pandas as pd

BAR_MS=15*60*1000
LB4=16
LB24=96
LB7D=96*7

def sym(path):
    b=os.path.basename(path)
    return b[:-7].upper() if b.endswith(".csv.gz") else os.path.splitext(b)[0].upper()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--trades",required=True)
    ap.add_argument("--raw",required=True)
    ap.add_argument("--out",required=True)
    a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)

    t=pd.read_csv(a.trades,compression="infer",usecols=["signal_ts","delay_min"])
    sig=np.sort(t.loc[t.delay_min.eq(0),"signal_ts"].astype("int64").unique())
    idxmap={int(x):i for i,x in enumerate(sig)}
    n=len(sig)

    n4=np.zeros(n,dtype=np.int64); pos4=np.zeros(n,dtype=np.int64); sum4=np.zeros(n,float); abs4=np.zeros(n,float)
    n24=np.zeros(n,dtype=np.int64); pos24=np.zeros(n,dtype=np.int64); sum24=np.zeros(n,float); abs24=np.zeros(n,float)
    btc4=np.full(n,np.nan); btc24=np.full(n,np.nan); btc7d=np.full(n,np.nan)

    files=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    for k,p in enumerate(files,1):
        s=sym(p)
        d=pd.read_csv(p,compression="gzip",usecols=["open_time","close"],
                      dtype={"open_time":"int64","close":"float64"}).sort_values("open_time").drop_duplicates("open_time")
        ts=d.open_time.to_numpy(np.int64); cl=d.close.to_numpy(float)
        ii=np.searchsorted(ts,sig)
        exact=(ii<len(ts))
        exact_idx=np.where(exact)[0]
        exact[exact_idx]=ts[ii[exact_idx]]==sig[exact_idx]

        valid4=exact & (ii>=LB4)
        jj=np.where(valid4)[0]
        if len(jj):
            cont=(ts[ii[jj]]-ts[ii[jj]-LB4])==LB4*BAR_MS
            jj=jj[cont]
            r=cl[ii[jj]]/cl[ii[jj]-LB4]-1.0
            n4[jj]+=1; pos4[jj]+=(r>0); sum4[jj]+=r; abs4[jj]+=np.abs(r)

        valid24=exact & (ii>=LB24)
        jj=np.where(valid24)[0]
        if len(jj):
            cont=(ts[ii[jj]]-ts[ii[jj]-LB24])==LB24*BAR_MS
            jj=jj[cont]
            r=cl[ii[jj]]/cl[ii[jj]-LB24]-1.0
            n24[jj]+=1; pos24[jj]+=(r>0); sum24[jj]+=r; abs24[jj]+=np.abs(r)

        if s=="BTCUSDT":
            for lb,dest in ((LB4,btc4),(LB24,btc24),(LB7D,btc7d)):
                v=exact & (ii>=lb)
                jj=np.where(v)[0]
                if len(jj):
                    cont=(ts[ii[jj]]-ts[ii[jj]-lb])==lb*BAR_MS
                    jj=jj[cont]
                    dest[jj]=cl[ii[jj]]/cl[ii[jj]-lb]-1.0

        if k%25==0 or k==len(files):
            print("BREADTH_PROGRESS",k,"/",len(files),flush=True)

    z=pd.DataFrame({
        "signal_ts":sig,
        "n4":n4,"pos4":pos4,"sum4":sum4,"abs4":abs4,
        "n24":n24,"pos24":pos24,"sum24":sum24,"abs24":abs24,
        "btc4":btc4,"btc24":btc24,"btc7d":btc7d,
    })
    z.to_csv(out/"breadth_partial.csv.gz",index=False,compression="gzip")
    print("BREADTH_PARTIAL_DONE",len(z),"files",len(files),"btc_rows",int(np.isfinite(btc24).sum()),flush=True)

if __name__=="__main__": main()
