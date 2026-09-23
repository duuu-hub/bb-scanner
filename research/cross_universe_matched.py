from __future__ import annotations
import pandas as pd, numpy as np
from pathlib import Path
OUT=Path("cross_universe_matched_results"); OUT.mkdir(exist_ok=True)
def pf(x):
 x=pd.to_numeric(x,errors="coerce").dropna(); neg=-x[x<0].sum(); return x[x>0].sum()/neg if neg>0 else np.inf
def met(g):
 return {"n":len(g),"symbols":g.symbol.nunique(),"avg":g.net_pct.mean(),"pf":pf(g.net_pct)}
def analyze(df,label):
 pct=next(c for c in df if "btc_rv7d_pctile" in c)
 rows=[]
 for lo,hi in [(33.333,45),(45,55),(55,66.667)]:
  for d in (1,2,3):
   g=df[(df[pct]>=lo)&(df[pct]<hi)&(df.delay_min==d)&(df.direction=="SHORT")]
   rows.append({"universe":label,"test":"MID_BUCKET","bucket":f"{lo}-{hi}","delay":d,**met(g)})
 # exact matched HIGH+T3UP, both directions from enriched rows
 if "t3_ret_24h" in df:
  mask=(df[pct]>=66.667)&(df.t3_ret_24h>0)
  for d in (1,2,3):
   base=df[mask&(df.delay_min==d)]
   for direction in ("LONG","SHORT"):
    g=base[base.direction==direction]
    rows.append({"universe":label,"test":"HIGH_T3UP_MATCHED","bucket":"HIGH_T3UP","delay":d,"direction":direction,**met(g)})
 return rows
def main():
 h=pd.read_csv("holdout/enriched_l1_trades.csv.gz")
 rows=analyze(h,"NEW66")
 # prior AUTO50 artifact: search enriched trade-like csvs and analyze compatible one
 for f in Path("auto50").rglob("*.csv*"):
  try:q=pd.read_csv(f)
  except:continue
  if {"symbol","delay_min","direction","net_pct"}.issubset(q.columns) and any("btc_rv7d_pctile" in c for c in q.columns):
   if "t3_ret_24h" not in q.columns: continue
   rows+=analyze(q,"AUTO50"); print("AUTO50 source",f); break
 else: print("WARN no compatible AUTO50 enriched trade file")
 out=pd.DataFrame(rows); out.to_csv(OUT/"cross_universe_mid_and_matched.csv",index=False); print(out.to_string(index=False))
if __name__=="__main__":main()
