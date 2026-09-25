#!/usr/bin/env python3
from pathlib import Path
import io,math,time,zipfile
import numpy as np,pandas as pd,requests
ROOT=Path("canonical_um"); OUT=Path("artifacts"); OUT.mkdir(exist_ok=True)
SYMS=("BTCUSDT","ETHUSDT"); BASE="https://data.binance.vision/data/spot/monthly/klines"
START=pd.Timestamp("2017-08-01",tz="UTC"); END=pd.Timestamp("2026-08-01",tz="UTC"); ER_T=.193654; RT=.25; L2FEE=.12
TF={"15M":"15min","30M":"30min","1H":"1h","4H":"4h","12H":"12h","1D":"1D","1W":"7D"}

def months(a,b):
 x=a
 while x<=b: yield x; x=x+pd.offsets.MonthBegin(1)
def todt(v):
 z=pd.to_numeric(v,errors="coerce"); return pd.to_datetime(np.where(z>1e14,z/1000,z),unit="ms",utc=True,errors="coerce")
def load_daily(sym):
 cols=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","tb","tq","ignore"]; fs=[]; s=requests.Session()
 for m in months(START,END):
  name=f"{sym}-1d-{m:%Y-%m}.zip"; r=s.get(f"{BASE}/{sym}/1d/{name}",timeout=30)
  if r.status_code==404: continue
  r.raise_for_status()
  with zipfile.ZipFile(io.BytesIO(r.content)) as z:
   q=[x for x in z.namelist() if x.endswith(".csv")]
   if q: fs.append(pd.read_csv(z.open(q[0]),header=None,names=cols))
  time.sleep(.002)
 x=pd.concat(fs,ignore_index=True); x["dt"]=todt(x.open_time)
 for c in ["open","close"]: x[c]=pd.to_numeric(x[c],errors="coerce")
 return x.dropna(subset=["dt","open","close"]).drop_duplicates("dt").sort_values("dt")
def core():
 ds={}
 for s in SYMS:
  d=load_daily(s)[["dt","open","close"]].copy(); c=d.close
  d[s+"_ret30"]=c/c.shift(30)-1; path=c.diff().abs().rolling(30,min_periods=15).sum(); d[s+"_er30"]=(c-c.shift(30)).abs()/path.replace(0,np.nan)
  d=d.rename(columns={"open":s+"_open","close":s+"_close"}); ds[s]=d
 x=ds["BTCUSDT"].merge(ds["ETHUSDT"],on="dt")
 active=((x.BTCUSDT_ret30>0)&(x.ETHUSDT_ret30>0)&(((x.BTCUSDT_er30+x.ETHUSDT_er30)/2)>=ER_T))
 base=active.shift(1).fillna(False).to_numpy(float)
 intr=((x.BTCUSDT_close/x.BTCUSDT_open-1)+(x.ETHUSDT_close/x.ETHUSDT_open-1))*50
 d2=np.zeros(len(x)); ent=None; forced=False
 for i in range(len(x)):
  if base[i]==1 and (i==0 or base[i-1]==0): ent=i; forced=False
  if base[i]==0: ent=None; forced=False
  if base[i]==1 and not forced:
   d2[i]=1
   if ent is not None and i-ent==2:
    a=x.iloc[ent]; b=x.iloc[i]; rr=((b.BTCUSDT_close/a.BTCUSDT_close-1)+(b.ETHUSDT_close/a.ETHUSDT_close-1))*50
    if np.isfinite(rr) and rr<=0: forced=True
 turn=pd.Series(d2).diff().abs().fillna(pd.Series(d2).abs())
 x["d2"]=d2; x["core_ret"]=d2*intr-turn*(RT/2)
 return x
def l2_signals(sym,df):
 df=df.sort_values("open_time").drop_duplicates("open_time").copy(); df["dt"]=pd.to_datetime(df.open_time,unit="ms",utc=True); b=df.set_index("dt")[["open","high","low","close"]].astype(float); idx=b.index; A={}
 for name,rule in TF.items():
  r=b.resample(rule,origin="epoch",label="left",closed="left").agg({"close":"last"}).dropna(); c=r.close; s=c.rolling(19).sum().shift(1); ss=(c*c).rolling(19).sum().shift(1); buckets=idx.floor(rule) if name!="1W" else idx.floor("7D"); sm=pd.Series(s.reindex(buckets).to_numpy(),index=idx); sqm=pd.Series(ss.reindex(buckets).to_numpy(),index=idx); p=b.open; mean=(sm+p)/20; var=(sqm+p*p)/20-mean*mean; A[name]=p>mean+2*np.sqrt(var.clip(lower=0))
 exact=pd.DataFrame(A,index=idx).sum(axis=1); p=b.open; raw=(exact>=6)&(p.pct_change(16)*100>=30); trig=raw & ~raw.shift(1,fill_value=False)
 out=[]
 for t in idx[trig]:
  pos=idx.get_indexer([t])[0]; path=b.iloc[pos+1:pos+5]
  if path.empty: continue
  en=float(p.loc[t]); tp=en*1.10; sl=en*.975; ex=float(path.iloc[-1].close); xt=path.index[-1]; outcome="TIME"; both=False
  for tt,z in path.iterrows():
   ht=z.high>=tp; hs=z.low<=sl
   if ht and hs: both=True
   if hs: ex=sl;xt=tt;outcome="SL";break
   if ht: ex=tp;xt=tt;outcome="TP";break
  out.append([sym,t,xt,(ex/en-1)*100-L2FEE,outcome,both])
 return out
