import argparse,glob,json,os
import pandas as pd, numpy as np
DISTS=[.001,.0025,.005,.0075,.01]; TFS={"1h":4,"4h":16}; RS=[1,1.5,2,3]

def psar(df,af0=.02,step=.02,afmax=.2):
 h=df.high.to_numpy(float); l=df.low.to_numpy(float); n=len(df); sar=np.full(n,np.nan); bull=np.ones(n,bool)
 if n<3:return sar,bull
 sar[1]=l[0]; ep=h[1]; af=af0
 for i in range(2,n):
  s=sar[i-1]+af*(ep-sar[i-1])
  if bull[i-1]:
   s=min(s,l[i-1],l[i-2])
   if l[i]<s: bull[i]=False;s=ep;ep=l[i];af=af0
   else:
    bull[i]=True
    if h[i]>ep:ep=h[i];af=min(af+step,afmax)
  else:
   s=max(s,h[i-1],h[i-2])
   if h[i]>s:bull[i]=True;s=ep;ep=h[i];af=af0
   else:
    bull[i]=False
    if l[i]<ep:ep=l[i];af=min(af+step,afmax)
  sar[i]=s
 return sar,bull

def load(p):
 d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"])
 d.index=pd.to_datetime(d.pop("open_time").astype("int64"),unit="ms",utc=True)
 return d.astype(float).sort_index()

def run(d,tf,mult,dist,horizon=12):
 x=d.resample(tf,label="left",closed="left").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
 x["sar"],x["bull"]=psar(x); idx=d.index; out={"LONG":{"signals":0,"fills":0},"SHORT":{"signals":0,"fills":0}}
 for side in out:
  for r in RS:out[side][f"{r}R_win"]=0;out[side][f"{r}R_loss"]=0;out[side][f"{r}R_amb"]=0
 for i in range(3,len(x)-horizon-1):
  s=x.sar.iat[i]
  if not np.isfinite(s):continue
  b=bool(x.bull.iat[i]); side="LONG" if b else "SHORT"; out[side]["signals"]+=1
  e=s*(1+dist if b else 1-dist)
  start=x.index[i+1]; end=start+pd.Timedelta(minutes=15*mult*horizon)
  child=d.loc[(idx>=start)&(idx<end)]
  if child.empty:continue
  # entry must occur in first signal-TF bar only
  first_end=start+pd.Timedelta(minutes=15*mult)
  first=child.loc[child.index<first_end]
  hit=np.flatnonzero((first.low.to_numpy()<=e)&(first.high.to_numpy()>=e))
  if len(hit)==0:continue
  out[side]["fills"]+=1; fill_ts=first.index[hit[0]]; post=child.loc[child.index>=fill_ts]
  risk=abs(e-s)
  for r in RS:
   tp=e+r*risk if b else e-r*risk; sl=s
   verdict=None
   for _,bar in post.iterrows():
    th=(bar.high>=tp) if b else (bar.low<=tp); sh=(bar.low<=sl) if b else (bar.high>=sl)
    if th and sh: verdict="amb"; break
    if th: verdict="win"; break
    if sh: verdict="loss"; break
   if verdict:out[side][f"{r}R_{verdict}"]+=1
 return out

ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--out",default="psar_replay_results.json");a=ap.parse_args()
files=glob.glob(a.data+"/**/*.csv.gz",recursive=True); assert files
agg={}; errors=[]
for z,p in enumerate(files,1):
 try:d=load(p)
 except Exception as e:errors.append([p,str(e)]);continue
 for tf,m in TFS.items():
  for dist in DISTS:
   rr=run(d,tf,m,dist); k=f"{tf}|{dist:.4f}"
   for side,v in rr.items():
    q=agg.setdefault(k,{}).setdefault(side,{kk:0 for kk in v})
    for kk,vv in v.items():q[kk]+=vv
 if z%20==0:print(f"progress {z}/{len(files)}",flush=True)
for k in agg:
 for side,q in agg[k].items():
  q["fill_pct"]=round(100*q["fills"]/q["signals"],3) if q["signals"] else 0
  for r in RS:
   den=q[f"{r}R_win"]+q[f"{r}R_loss"]
   q[f"{r}R_win_pct_ex_amb"]=round(100*q[f"{r}R_win"]/den,3) if den else None
res={"files":len(files),"load_errors":errors,"note":"15m chronological replay; same-15m TP+SL is ambiguous/excluded from win rate","summary":agg}
open(a.out,"w").write(json.dumps(res,indent=2));print(json.dumps(res,indent=2))
