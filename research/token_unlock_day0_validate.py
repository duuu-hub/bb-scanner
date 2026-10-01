#!/usr/bin/env python3
import io, json, zipfile
from pathlib import Path
import pandas as pd, requests

OUT=Path("token_unlock_day0_result")
events=pd.read_csv(OUT/"events_day0.csv")
s=requests.Session(); s.headers.update({"User-Agent":"bb-scanner-token-unlock-validation/1.0"})

def get_daily(symbol,date,market):
    base = "spot" if market=="spot" else "futures/um"
    url=f"https://data.binance.vision/data/{base}/daily/klines/{symbol}/1d/{symbol}-1d-{date}.zip"
    r=s.get(url,timeout=30)
    if r.status_code!=200: return None
    with zipfile.ZipFile(io.BytesIO(r.content)) as z: b=z.read(z.namelist()[0])
    df=pd.read_csv(io.BytesIO(b),header=None)
    first=str(df.iloc[0,0]).lower()
    if "open" in first or not first.replace(".","",1).isdigit(): df=df.iloc[1:].reset_index(drop=True)
    if df.empty: return None
    return float(df.iloc[0,1]), float(df.iloc[0,4])

rows=[]
for _,r in events.iterrows():
    sym=str(r.token_symbol)+"USDT"; date=str(r.unlock_date); market=str(r.market_source)
    x=get_daily(sym,date,market)
    if x is None:
        rows.append({"token_symbol":r.token_symbol,"unlock_date":date,"ok":False,"reason":"daily_missing"})
        continue
    o,c=x
    daily_ret=c/o-1.0
    diff_open=abs(o-float(r.day_open_utc))
    diff_ret=abs(daily_ret-float(r.ret_24h))
    ok=diff_open <= max(1e-12,abs(o)*1e-12) and diff_ret < 1e-12
    rows.append({"token_symbol":r.token_symbol,"unlock_date":date,"ok":ok,
                 "hourly_open":float(r.day_open_utc),"daily_open":o,
                 "hourly_ret24":float(r.ret_24h),"daily_ret24":daily_ret,
                 "abs_ret_diff":diff_ret})
val=pd.DataFrame(rows)
val.to_csv(OUT/"daily_crosscheck.csv",index=False)
summary={"n":len(val),"checked":int(val["daily_ret24"].notna().sum()) if "daily_ret24" in val else 0,
         "passed":int(val["ok"].sum()),"failed":int((~val["ok"]).sum())}
(OUT/"daily_crosscheck.json").write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
if summary["checked"] < int(0.9*summary["n"]) or summary["failed"] != 0:
    raise SystemExit("daily/hourly crosscheck failed")
print("DAILY_CROSSCHECK_PASS")