def perf(r):
 a=np.asarray(r,float); w=np.prod(1+a/100); curve=np.r_[1,np.cumprod(1+a/100)]; dd=(curve/np.maximum.accumulate(curve)-1)*100; sd=np.std(a)
 return (w-1)*100,np.mean(a)/sd*math.sqrt(365.25) if sd else np.nan,float(dd.min())
def main():
 x=core(); trs=[]
 for p in sorted(ROOT.glob("*.parquet")):
  try: trs+=l2_signals(p.stem,pd.read_parquet(p))
  except Exception as e: print("ERR",p.stem,e)
 tr=pd.DataFrame(trs,columns=["symbol","signal_time","exit_time","net_pct","outcome","samebar_both"])
 # Core OFF classification is exact D2_0 calendar-day state. L2 starts only when OFF; existing L2 is allowed to finish.
 state=x.set_index(x.dt.dt.floor("D")).d2
 tr["core_on"]=state.reindex(tr.signal_time.dt.floor("D")).fillna(0).to_numpy().astype(bool)
 off=tr[~tr.core_on].copy(); off.to_csv(OUT/"l2_coreoff_trades.csv",index=False)
 print("L2 total",len(tr),"off",len(off),"active",int(tr.core_on.sum()),"samebar_both",int(tr.samebar_both.sum()))
 def summ(g):
  gp=g.net_pct[g.net_pct>0].sum(); gl=-g.net_pct[g.net_pct<0].sum(); return len(g),g.net_pct.mean(),gp/gl if gl else np.inf
 print("L2 ALL/OFF/ACTIVE",summ(tr),summ(off),summ(tr[tr.core_on]))
 # Daily portfolio: core has full seed. When core OFF, divide seed equally among simultaneous L2 slots, capped at full seed.
 # Realized L2 returns are booked on exit day; slot weight = 1/max concurrent positions for that entry cohort/day.
 events=[]
 for _,r in off.iterrows(): events.append((r.signal_time,1)); events.append((r.exit_time,-1))
 events.sort(key=lambda z:(z[0],z[1])) # exits before entries at same timestamp
 conc=0; peak=0
 for t,d in events: conc+=d; peak=max(peak,conc)
 # Compare 30%-per-trade legacy and full-seed/slot convention requested.
 for mode in ["L2_30PCT","FULL_SEED_SLOT"]:
  daily=pd.Series(0.0,index=pd.DatetimeIndex(x.dt.dt.floor("D").unique()))
  daily.loc[x.dt.dt.floor("D")]=x.core_ret.to_numpy()
  # compute entry-time concurrency incl same timestamp entries; weight fixed for each trade
  entries=off.sort_values("signal_time").copy(); weights=[]
  for i,r in entries.iterrows():
   active=((entries.signal_time<=r.signal_time)&(entries.exit_time>r.signal_time))
   n=max(1,int(active.sum()))
   weights.append(.30 if mode=="L2_30PCT" else 1/n)
  entries["weight"]=weights
  for _,r in entries.iterrows():
   day=r.exit_time.floor("D")
   if day in daily.index: daily.loc[day]+=r.net_pct*r.weight
  rr=daily[(daily.index>=pd.Timestamp("2021-01-01",tz="UTC"))]
  P=perf(rr); print(mode,"2021+",{"return_pct":P[0],"sharpe":P[1],"mdd":P[2],"peak_l2_concurrency":peak,"l2_trades":len(entries)})
  yrs=[]
  for y,g in rr.groupby(rr.index.year): yrs.append((int(y),)+perf(g))
  print(mode,"YEARS",yrs)
 print("NOTE samebar_both uses conservative SL-first. Portfolio daily booking is realized-return approximation, not intraday MTM.")
if __name__=="__main__": main()
