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
  pos=idx.get_indexer([t])[0]; path=b.iloc[pos:pos+4]
  if path.empty: continue
  en=float(p.loc[t]); tp=en*1.10; sl=en*.975; ex=float(path.iloc[-1].close); xt=path.index[-1]; outcome="TIME"; both=False
  for tt,z in path.iterrows():
   ht=z.high>=tp; hs=z.low<=sl
   if ht and hs: both=True
   if hs: ex=sl;xt=tt;outcome="SL";break
   if ht: ex=tp;xt=tt;outcome="TP";break
  out.append([sym,t,xt,(ex/en-1)*100-L2FEE,outcome,both, bool(xt==t)])
 return out
def resolve_1m(tr):
 amb=tr[tr.samebar_both].copy(); resolved={}; sess=requests.Session(); cache={}
 def parse_zip(raw):
  with zipfile.ZipFile(io.BytesIO(raw)) as z:
   fn=[n for n in z.namelist() if n.endswith(".csv")][0]; m=pd.read_csv(z.open(fn),header=None)
  m=m.iloc[:,:6]; m.columns=["open_time","open","high","low","close","volume"]; m["dt"]=todt(m.open_time)
  for k in ["open","high","low","close"]: m[k]=pd.to_numeric(m[k],errors="coerce")
  return m.set_index("dt")
 for _,r in amb.iterrows():
  sym=r.symbol; bar=r.exit_time; mkey=(sym,bar.year,bar.month)
  if mkey not in cache:
   name=f"{sym}-1m-{bar:%Y-%m}.zip"; url=f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1m/{name}"
   q=sess.get(url,timeout=60)
   cache[mkey]=parse_zip(q.content) if q.status_code==200 else None
  qdf=cache[mkey]
  # monthly missing (typically current month): fetch exact daily archive containing the ambiguous bar
  if qdf is None:
   dkey=(sym,bar.date())
   if dkey not in cache:
    name=f"{sym}-1m-{bar:%Y-%m-%d}.zip"; url=f"https://data.binance.vision/data/futures/um/daily/klines/{sym}/1m/{name}"
    q=sess.get(url,timeout=60); cache[dkey]=parse_zip(q.content) if q.status_code==200 else None
   qdf=cache[dkey]
  if qdf is None:
   resolved[(r.symbol,r.signal_time)]="NO_1M_DATA"; continue
  en=float(r.entry); tp=en*1.10; sl=en*.975
  z=qdf.loc[(qdf.index>=bar)&(qdf.index<bar+pd.Timedelta(minutes=15))]; decision=None
  for tt,v in z.iterrows():
   ht=v.high>=tp; hs=v.low<=sl
   if ht and hs: decision="UNRESOLVED_1M"; break
   if hs: decision="SL"; break
   if ht: decision="TP"; break
  resolved[(r.symbol,r.signal_time)]=decision or "NO_HIT_IN_1M"
 tr["resolution_1m"]=[resolved.get((r.symbol,r.signal_time),"NA") for _,r in tr.iterrows()]
 for i,r in tr[tr.samebar_both].iterrows():
  if r.resolution_1m=="TP": tr.at[i,"net_pct"]=10.0-L2FEE; tr.at[i,"outcome"]="TP_1M"
  elif r.resolution_1m=="SL": tr.at[i,"net_pct"]=-2.5-L2FEE; tr.at[i,"outcome"]="SL_1M"
 return tr

def perf(r):
 a=np.asarray(r,float); w=np.prod(1+a/100); curve=np.r_[1,np.cumprod(1+a/100)]; dd=(curve/np.maximum.accumulate(curve)-1)*100; sd=np.std(a)
 return (w-1)*100,np.mean(a)/sd*math.sqrt(365.25) if sd else np.nan,float(dd.min())
