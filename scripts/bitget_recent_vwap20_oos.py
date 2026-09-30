import requests,time,pandas as pd,numpy as np
BASE="https://api.bitget.com"
cs=requests.get(BASE+"/api/v2/mix/market/contracts",params={"productType":"USDT-FUTURES"},timeout=30).json()["data"]
syms=[x["symbol"] for x in cs if x.get("symbol","").endswith("USDT")]
rows=[]
for k,s in enumerate(syms):
 try:
  z=requests.get(BASE+"/api/v2/mix/market/candles",params={"symbol":s,"productType":"USDT-FUTURES","granularity":"15m","limit":"1000"},timeout=30).json()
  if z.get("code")!="00000" or len(z.get("data",[]))<120: continue
  d=pd.DataFrame(z["data"],columns=["ts","open","high","low","close","vol","qvol"]).astype(float).sort_values("ts").reset_index(drop=True)
  vw=(d.qvol.rolling(96,min_periods=96).sum()/d.vol.rolling(96,min_periods=96).sum()).shift(1)
  dist=(d.open/vw-1)*100
  for side,mask,sgn in [("LONG",dist<=-20,1),("SHORT",dist>=20,-1)]:
   ev=np.where((mask & ~mask.shift(1,fill_value=False)).fillna(False))[0]
   for i in ev:
    if i+4>=len(d): continue
    ep=d.open.iloc[i]; hh=d.high.iloc[i+1:i+5].to_numpy(); ll=d.low.iloc[i+1:i+5].to_numpy()
    if side=="LONG": ht=np.where(hh>=ep*1.08)[0]; hs=np.where(ll<=ep*.99)[0]
    else: ht=np.where(ll<=ep*.92)[0]; hs=np.where(hh>=ep*1.01)[0]
    it=ht[0] if len(ht) else 999; is_=hs[0] if len(hs) else 999
    if it==is_ and it<999:g=-1
    elif it<is_:g=8
    elif is_<it:g=-1
    else:g=sgn*(d.close.iloc[i+4]/ep-1)*100
    rows.append([s,side,int(d.ts.iloc[i]),dist.iloc[i],g])
 except Exception as e: print("ERR",s,e)
 time.sleep(.06)
o=pd.DataFrame(rows,columns=["symbol","side","ts","dist","gross"])
o.to_csv("bitget_vwap20_recent_events.csv",index=False)
def pf(x):
 p=x[x>0].sum();n=-x[x<0].sum();return p/n if n else np.inf
out=[]
for side in ["LONG","SHORT","ALL"]:
 x=o.gross if side=="ALL" else o.loc[o.side==side,"gross"]
 for c in [0,.2,.4]:
  y=x-c
  out.append([side,c,len(y),(y>0).mean()*100 if len(y) else np.nan,y.mean() if len(y) else np.nan,pf(y) if len(y) else np.nan])
pd.DataFrame(out,columns=["side","cost_pct","n","wr","mean","pf"]).to_csv("bitget_vwap20_recent_summary.csv",index=False)
print(pd.read_csv("bitget_vwap20_recent_summary.csv").to_string(index=False))
