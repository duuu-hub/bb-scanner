import glob,pandas as pd,numpy as np,json,os
FEE=.001
files=sorted(glob.glob("market_data_store/bitget/15m/BTCUSDT/*.csv"))
d=pd.concat([pd.read_csv(f) for f in files],ignore_index=True);d["dt"]=pd.to_datetime(d.datetime_utc,utc=True);d=d.sort_values("dt").drop_duplicates("dt").set_index("dt")
h=d.resample("1h").agg(close=("close","last")).dropna()
fund=pd.read_feather("research/btc_strategy_lab_replication/source_data/funding.feather");fund["date"]=pd.to_datetime(fund.date,utc=True);fr=fund.set_index("date").sort_index()["funding"]
h["funding"]=fr.reindex(h.index,method="ffill");h["f3"]=h.funding.rolling(72,min_periods=24).mean();h["fpct"]=h.f3.rolling(4320,min_periods=720).rank(pct=True)*100
# TA-Lib style EMA
s=h.close;n=600;o=pd.Series(np.nan,index=s.index);o.iloc[n-1]=s.iloc[:n].mean();a=2/(n+1)
for i in range(n,len(s)):o.iloc[i]=a*s.iloc[i]+(1-a)*o.iloc[i-1]
h["ema"]=o
ent=(h.close>h.ema)&(h.close.shift(1)<=h.ema.shift(1))&((h.fpct<55)|h.fpct.isna())
ex=(h.close<h.ema*.98)&(h.close.shift(1)>=h.ema.shift(1)*.98)
x=h.loc[h.index>=pd.Timestamp("2019-10-01",tz="UTC")]
pos=False;last_exit=x.index[0];gaps=[];entries=[]
for dt,r in x.iterrows():
 if not pos and bool(ent.loc[dt]):
  gaps.append((last_exit,dt,(dt-last_exit).total_seconds()/86400));pos=True;entries.append(dt)
 elif pos and bool(ex.loc[dt]):
  pos=False;last_exit=dt
if not pos:gaps.append((last_exit,x.index[-1],(x.index[-1]-last_exit).total_seconds()/86400))
gaps=sorted(gaps,key=lambda z:z[2],reverse=True)
out=[{"start":str(a),"end":str(b),"days":round(c,1)} for a,b,c in gaps if c>=14]
os.makedirs("research/btc_strategy_lab_replication/out",exist_ok=True);open("research/btc_strategy_lab_replication/out/flat_gaps_14d.json","w").write(json.dumps(out,indent=2))
print(json.dumps(out[:30],indent=2))
