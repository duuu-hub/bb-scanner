import argparse,glob,json,os,re
import numpy as np,pandas as pd
BURN=100; H=(1,2,3,4,5,6,7,8,12)
def sym(p):
 b=os.path.basename(p); s=b[:-7].upper()
 if not b.endswith(".csv.gz") or not re.fullmatch(r"[A-Z0-9]+USDT",s): raise RuntimeError("filename")
 return s
def load(p):
 d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]).sort_values("open_time")
 x=[d[c].to_numpy(np.int64 if c=="open_time" else float) for c in ["open_time","open","high","low","close"]];t,o,h,l,c=x
 if not len(t) or np.any(np.diff(t)<=0) or np.any(t%900000):raise RuntimeError("time")
 if not all(np.all(np.isfinite(v)) for v in (o,h,l,c)) or np.any(h<np.maximum.reduce([o,l,c])) or np.any(l>np.minimum.reduce([o,h,c])):raise RuntimeError("ohlc")
 return x
def seg(t):
 q=np.r_[0,np.flatnonzero(np.diff(t)!=900000)+1,len(t)];return zip(q[:-1],q[1:])
def rs(t,o,h,l,c,m):
 b=t//(900000*m);q=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(t)];a,z=q[:-1],q[1:];g=(z-a)==m;a,z=a[g],z[g]
 g=np.array([t[i]%(900000*m)==0 and np.all(np.diff(t[i:j])==900000) for i,j in zip(a,z)],bool);a,z=a[g],z[g]
 return t[a],o[a],np.array([h[i:j].max() for i,j in zip(a,z)]),np.array([l[i:j].min() for i,j in zip(a,z)]),c[z-1]
def psar(h,l,af0=.02,step=.02,mx=.2):
 n=len(h);sout=np.full(n,np.nan);bull=np.ones(n,bool)
 if n<3:return sout,bull
 s=l[0];tr=True;ep=h[1];af=af0;sout[1]=s
 for i in range(2,n):
  z=s+af*(ep-s);z=min(z,l[i-1],l[i-2]) if tr else max(z,h[i-1],h[i-2]);sout[i]=z;bull[i]=tr
  if tr:
   if l[i]<z:tr=False;s=ep;ep=l[i];af=af0
   else:
    s=z
    if h[i]>ep:ep=h[i];af=min(af+step,mx)
  else:
   if h[i]>z:tr=True;s=ep;ep=h[i];af=af0
   else:
    s=z
    if l[i]<ep:ep=l[i];af=min(af+step,mx)
 return sout,bull
def abin(a):
 return "0" if a==0 else "1-2" if a<=2 else "3-4" if a<=4 else "5-8" if a<=8 else "9-16" if a<=16 else "17+"
def audit():
 h=np.array([10,11,12,13,14,13,12,11,10,9.]);l=h-1;s,b=psar(h,l);assert np.isfinite(s[2:]).all()
 h2=h.copy();l2=l.copy();h2[-1]=99;l2[-1]=.1;s2,b2=psar(h2,l2);assert np.allclose(s[:-1],s2[:-1],equal_nan=True) and np.array_equal(b[:-1],b2[:-1])
 assert [abin(x) for x in [0,1,3,5,9,17]]==["0","1-2","3-4","5-8","9-16","17+"]
 return True
ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--tf",choices=("1h","4h","1d"));ap.add_argument("--shard",type=int);ap.add_argument("--shards",type=int,default=8);ap.add_argument("--out");ap.add_argument("--audit-only",action="store_true");a=ap.parse_args()
for _ in range(30):assert audit()
streak=0
while streak<10:
 try:assert audit();streak+=1
 except Exception:streak=0;raise
print("AUDIT_PASS initial=30 consecutive_clean=10",flush=True)
if a.audit_only: raise SystemExit(0)
if a.tf is None or a.shard is None or not a.out: raise RuntimeError("tf/shard/out required")
m={"1h":4,"4h":16,"1d":96}[a.tf];files=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert files
# global overlap audit before sharding
seen={}
for p in files:
 t=load(p)[0];s=sym(p);lo,hi=int(t[0]),int(t[-1])
 for x,z,old in seen.get(s,[]):
  if max(lo,x)<=min(hi,z):raise RuntimeError(f"overlap {s} {old} {p}")
 seen.setdefault(s,[]).append((lo,hi,p))
