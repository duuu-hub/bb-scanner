import glob,pandas as pd,numpy as np,json,os,itertools
FEE=.001
files=sorted(glob.glob("market_data_store/bitget/15m/BTCUSDT/*.csv"))
df=pd.concat([pd.read_csv(f) for f in files],ignore_index=True)
df["dt"]=pd.to_datetime(df.datetime_utc,utc=True);df=df.sort_values("dt").drop_duplicates("dt").set_index("dt")
h=df.resample("1h").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("base_volume","sum")).dropna()
h=h.loc[h.index>=pd.Timestamp("2019-10-01",tz="UTC")].copy()
fund=pd.read_feather("research/btc_strategy_lab_replication/source_data/funding.feather");fund["date"]=pd.to_datetime(fund.date,utc=True)
fr=fund.set_index("date").sort_index()["funding"];h["funding"]=fr.reindex(h.index,method="ffill")
h["funding_3d"]=h.funding.rolling(72,min_periods=24).mean();h["funding_pct"]=h.funding_3d.rolling(4320,min_periods=720).rank(pct=True)*100
def ema(s,n):
 o=pd.Series(np.nan,index=s.index); seed=s.iloc[:n].mean();o.iloc[n-1]=seed;a=2/(n+1)
 for i in range(n,len(s)):o.iloc[i]=a*s.iloc[i]+(1-a)*o.iloc[i-1]
 return o
def bt(d,n,pct):
 e=ema(d.close,n); ex=e*.98; ent=(d.close>e)&(d.close.shift(1)<=e.shift(1));xit=(d.close<ex)&(d.close.shift(1)>=ex.shift(1))
 cap=1.;pos=False;entry=0;curve=[];tr=[]
 for dt,r in d.iterrows():
  ok=bool(ent.loc[dt]) and (pd.isna(r.funding_pct) or r.funding_pct<pct)
  if not pos and ok:pos=True;entry=r.close;cap*=1-FEE;ed=dt
  elif pos and bool(xit.loc[dt]):cap*=r.close/entry*(1-FEE);tr.append(r.close/entry-1-2*FEE);pos=False
  curve.append(cap*(r.close/entry if pos else 1))
 if pos:cap*=d.close.iloc[-1]/entry*(1-FEE);tr.append(d.close.iloc[-1]/entry-1-2*FEE)
 s=pd.Series(curve,index=d.index);dd=(s/s.cummax()-1).min();rr=s.pct_change().fillna(0);yrs=(d.index[-1]-d.index[0]).total_seconds()/(365.25*86400)
 pf=sum(x for x in tr if x>0)/-sum(x for x in tr if x<0) if any(x<0 for x in tr) else 999
 return {"ret":(s.iloc[-1]-1)*100,"cagr":(s.iloc[-1]**(1/yrs)-1)*100,"mdd":dd*100,"sharpe":np.sqrt(8760)*rr.mean()/rr.std(),"pf":pf,"trades":len(tr)}
rows=[]
for n,p in itertools.product([500,600,700],[40,50,55,60,70,80,90,100]):
 r=bt(h,n,p);r.update(ema=n,funding_pct=p);rows.append(r)
pd.DataFrame(rows).to_csv("research/btc_strategy_lab_replication/out/parameter_sweep.csv",index=False)
# walk-forward: trailing 24m choose best train Sharpe from grid, next 6m OOS; fixed exit=2%.
wins=[]; start=h.index.min()+pd.DateOffset(months=24); end=h.index.max()
while start<end:
 train0=start-pd.DateOffset(months=24); test1=min(start+pd.DateOffset(months=6),end)
 train=h.loc[(h.index>=train0)&(h.index<start)]; test=h.loc[(h.index>=start)&(h.index<test1)]
 if len(test)<100:break
 cand=[]
 for n,p in itertools.product([500,600,700],[40,50,55,60,70,80,90,100]):
  x=bt(train,n,p);cand.append((x["sharpe"],n,p,x))
 cand.sort(reverse=True,key=lambda z:z[0]);_,n,p,ins=cand[0];oos=bt(test,n,p)
 wins.append({"train_start":str(train0),"test_start":str(start),"test_end":str(test1),"ema":n,"funding_pct":p,"train_sharpe":ins["sharpe"],**{"oos_"+k:v for k,v in oos.items()}})
 start=test1
wf=pd.DataFrame(wins);wf.to_csv("research/btc_strategy_lab_replication/out/walk_forward.csv",index=False)
summary={"sweep_top_by_sharpe":pd.DataFrame(rows).sort_values("sharpe",ascending=False).head(10).to_dict("records"),"wf_windows":len(wf),"wf_profitable":int((wf.oos_ret>0).sum()),"wf_compound_return_pct":(np.prod(1+wf.oos_ret/100)-1)*100,"wf_worst_window_pct":wf.oos_ret.min(),"wf_params":wf[["ema","funding_pct"]].value_counts().rename("count").reset_index().to_dict("records")}
open("research/btc_strategy_lab_replication/out/robustness_summary.json","w").write(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
