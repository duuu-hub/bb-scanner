import argparse,glob,json,os
import numpy as np,pandas as pd
P='/tmp/canonical.py'
DIST=(.05,.1,.2,.3,.5,.75,1.,1.5,2.,2.5,3.)
HOURS=(6,12,24,48,72,120,168)
def load(p):
 d=pd.read_csv(p,compression='gzip',usecols=['open_time','open','high','low','close']).sort_values('open_time')
 return tuple(d[x].to_numpy(np.int64 if x=='open_time' else float) for x in ['open_time','open','high','low','close'])
def resample(t,o,h,l,c,m=96):
 bucket=t//(900000*m); cut=np.r_[0,np.flatnonzero(bucket[1:]!=bucket[:-1])+1,len(t)]
 st=cut[:-1]; en=cut[1:]; good=(en-st)==m; st=st[good]; en=en[good]
 rh=np.array([h[a:b].max() for a,b in zip(st,en)]); rl=np.array([l[a:b].min() for a,b in zip(st,en)])
 return t[st],o[st],rh,rl,c[en-1],st
def psar(h,l,af0=.02,step=.02,afmax=.2):
 n=len(h); out=np.full(n,np.nan); bull=np.ones(n,bool)
 if n<3:return out,bull
 sar=l[0]; trend=True; ep=h[1]; af=af0; out[1]=sar
 for i in range(2,n):
  z=sar+af*(ep-sar); z=min(z,l[i-1],l[i-2]) if trend else max(z,h[i-1],h[i-2]); out[i]=z; bull[i]=trend
  if trend:
   if l[i]<z: trend=False;sar=ep;ep=l[i];af=af0
   else:
    sar=z
    if h[i]>ep: ep=h[i];af=min(af+step,afmax)
  else:
   if h[i]>z: trend=True;sar=ep;ep=h[i];af=af0
   else:
    sar=z
    if l[i]<ep: ep=l[i];af=min(af+step,afmax)
 return out,bull
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--data');ap.add_argument('--shard',type=int);ap.add_argument('--shards',type=int,default=8);ap.add_argument('--out');a=ap.parse_args()
 files=sorted(glob.glob(a.data+'/**/*.csv.gz',recursive=True))[a.shard::a.shards]; rows=[]
 for p in files:
  t,o,h,l,c=load(p); rt,ro,rh,rl,rc,st=resample(t,o,h,l,c); sar,bull=psar(rh,rl)
  prev=np.r_[np.nan,rc[:-1]]; tr=np.maximum(rh-rl,np.maximum(abs(rh-prev),abs(rl-prev))); atr=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy(); atr=np.r_[np.nan,atr[:-1]]
  sym=os.path.basename(p)[:-7]
  for i in range(100,len(rt)):
   if bull[i] or not np.isfinite(sar[i]) or not np.isfinite(atr[i]) or atr[i]<=0: continue
   dist=(sar[i]-ro[i])/atr[i]
   if dist<0 or dist>3: continue
   start=st[i]; rec={'symbol':sym,'ts':int(rt[i]),'dist_atr':float(dist)}
   for hh in HOURS:
    z=min(len(t),start+int(hh*4)); hit=np.flatnonzero(h[start:z]>=sar[i]); rec[f'hit_{hh}h']=bool(hit.size)
    rec[f'mae_{hh}h']=float((ro[i]-l[start:z].min())/atr[i]) if z>start else None
    rec[f'mfe_{hh}h']=float((h[start:z].max()-sar[i])/atr[i]) if hit.size else None
   rows.append(rec)
 json.dump({'events':rows,'distances':DIST,'hours':HOURS},open(a.out,'w'))
 print('EVENTS',len(rows))
if __name__=='__main__':main()
