import glob,pandas as pd,numpy as np,json,os
FEE=.001
files=sorted(glob.glob("market_data_store/bitget/15m/BTCUSDT/*.csv"))
df=pd.concat([pd.read_csv(f) for f in files],ignore_index=True)
df["dt"]=pd.to_datetime(df.datetime_utc,utc=True); df=df.sort_values("dt").drop_duplicates("dt").set_index("dt")
h=df.resample("1h").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("base_volume","sum")).dropna()
h=h.loc[h.index>=pd.Timestamp("2019-10-01",tz="UTC")].copy()
# TA-Lib EMA convention: SMA seed then recursive EMA.
def talib_ema(s,n):
    out=pd.Series(np.nan,index=s.index,dtype=float)
    if len(s)<n:return out
    seed=s.iloc[:n].mean(); out.iloc[n-1]=seed; a=2/(n+1)
    for i in range(n,len(s)): out.iloc[i]=a*s.iloc[i]+(1-a)*out.iloc[i-1]
    return out
h["ema"]=talib_ema(h.close,600); h["ema_exit"]=h.ema*.98
fund=pd.read_feather("research/btc_strategy_lab_replication/source_data/funding.feather")
fund["date"]=pd.to_datetime(fund.date,utc=True); fr=fund.set_index("date").sort_index()["funding"]
h["funding"]=fr.reindex(h.index,method="ffill")
h["funding_3d"]=h.funding.rolling(72,min_periods=24).mean()
h["funding_pct"]=h.funding_3d.rolling(24*180,min_periods=24*30).rank(pct=True)*100
cross=(h.close>h.ema)&(h.close.shift(1)<=h.ema.shift(1))
exitcross=(h.close<h.ema_exit)&(h.close.shift(1)>=h.ema_exit.shift(1))
def run(use_filter):
    equity=1.;pos=False;entry=None;tr=[];eq=[]
    for dt,r in h.iterrows():
        enter=bool(cross.loc[dt]) and (not use_filter or pd.isna(r.funding_pct) or r.funding_pct<55)
        if not pos and enter:
            pos=True;entry=r.close;entdt=dt;equity*=1-FEE
        elif pos and bool(exitcross.loc[dt]):
            equity*=r.close/entry*(1-FEE);tr.append((entdt,dt,entry,r.close,r.close/entry-1-2*FEE,r.funding_pct));pos=False
        eq.append(equity*(r.close/entry if pos else 1))
    if pos:
        r=h.iloc[-1];equity*=r.close/entry*(1-FEE);tr.append((entdt,h.index[-1],entry,r.close,r.close/entry-1-2*FEE,r.funding_pct))
    s=pd.Series(eq,index=h.index); dd=s/s.cummax()-1; rr=s.pct_change().fillna(0); years=(h.index[-1]-h.index[0]).total_seconds()/(365.25*86400)
    t=pd.DataFrame(tr,columns=["entry_time","exit_time","entry","exit","net_trade_return","exit_funding_pct"])
    pf=t.loc[t.net_trade_return>0,"net_trade_return"].sum()/-t.loc[t.net_trade_return<0,"net_trade_return"].sum()
    ann=s.resample("YE").last().pct_change(); ann.iloc[0]=s.resample("YE").last().iloc[0]-1
    return {"trades":len(t),"total_return_pct":(s.iloc[-1]-1)*100,"CAGR_pct":(s.iloc[-1]**(1/years)-1)*100,"max_drawdown_pct":dd.min()*100,"sharpe":np.sqrt(365*24)*rr.mean()/rr.std(),"profit_factor":pf,"annual_returns_pct":{str(k.year):v*100 for k,v in ann.items()}},t
base,t0=run(False); filt,t1=run(True)
out={"data_start":str(h.index[0]),"data_end":str(h.index[-1]),"funding_start":str(fr.index.min()),"funding_end":str(fr.index.max()),"baseline_talib_semantics":base,"ema600_funding55":filt}
os.makedirs("research/btc_strategy_lab_replication/out",exist_ok=True)
open("research/btc_strategy_lab_replication/out/funding_metrics.json","w").write(json.dumps(out,indent=2))
t1.to_csv("research/btc_strategy_lab_replication/out/funding_trades.csv",index=False)
print(json.dumps(out,indent=2))