files=[p for i,p in enumerate(files) if i%a.shards==a.shard];obs=[];flipn=0
for p in files:
 data=load(p);symbol=sym(p)
 for aa,bb in seg(data[0]):
  if bb-aa<m*(BURN+max(H)+2):continue
  rt,ro,rh,rl,rc=rs(*(v[aa:bb] for v in data),m);n=len(rt)
  if n<=BURN+max(H)+1:continue
  sar,bull=psar(rh,rl);prev=np.r_[np.nan,rc[:-1]];tr=np.maximum(rh-rl,np.maximum(abs(rh-prev),abs(rl-prev)));atr_closed=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy();atr=np.r_[np.nan,atr_closed[:-1]]
  flip=np.r_[False,bull[1:]!=bull[:-1]];last=-1;d0=np.nan;eid=-1
  for i in range(BURN,n-max(H)):
   if not np.isfinite(sar[i]) or not np.isfinite(atr[i]) or atr[i]<=0:continue
   if flip[i]:last=i;eid=flipn;flipn+=1;d0=abs(rc[i-1]-sar[i])/atr[i]
   if last<0:continue
   side=1 if bull[i] else -1;dt=abs(ro[i]-sar[i])/atr[i];row={"symbol":symbol,"ts":int(rt[i]),"side":"BULL" if side==1 else "BEAR","event":eid,"age":i-last,"age_bin":abin(i-last),"d0":float(d0),"dt":float(dt)}\n   for z in (1,2,3,4,5,6,7,8): row[f"bull{z}"]=bool(bull[i+z])
   for z in H:
    row[f"ret{z}"]=float(side*(ro[i+z]/ro[i]-1)*100)
    row[f"mfe{z}"]=float((rh[i:i+z].max()/ro[i]-1)*100 if side==1 else (1-rl[i:i+z].min()/ro[i])*100)
    row[f"mae{z}"]=float((1-rl[i:i+z].min()/ro[i])*100 if side==1 else (rh[i:i+z].max()/ro[i]-1)*100)
   obs.append(row)
df=pd.DataFrame(obs)
out={"definition":{"tf":a.tf,"shard":a.shard,"shards":a.shards,"audits_initial":30,"clean_streak":10,"distance":"D0=abs(prev_confirmed_close-new_PSAR)/ATR14_confirmed; Dt=abs(current_open-PSAR)/ATR14_confirmed","flip":"change in open-projected PSAR side, observable at bar open from closed history","horizons":H,"scope":"descriptive candle study; no entry/TP/SL strategy"},"files":len(files),"flips":int(flipn),"observations":len(df),"rows":obs}

# Reflip-aware 1D validation
if a.tf=="1d" and len(df):
 x=df[(df.side=="BEAR") & (df.age==3)].copy()
 cut=1735689600000;x["period"]=np.where(x.ts<cut,"TRAIN","HOLDOUT")
 q=x.loc[x.period=="TRAIN","d0"].quantile([0,1/3,2/3,1]).to_numpy()
 x["band"]=pd.cut(x.d0,q,labels=["LOW","MID","HIGH"],include_lowest=True)
 x=x[x.band.isin(["MID","HIGH"])].copy()
 def mm(v):
  v=np.asarray(v,float)-.40;gp=v[v>0].sum();gl=-v[v<0].sum()
  return {"n":len(v),"pf":float(gp/gl) if gl>0 else None,"win":float((v>0).mean()*100),"avg":float(v.mean())}
 rr={}
 for z in [4,5,6,7,8]:
  rr[str(z)]={}
  for per in ["TRAIN","HOLDOUT"]:
   y=x[x.period==per]; fixed=y[f"ret{z}"].to_numpy(float); early=[];days=[]
   for _,r in y.iterrows():
    ex=z
    for k in range(1,z+1):
     if bool(r[f"bull{k}"]): ex=k;break
    early.append(float(r[f"ret{ex}"]));days.append(ex)
   rr[str(z)][per]={"FIXED":mm(fixed),"REFLIP_OR_MAX":mm(early),"avg_hold_days":float(np.mean(days)),"reflip_exit_pct":float(np.mean(np.asarray(days)<z)*100)}
 out={"rule":"1D PSAR BEAR age=3; TRAIN-frozen D0 LOW excluded; 40bp total cost; compare fixed max vs exit at first future OPEN where PSAR side is BULL","d0_edges":q.tolist(),"results":rr}
 open(a.out,"w").write(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
 raise SystemExit(0)
# Standalone 1D D0 broad horizon validation
if a.tf=="1d" and len(df):
 x=df[(df.side=="BEAR") & (df.age==3)].copy()
 cut=1735689600000;x["period"]=np.where(x.ts<cut,"TRAIN","HOLDOUT")
 q=x.loc[x.period=="TRAIN","d0"].quantile([0,1/3,2/3,1]).to_numpy()
 x["band"]=pd.cut(x.d0,q,labels=["LOW","MID","HIGH"],include_lowest=True)
 def met(v):
  v=np.asarray(v,float)-.40;gp=v[v>0].sum();gl=-v[v<0].sum()
  return {"n":len(v),"pf":float(gp/gl) if gl>0 else None,"win":float((v>0).mean()*100),"avg":float(v.mean())}
 hv={}
 for z in H:
  hv[str(z)]={}
  for per in ["TRAIN","HOLDOUT"]:
   y=x[(x.period==per)&x.band.isin(["MID","HIGH"])]
   hv[str(z)][per]=met(y[f"ret{z}"])
 out["d0_edges"]=q.tolist();out["horizon_validation_40bp"]=hv
open(a.out,"w").write(json.dumps(out));print(json.dumps({k:v for k,v in out.items() if k!="rows"},indent=2))
