import argparse,glob,json,os,io,urllib.request,zipfile,re
from datetime import datetime,timezone
import numpy as np,pandas as pd
DIST=(.1,.15,.2); TP=(.5,); SL=(2.,); HOURS=(12,)
_ONE_MIN_CACHE={}
def symbol_of(p):
 b=os.path.basename(p);return b[:-7].upper()
def one_min(sym,ts):
 ym=datetime.fromtimestamp(int(ts)/1000,tz=timezone.utc).strftime("%Y-%m");key=(sym,ym)
 if key in _ONE_MIN_CACHE:return _ONE_MIN_CACHE[key]
 u=f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1m/{sym}-1m-{ym}.zip"
 raw=urllib.request.urlopen(u,timeout=60).read()
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  d=pd.read_csv(z.open([x for x in z.namelist() if x.endswith(".csv")][0]),header=None,dtype=str)
 if not pd.to_numeric(pd.Series([d.iat[0,0]]),errors="coerce").notna().iat[0]:d=d.iloc[1:]
 v=(pd.to_numeric(d.iloc[:,0]).to_numpy(np.int64),pd.to_numeric(d.iloc[:,1]).to_numpy(float),pd.to_numeric(d.iloc[:,2]).to_numpy(float),pd.to_numeric(d.iloc[:,3]).to_numpy(float),pd.to_numeric(d.iloc[:,4]).to_numpy(float))
 _ONE_MIN_CACHE[key]=v;return v
def entry_bar_1m(sym,ts,entry,target,stop):
 mt,mo,mh,ml,mc=one_min(sym,ts);a=np.searchsorted(mt,ts);z=np.searchsorted(mt,ts+900000);entered=False
 if z-a!=15:return "gap",None
 for k in range(a,z):
  if not entered:
   if mo[k]>=entry: entered=True; fill=mo[k]
   elif mh[k]>=entry: entered=True; fill=entry
   else: continue
   ht=mh[k]>=target;hs=ml[k]<=stop
   if ht or hs:return "loss",stop
  else:
   ht=mh[k]>=target;hs=ml[k]<=stop
   if ht and hs:return "loss",stop
   if hs:return "loss",stop
   if ht:return "win",target
 return ("continue",None) if entered else ("mismatch",None)

def load(p):
 d=pd.read_csv(p,compression='gzip',usecols=['open_time','open','high','low','close']).sort_values('open_time')
 return tuple(d[x].to_numpy(np.int64 if x=='open_time' else float) for x in ['open_time','open','high','low','close'])
def resample(t,o,h,l,c,m=16):
 b=t//(900000*m);q=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(t)];a=q[:-1];z=q[1:];g=(z-a)==m;a=a[g];z=z[g]
 return t[a],o[a],np.array([h[x:y].max() for x,y in zip(a,z)]),np.array([l[x:y].min() for x,y in zip(a,z)]),c[z-1],a
def psar_open_projection(h,l,af0=.02,step=.02,afmax=.2):
 n=len(h);out=np.full(n,np.nan);bull=np.ones(n,bool)
 if n<3:return out,bull
 sar=l[0];trend=True;ep=h[1];af=af0;out[1]=sar;bull[1]=trend
 for i in range(2,n):
  z=sar+af*(ep-sar);z=min(z,l[i-1],l[i-2]) if trend else max(z,h[i-1],h[i-2]);out[i]=z;bull[i]=trend
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
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--data');ap.add_argument('--shard',type=int);ap.add_argument('--shards',type=int,default=8);ap.add_argument('--out');a=ap.parse_args()
 out={}
 for p in sorted(glob.glob(a.data+'/**/*.csv.gz',recursive=True))[a.shard::a.shards]:
  sym=symbol_of(p);t,o,h,l,c=load(p);rt,ro,rh,rl,rc,st=resample(t,o,h,l,c);sar,bull=psar_open_projection(rh,rl)
  prev=np.r_[np.nan,rc[:-1]];tr=np.maximum(rh-rl,np.maximum(abs(rh-prev),abs(rl-prev)));atr_closed=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy();atr_open=np.r_[np.nan,atr_closed[:-1]];ao=atr_open
  used={d:False for d in DIST}; prevbull=None
  for i in range(100,len(rt)):
   valid=(not bull[i]) and np.isfinite(sar[i]) and np.isfinite(ao[i]) and ao[i]>0
   if prevbull is None or bull[i]!=prevbull:
    used={d:False for d in DIST}
   prevbull=bull[i]
   if not valid: continue
   base=st[i]; atr=ao[i]; ps=sar[i]
   for d in DIST:
    if used[d]: continue
    trigger=ps-d*atr
    hitj=None; entry=None
    for j in range(base,min(base+16,len(t))):
     if o[j]>=ps: break
     if o[j]>=trigger: hitj=j;entry=o[j];break
     if h[j]>=trigger: hitj=j;entry=trigger;break
    if hitj is None: continue
    used[d]=True
    for tpbuf in TP:
     target=ps+tpbuf*atr
     if target<=entry: continue
     for slatr in SL:
      stop=entry-slatr*atr
      for hh in HOURS:
       endj=min(len(t),hitj+hh*4);res=None;exitp=None
       rr,ep=entry_bar_1m(sym,int(t[hitj]),entry,target,stop)
       if rr in ('win','loss'):res=rr;exitp=ep
       elif rr in ('gap','mismatch'):continue
       if res is None:
        for j in range(hitj+1,endj):
         ht=h[j]>=target;hs=l[j]<=stop
         if ht and hs:res='loss';exitp=stop;break
         if hs:res='loss';exitp=stop;break
         if ht:res='win';exitp=target;break
       if res is None:
        res='timeout';exitp=c[endj-1] if endj>hitj else entry
       r=(exitp-entry)/(entry-stop)
       k=f'D{d:g}|TP{tpbuf:g}|SL{slatr:g}|T{hh}h';q=out.setdefault(k,{'n':0,'win':0,'loss':0,'timeout':0,'sumR':0.})
       q['n']+=1;q[res]+=1;q['sumR']+=r
 json.dump(out,open(a.out,'w'));print('PARAMS',len(out),'EVENTS',sum(x['n'] for x in out.values()))
if __name__=='__main__':main()
