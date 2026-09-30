import argparse,glob,os
import pandas as pd,numpy as np
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data");ap.add_argument("--shard",type=int);ap.add_argument("--shards",type=int);ap.add_argument("--out");a=ap.parse_args()
 fs=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));fs=[p for i,p in enumerate(fs) if i%a.shards==a.shard]
 rows=[]; edges=[-np.inf]+list(range(-20,21))+[np.inf]
 labels=["<-20"]+[f"{i}~{i+1}" for i in range(-20,20)]+[">=20"]
 for p in fs:
  d=pd.read_csv(p,usecols=["open_time","open","close","volume","quote_volume"]).sort_values("open_time")
  if len(d)<100: continue
  v=d.volume.astype(float);q=d.quote_volume.astype(float)
  vw=(q.rolling(96,min_periods=32).sum()/v.rolling(96,min_periods=32).sum()).shift(1)
  op=d.open.astype(float);dist=(op/vw-1)*100
  bucket=pd.cut(dist,edges,labels=labels,right=False)
  for h in [4,16,96]:
   ret=(d.close.shift(-h)/op-1)*100
   x=pd.DataFrame({"b":bucket,"r":ret}).dropna()
   g=x.groupby("b",observed=True).r.agg(["count","mean","median"])
   up=x.assign(up=x.r>0).groupby("b",observed=True).up.mean()*100
   for b,z in g.iterrows(): rows.append([h,str(b),int(z["count"]),z["mean"],z["median"],up.loc[b]])
 pd.DataFrame(rows,columns=["h15m","vwap_dist_pct","n","mean_ret_pct","median_ret_pct","up_pct"]).to_csv(a.out,index=False)
 print("PASS",a.shard,len(fs),len(rows))
if __name__=="__main__":main()
