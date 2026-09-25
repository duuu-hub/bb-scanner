import glob,os,io,zipfile,urllib.request,json,calendar
import pandas as pd,numpy as np
ATR=.25; RS=(2.,3.); M=16; HORIZON=12

def psar(h,l,af0=.02,step=.02,afmax=.2):
 n=len(h);s=np.full(n,np.nan);b=np.ones(n,bool)
 if n<3:return s,b
 s[1]=l[0];ep=h[1];af=af0
 for i in range(2,n):
  z=s[i-1]+af*(ep-s[i-1])
  if b[i-1]:
   z=min(z,l[i-1],l[i-2])
   if l[i]<z:b[i]=False;z=ep;ep=l[i];af=af0
   elif h[i]>ep:ep=h[i];af=min(af+step,afmax)
  else:
   z=max(z,h[i-1],h[i-2])
   if h[i]>z:b[i]=True;z=ep;ep=h[i];af=af0
   else:
    b[i]=False
    if l[i]<ep:ep=l[i];af=min(af+step,afmax)
  s[i]=z
 return s,b

def load(p):
 d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]).sort_values("open_time")
 return tuple(d[x].to_numpy(np.int64 if x=="open_time" else float) for x in ["open_time","open","high","low","close"])

def resample(t,o,h,l,c):
 bucket=t//(900000*M);cut=np.r_[0,np.flatnonzero(bucket[1:]!=bucket[:-1])+1,len(t)]
 st=cut[:-1];en=cut[1:];g=(en-st)==M;st=st[g];en=en[g]
 return t[st],o[st],np.maximum.reduceat(h,st),np.minimum.reduceat(l,st),c[en-1]

def ambiguous_events(p):
 sym=os.path.basename(p).split(".")[0];t,o,h,l,c=load(p);rt,ro,rh,rl,rc=resample(t,o,h,l,c)
 sar,bull=psar(rh,rl);prev=np.r_[np.nan,rc[:-1]]
 tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prev),np.abs(rl-prev)))
 atr=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy();pos=np.searchsorted(t,rt);ev=[]
 for i in range(14,len(rt)-HORIZON-1):
  if not np.isfinite(sar[i]) or not np.isfinite(atr[i]):continue
  b=bool(bull[i]);s=sar[i];e=s+(ATR*atr[i] if b else -ATR*atr[i]);a=pos[i+1]
  hit=np.flatnonzero((l[a:a+M]<=e)&(h[a:a+M]>=e))
  if not hit.size:continue
  fs=a+hit[0];end=min(a+M*HORIZON,len(t));ph=h[fs:end];pl=l[fs:end]
  risk=abs(e-s)
  for r in RS:
   tp=e+r*risk if b else e-r*risk
   th=(ph>=tp) if b else (pl<=tp);sh=(pl<=s) if b else (ph>=s)
   ti=np.flatnonzero(th);si=np.flatnonzero(sh)
   if ti.size and si.size and ti[0]==si[0]:
    k=fs+ti[0];ev.append({"symbol":sym,"bar":int(t[k]),"side":"LONG" if b else "SHORT","r":r,"entry":e,"sl":s,"tp":tp})
 return ev

def get1m(sym,ms):
 dt=pd.to_datetime(ms,unit="ms",utc=True);ym=dt.strftime("%Y-%m")
 url=f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1m/{sym}-1m-{ym}.zip"
 try:
  raw=urllib.request.urlopen(url,timeout=45).read()
  z=zipfile.ZipFile(io.BytesIO(raw));df=pd.read_csv(z.open(z.namelist()[0]),header=None,usecols=[0,2,3])
  # newer archives can store microseconds
  tt=df.iloc[:,0].to_numpy(np.int64)
  if tt[0]>10**14:tt=tt//1000
  return tt,df.iloc[:,1].to_numpy(float),df.iloc[:,2].to_numpy(float)
 except Exception as e:
  print("DOWNLOAD_FAIL",sym,ym,e,flush=True);return None

files=glob.glob("data/**/*.csv.gz",recursive=True);events=[]
for n,p in enumerate(files,1):
 try:events.extend(ambiguous_events(p))
 except Exception as e:print("SCAN_FAIL",p,e,flush=True)
 if n%20==0:print("scan",n,len(files),"events",len(events),flush=True)
print("EVENTS",len(events),flush=True)
groups={}
for e in events:
 dt=pd.to_datetime(e["bar"],unit="ms",utc=True);key=(e["symbol"],dt.strftime("%Y-%m"));groups.setdefault(key,[]).append(e)
out={"meta":{"events":len(events),"groups":len(groups),"atr_mult":ATR},"2R":{"LONG":{"win":0,"loss":0,"unresolved":0},"SHORT":{"win":0,"loss":0,"unresolved":0}},"3R":{"LONG":{"win":0,"loss":0,"unresolved":0},"SHORT":{"win":0,"loss":0,"unresolved":0}}}
for gi,((sym,ym),es) in enumerate(groups.items(),1):
 data=get1m(sym,es[0]["bar"])
 if data is None:
  for e in es:out[f'{e["r"]:g}R'][e["side"]]["unresolved"]+=1
  continue
 tt,hh,ll=data
 for e in es:
  q=out[f'{e["r"]:g}R'][e["side"]];a=np.searchsorted(tt,e["bar"]);b=np.searchsorted(tt,e["bar"]+900000)
  if a>=len(tt) or a==b:q["unresolved"]+=1;continue
  H=hh[a:b];L=ll[a:b];long=e["side"]=="LONG"
  th=(H>=e["tp"]) if long else (L<=e["tp"]);sh=(L<=e["sl"]) if long else (H>=e["sl"])
  ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 999;is_=si[0] if si.size else 999
  if it<is_:q["win"]+=1
  elif is_<it:q["loss"]+=1
  else:q["unresolved"]+=1
 if gi%100==0:print("resolve",gi,len(groups),flush=True)
for rk in ("2R","3R"):
 for side,q in out[rk].items():
  d=q["win"]+q["loss"];q["resolved_win_pct"]=round(100*q["win"]/d,3) if d else None
open("psar_1m_resolution.json","w").write(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
