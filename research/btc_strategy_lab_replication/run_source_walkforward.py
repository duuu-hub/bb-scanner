import glob,pandas as pd,numpy as np,json,os,itertools
FEE=.001
files=sorted(glob.glob("market_data_store/bitget/15m/BTCUSDT/*.csv"))
df=pd.concat([pd.read_csv(f) for f in files],ignore_index=True);df["dt"]=pd.to_datetime(df.datetime_utc,utc=True)
df=df.sort_values("dt").drop_duplicates("dt").set_index("dt")
h=df.resample("1h").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("base_volume","sum")).dropna()
def ema(s,n):
 o=pd.Series(np.nan,index=s.index,dtype=float)
 if len(s)<n:return o
 o.iloc[n-1]=s.iloc[:n].mean();a=2/(n+1)
 for i in range(n,len(s)):o.iloc[i]=a*s.iloc[i]+(1-a)*o.iloc[i-1]
 return o
def bt(a,b,n,thr):
 # warmup before requested timerange, approximating freqtrade startup candles
 x=h.loc[(h.index>=a-pd.Timedelta(hours=1300))&(h.index<b)].copy(); e=ema(x.close,n); ex=e*(1-thr/100)
 ent=(x.close>e)&(x.close.shift(1)<=e.shift(1));xit=(x.close<ex)&(x.close.shift(1)>=ex.shift(1))
 cap=1.;pos=False;entry=0;curve=[];tr=0
 for dt,r in x.loc[x.index>=a].iterrows():
  if not pos and bool(ent.loc[dt]):pos=True;entry=r.close;cap*=1-FEE
  elif pos and bool(xit.loc[dt]):cap*=r.close/entry*(1-FEE);pos=False;tr+=1
  curve.append(cap*(r.close/entry if pos else 1))
 if not curve:return -999,100,0
 if pos:cap*=x.loc[x.index<b].close.iloc[-1]/entry*(1-FEE);tr+=1;curve[-1]=cap
 s=pd.Series(curve);dd=(s/s.cummax()-1).min()*-100
 return (s.iloc[-1]-1)*100,dd,tr
def calmar(p,dd):
 if p<=-99:return -99
 ann=(1+p/100)**.5-1
 return ann/(dd/100) if dd>1 else ann
GRID=list(itertools.product([400,500,600,700,800,1000,1200],[1.,2.,3.]))
START=pd.Timestamp("2018-01-01",tz="UTC");END=pd.Timestamp("2026-08-01",tz="UTC")
# Bitget history starts 2019-07, so exact source 2018 start cannot be run. Start first fold whose 24m train is fully covered.
DATA0=h.index.min(); rows=[]; t=START
while True:
 trb=t+pd.DateOffset(months=24);teb=trb+pd.DateOffset(months=6)
 if teb>END:break
 if t>=DATA0:
  best=None
  for n,thr in GRID:
   p,dd,tr=bt(t,trb,n,thr);s=calmar(p,dd)
   if best is None or s>best[0]:best=(s,n,thr,p,dd)
  _,n,thr,ip,idd=best;p,dd,tr=bt(trb,teb,n,thr)
  rows.append({"train_start":str(t),"test_start":str(trb),"test_end":str(teb),"ema":n,"exit_threshold":thr,"train_profit":ip,"train_dd":-idd,"oos_profit":p,"oos_dd":-dd,"trades":tr})
 t+=pd.DateOffset(months=6)
out=pd.DataFrame(rows);os.makedirs("research/btc_strategy_lab_replication/out",exist_ok=True);out.to_csv("research/btc_strategy_lab_replication/out/walk_forward_source_logic.csv",index=False)
summary={"note":"Source walk_forward_fast.py logic: EmaCross WITHOUT funding; grid EMA 400,500,600,700,800,1000,1200 x exit 1,2,3; select by 24m Calmar; test next 6m. Bitget data starts 2019-07 so source 2018 folds unavailable.","windows":len(out),"profitable":int((out.oos_profit>0).sum()),"compound_oos_pct":(np.prod(1+out.oos_profit/100)-1)*100,"median_window_pct":out.oos_profit.median(),"worst_window_pct":out.oos_profit.min(),"trades":int(out.trades.sum()),"selected_emas":sorted(out.ema.unique().tolist()),"rows":out.to_dict("records")}
open("research/btc_strategy_lab_replication/out/walk_forward_source_logic_summary.json","w").write(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
