import glob,os,io,zipfile,urllib.request,json
import pandas as pd,numpy as np
from psar_open_spider_grid import load,resample,psar_open_projection
CANDS=[(0.,.1,2.),(0.,.1,2.5),(0.,.1,3.),(0.,.1,4.),(.25,0.,1.)]
M=16;HORIZON=12
def events(p):
 t,o,h,l,c=load(p);rt,ro,rh,rl,rc=resample(t,o,h,l,c);sar,bull=psar_open_projection(rh,rl)
 prev=np.r_[np.nan,rc[:-1]];tr=np.maximum(rh-rl,np.maximum(abs(rh-prev),abs(rl-prev)))
 ac=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy();ao=np.r_[np.nan,ac[:-1]];pos=np.searchsorted(t,rt);ev=[]
 for i in range(15,len(rt)-HORIZON):
  if not np.isfinite(sar[i]) or not np.isfinite(ao[i]) or ao[i]<=0:continue
  b=bool(bull[i]);start=pos[i];end=min(start+M*HORIZON,len(t))
  for em,sb,r in CANDS:
   e=sar[i]+(em*ao[i] if b else -em*ao[i]); hit=np.flatnonzero((l[start:start+M]<=e)&(h[start:start+M]>=e))
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
