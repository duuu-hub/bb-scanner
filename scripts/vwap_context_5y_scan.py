import argparse,glob,os,json
import pandas as pd,numpy as np

def symbol(p): return os.path.basename(p).replace(".csv.gz","").upper()
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--data");ap.add_argument("--shard",type=int);ap.add_argument("--shards",type=int);ap.add_argument("--out");a=ap.parse_args()
 fs=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True)); fs=[p for i,p in enumerate(fs) if i%a.shards==a.shard]
 rows=[]
 for p in fs:
  d=pd.read_csv(p,usecols=["open_time","open","high","low","close","volume","quote_volume"])
  if len(d)<100: continue
  d=d.sort_values("open_time").reset_index(drop=True)
  # strictly prior-candle information: rolling 24h VWAP (96x15m), shifted 1 bar
  vol=d.volume.astype(float); q=d.quote_volume.astype(float)
  rv=vol.rolling(96,min_periods=32).sum().shift(1); rq=q.rolling(96,min_periods=32).sum().shift(1)
  vw=rq/rv
  op=d.open.astype(float); dist=(op/vw-1)*100
  # prior-only VWAP slope over 1h
  slope=(vw/vw.shift(4)-1)*100
  for h in [1,4,16,96]:
   fut=d.close.shift(-h)/op-1
   tmp=pd.DataFrame({"symbol":symbol(p),"dist":dist,"slope":slope,"ret":fut*100}).dropna()
   bins=[-np.inf,-2,-1,-.5,0,.5,1,2,np.inf]
   tmp["bin"]=pd.cut(tmp.dist,bins,right=False).astype(str)
   for b,g in tmp.groupby("bin",observed=True):
    rows.append([h,b,len(g),g.ret.mean(),g.ret.median(),(g.ret>0).mean()*100,g.slope.mean()])
 pd.DataFrame(rows,columns=["h15m","vwap_dist_bin","n","mean_ret_pct","median_ret_pct","up_pct","mean_vwap_slope_pct"]).to_csv(a.out,index=False)
 print("PASS",a.shard,len(fs),len(rows))
if __name__=="__main__":main()
