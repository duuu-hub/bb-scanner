"""Frozen EMA600/F55 forward audit: official Binance spot + USD-M funding.
Never treat missing funding as an allowed NaN. Historical data only warms indicators.
"""
import io,json,os,sys,time,zipfile,hashlib
from datetime import datetime,timezone,timedelta
import requests,numpy as np,pandas as pd

OUT="research/btc_strategy_lab_replication/out/forward_official_checkpoint.json"
FREEZE=pd.Timestamp("2026-09-24T14:00:00Z")
CHECKPOINT=pd.Timestamp("2026-10-14T17:00:00Z")
COST=.001
S=requests.Session()
def get_zip(url):
 for k in range(3):
  try:
   r=S.get(url,timeout=30)
   if r.status_code==404:return None
   r.raise_for_status()
   z=zipfile.ZipFile(io.BytesIO(r.content))
   with z.open([p for p in z.namelist() if p.endswith(".csv")][0]) as f:
    a=pd.read_csv(f,header=None)
   return a
  except Exception as e:
   if k==2:print("FETCH_ERROR",url,str(e)[:180])
   time.sleep(1+k)
 return None

def parse_date(s):
 if pd.api.types.is_numeric_dtype(s):
  v=pd.to_numeric(s,errors="coerce")
  m=v.dropna().median()
  unit="us" if m>1e14 else ("ms" if m>1e11 else "s")
  return pd.to_datetime(v,unit=unit,utc=True,errors="coerce")
 return pd.to_datetime(s,utc=True,errors="coerce")

def spot_parse(a):
 if a is None or len(a)==0:return None
 if not pd.api.types.is_numeric_dtype(a.iloc[:,0]):
  if str(a.iloc[0,0]).lower() in ("open_time","timestamp","date"):a=a.iloc[1:]
 if a.shape[1]<6:return None
 x=a.iloc[:,:6].copy();x.columns=["time","open","high","low","close","volume"]
 x["time"]=parse_date(x.time)
 for col in ["open","high","low","close","volume"]:x[col]=pd.to_numeric(x[col],errors="coerce")
 return x.dropna().set_index("time").sort_index()

def funding_parse(a):
 if a is None or len(a)==0:return None
 # Binance Vision funding archives typically have headers calc_time, funding_interval_hours, last_funding_rate
 hdr=[str(t).strip().lower() for t in a.iloc[0].tolist()]
 if any("funding" in h or "calc_time" in h for h in hdr):
  a=a.iloc[1:].copy();a.columns=hdr
  timecols=[c for c in a if "calc_time" in c or "funding_time" in c or c in ("time","timestamp")]
  ratecols=[c for c in a if "last_funding_rate" in c or c=="fundingrate" or c=="funding_rate"]
  if not timecols or not ratecols:print("FUNDING_SCHEMA_UNKNOWN",hdr);return None
  t=a[timecols[0]];rate=a[ratecols[0]]
 else:
  # positional archive schema: calc_time, funding_interval_hours, last_funding_rate
  t=a.iloc[:,0];rate=a.iloc[:,2] if a.shape[1]>=3 else a.iloc[:,1]
 d=pd.DataFrame({"date":parse_date(t),"funding":pd.to_numeric(rate,errors="coerce")})
 return d.dropna().drop_duplicates("date").set_index("date").sort_index()

spot_parts=[];spot_files=[];missing_spot=[]
base="https://data.binance.vision/data/spot"
for p in pd.period_range("2019-01","2026-09",freq="M"):
 url=f"{base}/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-{p}.zip"
 x=spot_parse(get_zip(url))
 if x is None:missing_spot.append(str(p));continue
 spot_parts.append(x);spot_files.append(str(p))
for d in pd.date_range("2026-10-01","2026-10-28",freq="D"):
 day=d.strftime("%Y-%m-%d")
 url=f"{base}/daily/klines/BTCUSDT/1h/BTCUSDT-1h-{day}.zip"
 x=spot_parse(get_zip(url))
 if x is None:missing_spot.append(day);continue
 spot_parts.append(x);spot_files.append(day)
if not spot_parts:raise RuntimeError("No official spot data retrieved")
spot=pd.concat(spot_parts).sort_index()
spot=spot[~spot.index.duplicated(keep="last")]
spot=spot.loc[:pd.Timestamp("2026-10-28T23:00:00Z")]

