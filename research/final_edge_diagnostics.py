from __future__ import annotations
import pandas as pd, numpy as np
from pathlib import Path
OUT=Path("final_edge_diagnostics_results"); OUT.mkdir(exist_ok=True)
def m(g):
 x=pd.to_numeric(g.net_pct,errors="coerce").dropna(); pos=x[x>0].sum(); neg=-x[x<0].sum()
 return {"n":len(x),"symbols":g.symbol.nunique(),"avg":x.mean() if len(x) else np.nan,"pf":pos/neg if neg>0 else np.inf}
def main():
 p=Path("prior_switch")
 files=list(p.rglob("*.csv"))
 cand=[x for x in files if "trade" in x.name.lower() or "row" in x.name.lower()]
 if not cand: cand=files
 df=None
 for f in cand:
  q=pd.read_csv(f)
  if {"symbol","net_pct"}.issubset(q.columns) and any("btc_rv7d_pctile" in c for c in q.columns):
   df=q; print("using",f); break
 if df is None: raise RuntimeError("No enriched holdout trade CSV found: "+str([str(x) for x in files]))
 pct=[c for c in df.columns if "btc_rv7d_pctile" in c][0]
 rows=[]
 # 1: MID internal structure, diagnostic only
 for lo,hi in [(33.333,45),(45,55),(55,66.667)]:
  for d in (1,2,3):
   g=df[(df[pct]>=lo)&(df[pct]<hi)&(df.delay_min==d)&(df.direction=="SHORT")]
   rows.append({"test":"MID_BUCKET","bucket":f"{lo}-{hi}","delay":d,**m(g)})
 # 3: HIGH/TOTAL3 anatomy without optimizing thresholds
 high=df[pct]>=66.667
 t3col=next((c for c in ["t3_ret24h","t3_ret_24h","total3_ret24h","t3_24h_ret"] if c in df.columns),None)
 if t3col:
  defs={"HIGH_ONLY":high,"T3UP_ONLY":df[t3col]>0,"HIGH_AND_T3UP":high&(df[t3col]>0)}
  for name,mask in defs.items():
   for d in (1,2,3):
    for direction in ("LONG","SHORT"):
     g=df[mask&(df.delay_min==d)&(df.direction==direction)]
     rows.append({"test":"TOTAL3_ANATOMY","bucket":name,"delay":d,"direction":direction,**m(g)})
 pd.DataFrame(rows).to_csv(OUT/"diagnostics.csv",index=False)
 # 2: AUTO50 vs holdout characteristics available in enriched file
 charcols=[c for c in ["btc_rv7d_pctile_365d","btc_rv7d_pctile365","t3_ret24h","t3_ret_24h","signal_ts"] if c in df.columns]
 df.groupby("symbol").agg(trades=("net_pct","size"),avg=("net_pct","mean"),pnl=("net_pct","sum")).to_csv(OUT/"holdout_symbol_profile.csv")
 print(pd.DataFrame(rows).to_string(index=False))
if __name__=="__main__": main()
