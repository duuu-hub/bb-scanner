import argparse,glob,json,math
from pathlib import Path
import numpy as np,pandas as pd

BAR15=15*60_000
BAR4H=4*60*60_000
BAR1D=24*60*60_000
BURN=100

def psar_open_projection(h,l,af0=.02,step=.02,afmax=.2):
    n=len(h); out=np.full(n,np.nan); bull=np.ones(n,bool)
    if n<3:return out,bull
    sar=l[0]; trend=True; ep=h[1]; af=af0
    out[1]=sar; bull[1]=trend
    for i in range(2,n):
        z=sar+af*(ep-sar)
        if trend:z=min(z,l[i-1],l[i-2])
        else:z=max(z,h[i-1],h[i-2])
        out[i]=z; bull[i]=trend
        if trend:
            if l[i]<z: trend=False; sar=ep; ep=l[i]; af=af0
            else:
                sar=z
                if h[i]>ep: ep=h[i]; af=min(af+step,afmax)
        else:
            if h[i]>z: trend=True; sar=ep; ep=h[i]; af=af0
            else:
                sar=z
                if l[i]<ep: ep=l[i]; af=min(af+step,afmax)
    return out,bull

def load_raw(root):
    files=sorted(Path(root).glob("20??/??/*.csv.gz"))
    if not files: raise RuntimeError(f"no raw files under {root}")
    need=["symbol","timestamp_ms","open","high","low","close"]
    frames=[]
    for f in files:
        x=pd.read_csv(f,compression="gzip")
        cols={c.lower():c for c in x.columns}
        if not all(k in cols for k in need):continue
        y=x[[cols[k] for k in need]].copy();y.columns=need
        frames.append(y)
    d=pd.concat(frames,ignore_index=True)
    for c in need[1:]:d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d.dropna(subset=need).drop_duplicates(["symbol","timestamp_ms"],keep="last")
    d=d[(d.open>0)&(d.high>0)&(d.low>0)&(d.close>0)]
    return d.sort_values(["symbol","timestamp_ms"]).reset_index(drop=True)

def build_refs_1d(raw):
    rows=[]
    for sym,g in raw.groupby("symbol",sort=False):
        g=g.sort_values("timestamp_ms")
        t=g.timestamp_ms.to_numpy(np.int64);o=g.open.to_numpy(float);h=g.high.to_numpy(float);l=g.low.to_numpy(float);c=g.close.to_numpy(float)
        # split on gaps; never carry PSAR state across missing 15m data
        cuts=np.r_[0,np.flatnonzero(np.diff(t)!=BAR15)+1,len(t)]
        for aa,bb in zip(cuts[:-1],cuts[1:]):
            tt=t[aa:bb];oo=o[aa:bb];hh=h[aa:bb];ll=l[aa:bb];cc=c[aa:bb]
            if len(tt)<96*(BURN+2):continue
            bucket=tt//BAR1D
            cut=np.r_[0,np.flatnonzero(bucket[1:]!=bucket[:-1])+1,len(tt)]
            st=cut[:-1];en=cut[1:];good=(en-st)==16;st=st[good];en=en[good]
            if not len(st):continue
            ok=np.array([tt[a]%BAR1D==0 and np.all(np.diff(tt[a:b])==BAR15) for a,b in zip(st,en)],bool)
            st=st[ok];en=en[ok]
            if len(st)<=BURN:continue
            rt=tt[st]; rh=np.array([hh[a:b].max() for a,b in zip(st,en)]); rl=np.array([ll[a:b].min() for a,b in zip(st,en)])
            sar,bull=psar_open_projection(rh,rl)
            for i in range(BURN,len(rt)):
                if np.isfinite(sar[i]):
                    rows.append((sym,int(rt[i]),float(sar[i]),bool(bull[i])))
    return pd.DataFrame(rows,columns=["symbol","bar4h_ts","psar_ref","psar_bull"])

def attach(trades,refs,entry_ts_col,entry_px_col):
    z=trades.copy();z["symbol"]=z.symbol.astype(str)
    z["entry_ts_num"]=pd.to_numeric(z[entry_ts_col],errors="coerce").astype("Int64")
    z=z.dropna(subset=["entry_ts_num"]).copy()
    z["bar1d_ts"]=(z.entry_ts_num.astype("int64")//BAR1D)*BAR1D
    z=z.merge(refs,on=["symbol","bar1d_ts"],how="left",validate="many_to_one")
    z["psar_1d_age3_8"]=(~z.psar_bull.fillna(True))&z.psar_age.between(3,8)
    return z
def groups(z):
    return {"BASE":np.ones(len(z),bool),"PSAR_1D_BEAR_AGE3_8":z.psar_1d_age3_8.fillna(False).to_numpy()}

