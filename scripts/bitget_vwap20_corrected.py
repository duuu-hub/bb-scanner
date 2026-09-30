import requests,time,glob,pandas as pd,numpy as np
BASE="https://api.bitget.com"; NOW=int(time.time()*1000); BAR=900000
def contracts():
 cs=requests.get(BASE+"/api/v2/mix/market/contracts",params={"productType":"USDT-FUTURES"},timeout=30).json()["data"]
 return [x["symbol"] for x in cs if x.get("symbol","").endswith("USDT") and x.get("symbolType")=="perpetual" and x.get("symbolStatus")=="normal" and str(x.get("quoteCoin","")).upper()=="USDT" and str(x.get("isRwa","NO")).upper()!="YES"]
def resolve1m(sym,ts,ep,side):
 try:
  z=requests.get(BASE+"/api/v2/mix/market/history-candles",params={"symbol":sym,"productType":"USDT-FUTURES","granularity":"1m","startTime":int(ts),"endTime":int(ts+BAR-1),"limit":100},timeout=20).json()
  if z.get("code")!="00000": return -1
  a=sorted(z.get("data",[]),key=lambda r:int(r[0]))
  for r in a:
   h=float(r[2]);l=float(r[3])
   if side=="LONG": t=h>=ep*1.08;s=l<=ep*.99
   else: t=l<=ep*.92;s=h>=ep*1.01
   if t and s:return -1
   if t:return 8
   if s:return -1
 except: pass
 return -1
def calc(x,sym,source):
 x=x.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
 # split contiguous segments so rolling never bridges gaps
 seg=(x.ts.diff().fillna(BAR)!=BAR).cumsum(); out=[]
 for _,d in x.groupby(seg):
  d=d.reset_index(drop=True)
  if len(d)<101: continue
  vw=(d.qvol.rolling(96,min_periods=96).sum()/d.vol.rolling(96,min_periods=96).sum()).shift(1)
  dist=(d.open/vw-1)*100
  for side,mask,sgn in [("LONG",dist<=-20,1),("SHORT",dist>=20,-1)]:
   for i in np.where((mask & ~mask.shift(1,fill_value=False)).fillna(False))[0]:
    if i+3>=len(d): continue
    ep=float(d.open.iloc[i]); hs=[]
    for j in range(i,i+4): # exact 1h: entry bar + next 3
     h=float(d.high.iloc[j]);l=float(d.low.iloc[j])
     if side=="LONG": t=h>=ep*1.08;s=l<=ep*.99
     else: t=l<=ep*.92;s=h>=ep*1.01
     if t and s:
      g=resolve1m(sym,int(d.ts.iloc[j]),ep,side);hs=[g];break
     if t: hs=[8];break
     if s: hs=[-1];break
    g=hs[0] if hs else sgn*(float(d.close.iloc[i+3])/ep-1)*100
    out.append([source,sym,side,int(d.ts.iloc[i]),float(dist.iloc[i]),g])
 return out
syms=contracts(); print("UNIVERSE",len(syms)); rows=[]
# API recent: only fully closed candles
for k,sym in enumerate(syms):
 try:
  z=requests.get(BASE+"/api/v2/mix/market/candles",params={"symbol":sym,"productType":"USDT-FUTURES","granularity":"15m","limit":"1000"},timeout=30).json()
  if z.get("code")!="00000":continue
  a=z.get("data",[])
  if len(a)<120:continue
  d=pd.DataFrame(a,columns=["ts","open","high","low","close","vol","qvol"]).astype(float)
  d=d[d.ts+BAR<=NOW]
  rows+=calc(d,sym,"API")
 except Exception as e: print("ERR",sym,e)
 time.sleep(.04)
# Stored immutable data
fs=sorted(glob.glob("market_data_store/bitget/universe_15m/2026/09/2026-09-*.csv.gz"))
fs=[f for f in fs if "2026-09-22"<=f.split("/")[-1][:10]<="2026-09-29"]
sd=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
for sym,x in sd.groupby("symbol"):
 d=pd.DataFrame({"ts":x.timestamp_ms.astype(float),"open":x.open.astype(float),"high":x.high.astype(float),"low":x.low.astype(float),"close":x.close.astype(float),"vol":x.base_volume.astype(float),"qvol":x.quote_volume.astype(float)})
 rows+=calc(d,sym,"STORED")
o=pd.DataFrame(rows,columns=["source","symbol","side","ts","dist","gross"]);o.to_csv("bitget_vwap20_corrected_events.csv",index=False)
def pf(y):
 p=y[y>0].sum();n=-y[y<0].sum();return p/n if n else np.inf
r=[]
for src in ["API","STORED"]:
 for side in ["LONG","SHORT","ALL"]:
  z=o[o.source.eq(src)].gross if side=="ALL" else o[o.source.eq(src)&o.side.eq(side)].gross
  for c in [0,.2,.4]:
   y=z-c;r.append([src,side,c,len(y),(y>0).mean()*100 if len(y) else np.nan,y.mean() if len(y) else np.nan,pf(y) if len(y) else np.nan])
q=pd.DataFrame(r,columns=["source","side","cost_pct","n","wr","mean","pf"]);q.to_csv("bitget_vwap20_corrected_summary.csv",index=False);print(q.to_string(index=False))
