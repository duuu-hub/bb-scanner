import argparse,glob,json,os
import numpy as np,pandas as pd
DIST=(.02,.03,.05,.075,.1,.15,.2,.3,.5); TP=(0,.1,.25,.5); SL=(.25,.5,.75,1.,1.5,2.); HOURS=(6,12,24,48)
def load(p):
 d=pd.read_csv(p,compression='gzip',usecols=['open_time','open','high','low','close']).sort_values('open_time')
 return tuple(d[x].to_numpy(np.int64 if x=='open_time' else float) for x in ['open_time','open','high','low','close'])
def resample(t,o,h,l,c,m=16):
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
  used={d:False for d in DIST}; prevbull=None
  for i in range(100,len(rt)):
   valid=bull[i] and np.isfinite(sar[i]) and np.isfinite(ao[i]) and ao[i]>0
   if prevbull is None or bull[i]!=prevbull:
    used={d:False for d in DIST}
   prevbull=bull[i]
   if not valid: continue
   base=st[i]; atr=ao[i]; ps=sar[i]
   for d in DIST:
    if used[d]: continue
    trigger=ps+d*atr
    hitj=None;entry=None
    for j in range(base,min(base+16,len(t))):
     if o[j]<=ps: break
     if o[j]<=trigger: hitj=j;entry=o[j];break
     if l[j]<=trigger: hitj=j;entry=trigger;break
    if hitj is None: continue
    used[d]=True
    for tpbuf in TP:
     target=ps-tpbuf*atr
     if target>=entry: continue
     for slatr in SL:
      stop=entry+slatr*atr
      for hh in HOURS:
       endj=min(len(t),hitj+hh*4);res=None;exitp=None
       for j in range(hitj,endj):
        ht=l[j]<=target;hs=h[j]>=stop
        if ht and hs:res='loss';exitp=stop;break
        if hs:res='loss';exitp=stop;break
        if ht:res='win';exitp=target;break
       if res is None:
        res='timeout';exitp=c[endj-1] if endj>hitj else entry
       r=(entry-exitp)/(stop-entry)
       k=f'D{d:g}|TP{tpbuf:g}|SL{slatr:g}|T{hh}h';q=out.setdefault(k,{'n':0,'win':0,'loss':0,'timeout':0,'sumR':0.})
       q['n']+=1;q[res]+=1;q['sumR']+=r
 json.dump(out,open(a.out,'w'));print('PARAMS',len(out),'EVENTS',sum(x['n'] for x in out.values()))
if __name__=='__main__':main()
