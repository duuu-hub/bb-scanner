from __future__ import annotations
import json,time,urllib.parse,urllib.request,zipfile
from pathlib import Path
import pandas as pd, numpy as np
ZIP=Path("input.zip"); WORK=Path("work"); OUT=Path("continuation_1m_final"); OUT.mkdir(exist_ok=True); WORK.mkdir(exist_ok=True)
with zipfile.ZipFile(ZIP) as z:z.extractall(WORK)
d=pd.read_csv(WORK/"trades_one_position_per_symbol.csv.gz")
a=d[d.exit_reason.eq("BOTH_SL")].copy()
API="https://api.bitget.com/api/v3/market/history-candles"
def fetch(sym,ts):
 q=urllib.parse.urlencode(dict(category="USDT-FUTURES",symbol=sym,interval="1m",startTime=str(int(ts)),endTime=str(int(ts)+900000-1),type="market",limit="100"))
 req=urllib.request.Request(API+"?"+q,headers={"User-Agent":"bb-scanner-research/1.0"})
 with urllib.request.urlopen(req,timeout=30) as r:o=json.load(r)
 if str(o.get("code"))!="00000": raise RuntimeError(o)
 return sorted([(int(x[0]),float(x[2]),float(x[3])) for x in o.get("data",[]) if int(ts)<=int(x[0])<int(ts)+900000])
def resolve(r,b):
 ep=float(r.entry_px); tp=ep*.95; sl=ep*1.03
 expected=set(range(int(r.exit_ts),int(r.exit_ts)+900000,60000)); got={x[0] for x in b}
 if expected-got:return "INCOMPLETE"
 for t,h,l in b:
  ht=l<=tp; hs=h>=sl
  if ht and hs:return "BOTH_1M_SL"
  if ht:return "TP_FIRST"
  if hs:return "SL_FIRST"
 return "NO_TOUCH"
rows=[]
for r in a.itertuples():
 try:v=resolve(r,fetch(r.symbol,int(r.exit_ts))); err=""
 except Exception as e:v="FETCH_ERROR";err=repr(e)
 rows.append(dict(symbol=r.symbol,exit_ts=int(r.exit_ts),verdict=v,error=err)); print(r.symbol,r.exit_ts,v,flush=True);time.sleep(.08)
z=pd.DataFrame(rows);z.to_csv(OUT/"resolution.csv",index=False)
tp=set(zip(z.loc[z.verdict.eq("TP_FIRST"),"symbol"],z.loc[z.verdict.eq("TP_FIRST"),"exit_ts"]))
for i,r in d[d.exit_reason.eq("BOTH_SL")].iterrows():
 if (r.symbol,int(r.exit_ts)) in tp:d.at[i,"gross_ret_pct"]=5.;d.at[i,"exit_reason"]="TP_1M"
net=d.gross_ret_pct-.20; pf=net[net>0].sum()/abs(net[net<0].sum())
summary=dict(n=len(d),win_rate=float((net>0).mean()),expectancy_pct=float(net.mean()),pf=float(pf),ambiguous_15m=len(a),tp_first=len(tp),unresolved=int((~z.verdict.isin(["TP_FIRST","SL_FIRST","BOTH_1M_SL"])).sum()))
pd.DataFrame([summary]).to_csv(OUT/"summary.csv",index=False);d.to_csv(OUT/"trades_final.csv.gz",index=False,compression="gzip")
print("FINAL",summary,flush=True);print(z.verdict.value_counts(),flush=True)
