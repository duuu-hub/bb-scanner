import io, zipfile, requests, pandas as pd, numpy as np
from datetime import datetime
BASE="https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/4h"
frames=[]
for p in pd.period_range("2021-09","2026-08",freq="M"):
    u=f"{BASE}/BTCUSDT-4h-{p}.zip"
    x=requests.get(u,timeout=30)
    if x.status_code!=200: continue
    z=zipfile.ZipFile(io.BytesIO(x.content))
    d=pd.read_csv(z.open(z.namelist()[0]),header=None)
    d=d.iloc[:,:6]; d.columns=["ts","open","high","low","close","volume"]
    frames.append(d)
df=pd.concat(frames,ignore_index=True)
for c in ["open","high","low","close"]: df[c]=pd.to_numeric(df[c])
df["time"]=pd.to_datetime(df.ts,unit="ms")
hi,lo,cl=df.high,df.low,df.close
ten=(hi.rolling(9).max()+lo.rolling(9).min())/2
kij=(hi.rolling(26).max()+lo.rolling(26).min())/2
spa=((ten+kij)/2).shift(26); spb=((hi.rolling(52).max()+lo.rolling(52).min())/2).shift(26)
# Chikou condition expressed without future lookahead: current close > close 26 bars ago.
base=(cl>pd.concat([spa,spb],axis=1).max(axis=1))&(ten>kij)&(spa>spb)&(cl>cl.shift(26))
# Standard iterative PSAR, using only current/past OHLC.
ps=np.full(len(df),np.nan); trend=np.ones(len(df),dtype=int); af=.02; ep=hi.iloc[0]; ps[0]=lo.iloc[0]
for i in range(1,len(df)):
    prev=ps[i-1]; up=trend[i-1]==1
    cur=prev+af*(ep-prev)
    if up:
        cur=min(cur,lo.iloc[i-1],lo.iloc[i-2] if i>1 else lo.iloc[i-1])
        if lo.iloc[i]<cur: trend[i]=-1; cur=ep; ep=lo.iloc[i]; af=.02
        else:
            trend[i]=1
            if hi.iloc[i]>ep: ep=hi.iloc[i]; af=min(.2,af+.02)
    else:
        cur=max(cur,hi.iloc[i-1],hi.iloc[i-2] if i>1 else hi.iloc[i-1])
        if hi.iloc[i]>cur: trend[i]=1; cur=ep; ep=hi.iloc[i]; af=.02
        else:
            trend[i]=-1
            if lo.iloc[i]<ep: ep=lo.iloc[i]; af=min(.2,af+.02)
    ps[i]=cur
def bt(use_psar):
    eq=1.; peak=1.; mdd=0.; pos=False; entry=0.; trades=[]; curve=[]
    fee=.001; slip=.0003
    for i in range(52,len(df)):
        buy=bool(base.iloc[i]) and (not use_psar or trend[i]==1)
        sell=bool(ten.iloc[i]<kij.iloc[i]) and (not use_psar or trend[i]==-1)
        if pos and sell:
            xp=cl.iloc[i]*(1-slip); r=xp/entry*(1-fee)-1
            eq*=1+r; trades.append(r); pos=False
        if not pos and buy:
            entry=cl.iloc[i]*(1+slip)/(1-fee); pos=True
        mark=eq*(cl.iloc[i]/entry if pos else 1)
        peak=max(peak,mark); mdd=min(mdd,mark/peak-1); curve.append(mark)
    if pos:
        xp=cl.iloc[-1]*(1-slip); r=xp/entry*(1-fee)-1; eq*=1+r; trades.append(r)
    years=(df.time.iloc[-1]-df.time.iloc[0]).days/365.25
    arr=np.array(trades)
    return dict(trades=len(arr),win_rate=float((arr>0).mean()*100),return_pct=(eq-1)*100,cagr_pct=(eq**(1/years)-1)*100,mdd_pct=mdd*100,pf=float(arr[arr>0].sum()/-arr[arr<0].sum()) if (arr<0).any() else None)
print("DATA",df.time.iloc[0],df.time.iloc[-1],len(df))
print("NO_PSAR",bt(False))
print("WITH_PSAR",bt(True))
