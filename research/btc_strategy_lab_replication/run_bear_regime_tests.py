import glob,pandas as pd,numpy as np,json,os,itertools
FEE=.001
files=sorted(glob.glob("market_data_store/bitget/15m/BTCUSDT/*.csv"))
d=pd.concat([pd.read_csv(f) for f in files],ignore_index=True);d["dt"]=pd.to_datetime(d.datetime_utc,utc=True);d=d.sort_values("dt").drop_duplicates("dt").set_index("dt")
h=d.resample("1h").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("base_volume","sum")).dropna()
fund=pd.read_feather("research/btc_strategy_lab_replication/source_data/funding.feather");fund["date"]=pd.to_datetime(fund.date,utc=True);fr=fund.set_index("date").sort_index()["funding"]
h["funding"]=fr.reindex(h.index,method="ffill");h["funding3"]=h.funding.rolling(72,min_periods=24).mean();h["fpct"]=h.funding3.rolling(4320,min_periods=720).rank(pct=True)*100
def ema(s,n): return s.ewm(span=n,adjust=False,min_periods=n).mean()
h["e600"]=ema(h.close,600);h["e1200"]=ema(h.close,1200);h["e2400"]=ema(h.close,2400)
# causal regime features only
h["r30"]=h.close.pct_change(24*30);h["r7"]=h.close.pct_change(24*7)
h["slope600"]=h.e600/h.e600.shift(24*7)-1
h["vol30"]=h.close.pct_change().rolling(24*30).std()*np.sqrt(8760)
# base long signal
h["long_ent"]=(h.close>h.e600)&(h.close.shift(1)<=h.e600.shift(1))&((h.fpct<55)|h.fpct.isna())
h["long_exit"]=(h.close<h.e600*.98)&(h.close.shift(1)>=h.e600.shift(1)*.98)
def regime(row,kind):
 if kind=="price1200": return "bear" if row.close<row.e1200 else "bull"
 if kind=="price2400": return "bear" if row.close<row.e2400 else "bull"
 if kind=="ret30": return "bear" if row.r30<0 else "bull"
 if kind=="stack": return "bear" if (row.close<row.e1200 and row.slope600<0) else "bull"
 return "all"
def bt(kind="all",mode="avoid",short_rule="mirror"):
 cap=1.;pos=0;entry=0;eq=[];tr=[];side=[]
 for dt,r in h.loc[h.index>=pd.Timestamp("2020-01-01",tz="UTC")].iterrows():
  rg=regime(r,kind)
  if pos==0:
   if bool(r.long_ent) and (kind=="all" or rg!="bear"):
    pos=1;entry=r.close;cap*=1-FEE;side.append(("L",dt))
   elif mode=="short" and rg=="bear":
    # short sustained downside: price below EMA600, negative 7d/30d, funding not extremely negative
    prev=h.shift(1).loc[dt]
    trigger=(r.close<r.e600 and r.r7<0 and r.r30<0 and r.close<prev.close and (pd.isna(r.fpct) or r.fpct>10))
    if trigger:
     pos=-1;entry=r.close;cap*=1-FEE;side.append(("S",dt))
  elif pos==1 and bool(r.long_exit):
   ret=r.close/entry;cap*=ret*(1-FEE);tr.append(ret-1-2*FEE);pos=0
  elif pos==-1:
   # cover when price recovers EMA600 or 7d momentum positive
   if r.close>r.e600 or r.r7>0:
    ret=entry/r.close;cap*=ret*(1-FEE);tr.append(ret-1-2*FEE);pos=0
  mark=cap*((r.close/entry) if pos==1 else (entry/r.close if pos==-1 else 1));eq.append(mark)
 s=pd.Series(eq);dd=(s/s.cummax()-1).min()*100; rr=s.pct_change().fillna(0)
 neg=sum(x for x in tr if x<0);pf=sum(x for x in tr if x>0)/-neg if neg else 999
 return {"regime":kind,"mode":mode,"return_pct":(s.iloc[-1]-1)*100,"mdd_pct":dd,"sharpe":np.sqrt(8760)*rr.mean()/rr.std(),"pf":pf,"trades":len(tr),"long_entries":sum(x[0]=="L" for x in side),"short_entries":sum(x[0]=="S" for x in side)}
rows=[]
for k in ["all","price1200","price2400","ret30","stack"]:rows.append(bt(k,"avoid"))
for k in ["price1200","price2400","ret30","stack"]:rows.append(bt(k,"short"))
os.makedirs("research/btc_strategy_lab_replication/out",exist_ok=True)
pd.DataFrame(rows).to_csv("research/btc_strategy_lab_replication/out/regime_bear_tests.csv",index=False)
open("research/btc_strategy_lab_replication/out/regime_bear_tests.json","w").write(json.dumps(rows,indent=2));print(json.dumps(rows,indent=2))