def main():
 x=core(); trs=[]
 for p in sorted(ROOT.glob("*.parquet")):
  try: trs+=l2_signals(p.stem,pd.read_parquet(p))
  except Exception as e: print("ERR",p.stem,e)
 tr=pd.DataFrame(trs,columns=["symbol","signal_time","exit_time","net_pct","outcome","samebar_both","exit_entry_bar"])
 tr["entry"]=np.nan
 # recover exact entry from net/outcome is unsafe; load entry prices from canonical signal bars
 px={}
 for p in sorted(ROOT.glob("*.parquet")):
  d=pd.read_parquet(p); d["dt"]=pd.to_datetime(d.open_time,unit="ms",utc=True); px[p.stem]=d.set_index("dt").open.astype(float)
 for i,r in tr.iterrows(): tr.at[i,"entry"]=float(px[r.symbol].loc[r.signal_time])
 tr=resolve_1m(tr)
 print("1M_RESOLUTION",tr.loc[tr.samebar_both,"resolution_1m"].value_counts(dropna=False).to_dict())
 print("ENTRY_BAR_EXITS",int(tr.exit_entry_bar.sum()),tr.loc[tr.exit_entry_bar,"outcome"].value_counts().to_dict())
 # Core OFF classification is exact D2_0 calendar-day state. L2 starts only when OFF; existing L2 is allowed to finish.
 state=x.set_index(x.dt.dt.floor("D")).d2
 tr["core_on"]=state.reindex(tr.signal_time.dt.floor("D")).fillna(0).to_numpy().astype(bool)
 off=tr[~tr.core_on].copy(); off.to_csv(OUT/"l2_coreoff_trades.csv",index=False)
 print("L2 total",len(tr),"off",len(off),"active",int(tr.core_on.sum()),"samebar_both",int(tr.samebar_both.sum()))
 def summ(g):
  gp=g.net_pct[g.net_pct>0].sum(); gl=-g.net_pct[g.net_pct<0].sum(); return len(g),g.net_pct.mean(),gp/gl if gl else np.inf
 print("L2 ALL/OFF/ACTIVE",summ(tr),summ(off),summ(tr[tr.core_on]))
 # Portfolio event simulation. D2_0 is authoritative gate. Existing L2 may finish after core turns on; no new L2 while core on.
 # At each timestamp: close exits first, then batch all new entries. Total L2 allocated exposure is capped at 100%.
 # FULL_SEED_SLOT: if flat, split 100% equally across the simultaneous entry batch. If positions already open, no new entries (seed fully allocated).
 # Also report SAMEBAR_BEST/WORST sensitivity for bars where TP and SL both touched.
 print("D2_0 OFF audit is intentionally not expected to equal prior BASE-core OFF 1170-trade split.")
 def simulate(mode, collision="SL"):
  rows=off.sort_values(["signal_time","symbol"]).copy()
  if collision=="TP":
   rows.loc[rows.samebar_both,"net_pct"]=10.0-L2FEE
  byentry={k:g for k,g in rows.groupby("signal_time",sort=True)}
  exits={}
  active=[]; realized=[]
  timeline=sorted(set(byentry.keys())|set(rows.exit_time))
  for t in timeline:
   # release all positions whose exit time is now or earlier
   still=[]
   for q in active:
    if q["exit_time"]<=t:
     realized.append(q)
    else: still.append(q)
   active=still
   batch=byentry.get(t)
   if batch is None: continue
   if mode=="L2_30PCT":
    cap=max(0.0,1.0-sum(q["weight"] for q in active))
    for _,r in batch.iterrows():
     if cap<=1e-12: break
     w=min(.30,cap); cap-=w; q=r.to_dict(); q["weight"]=w; active.append(q)
   else:
    # full seed is already committed while any L2 is open; otherwise split this timestamp's slots equally
    if active: continue
    w=1.0/len(batch)
    for _,r in batch.iterrows():
     q=r.to_dict(); q["weight"]=w; active.append(q)
  realized+=active
  z=pd.DataFrame(realized)
  daily=pd.Series(0.0,index=pd.DatetimeIndex(x.dt.dt.floor("D").unique()))
  daily.loc[x.dt.dt.floor("D")]=x.core_ret.to_numpy()
  for _,r in z.iterrows():
   day=r["exit_time"].floor("D")
   if day in daily.index: daily.loc[day]+=r["net_pct"]*r["weight"]
  rr=daily[daily.index>=pd.Timestamp("2021-01-01",tz="UTC")]
  P=perf(rr)
  return z,rr,P
 for mode in ["L2_30PCT","FULL_SEED_SLOT"]:
  for collision in ["SL"]:
   z,rr,P=simulate(mode,collision)
   print(mode,collision,"2021+",{"return_pct":P[0],"sharpe":P[1],"mdd":P[2],"accepted_l2":len(z),"max_weight_sum_rule":1.0})
   print(mode,collision,"YEARS",[(int(y),)+perf(g) for y,g in rr.groupby(rr.index.year)])
 # Focused 2025-2026 audit: unresolved 1m remains SL by construction.
 z,rr,P=simulate("L2_30PCT","SL")
 z["year"]=z.exit_time.dt.year
 for y in [2025,2026]:
  q=z[z.year==y].copy()
  if q.empty: continue
  gp=q.loc[q.net_pct>0,"net_pct"].sum(); gl=-q.loc[q.net_pct<0,"net_pct"].sum()
  print("AUDIT_YEAR",y,{"accepted":len(q),"wins":int((q.net_pct>0).sum()),"losses":int((q.net_pct<0).sum()),"avg_net":float(q.net_pct.mean()),"pf_trade":float(gp/gl) if gl else None,"samebar15":int(q.samebar_both.sum()),"unresolved1m":int((q.resolution_1m=="UNRESOLVED_1M").sum()),"unique_symbols":int(q.symbol.nunique())})
  bys=q.groupby("symbol").agg(n=("net_pct","size"),sum_net=("net_pct","sum"),avg=("net_pct","mean")).sort_values("sum_net",ascending=False)
  print("AUDIT_TOP_SYMBOLS",y,bys.head(10).reset_index().to_dict("records"))
  print("AUDIT_BOTTOM_SYMBOLS",y,bys.tail(10).reset_index().to_dict("records"))
  # continuity: every trade path must have expected 15m spacing from signal to exit, never > 60m
  dur=(q.exit_time-q.signal_time).dt.total_seconds()/60
  print("AUDIT_DURATION",y,{"min":float(dur.min()),"median":float(dur.median()),"max":float(dur.max()),"over60":int((dur>60).sum()),"non15multiple":int(((dur%15)!=0).sum())})
  # contribution concentration on unweighted trade-net basis
  pos=q.groupby("symbol").net_pct.sum().sort_values(ascending=False); total=q.net_pct.sum()
  print("AUDIT_CONCENTRATION",y,{"top1_share_net":float(pos.iloc[0]/total) if total else None,"top5_share_net":float(pos.head(5).sum()/total) if total else None})
 print("NOTE unresolved 1m collisions remain SL. Daily portfolio is realized-return approximation; intraday MTM MDD remains a limitation.")
if __name__=="__main__": main()
