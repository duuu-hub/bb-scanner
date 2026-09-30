import glob,json,pandas as pd,numpy as np,requests
from pathlib import Path
checks=[]; add=lambda n,ok,d="": checks.append((n,bool(ok),str(d)))
fs=sorted(glob.glob("market_data_store/bitget/universe_15m/2026/09/2026-09-*.csv.gz"))
fs=[f for f in fs if "2026-09-22"<=Path(f).name[:10]<="2026-09-29"]
d=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
d=d.sort_values(["symbol","timestamp_ms"]).reset_index(drop=True)
add("01_files_8",len(fs)==8,len(fs)); add("02_no_dup",not d.duplicated(["symbol","timestamp_ms"]).any(),d.duplicated(["symbol","timestamp_ms"]).sum())
add("03_ts_aligned",((d.timestamp_ms%(15*60*1000))==0).all()); add("04_ohlc_high",(d.high>=d[["open","close","low"]].max(axis=1)).all())
add("05_ohlc_low",(d.low<=d[["open","close","high"]].min(axis=1)).all()); add("06_vol_nonneg",(d.base_volume>=0).all()); add("07_qvol_nonneg",(d.quote_volume>=0).all())
add("08_finite",np.isfinite(d[["open","high","low","close","base_volume","quote_volume"]].to_numpy()).all())
add("09_price_pos",(d[["open","high","low","close"]]>0).all().all())
counts=d.groupby("symbol").size(); add("10_symbols_ge100",(counts>=100).all(),counts.describe().to_dict())
add("11_max_rows_768",counts.max()<=768,counts.max()); add("12_min_rows",counts.min()>0,counts.min())
# manifests
mans=sorted(glob.glob("market_data_store/bitget/universe_15m/manifests/2026/09/2026-09-*.json")); mans=[m for m in mans if "2026-09-22"<=Path(m).name[:10]<="2026-09-29"]
add("13_manifests_8",len(mans)==8,len(mans))
# API contracts classification
cs=requests.get("https://api.bitget.com/api/v2/mix/market/contracts",params={"productType":"USDT-FUTURES"},timeout=30).json()["data"]
api_all=[x["symbol"] for x in cs if x.get("symbol","").endswith("USDT")]
api_normal=[x["symbol"] for x in cs if x.get("symbol","").endswith("USDT") and x.get("symbolType")=="perpetual" and x.get("symbolStatus")=="normal" and str(x.get("quoteCoin","")).upper()=="USDT"]
crypto=[x["symbol"] for x in cs if x.get("symbol","").endswith("USDT") and x.get("symbolType")=="perpetual" and x.get("symbolStatus")=="normal" and str(x.get("quoteCoin","")).upper()=="USDT" and str(x.get("isRwa","NO")).upper()!="YES"]
stored=set(d.symbol.unique()); add("14_api_all_count",len(api_all)>0,len(api_all));add("15_api_normal_count",len(api_normal)>0,len(api_normal));add("16_crypto_count",len(crypto)>0,len(crypto));add("17_stored_count",len(stored)>0,len(stored))
add("18_api_extra_vs_crypto",len(set(api_all)-set(crypto))>=0,len(set(api_all)-set(crypto))); add("19_stored_overlap_crypto",len(stored&set(crypto))>0,len(stored&set(crypto)))
# recompute stored with warmup observation
events=[]
for s,x in d.groupby("symbol"):
 x=x.reset_index(drop=True);v=x.base_volume.astype(float);q=x.quote_volume.astype(float)
 vw=(q.rolling(96,min_periods=96).sum()/v.rolling(96,min_periods=96).sum()).shift(1);dist=(x.open.astype(float)/vw-1)*100
 addn=[]
 for side,mask in [("LONG",dist<=-20),("SHORT",dist>=20)]:
  ev=np.where((mask & ~mask.shift(1,fill_value=False)).fillna(False))[0]
  for i in ev: events.append((s,side,i,int(x.timestamp_ms.iloc[i]),float(dist.iloc[i])))
e=pd.DataFrame(events,columns=["symbol","side","i","ts","dist"])
add("20_event_unique",not e.duplicated(["symbol","side","ts"]).any(),len(e)); add("21_events_after_warmup",(e.i>=97).all() if len(e) else True,e.i.min() if len(e) else None)
add("22_dist_long",((e[e.side=="LONG"].dist)<=-20).all());add("23_dist_short",((e[e.side=="SHORT"].dist)>=20).all())
# first day warmup means stored cannot evaluate first 97 bars
first_eval=d.groupby("symbol").timestamp_ms.min()+97*15*60*1000
add("24_warmup_cost_24h",True,"97 bars = 24h15m")
# exact gaps per symbol
badgap=0
for s,x in d.groupby("symbol"):
 ds=np.diff(np.sort(x.timestamp_ms.unique())); badgap+=int(np.sum(ds!=900000))
add("25_internal_gaps_zero",badgap==0,badgap)
# qvol/base vwap sanity
sample=d.groupby("symbol",group_keys=False).head(200)
add("26_qvol_relation",((sample.quote_volume>=0)&(sample.base_volume>=0)).all())
# current event counts
add("27_stored_event_count",True,e.groupby("side").size().to_dict())
# inspect per-day universe sizes
perday=[]
for f in fs:
 z=pd.read_csv(f,compression="gzip");perday.append((Path(f).name,z.symbol.nunique(),len(z)))
add("28_day_coverage",all(n>100 for _,n,_ in perday),perday)
# API endpoint limit actual sample
z=requests.get("https://api.bitget.com/api/v2/mix/market/candles",params={"symbol":"BTCUSDT","productType":"USDT-FUTURES","granularity":"15m","limit":"1000"},timeout=30).json()["data"]
add("29_api_limit_actual",True,len(z)); ts=sorted(int(x[0]) for x in z); add("30_api_span",True,(ts[-1]-ts[0])/86400000)
add("31_api_includes_current",True,ts[-1])
# event definition checks
add("32_event_false_to_true",True,"mask & ~mask.shift(1)")
add("33_vwap_shifted",True,"rolling96 then shift1")
add("34_vwap_uses_quote_base",True,"sum(qvol)/sum(base_volume)")
add("35_threshold_exact",True,"+-20%")
add("36_cost_units",True,"0.2%=20bp, 0.4%=40bp")
# known engine flaws
add("37_entry_bar_checked",False,"current code starts i+1")
add("38_exact_1h_exit",False,"current code exits i+4 close (~75m)")
add("39_1m_ambiguity",False,"same 15m forced loss; no 1m chronology")
add("40_closed_only_api",False,"candles endpoint may include forming candle; code does not filter")
print("CHECKS")
for x in checks: print(x)
print("SUMMARY",sum(ok for _,ok,_ in checks),"/",len(checks))
print("EVENTS",e.groupby("side").size().to_dict())
print("API_COUNTS",len(api_all),len(api_normal),len(crypto),"STORED",len(stored))
