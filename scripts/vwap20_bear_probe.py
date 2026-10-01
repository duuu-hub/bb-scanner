import pandas as pd,glob,numpy as np
# raw condition events from count-audit shards; these contain event date/symbol and gross if available
fs=glob.glob("raw/**/*events*.csv",recursive=True)+glob.glob("raw/**/*event*.csv",recursive=True)
print("FILES",fs)
parts=[]
for f in fs:
 try:
  x=pd.read_csv(f)
  print(f,list(x.columns),len(x))
  if len(x): parts.append(x)
 except: pass
if not parts: raise RuntimeError("no event csv found")
d=pd.concat(parts,ignore_index=True)
r=pd.read_csv("reg/btc_regime.csv")
d["date"]=d["date"].astype(str)
d=d.merge(r[["date","btc_regime"]],on="date",how="left")
print("REGIME_COUNTS",d.groupby([pd.to_datetime(d.date).dt.year,"btc_regime"]).size())
d.to_csv("bear_raw_events.csv",index=False)
