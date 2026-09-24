import glob,pandas as pd,numpy as np,json,os
FEE=.001
files=sorted(glob.glob("market_data_store/bitget/15m/BTCUSDT/*.csv"))
d=pd.concat([pd.read_csv(f) for f in files],ignore_index=True);d["dt"]=pd.to_datetime(d.datetime_utc,utc=True);d=d.sort_values("dt").drop_duplicates("dt").set_index("dt")
h=d.resample("1h").agg(close=("close","last")).dropna()
fund=pd.read_feather("research/btc_strategy_lab_replication/source_data/funding.feather");fund["date"]=pd.to_datetime(fund.date,utc=True);fr=fund.set_index("date").sort_index()["funding"]
h["funding"]=fr.reindex(h.index,method="ffill");h["f3"]=h.funding.rolling(72,min_periods=24).mean();h["fpct"]=h.f3.rolling(4320,min_periods=720).rank(pct=True)*100
s=h.close;n=600;e=pd.Series(np.nan,index=s.index);e.iloc[n-1]=s.iloc[:n].mean();alpha=2/(n+1)
for i in range(n,len(s)):e.iloc[i]=alpha*s.iloc[i]+(1-alpha)*e.iloc[i-1]
h["ema"]=e
ent=(h.close>h.ema)&(h.close.shift(1)<=h.ema.shift(1))&((h.fpct<55)|h.fpct.isna())
ex=(h.close<h.ema*.98)&(h.close.shift(1)>=h.ema.shift(1)*.98)
x=h.loc[h.index>=pd.Timestamp("2019-10-01",tz="UTC")]
cap=1.;pos=False;entry=0.;eq=[]
for dt,r in x.iterrows():
 if not pos and bool(ent.loc[dt]):pos=True;entry=r.close;cap*=1-FEE
 elif pos and bool(ex.loc[dt]):cap*=r.close/entry*(1-FEE);pos=False
 mark=cap*(r.close/entry if pos else 1);eq.append((dt,mark,pos))
q=pd.DataFrame(eq,columns=["dt","equity","pos"]).set_index("dt")
rows=[]
for p,g in q.groupby(q.index.to_period("Q")):
 ret=(g.equity.iloc[-1]/g.equity.iloc[0]-1)*100
 exposure=g.pos.mean()*100
 rows.append({"quarter":str(p),"return_pct":ret,"exposure_pct":exposure,"flat_pct":100-exposure})
out=pd.DataFrame(rows)
os.makedirs("research/btc_strategy_lab_replication/out",exist_ok=True)
out.to_csv("research/btc_strategy_lab_replication/out/core_quarterly.csv",index=False)
open("research/btc_strategy_lab_replication/out/core_quarterly.json","w").write(out.to_json(orient="records",indent=2))
print(out.to_string(index=False))
