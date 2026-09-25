import argparse,glob,gzip,json,os
import pandas as pd, numpy as np

def psar(df, af0=.02, step=.02, afmax=.2):
    h=df.high.to_numpy(float); l=df.low.to_numpy(float); n=len(df)
    sar=np.full(n,np.nan); bull=np.ones(n,dtype=bool)
    if n<3:return sar,bull
    sar[1]=l[0]; ep=h[1]; af=af0
    for i in range(2,n):
        s=sar[i-1]+af*(ep-sar[i-1])
        if bull[i-1]:
            s=min(s,l[i-1],l[i-2])
            if l[i] < s:
                bull[i]=False; s=ep; ep=l[i]; af=af0
            else:
                bull[i]=True
                if h[i]>ep: ep=h[i]; af=min(af+step,afmax)
        else:
            s=max(s,h[i-1],h[i-2])
            if h[i] > s:
                bull[i]=True; s=ep; ep=h[i]; af=af0
            else:
                bull[i]=False
                if l[i]<ep: ep=l[i]; af=min(af+step,afmax)
        sar[i]=s
    return sar,bull

def load(path):
    d=pd.read_csv(path,compression="gzip")
    t=pd.to_datetime(d.open_time.astype("int64"),unit="ms",utc=True)
    d.index=t
    return d[["open","high","low","close"]].astype(float).sort_index()

def test(d,tf,dist,horizon=12):
    x=d.resample(tf,label="left",closed="left").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
    x["sar"],x["bull"]=psar(x)
    out=[]
    # order created only after bar i is fully closed; eligible from i+1
    for i in range(3,len(x)-horizon-1):
        sar=x.sar.iat[i]; bull=bool(x.bull.iat[i])
        if not np.isfinite(sar): continue
        nxt=x.iloc[i+1]
        entry=sar*(1+dist if bull else 1-dist)
        filled=(nxt.low<=entry<=nxt.high)
        if not filled: continue
        fut=x.iloc[i+1:i+1+horizon]
        if bull:
            mfe=(fut.high.max()/entry-1); mae=(fut.low.min()/entry-1)
            risk=max(entry-sar,entry*1e-9)/entry
            r=mfe/risk
        else:
            mfe=(1-fut.low.min()/entry); mae=(1-fut.high.max()/entry)
            risk=max(sar-entry,entry*1e-9)/entry
            r=mfe/risk
        out.append((mfe,mae,r))
    if not out:return {"fills":0}
    a=np.array(out)
    return {"fills":len(out),"mfe_med_pct":round(np.median(a[:,0])*100,4),"mae_med_pct":round(np.median(a[:,1])*100,4),
      "r1_pct":round((a[:,2]>=1).mean()*100,2),"r1_5_pct":round((a[:,2]>=1.5).mean()*100,2),
      "r2_pct":round((a[:,2]>=2).mean()*100,2),"r3_pct":round((a[:,2]>=3).mean()*100,2)}

ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--out",default="psar_results.json");a=ap.parse_args()
files=glob.glob(a.data+"/**/*.csv.gz",recursive=True); assert files,"no data"
agg={}
for p in files:
    try:d=load(p)
    except Exception as e: continue
    sym=os.path.basename(p).replace(".csv.gz","")
    for tf in ["1h","4h"]:
      for dist in [.001,.0025,.005,.0075,.01]:
        k=f"{tf}|{dist:.4f}"
        r=test(d,tf,dist)
        if r.get("fills",0):
          agg.setdefault(k,[]).append(r)
summary={}
for k,rs in agg.items():
    n=sum(r["fills"] for r in rs)
    summary[k]={"fills":n}
    for m in ["mfe_med_pct","mae_med_pct","r1_pct","r1_5_pct","r2_pct","r3_pct"]:
        summary[k][m]=round(sum(r[m]*r["fills"] for r in rs)/n,3)
open(a.out,"w").write(json.dumps({"files":len(files),"summary":summary},indent=2))
print(json.dumps({"files":len(files),"summary":summary},indent=2))
