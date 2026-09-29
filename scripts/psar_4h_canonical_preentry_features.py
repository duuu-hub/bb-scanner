import argparse,glob,json,os,re,time
import numpy as np,pandas as pd

PSAR_BURNIN_BARS=100
BTC_BLACKLIST={"BTCUSDT"}
FEATURES=("gap_pct","atr_pct","trend_age","ret24_pct","ret72_pct","bounce24_pct","bounce72_pct","dd24_pct","dd72_pct")

def _symbol(p):
    b=os.path.basename(p)
    if not b.endswith(".csv.gz"): raise RuntimeError(f"unexpected filename {b}")
    s=b[:-7].upper()
    if not re.fullmatch(r"[A-Z0-9]+USDT",s): raise RuntimeError(f"unsafe symbol {b}")
    return s

def load(p):
    d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]).sort_values("open_time")
    t=d.open_time.to_numpy(np.int64);o=d.open.to_numpy(float);h=d.high.to_numpy(float);l=d.low.to_numpy(float);c=d.close.to_numpy(float)
    if len(t)==0:return t,o,h,l,c
    if np.any(t%900000!=0):raise RuntimeError("misaligned 15m")
    if len(t)>1 and np.any(np.diff(t)<=0):raise RuntimeError("non-monotonic 15m")
    return t,o,h,l,c

def contiguous_segments(t):
    if len(t)==0:return []
    cut=np.r_[0,np.flatnonzero(np.diff(t)!=900000)+1,len(t)]
    return [(int(a),int(b)) for a,b in zip(cut[:-1],cut[1:])]

def resample(t,o,h,l,c,m=16):
    bucket=t//(900000*m);cut=np.r_[0,np.flatnonzero(bucket[1:]!=bucket[:-1])+1,len(t)]
    st=cut[:-1];en=cut[1:];good=(en-st)==m;st=st[good];en=en[good]
    if len(st):
        span=900000*m
        ok=np.array([t[a]%span==0 and np.all(np.diff(t[a:b])==900000) for a,b in zip(st,en)],dtype=bool)
        st=st[ok];en=en[ok]
    rh=np.array([np.max(h[a:b]) for a,b in zip(st,en)],float)
    rl=np.array([np.min(l[a:b]) for a,b in zip(st,en)],float)
    return t[st],o[st],rh,rl,c[en-1]

def psar_open_projection(h,l,af0=.02,step=.02,afmax=.2):
    n=len(h);out=np.full(n,np.nan);bull=np.ones(n,bool)
    if n<3:return out,bull
    sar=l[0];trend=True;ep=h[1];af=af0
    out[1]=sar;bull[1]=trend
    for i in range(2,n):
        z=sar+af*(ep-sar)
        if trend:z=min(z,l[i-1],l[i-2])
        else:z=max(z,h[i-1],h[i-2])
        out[i]=z;bull[i]=trend
        if trend:
            if l[i]<z:trend=False;sar=ep;ep=l[i];af=af0
            else:
                sar=z
                if h[i]>ep:ep=h[i];af=min(af+step,afmax)
        else:
            if h[i]>z:trend=True;sar=ep;ep=h[i];af=af0
            else:
                sar=z
                if l[i]<ep:ep=l[i];af=min(af+step,afmax)
    return out,bull

def trend_age(bull,i):
    z=0;j=i
    while j>=0 and not bool(bull[j]):z+=1;j-=1
    return z

def extract(t,o,h,l,c,symbol):
    rt,ro,rh,rl,rc=resample(t,o,h,l,c,16)
    sar,bull=psar_open_projection(rh,rl);n=len(rt)
    prev=np.r_[np.nan,rc[:-1]]
    tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prev),np.abs(rl-prev)))
    atr_closed=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()
    atr_open=np.r_[np.nan,atr_closed[:-1]]
    rows=[]
    for i in range(max(PSAR_BURNIN_BARS,18),n):
        if bool(bull[i]):continue
        s=float(sar[i]);px=float(ro[i]);a0=float(atr_open[i])
        if not np.isfinite(s) or not np.isfinite(px) or not np.isfinite(a0) or px<=0 or s<=0 or a0<=0:continue
        lo24=float(np.min(rl[i-6:i]));lo72=float(np.min(rl[i-18:i]))
        hi24=float(np.max(rh[i-6:i]));hi72=float(np.max(rh[i-18:i]))
        rows.append({
          "symbol":symbol,"signal_ts":int(rt[i]),
          "gap_pct":(s-px)/s*100.0,"atr_pct":a0/px*100.0,"trend_age":float(trend_age(bull,i)),
          "ret24_pct":(px/ro[i-6]-1.0)*100.0,"ret72_pct":(px/ro[i-18]-1.0)*100.0,
          "bounce24_pct":(px/lo24-1.0)*100.0,"bounce72_pct":(px/lo72-1.0)*100.0,
          "dd24_pct":(px/hi24-1.0)*100.0,"dd72_pct":(px/hi72-1.0)*100.0,
        })
    return rows

ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--out",default="features.csv.gz");ap.add_argument("--meta",default="meta.json");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);a=ap.parse_args()
all_files=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert all_files
files=[p for j,p in enumerate(all_files) if j%a.shards==a.shard]
rows=[];started=time.time()
for z,p in enumerate(files,1):
    sym=_symbol(p)
    if sym in BTC_BLACKLIST:continue
    t,o,h,l,c=load(p)
    for aa,bb in contiguous_segments(t):
        if bb-aa<16*(PSAR_BURNIN_BARS+1):continue
        rows.extend(extract(t[aa:bb],o[aa:bb],h[aa:bb],l[aa:bb],c[aa:bb],sym))
    print(f"PROGRESS feature shard={a.shard}/{a.shards} file={z}/{len(files)} sym={sym} rows={len(rows)} elapsed={(time.time()-started)/60:.1f}m",flush=True)
pd.DataFrame(rows).to_csv(a.out,index=False,compression="gzip")
meta={"definition":{"source_data_run":"36095439671","tf":"4h","side":"SHORT","psar":"open projection from closed history only","atr":"SMA14 prior closed bars","features":FEATURES,"btc_blacklist":["BTCUSDT"],"lookahead":"none; all features at signal 4H OPEN"},"files":len(files),"rows":len(rows)}
json.dump(meta,open(a.meta,"w"),indent=2)
print("PREENTRY_FEATURE_PASS",len(files),len(rows),flush=True)
