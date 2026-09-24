import glob, pandas as pd, numpy as np, json, os
FEE=.001
files=sorted(glob.glob("market_data_store/bitget/15m/BTCUSDT/*.csv"))
if not files: raise SystemExit("BTC data missing")
df=pd.concat([pd.read_csv(f) for f in files],ignore_index=True)
df["dt"]=pd.to_datetime(df["datetime_utc"],utc=True)
df=df.sort_values("dt").drop_duplicates("dt").set_index("dt")
h=df.resample("1h").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last")).dropna()
h=h.loc[h.index>=pd.Timestamp("2019-10-01",tz="UTC")].copy()
h["ema"]=h.close.ewm(span=600,adjust=False).mean()
h["cross"]=(h.close>h.ema)&(h.close.shift(1)<=h.ema.shift(1))
equity=1.; pos=False; entry=None; trades=[]; eq=[]
for dt,r in h.iterrows():
    if not pos and r.cross:
        pos=True; entry=r.close; equity*=1-FEE; entdt=dt
    elif pos and r.close < r.ema*.98:
        ret=r.close/entry-1
        equity*=r.close/entry*(1-FEE)
        trades.append((entdt,dt,entry,r.close,ret-2*FEE)); pos=False
    eq.append(equity*(r.close/entry if pos else 1))
if pos:
    r=h.iloc[-1]; equity*=r.close/entry*(1-FEE); trades.append((entdt,h.index[-1],entry,r.close,r.close/entry-1-2*FEE))
ser=pd.Series(eq,index=h.index)
dd=ser/ser.cummax()-1
rets=ser.pct_change().fillna(0)
years=(h.index[-1]-h.index[0]).total_seconds()/(365.25*86400)
cagr=ser.iloc[-1]**(1/years)-1
sharpe=np.sqrt(365*24)*rets.mean()/rets.std() if rets.std() else np.nan
td=pd.DataFrame(trades,columns=["entry_time","exit_time","entry","exit","net_trade_return"])
wins=td.net_trade_return[td.net_trade_return>0].sum(); losses=-td.net_trade_return[td.net_trade_return<0].sum()
pf=wins/losses if losses else np.inf
annual=ser.resample("YE").last().pct_change()
annual.iloc[0]=ser.resample("YE").last().iloc[0]-1
out={"start":str(h.index[0]),"end":str(h.index[-1]),"bars":len(h),"trades":len(td),"total_return_pct":(ser.iloc[-1]-1)*100,"CAGR_pct":cagr*100,"max_drawdown_pct":dd.min()*100,"sharpe_hourly_annualized":sharpe,"profit_factor_trade_returns":pf,"annual_returns_pct":{str(k.year):v*100 for k,v in annual.items()}}
os.makedirs("research/btc_strategy_lab_replication/out",exist_ok=True)
open("research/btc_strategy_lab_replication/out/baseline_metrics.json","w").write(json.dumps(out,indent=2))
td.to_csv("research/btc_strategy_lab_replication/out/baseline_trades.csv",index=False)
print(json.dumps(out,indent=2))
