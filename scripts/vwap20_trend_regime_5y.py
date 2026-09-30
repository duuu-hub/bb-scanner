import argparse,glob,pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--data");ap.add_argument("--shard",type=int);ap.add_argument("--shards",type=int);ap.add_argument("--out");a=ap.parse_args()
fs=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));fs=[p for i,p in enumerate(fs) if i%a.shards==a.shard];rows=[]
for p in fs:
 d=pd.read_csv(p,usecols=["open_time","open","high","low","close","volume","quote_volume"]).sort_values("open_time").reset_index(drop=True)
 if len(d)<800:continue
 v=d.volume.astype(float);q=d.quote_volume.astype(float);vw=(q.rolling(96,min_periods=96).sum()/v.rolling(96,min_periods=96).sum()).shift(1);dist=(d.open.astype(float)/vw-1)*100
 # market regime: BTC-like symbol's own medium trend proxy, prior-only 7d return; no future info
 # use each symbol's prior 7d return to classify local market trend
 r7=(d.open/d.open.shift(672)-1)*100
 regime=pd.Series(np.where(r7>=5,"UP",np.where(r7<=-5,"DOWN","SIDE")),index=d.index)
 for side,mask,sgn in [("LONG",dist<=-20,1),("SHORT",dist>=20,-1)]:
  ev=np.where((mask & ~mask.shift(1,fill_value=False)).fillna(False))[0]
  for i in ev:
   if i<672 or i+3>=len(d):continue
   ep=float(d.open.iloc[i]);g=None
   for j in range(i,i+4):
    h=float(d.high.iloc[j]);l=float(d.low.iloc[j])
    if side=="LONG":t=h>=ep*1.08;s=l<=ep*.99
    else:t=l<=ep*.92;s=h>=ep*1.01
    # 15m ambiguous => conservative loss in 5Y coarse chronology; count separately
    if t and s:g=-1;break
    if t:g=8;break
    if s:g=-1;break
   if g is None:g=sgn*(float(d.close.iloc[i+3])/ep-1)*100
   rows.append([side,regime.iloc[i],float(r7.iloc[i]),g])
pd.DataFrame(rows,columns=["side","regime","ret7d_pct","gross"]).to_csv(a.out,index=False);print("PASS",a.shard,len(fs),len(rows))
