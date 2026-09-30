import argparse,glob,os,pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--data");ap.add_argument("--shard",type=int);ap.add_argument("--shards",type=int);ap.add_argument("--out");a=ap.parse_args()
fs=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));fs=[p for i,p in enumerate(fs) if i%a.shards==a.shard];rows=[]
thresholds=[12,15,18,20]; tps=[1,2,3,4,6,8]; sls=[1,2,3,4,6,8,10]; limits=[4,16,48,96]
for p in fs:
 d=pd.read_csv(p,usecols=["open_time","open","high","low","close","volume","quote_volume"]).sort_values("open_time").reset_index(drop=True)
 if len(d)<200:continue
 v=d.volume.astype(float);q=d.quote_volume.astype(float);vw=(q.rolling(96,min_periods=32).sum()/v.rolling(96,min_periods=32).sum()).shift(1);dist=(d.open.astype(float)/vw-1)*100
 for th in thresholds:
  for side,mask,sgn in [("LONG",dist<=-th,1),("SHORT",dist>=th,-1)]:
   ev=np.where((mask & ~mask.shift(1,fill_value=False)).fillna(False))[0]
   for i in ev:
    ep=float(d.open.iloc[i])
    for lim in limits:
     end=min(i+lim,len(d)-1)
     if end<=i:continue
     hh=d.high.iloc[i+1:end+1].to_numpy(float);ll=d.low.iloc[i+1:end+1].to_numpy(float)
     for tp in tps:
      for sl in sls:
       if side=="LONG": hit_tp=np.where(hh>=ep*(1+tp/100))[0];hit_sl=np.where(ll<=ep*(1-sl/100))[0]
       else: hit_tp=np.where(ll<=ep*(1-tp/100))[0];hit_sl=np.where(hh>=ep*(1+sl/100))[0]
       it=hit_tp[0] if len(hit_tp) else 10**9; is_=hit_sl[0] if len(hit_sl) else 10**9
       if it==is_ and it<10**9: gross=-sl
       elif it<is_: gross=tp
       elif is_<it:gross=-sl
       else:gross=sgn*(float(d.close.iloc[end])/ep-1)*100
       rows.append([side,th,tp,sl,lim,gross])
pd.DataFrame(rows,columns=["side","threshold","tp","sl","limit15m","gross_pct"]).to_csv(a.out,index=False);print("PASS",a.shard,len(rows))
