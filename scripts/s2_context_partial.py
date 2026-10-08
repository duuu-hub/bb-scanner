#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, os
from pathlib import Path
import numpy as np, pandas as pd

BAR=15*60*1000
H={"1h":4,"4h":16,"24h":96}

def sym(path):
    b=os.path.basename(path)
    return b[:-7].upper() if b.endswith(".csv.gz") else os.path.splitext(b)[0].upper()

def ret_at(ts_arr, close, signal_ts, bars):
    # Use last fully completed 15m close before signal boundary.
    target=signal_ts-BAR
    i=int(np.searchsorted(ts_arr,target))
    if i>=len(ts_arr) or int(ts_arr[i])!=target or i<bars:return None
    j=i-bars
    if int(ts_arr[i])-int(ts_arr[j])!=bars*BAR:return None
    if close[j]<=0:return None
    return (float(close[i])/float(close[j])-1.0)*100.0

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--raw",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    t=pd.read_csv(a.trades,compression="gzip")
    times=np.sort(t.loc[t.delay_min.eq(0),"signal_ts"].astype("int64").unique())
    idx={int(x):i for i,x in enumerate(times)};n=len(times)

    acc={h:{"n":np.zeros(n,int),"pos":np.zeros(n,int),"sum":np.zeros(n,float)} for h in H}
    btc={h:np.full(n,np.nan) for h in H};eth={h:np.full(n,np.nan) for h in H}

    files=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    for k,p in enumerate(files,1):
        s=sym(p)
        d=pd.read_csv(p,compression="gzip",usecols=["open_time","close"],
                      dtype={"open_time":"int64","close":"float64"}).sort_values("open_time").drop_duplicates("open_time")
        ts=d.open_time.to_numpy(np.int64);cl=d.close.to_numpy(float)
        for q in times:
            ii=idx[int(q)]
            vals={}
            for h,bars in H.items():
                r=ret_at(ts,cl,int(q),bars);vals[h]=r
                if s=="BTCUSDT":
                    if r is not None:btc[h][ii]=r
                elif s=="ETHUSDT":
                    if r is not None:eth[h][ii]=r
                else:
                    if r is not None and np.isfinite(r):
                        acc[h]["n"][ii]+=1;acc[h]["pos"][ii]+=int(r>0);acc[h]["sum"][ii]+=r
        if k%20==0 or k==len(files):print("CTX_PROGRESS",k,"/",len(files),flush=True)

    z=pd.DataFrame({"signal_ts":times})
    for h in H:
        z[f"alt_n_{h}"]=acc[h]["n"];z[f"alt_pos_{h}"]=acc[h]["pos"];z[f"alt_sum_{h}"]=acc[h]["sum"]
        z[f"btc_{h}"]=btc[h];z[f"eth_{h}"]=eth[h]
    z.to_csv(out/"context_partial.csv.gz",index=False,compression="gzip")
    print("CTX_PARTIAL_DONE",len(z),flush=True)
if __name__=="__main__":main()
