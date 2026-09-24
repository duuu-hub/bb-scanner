import io,zipfile,requests,pandas as pd,numpy as np,json,math,os
from datetime import datetime
BASE="https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/"
months=pd.period_range("2019-01","2026-08",freq="M")
parts=[]
for m in months:
 u=f"{BASE}BTCUSDT-1h-{m}.zip"
 z=requests.get(u,timeout=30)
 if z.status_code!=200: continue
 with zipfile.ZipFile(io.BytesIO(z.content)) as zz:
  a=pd.read_csv(zz.open(zz.namelist()[0]),header=None)
  a=a.iloc[:,:6];a.columns=["ts","open","high","low","close","volume"]
  # Binance archive timestamps may be ms or us
  unit="us" if a.ts.iloc[0]>1e14 else "ms"
  a["dt"]=pd.to_datetime(a.ts,unit=unit,utc=True)
  parts.append(a[["dt","open","high","low","close","volume"]])
d=pd.concat(parts).drop_duplicates("dt").sort_values("dt").set_index("dt").astype(float)
fund=pd.read_feather("research/btc_strategy_lab_replication/source_data/funding.feather")
fund["date"]=pd.to_datetime(fund.date,utc=True); fr=fund.set_index("date").sort_index()["funding"]
d["funding"]=fr.reindex(d.index,method="ffill");d["f3"]=d.funding.rolling(72,min_periods=24).mean();d["fpct"]=d.f3.rolling(24*180,min_periods=24*30).rank(pct=True)*100
def ema_talib(s,n):
 o=pd.Series(np.nan,index=s.index);o.iloc[n-1]=s.iloc[:n].mean();a=2/(n+1)
 for i in range(n,len(s)):o.iloc[i]=a*s.iloc[i]+(1-a)*o.iloc[i-1]
 return o
d["ema"]=ema_talib(d.close,600)
sig=(d.close>d.ema)&(d.close.shift(1)<=d.ema.shift(1))&((d.fpct<55)|d.fpct.isna())
xit=(d.close<d.ema*.98)&(d.close.shift(1)>=d.ema.shift(1)*.98)
# next-open execution to approximate Freqtrade signal->next candle fill; include trailing stop 15% source strategy
def run(cost_side, start="2019-10-01", end="2026-08-21"):
 x=d.loc[start:end].copy(); cap=1.;pos=False;ep=0.;peak=0.;tr=[];eq=[];pending=None
 for i,(dt,r) in enumerate(x.iterrows()):
  # execute pending at candle open
  if pending=="buy" and not pos:
   ep=r.open;peak=ep;cap*=1-cost_side;pos=True;entdt=dt;pending=None
  elif pending=="sell" and pos:
   cap*=r.open/ep*(1-cost_side);tr.append((entdt,dt,r.open/ep-1));pos=False;pending=None
  if pos:
   peak=max(peak,r.high)
   # stoploss/trailing conservative fill at stop price if low breaches 15% trailing
   stop=peak*.85
   if r.low<=stop:
    px=min(r.open,stop);cap*=px/ep*(1-cost_side);tr.append((entdt,dt,px/ep-1));pos=False;pending=None
  if not pos and bool(sig.loc[dt]): pending="buy"
  elif pos and bool(xit.loc[dt]): pending="sell"
  mark=cap*(r.close/ep if pos else 1);eq.append(mark)
 if pos: cap*=x.close.iloc[-1]/ep*(1-cost_side);tr.append((entdt,x.index[-1],x.close.iloc[-1]/ep-1))
 eq=np.array(eq); peakv=np.maximum.accumulate(eq);mdd=np.min(eq/peakv-1)
 years=(x.index[-1]-x.index[0]).total_seconds()/31557600;cagr=cap**(1/years)-1
 rets=pd.Series(eq).pct_change().fillna(0);sh=(rets.mean()/rets.std()*np.sqrt(24*365)) if rets.std()>0 else 0
 wins=sum(max(t[2],0) for t in tr);loss=-sum(min(t[2],0) for t in tr);pf=wins/loss if loss else 999
 return dict(cost_per_side_pct=cost_side*100,total_return_pct=(cap-1)*100,cagr_pct=cagr*100,mdd_pct=mdd*100,sharpe=sh,pf=pf,trades=len(tr))
costs=[0.001,0.0015,0.002,0.003,0.005,0.01]
res=[run(c) for c in costs]
# pseudo-forward slices: NOT clean OOS because params were source-selected; diagnostic only
slices=[]
for st in ["2022-01-01","2023-01-01","2024-01-01","2025-01-01","2026-01-01"]:
 r=run(.001,st,"2026-08-21");r["start"]=st;slices.append(r)
out={"data_source":"Binance official spot monthly 1h archive","range":[str(d.index.min()),str(d.index.max())],"funding_end":"2026-08-21","execution":"next candle open; source 15% trailing stop modeled; costs per side","cost_stress":res,"recent_fixed_parameter_diagnostics":slices,"forward_note":"These recent slices are not clean OOS because EMA600/F55 was already selected by source research. True forward validation must start after parameter freeze."}
os.makedirs("research/btc_strategy_lab_replication/out",exist_ok=True)
open("research/btc_strategy_lab_replication/out/binance_cost_forward_validation.json","w").write(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
