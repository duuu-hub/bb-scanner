import argparse,glob,json,os
import pandas as pd, numpy as np

DISTS=np.array([.001,.0025,.005,.0075,.01],dtype=float)
TFS=["1h","4h"]

def psar(df,af0=.02,step=.02,afmax=.2):
    h=df.high.to_numpy(float); l=df.low.to_numpy(float); n=len(df)
    sar=np.full(n,np.nan); bull=np.ones(n,dtype=bool)
    if n<3:return sar,bull
    sar[1]=l[0]; ep=h[1]; af=af0
    for i in range(2,n):
        s=sar[i-1]+af*(ep-sar[i-1])
        if bull[i-1]:
            s=min(s,l[i-1],l[i-2])
            if l[i]<s: bull[i]=False; s=ep; ep=l[i]; af=af0
            else:
                bull[i]=True
                if h[i]>ep: ep=h[i]; af=min(af+step,afmax)
        else:
            s=max(s,h[i-1],h[i-2])
            if h[i]>s: bull[i]=True; s=ep; ep=h[i]; af=af0
            else:
                bull[i]=False
                if l[i]<ep: ep=l[i]; af=min(af+step,afmax)
        sar[i]=s
    return sar,bull

def load(p):
    d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"])
    d.index=pd.to_datetime(d.pop("open_time").astype("int64"),unit="ms",utc=True)
    return d.astype(float).sort_index()

def prep(d,tf):
    x=d.resample(tf,label="left",closed="left").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
    x["sar"],x["bull"]=psar(x); return x

def evaluate(x,dist,horizon=12):
    n=len(x); sar=x.sar.to_numpy(); bull=x.bull.to_numpy(bool)
    hi=x.high.to_numpy(); lo=x.low.to_numpy()
    rows=[]
    # exact same semantics as v1; only removes repeated resample/PSAR work
    for i in range(3,n-horizon-1):
        s=sar[i]
        if not np.isfinite(s): continue
        b=bull[i]; e=s*(1+dist if b else 1-dist)
        if not (lo[i+1]<=e<=hi[i+1]): continue
        fh=hi[i+1:i+1+horizon]; fl=lo[i+1:i+1+horizon]
        if b:
            mfe=fh.max()/e-1; mae=fl.min()/e-1; risk=max(e-s,e*1e-9)/e
        else:
            mfe=1-fl.min()/e; mae=1-fh.max()/e; risk=max(s-e,e*1e-9)/e
        rows.append((mfe,mae,mfe/risk))
    if not rows:return None
    a=np.asarray(rows)
    return {"fills":len(a),"mfe_med_pct":np.median(a[:,0])*100,"mae_med_pct":np.median(a[:,1])*100,
      "r1_pct":(a[:,2]>=1).mean()*100,"r1_5_pct":(a[:,2]>=1.5).mean()*100,
      "r2_pct":(a[:,2]>=2).mean()*100,"r3_pct":(a[:,2]>=3).mean()*100}

ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--out",default="psar_results.json");a=ap.parse_args()
files=glob.glob(a.data+"/**/*.csv.gz",recursive=True); assert files,"no data"
agg={}; errors=[]
for z,p in enumerate(files,1):
    try:d=load(p)
    except Exception as e: errors.append([p,str(e)]); continue
    for tf in TFS:
        x=prep(d,tf)
        for dist in DISTS:
            r=evaluate(x,float(dist))
            if r: agg.setdefault(f"{tf}|{dist:.4f}",[]).append(r)
    if z%20==0: print(f"progress {z}/{len(files)}",flush=True)
summary={}
for k,rs in agg.items():
    n=sum(r["fills"] for r in rs); summary[k]={"fills":n}
    for m in ["mfe_med_pct","mae_med_pct","r1_pct","r1_5_pct","r2_pct","r3_pct"]:
        summary[k][m]=round(sum(r[m]*r["fills"] for r in rs)/n,3)
res={"files":len(files),"load_errors":errors,"summary":summary}
open(a.out,"w").write(json.dumps(res,indent=2)); print(json.dumps(res,indent=2))
