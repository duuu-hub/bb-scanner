import argparse,glob,json
import pandas as pd,numpy as np

ENTRY_ATR=(0.0,.25,.5,.75,1.0,1.25,1.5)
SL_BUFFER_ATR=(0.0,.10,.20,.30,.50)
RS=(.5,.75,1.,1.25,1.5,2.,2.5,3.,4.)
M=16; HORIZON=12; MIN_RISK_EPS=1e-12

def psar_open_projection(h,l,af0=.02,step=.02,afmax=.2):
    n=len(h); out=np.full(n,np.nan); bull=np.ones(n,bool)
    if n<3:return out,bull
    sar=l[0]; trend=True; ep=h[1]; af=af0
    out[1]=sar; bull[1]=trend
    for i in range(2,n):
        # value available at bar i OPEN: derived only from bars <= i-1
        z=sar+af*(ep-sar)
        if trend:z=min(z,l[i-1],l[i-2])
        else:z=max(z,h[i-1],h[i-2])
        out[i]=z; bull[i]=trend
        # only after bar i closes may its H/L change next bar's state
        if trend:
            if l[i]<z: trend=False;sar=ep;ep=l[i];af=af0
            else:
                sar=z
                if h[i]>ep:ep=h[i];af=min(af+step,afmax)
        else:
            if h[i]>z: trend=True;sar=ep;ep=h[i];af=af0
            else:
                sar=z
                if l[i]<ep:ep=l[i];af=min(af+step,afmax)
    return out,bull

def load(p):
    d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]).sort_values("open_time")
    return tuple(d[x].to_numpy(np.int64 if x=="open_time" else float) for x in ["open_time","open","high","low","close"])

def resample(t,o,h,l,c,m=16):
    bucket=t//(900000*m); cut=np.r_[0,np.flatnonzero(bucket[1:]!=bucket[:-1])+1,len(t)]
    st=cut[:-1];en=cut[1:];good=(en-st)==m;st=st[good];en=en[good]
    return t[st],o[st],np.maximum.reduceat(h,st),np.minimum.reduceat(l,st),c[en-1]

def evaluate(t,o,h,l,c):
    rt,ro,rh,rl,rc=resample(t,o,h,l,c); sar,bull=psar_open_projection(rh,rl); n=len(rt)
    # ATR available at bar i open = ATR14 through bar i-1 only
    prev=np.r_[np.nan,rc[:-1]]
    tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prev),np.abs(rl-prev)))
    atr_closed=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()
    atr_open=np.r_[np.nan,atr_closed[:-1]]
    pos=np.searchsorted(t,rt); out={}
    for i in range(15,n-HORIZON):
        s=sar[i];a0=atr_open[i]
        if not np.isfinite(s) or not np.isfinite(a0) or a0<=0:continue
        b=bool(bull[i]);side="LONG" if b else "SHORT"; start=pos[i]; end=min(start+M*HORIZON,len(t))
        # spider is live immediately from this bar open
        for em in ENTRY_ATR:
            e=s+(em*a0 if b else -em*a0)
            hits=np.flatnonzero((l[start:min(start+M,len(t))]<=e)&(h[start:min(start+M,len(t))]>=e))
            if not hits.size:continue
            fs=start+int(hits[0]); ph=h[fs:end];pl=l[fs:end]
            for sb in SL_BUFFER_ATR:
                sl=s-(sb*a0 if b else -sb*a0); risk=abs(e-sl)
                if risk<=MIN_RISK_EPS*max(1.,abs(e),abs(sl)):continue
                for r in RS:
                    tp=e+r*risk if b else e-r*risk
                    th=(ph>=tp) if b else (pl<=tp); sh=(pl<=sl) if b else (ph>=sl)
                    ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 10**9;is_=si[0] if si.size else 10**9
                    k=f"E{em:g}|SB{sb:g}|R{r:g}|{side}";q=out.setdefault(k,{"fills":0,"win":0,"loss":0,"amb":0,"timeout":0})
                    q["fills"]+=1
                    if it==is_ and it<10**9:q["amb"]+=1
                    elif it<is_:q["win"]+=1
                    elif is_<it:q["loss"]+=1
                    else:q["timeout"]+=1
    return out

ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--out",default="psar_open_spider_grid.json");a=ap.parse_args()
files=glob.glob(a.data+"/**/*.csv.gz",recursive=True);assert files
agg={};errors=[]
for z,p in enumerate(files,1):
    try: rr=evaluate(*load(p))
    except Exception as e:errors.append([p,str(e)]);continue
    for k,v in rr.items():
        q=agg.setdefault(k,{kk:0 for kk in v})
        for kk,vv in v.items():q[kk]+=vv
    if z%20==0:print("progress",z,len(files),flush=True)
for k,q in agg.items():
    resolved=q["win"]+q["loss"]+q["amb"]
    q["win_pct_amb_loss"]=round(100*q["win"]/resolved,3) if resolved else None
    # expectancy in R with ambiguous conservatively loss; timeout excluded from realized R
    r=float(k.split("|R")[1].split("|")[0])
    q["expectancy_R_amb_loss"]=round((q["win"]*r-(q["loss"]+q["amb"]))/resolved,5) if resolved else None
res={"definition":{"tf":"4h","order_live":"same 4h bar open","psar":"projected at open using closed history only","atr":"ATR14 through prior closed 4h bar","entry_atr":ENTRY_ATR,"sl_buffer_atr":SL_BUFFER_ATR,"tp_R":RS,"horizon_bars":HORIZON,"ambiguous":"conservative loss until 1m resolution"},"files":len(files),"errors":errors,"summary":agg}
open(a.out,"w").write(json.dumps(res,indent=2));print(json.dumps(res["definition"],indent=2))