# Source funding provides historical lookback through 2026-08-21.
f=pd.read_feather("research/btc_strategy_lab_replication/source_data/funding.feather")
f["date"]=pd.to_datetime(f.date,utc=True)
old=f.set_index("date")["funding"].astype(float).sort_index()
fund_parts=[old.to_frame("funding")];fund_files=[];missing_funding=[]
fundbase="https://data.binance.vision/data/futures/um"
for p in ["2026-08","2026-09"]:
 url=f"{fundbase}/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{p}.zip"
 raw=get_zip(url)
 print("FUNDING_MONTH_RAW",p,raw.head(4).to_string(index=False,header=False) if raw is not None else "NONE")
 a=funding_parse(raw)
 print("FUNDING_MONTH_PARSED",p, a.head(3).to_string() if a is not None else "NONE",str(a.index.min()) if a is not None else "",str(a.index.max()) if a is not None else "")
 if a is None or a.empty:missing_funding.append(p);continue
 fund_parts.append(a);fund_files.append(p)
for d in pd.date_range("2026-10-01","2026-10-28",freq="D"):
 day=d.strftime("%Y-%m-%d")
 url=f"{fundbase}/daily/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{day}.zip"
 a=funding_parse(get_zip(url))
 if a is None or a.empty:missing_funding.append(day);continue
 fund_parts.append(a);fund_files.append(day)

# API fallback for funding if archives unavailable; do not silently fill missing.
if missing_funding:
 try:
  start=int(pd.Timestamp("2026-08-20T00:00:00Z").timestamp()*1000)
  end=int(pd.Timestamp("2026-10-29T00:00:00Z").timestamp()*1000)
  url="https://fapi.binance.com/fapi/v1/fundingRate"
  r=S.get(url,params={"symbol":"BTCUSDT","startTime":start,"endTime":end,"limit":1000},timeout=25)
  if r.ok:
   rows=r.json()
   if isinstance(rows,list) and rows:
    ff=pd.DataFrame(rows)
    ff["date"]=pd.to_datetime(ff.fundingTime,unit="ms",utc=True)
    ff["funding"]=pd.to_numeric(ff.fundingRate)
    fund_parts.append(ff.set_index("date")[["funding"]]);fund_files.append("fapi-fallback")
  else:print("FUNDING_API_STATUS",r.status_code,r.text[:120])
 except Exception as e:print("FUNDING_API_ERROR",str(e)[:180])
fund=pd.concat(fund_parts).sort_index()
fund=fund[~fund.index.duplicated(keep="last")]
fund=fund.loc[:pd.Timestamp("2026-10-28T23:00:00Z")]

# No forward-fill past an 8h settlement interval; preserve the original 72h / 180d rank formula.
h=spot.copy()
h["funding"]=fund.funding.reindex(h.index,method="ffill",tolerance=pd.Timedelta("8h"))
h["funding_3d"]=h.funding.rolling(72,min_periods=24).mean()
h["funding_pct"]=h.funding_3d.rolling(4320,min_periods=720).rank(pct=True)*100
s=h.close;n=600
ema=np.full(len(s),np.nan)
ema[n-1]=s.iloc[:n].mean();alpha=2/(n+1)
for i in range(n,len(s)):ema[i]=alpha*s.iloc[i]+(1-alpha)*ema[i-1]
h["ema"]=ema
h["ema_exit"]=h.ema*.98
cross=(h.close>h.ema)&(h.close.shift(1)<=h.ema.shift(1))
exitcross=(h.close<h.ema_exit)&(h.close.shift(1)>=h.ema_exit.shift(1))
h["cross_up"]=cross
h["cross_exit"]=exitcross
# Funding percentile is only trustworthy if there is no recent 72h gap.
h["funding_valid"]=h.funding.notna().rolling(72,min_periods=72).sum().eq(72)&h.funding_pct.notna()
h["entry_eligible"]=h.cross_up&h.funding_valid&(h.funding_pct<55)

