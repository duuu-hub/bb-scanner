import glob,pandas as pd,numpy as np,json,os
FEE=.001
files=sorted(glob.glob("market_data_store/bitget/15m/BTCUSDT/*.csv"))
df=pd.concat([pd.read_csv(f) for f in files],ignore_index=True);df["dt"]=pd.to_datetime(df.datetime_utc,utc=True);df=df.sort_values("dt").drop_duplicates("dt").set_index("dt")
h=df.resample("1h").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("base_volume","sum")).dropna()
fund=pd.read_feather("research/btc_strategy_lab_replication/source_data/funding.feather");fund["date"]=pd.to_datetime(fund.date,utc=True);fr=fund.set_index("date").sort_index()["funding"]
h["funding"]=fr.reindex(h.index,method="ffill");h["funding_3d"]=h.funding.rolling(72,min_periods=24).mean();h["funding_pct"]=h.funding_3d.rolling(4320,min_periods=720).rank(pct=True)*100
def ema(s,n):
 o=pd.Series(np.nan,index=s.index,dtype=float)
 if len(s)<n:return o
 o.iloc[n-1]=s.iloc[:n].mean();a=2/(n+1)
 for i in range(n,len(s)):o.iloc[i]=a*s.iloc[i]+(1-a)*o.iloc[i-1]
 return o
def run_window(d,n,pct):
 # compute EMA using full-history values already available by slicing signals from global h
 e=ema(h.close,n);ex=e*.98;ent=(h.close>e)&(h.close.shift(1)<=e.shift(1));xit=(h.close<ex)&(h.close.shift(1)>=ex.shift(1))
 cap=1.;pos=False;entry=0;eq=[];tr=[]
 for dt,r in d.iterrows():
  ok=bool(ent.loc[dt]) and (pd.isna(r.funding_pct) or r.funding_pct<pct)
  if not pos and ok:pos=True;entry=r.close;cap*=1-FEE
  elif pos and bool(xit.loc[dt]):cap*=r.close/entry*(1-FEE);tr.append(r.close/entry-1-2*FEE);pos=False
  eq.append(cap*(r.close/entry if pos else 1))
 if pos:cap*=d.close.iloc[-1]/entry*(1-FEE);tr.append(d.close.iloc[-1]/entry-1-2*FEE);eq[-1]=cap
 s=pd.Series(eq);dd=(s/s.cummax()-1).min()*100
 return {"trades":len(tr),"total_return_pct":(s.iloc[-1]-1)*100,"max_drawdown_pct":dd},tr

# fixed EMA600 + Funding55 six-month independent windows
windows=[]
start=pd.Timestamp("2020-01-01",tz="UTC")
end=h.index.max()
while start<end:
    stop=min(start+pd.DateOffset(months=6),end)
    d=h.loc[(h.index>=start)&(h.index<stop)].copy()
    if len(d)>600:
        m,_=run_window(d,600,55)
        windows.append({"start":str(start),"end":str(stop),**m})
    start=stop
wdf=pd.DataFrame(windows)
wdf.to_csv("research/btc_strategy_lab_replication/out/fixed_ema600_f55_6m.csv",index=False)
summary={"windows":len(wdf),"profitable":int((wdf.total_return_pct>0).sum()),"compound_return_pct":float(np.prod(1+wdf.total_return_pct/100)-1)*100,"median_window_pct":float(wdf.total_return_pct.median()),"worst_window_pct":float(wdf.total_return_pct.min()),"best_window_pct":float(wdf.total_return_pct.max()),"total_trades":int(wdf.trades.sum()),"rows":wdf.to_dict("records")}
open("research/btc_strategy_lab_replication/out/fixed_ema600_f55_6m_summary.json","w").write(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
