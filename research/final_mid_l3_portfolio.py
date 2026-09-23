from __future__ import annotations
import pandas as pd, numpy as np
from pathlib import Path
OUT=Path("final_portfolio_results"); OUT.mkdir(exist_ok=True)
def pf(x):
 x=pd.to_numeric(x,errors="coerce").dropna(); n=-x[x<0].sum(); return x[x>0].sum()/n if n>0 else np.inf
def stats(g):
 x=g.sort_values("signal_ts").net_pct.astype(float); eq=x.cumsum(); dd=eq-eq.cummax()
 return {"n":len(x),"symbols":g.symbol.nunique(),"avg_pct":x.mean(),"sum_pct":x.sum(),"pf":pf(x),"mdd_additive_pct":dd.min() if len(x) else np.nan}
def main():
 h=pd.read_csv("holdout/enriched_l1_trades.csv.gz")
 pct=next(c for c in h if "btc_rv7d_pctile" in c)
 # frozen diagnostic candidate 45-55; SHORT only
 mid=h[(h[pct]>=45)&(h[pct]<55)&(h.direction=="SHORT")].copy(); mid["leg"]="MID45_55_SHORT"
 # L3 rows from exhaustive artifact; use policy output if available
 l3=None
 for f in Path("context").rglob("*.csv*"):
  try:q=pd.read_csv(f)
  except:continue
  if {"symbol","signal_ts","delay_min","direction","net_pct"}.issubset(q.columns):
   # prefer explicit L3 policy labels
   cols=" ".join(q.columns).lower()
   if "policy" in cols and any(q[c].astype(str).str.contains("L3",case=False,na=False).any() for c in q.columns if "policy" in c.lower()):
    l3=q; print("L3 source",f); break
 if l3 is None:
  raise RuntimeError("No explicit L3 policy trade artifact found")
 pc=next(c for c in l3.columns if "policy" in c.lower() and l3[c].astype(str).str.contains("L3",case=False,na=False).any())
 l3=l3[l3[pc].astype(str).str.contains("L3",case=False,na=False)&(l3.direction=="SHORT")].copy(); l3["leg"]="L3_SPECIAL_SHORT"
 rows=[]; overlaps=[]
 for d in (1,2,3):
  a=mid[mid.delay_min==d].copy(); b=l3[l3.delay_min==d].copy()
  # dedupe exact symbol/time: special L3 takes precedence
  keys=set(zip(b.symbol,b.signal_ts)); aa=a[[ (s,t) not in keys for s,t in zip(a.symbol,a.signal_ts)]]
  combo=pd.concat([aa,b],ignore_index=True)
  for name,g in [("MID45_55_SHORT",a),("L3_SPECIAL_SHORT",b),("COMBINED",combo)]:
   rows.append({"delay":d,"scope":name,**stats(g)})
  overlaps.append({"delay":d,"mid_events":len(a),"l3_events":len(b),"exact_overlap":len(a)-len(aa)})
 pd.DataFrame(rows).to_csv(OUT/"portfolio_summary.csv",index=False); pd.DataFrame(overlaps).to_csv(OUT/"overlap.csv",index=False)
 print(pd.DataFrame(rows).to_string(index=False)); print(pd.DataFrame(overlaps).to_string(index=False))
if __name__=="__main__":main()