# The freeze date was confirmed flat in the earlier 2026-05 to 2026-09 historical run.
# Start a completely independent forward equity curve; do not import pre-freeze PnL.
fw=h.loc[(h.index>=FREEZE)&(h.index<=pd.Timestamp("2026-10-28T23:00:00Z"))].copy()
missing_hours=pd.date_range(fw.index.min(),fw.index.max(),freq="h",tz="UTC").difference(fw.index)
gap_hours=int(fw.funding.isna().sum())
signals=[];trades=[];events=[]
equity=1.;position=False;entry=0.;peak=0.;pending=None;eq=[]
for dt,r in fw.iterrows():
 if pending=="buy" and not position:
  entry=float(r.open);peak=entry;position=True;entdt=dt;equity*=1-COST
  events.append({"time":str(dt),"type":"ENTRY","price":entry})
  pending=None
 elif pending=="sell" and position:
  price=float(r.open);equity*=price/entry*(1-COST)
  trades.append({"entry":str(entdt),"exit":str(dt),"entry_price":entry,"exit_price":price,"gross_return_pct":(price/entry-1)*100,"net_return_pct":((price/entry)*(1-COST)**2-1)*100,"reason":"EMA-2%"})
  events.append({"time":str(dt),"type":"EXIT_EMA","price":price})
  position=False;pending=None
 if position:
  peak=max(peak,float(r.high));stop=peak*.85
  if r.low<=stop:
   price=min(float(r.open),stop)
   equity*=price/entry*(1-COST)
   trades.append({"entry":str(entdt),"exit":str(dt),"entry_price":entry,"exit_price":price,"gross_return_pct":(price/entry-1)*100,"net_return_pct":((price/entry)*(1-COST)**2-1)*100,"reason":"trailing15"})
   events.append({"time":str(dt),"type":"EXIT_TRAILING","price":price})
   position=False;pending=None
 if bool(r.cross_up):
  signals.append({"time":str(dt),"type":"EMA600_CROSS_UP","funding_pct":float(r.funding_pct) if pd.notna(r.funding_pct) else None,"funding_valid":bool(r.funding_valid),"eligible":bool(r.entry_eligible)})
 if bool(r.cross_exit):
  signals.append({"time":str(dt),"type":"EMA_MINUS2_CROSS_DOWN"})
 if not position and bool(r.entry_eligible):pending="buy"
 elif position and bool(r.cross_exit):pending="sell"
 mark=equity*(float(r.close)/entry*(1-COST) if position else 1)
 eq.append(mark)

eq=np.asarray(eq);mdd=float(np.min(eq/np.maximum.accumulate(eq)-1)*100) if len(eq) else None
new=[x for x in signals if pd.Timestamp(x["time"])>CHECKPOINT]
out={
 "strategy":"EMA600 + Funding55 LONG (frozen)",
 "forward_start_utc":str(FREEZE),"last_completed_candle_utc":str(fw.index.max()),
 "spot_source":"Binance Vision official Spot BTCUSDT 1h","funding_source":"Binance Vision official USD-M BTCUSDT fundingRate + source historical feather",
 "spot_archive_count":len(spot_files),"spot_missing_archives":missing_spot,
 "funding_archive_count":len(fund_files),"funding_files":fund_files,"funding_missing_archives":missing_funding,
 "funding_last_actual":str(fund.index.max()),"funding_missing_hours_forward":gap_hours,
 "spot_missing_hours_forward":len(missing_hours),
 "price_cross_up_count":int(fw.cross_up.sum()),"price_cross_exit_count":int(fw.cross_exit.sum()),
 "eligible_entry_signals":int(fw.entry_eligible.sum()),
 "signals_since_prior_checkpoint":new,"all_signals":signals,"events":events,"closed_trades":trades,
 "closed_trade_count":len(trades),"open_position":position,
 "equity_net_cost_pct":float((eq[-1]-1)*100) if len(eq) else None,
 "mdd_net_cost_pct":mdd,"cost_per_side_pct":COST*100,"roundtrip_cost_approx_pct":2*COST*100,
 "forward_certified":len(missing_hours)==0 and gap_hours==0 and not missing_spot and bool(fund_files),
 "notes":["Historical candles and funding only warm indicators; PnL starts at 1 on freeze date.",
          "Original next-open entry/exit, EMA-2%, trailing15%, and 0.1% per-side execution retained.",
          "Any funding gap over 8h blocks new entries, never treated as the original early-history NaN exception.",
          "Open position is marked to market with estimated exit fee; no forced closing."]
}
os.makedirs(os.path.dirname(OUT),exist_ok=True)
with open(OUT,"w") as f:json.dump(out,f,indent=2,ensure_ascii=False)
print("RESULT",json.dumps({k:out[k] for k in ("last_completed_candle_utc","funding_last_actual","spot_missing_archives","funding_missing_archives","funding_missing_hours_forward","spot_missing_hours_forward","price_cross_up_count","price_cross_exit_count","eligible_entry_signals","closed_trade_count","open_position","equity_net_cost_pct","mdd_net_cost_pct","forward_certified")},indent=2))
print("NEW_SIGNALS",json.dumps(new,indent=2))
print("EVENTS",json.dumps(events,indent=2))
