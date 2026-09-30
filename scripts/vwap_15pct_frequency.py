import argparse,glob,os,pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--data");ap.add_argument("--shard",type=int);ap.add_argument("--shards",type=int);ap.add_argument("--out");a=ap.parse_args()
fs=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));fs=[p for i,p in enumerate(fs) if i%a.shards==a.shard]
rows=[]
for p in fs:
 d=pd.read_csv(p,usecols=["open_time","open","volume","quote_volume"]).sort_values("open_time").reset_index(drop=True)
 if len(d)<100: continue
 v=d.volume.astype(float);q=d.quote_volume.astype(float)
 vw=(q.rolling(96,min_periods=32).sum()/v.rolling(96,min_periods=32).sum()).shift(1)
 dist=(d.open.astype(float)/vw-1)*100; ok=dist.notna()
 for side,mask in [("below_-15",dist<=-15),("above_+15",dist>=15),("abs_15",dist.abs()>=15)]:
  m=(mask & ok).fillna(False)
  # independent event = false->true transition
  events=(m & ~m.shift(1,fill_value=False)).sum()
  rows.append([os.path.basename(p).replace(".csv.gz",""),side,int(ok.sum()),int(m.sum()),int(events),
               int(d.loc[ok,"open_time"].min()) if ok.any() else 0,int(d.loc[ok,"open_time"].max()) if ok.any() else 0])
pd.DataFrame(rows,columns=["symbol","side","valid_candles","extreme_candles","events","min_ts","max_ts"]).to_csv(a.out,index=False)
print("PASS",a.shard,len(fs))
