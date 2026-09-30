import argparse,glob,os,pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--data");ap.add_argument("--shard",type=int);ap.add_argument("--shards",type=int);ap.add_argument("--out");a=ap.parse_args()
fs=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));fs=[p for i,p in enumerate(fs) if i%a.shards==a.shard]; rows=[]
for p in fs:
 d=pd.read_csv(p,usecols=["open_time","open","high","low","close","volume","quote_volume"]).sort_values("open_time").reset_index(drop=True)
 if len(d)<200:continue
 v=d.volume.astype(float);q=d.quote_volume.astype(float); vw=(q.rolling(96,min_periods=32).sum()/v.rolling(96,min_periods=32).sum()).shift(1)
 dist=(d.open.astype(float)/vw-1)*100
 for side,mask,sgn in [("LONG",dist<=-15,1),("SHORT",dist>=15,-1)]:
  event=(mask & ~mask.shift(1,fill_value=False)).fillna(False)
  for confirm in ["touch","reentry"]:
   idx=np.where(event)[0]
   for i in idx:
    entry=i
    if confirm=="reentry":
     z=np.where((dist.iloc[i+1:min(i+97,len(d))].abs()<15).values)[0]
     if not len(z):continue
     entry=i+1+int(z[0])
    ep=float(d.open.iloc[entry])
    for h in [4,16,48,96]:
     j=entry+h
     if j>=len(d):continue
     r=sgn*(float(d.close.iloc[j])/ep-1)*100
     rows.append([os.path.basename(p).replace(".csv.gz",""),side,confirm,h,int(d.open_time.iloc[entry]),r])
pd.DataFrame(rows,columns=["symbol","side","entry","h15m","ts","ret_pct"]).to_csv(a.out,index=False);print("PASS",a.shard,len(rows))
