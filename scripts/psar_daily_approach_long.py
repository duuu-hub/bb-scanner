import argparse,glob,json,os
import numpy as np,pandas as pd
DIST=(.05,.1,.15,.2,.3); TP=(0,.1,.25,.5); SL=(.25,.5,.75,1.,1.5,2.); HOURS=(6,12,24,48)
def load(p):
 d=pd.read_csv(p,compression='gzip',usecols=['open_time','open','high','low','close']).sort_values('open_time')
 return tuple(d[x].to_numpy(np.int64 if x=='open_time' else float) for x in ['open_time','open','high','low','close'])
def resample(t,o,h,l,c,m=96):
 b=t//(900000*m);q=np.r_[0,np.flatnonzero(b[1:]!=b[:-1])+1,len(t)];a=q[:-1];z=q[1:];g=(z-a)==m;a=a[g];z=z[g]
 return t[a],o[a],np.array([h[x:y].max() for x,y in zip(a,z)]),np.array([l[x:y].min() for x,y in zip(a,z)]),c[z-1],a
def psar(h,l):
 n=len(h);s=np.full(n,np.nan);bull=np.ones(n,bool)
 if n<3:return s,bull
 sar=l[0];trend=True;ep=h[1];af=.02;s[1]=sar
 for i in range(2,n):
  v=sar+af*(ep-sar);v=min(v,l[i-1],l[i-2]) if trend else max(v,h[i-1],h[i-2]);s[i]=v;bull[i]=trend
  if trend:
   if l[i]<v:trend=False;sar=ep;ep=l[i];af=.02
   else:
    sar=v
    if h[i]>ep:ep=h[i];af=min(af+.02,.2)
  else:
   if h[i]>v:trend=True;sar=ep;ep=h[i];af=.02
   else:
    sar=v
    if l[i]<ep:ep=l[i];af=min(af+.02,.2)
 return s,bull
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--data');ap.add_argument('--shard',type=int);ap.add_argument('--shards',type=int,default=8);ap.add_argument('--out');a=ap.parse_args()
 out={}
 for p in sorted(glob.glob(a.data+'/**/*.csv.gz',recursive=True))[a.shard::a.shards]:
  t,o,h,l,c=load(p);rt,ro,rh,rl,rc,st=resample(t,o,h,l,c);sar,bull=psar(rh,rl)
  prev=np.r_[np.nan,rc[:-1]];tr=np.maximum(rh-rl,np.maximum(abs(rh-prev),abs(rl-prev)));ac=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy();ao=np.r_[np.nan,ac[:-1]]
  prev_near={d:False for d in DIST}
  for i in range(100,len(rt)):
   valid=(not bull[i]) and np.isfinite(sar[i]) and np.isfinite(ao[i]) and ao[i]>0
   dist=(sar[i]-ro[i])/ao[i] if valid else 999.
   for d in DIST:
    near=valid and 0<=dist<=d
    first=near and not prev_near[d];prev_near[d]=near
    if not first:continue
    start=st[i];entry=ro[i];atr=ao[i]
    for tpbuf in TP:
     target=sar[i]+tpbuf*atr
     if target<=entry:continue
     for slatr in SL:
      stop=entry-slatr*atr
      for hh in HOURS:
       end=min(len(t),start+hh*4);res=None;exitp=None
       for j in range(start,end):
        ht=h[j]>=target;hs=l[j]<=stop
        if ht and hs:res='loss';exitp=stop;break
        if hs:res='loss';exitp=stop;break
        if ht:res='win';exitp=target;break
       if res is None:
        res='timeout';exitp=c[end-1] if end>start else entry
       r=(exitp-entry)/(entry-stop)
       k=f'D{d:g}|TP{tpbuf:g}|SL{slatr:g}|T{hh}h';q=out.setdefault(k,{'n':0,'win':0,'loss':0,'timeout':0,'sumR':0.})
       q['n']+=1;q[res]+=1;q['sumR']+=r
 json.dump(out,open(a.out,'w'));print('PARAMS',len(out),'EVENTS',sum(x['n'] for x in out.values()))
if __name__=='__main__':main()
