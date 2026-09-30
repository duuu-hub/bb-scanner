import glob,pandas as pd,numpy as np
fs=sorted(glob.glob("market_data_store/bitget/universe_15m/2026/09/2026-09-*.csv.gz"))
fs=[f for f in fs if "2026-09-22"<=f.split("/")[-1][:10]<="2026-09-29"]
d=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
d=d.sort_values(["symbol","timestamp_ms"]); rows=[]
for s,x in d.groupby("symbol"):
 x=x.reset_index(drop=True)
 if len(x)<100: continue
 v=x.base_volume.astype(float);q=x.quote_volume.astype(float)
 vw=(q.rolling(96,min_periods=96).sum()/v.rolling(96,min_periods=96).sum()).shift(1)
 dist=(x.open.astype(float)/vw-1)*100
 for side,mask,sgn in [("LONG",dist<=-20,1),("SHORT",dist>=20,-1)]:
  ev=np.where((mask & ~mask.shift(1,fill_value=False)).fillna(False))[0]
  for i in ev:
   if i+4>=len(x):continue
   ep=float(x.open.iloc[i]);hh=x.high.iloc[i+1:i+5].to_numpy(float);ll=x.low.iloc[i+1:i+5].to_numpy(float)
   if side=="LONG": ht=np.where(hh>=ep*1.08)[0];hs=np.where(ll<=ep*.99)[0]
   else: ht=np.where(ll<=ep*.92)[0];hs=np.where(hh>=ep*1.01)[0]
   it=ht[0] if len(ht) else 999;isl=hs[0] if len(hs) else 999
   if it==isl and it<999:g=-1
   elif it<isl:g=8
   elif isl<it:g=-1
   else:g=sgn*(float(x.close.iloc[i+4])/ep-1)*100
   rows.append([s,side,int(x.timestamp_ms.iloc[i]),float(dist.iloc[i]),g])
o=pd.DataFrame(rows,columns=["symbol","side","ts","dist","gross"]);o.to_csv("stored_bitget_vwap20_events.csv",index=False)
def pf(y):
 p=y[y>0].sum();n=-y[y<0].sum();return p/n if n else np.inf
r=[]
for side in ["LONG","SHORT","ALL"]:
 z=o.gross if side=="ALL" else o.loc[o.side==side,"gross"]
 for c in [0,.2,.4]:
  y=z-c;r.append([side,c,len(y),(y>0).mean()*100 if len(y) else np.nan,y.mean() if len(y) else np.nan,pf(y) if len(y) else np.nan])
pd.DataFrame(r,columns=["side","cost_pct","n","wr","mean","pf"]).to_csv("stored_bitget_vwap20_summary.csv",index=False)
print(pd.DataFrame(r,columns=["side","cost_pct","n","wr","mean","pf"]).to_string(index=False))
