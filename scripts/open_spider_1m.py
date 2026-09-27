import glob,os,io,zipfile,urllib.request,json
import pandas as pd,numpy as np
def load(p):
 d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]).sort_values("open_time")
 return tuple(d[x].to_numpy(np.int64 if x=="open_time" else float) for x in ["open_time","open","high","low","close"])
def resample(t,o,h,l,c,m=16):
 bucket=t//(900000*m);cut=np.r_[0,np.flatnonzero(bucket[1:]!=bucket[:-1])+1,len(t)]
 st=cut[:-1];en=cut[1:];good=(en-st)==m;st=st[good];en=en[good]
 return t[st],o[st],np.maximum.reduceat(h,st),np.minimum.reduceat(l,st),c[en-1]
def psar_open_projection(h,l,af0=.02,step=.02,afmax=.2):
 n=len(h);out=np.full(n,np.nan);bull=np.ones(n,bool)
 if n<3:return out,bull
 sar=l[0];trend=True;ep=h[1];af=af0;out[1]=sar;bull[1]=trend
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
CANDS=[(e,s,r) for e in (0.,.25,.5,1.,2.,3.,4.,5.,6.,8.,10.) for s in (0.,.1,.2,.3,.5,.75,1.,1.5,2.) for r in (.5,1.,1.5,2.,2.5,3.,4.,5.,6.,8.,10.) if abs(e)+abs(s)>0]
M=16;HORIZON=12
def events(p):
 t,o,h,l,c=load(p);rt,ro,rh,rl,rc=resample(t,o,h,l,c);sar,bull=psar_open_projection(rh,rl)
 prev=np.r_[np.nan,rc[:-1]];tr=np.maximum(rh-rl,np.maximum(abs(rh-prev),abs(rl-prev)))
 ac=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy();ao=np.r_[np.nan,ac[:-1]];pos=np.searchsorted(t,rt);ev=[]
 for i in range(15,len(rt)-HORIZON):
  if not np.isfinite(sar[i]) or not np.isfinite(ao[i]) or ao[i]<=0:continue
  b=bool(bull[i]);start=pos[i];end=min(start+M*HORIZON,len(t))
  for em,sb,r in CANDS:
   e=sar[i]+(em*ao[i] if b else -em*ao[i])
   # Must match grid: order has to be a valid post-only maker at the 4H open.
   if (b and e>=ro[i]) or ((not b) and e<=ro[i]):continue
   hit=np.flatnonzero((l[start:start+M]<=e)&(h[start:start+M]>=e))
   if not hit.size:continue
   fs=start+int(hit[0]);sl=sar[i]-(sb*ao[i] if b else -sb*ao[i]);risk=abs(e-sl)
   if risk<=1e-12*max(1.,abs(e),abs(sl)):continue
   tp=e+(r*risk if b else -r*risk);ph=h[fs:end];pl=l[fs:end]
   th=(ph>=tp) if b else (pl<=tp);sh=(pl<=sl) if b else (ph>=sl);ti=np.flatnonzero(th);si=np.flatnonzero(sh)
   if ti.size and si.size and ti[0]==si[0]:
    k=fs+int(ti[0]);ev.append(dict(symbol=os.path.basename(p).split('.')[0],side='LONG' if b else 'SHORT',em=em,sb=sb,r=r,entry=e,sl=sl,tp=tp,amb_ts=int(t[k])))
 return ev
def get1m(sym,ms):
 ym=pd.to_datetime(ms,unit='ms',utc=True).strftime('%Y-%m');u=f'https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1m/{sym}-1m-{ym}.zip'
 try:
  z=zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u,timeout=45).read()));d=pd.read_csv(z.open(z.namelist()[0]),header=None,usecols=[0,2,3],dtype=str)
  tt=pd.to_numeric(d.iloc[:,0],errors='coerce');hh=pd.to_numeric(d.iloc[:,1],errors='coerce');ll=pd.to_numeric(d.iloc[:,2],errors='coerce');v=tt.notna()&hh.notna()&ll.notna()
  tt=tt[v].to_numpy(np.int64);hh=hh[v].to_numpy(float);ll=ll[v].to_numpy(float)
  if tt[0]>10**14:tt//=1000
  return tt,hh,ll
 except Exception:return None
files=glob.glob('data/**/*.csv.gz',recursive=True);es=[]
for p in files:
 try:es+=events(p)
 except Exception as e:print('SCAN_FAIL',p,e)
groups={}
for e in es:groups.setdefault((e['symbol'],pd.to_datetime(e['amb_ts'],unit='ms',utc=True).strftime('%Y-%m')),[]).append(e)
out={}
for (sym,ym),xs in groups.items():
 d=get1m(sym,xs[0]['amb_ts'])
 for e in xs:
  key=f"E{e['em']:g}|SB{e['sb']:g}|R{e['r']:g}|{e['side']}";q=out.setdefault(key,dict(win=0,loss=0,same1m=0,missing=0))
  if d is None:q['missing']+=1;continue
  tt,hh,ll=d;a=np.searchsorted(tt,e['amb_ts']);b=np.searchsorted(tt,e['amb_ts']+900000)
  if a>=len(tt) or a==b:q['missing']+=1;continue
  H=hh[a:b];L=ll[a:b];lng=e['side']=='LONG';ti=np.flatnonzero((H>=e['tp']) if lng else (L<=e['tp']));si=np.flatnonzero((L<=e['sl']) if lng else (H>=e['sl']))
  it=ti[0] if ti.size else 999;ss=si[0] if si.size else 999
  if it<ss:q['win']+=1
  elif ss<it:q['loss']+=1
  else:q['same1m']+=1
open('open_spider_1m.json','w').write(json.dumps({'events':len(es),'results':out},indent=2));print(json.dumps({'events':len(es),'results':out},indent=2))
