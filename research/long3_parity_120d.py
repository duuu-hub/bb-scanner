from pathlib import Path
import pandas as pd, numpy as np
from long3_5y_regime import signals
Path("parity_results").mkdir(exist_ok=True)
orig=pd.read_csv("original_signals.csv")
lo,hi=int(orig.ts.min()),int(orig.ts.max())
parquets=sorted(Path("canonical_um").glob("*.parquet"))
available={p.stem for p in parquets}
rows=[]
for p in parquets:
 d=pd.read_parquet(p)
 # Weekly BB needs 19 completed weeks before the comparison window.
 d=d[(d.open_time>=lo-180*24*3600*1000)&(d.open_time<=hi+24*3600*1000)]
 if len(d)<100: continue
 try:
  ss,_=signals(p.stem,d)
  for sym,t,st,price in ss:
   ts=int(t.timestamp()*1000)
   if lo<=ts<=hi: rows.append([sym,ts,st,price])
 except Exception as e: print("ERR",p.stem,e)
new=pd.DataFrame(rows,columns=["symbol","ts","strategy","price"])
# 5Y engine originally did not priority-dedupe; compare both raw and priority-deduped
priority={"L1":0,"L2":1,"L3":2}
new["pri"]=new.strategy.map(priority)
nd=new.sort_values(["ts","symbol","pri"]).drop_duplicates(["ts","symbol"],keep="first").drop(columns="pri")
omap={"L1_MOMENTUM_1H10":"L1","L2_EXPLOSIVE_4H30":"L2","L3_4H_LAG":"L3"}
o=orig[orig.symbol.isin(available)].copy();o["strategy"]=o.strategy.map(omap)
print("COMMON_UNIVERSE",len(available),"ORIGINAL_COMMON",len(o),"ORIGINAL_ALL",len(orig))
keys=["symbol","ts","strategy"]
a=set(map(tuple,o[keys].values.tolist()));b=set(map(tuple,nd[keys].values.tolist()))
print("ORIGINAL",len(o),o.strategy.value_counts().to_dict())
print("RECON",len(nd),nd.strategy.value_counts().to_dict())
print("MATCH",len(a&b),"ORIG_ONLY",len(a-b),"RECON_ONLY",len(b-a))
print("recall_pct",100*len(a&b)/len(a) if a else None,"precision_pct",100*len(a&b)/len(b) if b else None)
pd.DataFrame(list(a-b),columns=keys).to_csv("parity_results/original_only.csv",index=False)
pd.DataFrame(list(b-a),columns=keys).to_csv("parity_results/reconstructed_only.csv",index=False)
pd.DataFrame(list(a&b),columns=keys).to_csv("parity_results/matched.csv",index=False)
