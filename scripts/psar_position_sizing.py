import argparse,glob,json,os,heapq
import pandas as pd,numpy as np

M=16; HORIZON=12
TARGETS=[("E0_SHORT",0.0,0.10,3.0,False),("E5_LONG",5.0,0.50,1.0,True)]
SIZES=[0.001,0.0025,0.005,0.01,0.02]

def psar(h,l,af0=.02,step=.02,afmax=.2):
    n=len(h); out=np.full(n,np.nan); bull=np.ones(n,bool)
    if n<3:return out,bull
    sar=l[0];trend=True;ep=h[1];af=af0;out[1]=sar
    for i in range(2,n):
        z=sar+af*(ep-sar)
        z=min(z,l[i-1],l[i-2]) if trend else max(z,h[i-1],h[i-2])
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

def load(p):
    d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]).sort_values("open_time")
    return tuple(d[x].to_numpy(np.int64 if x=="open_time" else float) for x in ["open_time","open","high","low","close"])

def resample(t,o,h,l,c):
    b=t//(900000*M);cut=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(t)]
    st=cut[:-1];en=cut[1:];g=(en-st)==M;st=st[g];en=en[g]
    return t[st],o[st],np.maximum.reduceat(h,st),np.minimum.reduceat(l,st),c[en-1]

def one_file(p):
    t,o,h,l,c=load(p);rt,ro,rh,rl,rc=resample(t,o,h,l,c);sar,bull=psar(rh,rl)
    prev=np.r_[np.nan,rc[:-1]];tr=np.maximum(rh-rl,np.maximum(abs(rh-prev),abs(rl-prev)))
    ac=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy();ao=np.r_[np.nan,ac[:-1]]
    pos=np.searchsorted(t,rt);sym=os.path.basename(p).split(".")[0].replace("-15m","")
    rows=[]
    for i in range(15,len(rt)-HORIZON):
        s,a=sar[i],ao[i]
        if not np.isfinite(s) or not np.isfinite(a) or a<=0:continue
        for name,em,sb,R,want_long in TARGETS:
            if bool(bull[i])!=want_long:continue
            e=s+(em*a if want_long else -em*a)
            if (want_long and e>=ro[i]) or ((not want_long) and e<=ro[i]):continue
            start=pos[i];first_end=min(start+M,len(t))
            hits=np.flatnonzero((l[start:first_end]<=e)&(h[start:first_end]>=e))
            if not hits.size:continue
            fs=start+int(hits[0]);end=min(start+M*HORIZON,len(t))
            sl=s-(sb*a if want_long else -sb*a);risk=abs(e-sl)
            if risk<=1e-12*max(1.,abs(e),abs(sl)):continue
            tp=e+R*risk if want_long else e-R*risk
            ph=h[fs:end];pl=l[fs:end];th=(ph>=tp) if want_long else (pl<=tp);sh=(pl<=sl) if want_long else (ph>=sl)
            ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=int(ti[0]) if ti.size else 10**9;ix=int(si[0]) if si.size else 10**9
            if it==ix and it<10**9:outR=-1.;ex=fs+ix;status="amb_loss"
            elif it<ix:outR=R;ex=fs+it;status="win"
            elif ix<it:outR=-1.;ex=fs+ix;status="loss"
            else:outR=0.;ex=end-1;status="timeout"
            rows.append([name,sym,int(t[fs]),int(t[ex]),float(e),float(risk/e),float(outR),status])
    return rows

def concurrency(df):
    ev=[]
    for r in df.itertuples():ev.append((r.entry_ms,1));ev.append((r.exit_ms,-1))
    ev.sort(key=lambda x:(x[0],x[1]))
    cur=mx=0;vals=[]
    for _,d in ev:cur+=d;mx=max(mx,cur);vals.append(cur)
    # entry-time active counts, more useful percentiles
    exits=[];counts=[]
    for r in df.sort_values("entry_ms").itertuples():
        while exits and exits[0] <= r.entry_ms:heapq.heappop(exits)
        heapq.heappush(exits,r.exit_ms);counts.append(len(exits))
    a=np.array(counts)
    return {"max":int(mx),"entry_p50":float(np.percentile(a,50)),"entry_p95":float(np.percentile(a,95)),"entry_p99":float(np.percentile(a,99))}

def sim(df,size):
    # realized-equity simulation; allocation is fraction of current equity at entry, no gross cap
    eq=1.;peak=1.;mdd=0.;active=[];maxgross=0.;skipped=0
    events=[]
    for r in df.itertuples():
        events.append((r.entry_ms,0,r));events.append((r.exit_ms,1,r))
    # reserve notional at entry based on equity then; exit PnL = notional * R * stop-distance fraction
    notionals={};gross=0.
    for ts,typ,r in sorted(events,key=lambda x:(x[0],-x[1])):
        key=(r.Index)
        if typ==1:
            n=notionals.pop(key,0.);gross-=n
            eq += n*r.outcome_R*r.risk_frac
            peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 1.)
        else:
            n=eq*size;notionals[key]=n;gross+=n;maxgross=max(maxgross,gross/max(eq,1e-12))
    return {"final_equity":eq,"return_pct":(eq-1)*100,"realized_mdd_pct":mdd*100,"max_gross_x":maxgross}

ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--out",default="psar_position_sizing.json");a=ap.parse_args()
files=glob.glob(a.data+"/**/*.csv.gz",recursive=True);assert files
rows=[];errors=[]
for z,p in enumerate(files,1):
    try:rows.extend(one_file(p))
    except Exception as e:errors.append([p,str(e)])
    if z%20==0:print("progress",z,len(files),len(rows),flush=True)
df=pd.DataFrame(rows,columns=["strategy","symbol","entry_ms","exit_ms","entry","risk_frac","outcome_R","status"])
summary={}
for name in df.strategy.unique():
    x=df[df.strategy==name].copy()
    d={"trades":len(x),"symbols":int(x.symbol.nunique()),"concurrency":concurrency(x),"sizes":{}}
    for s in SIZES:d["sizes"][str(s)]=sim(x,s)
    summary[name]=d
# combined representative sleeves
if len(df):
    d={"trades":len(df),"symbols":int(df.symbol.nunique()),"concurrency":concurrency(df),"sizes":{}}
    for s in SIZES:d["sizes"][str(s)]=sim(df,s)
    summary["COMBINED"]=d
df.to_csv("psar_position_sizing_ledger.csv.gz",index=False,compression="gzip")
json.dump({"targets":TARGETS,"size_fractions":SIZES,"files":len(files),"errors":errors,"summary":summary},open(a.out,"w"),indent=2)
print(json.dumps(summary,indent=2))
