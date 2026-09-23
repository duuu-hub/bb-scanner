from __future__ import annotations
import pandas as pd, numpy as np
from pathlib import Path
OUT=Path("final_portfolio_results"); OUT.mkdir(exist_ok=True)
def pf(x):
 x=pd.to_numeric(x,errors="coerce").dropna(); neg=-x[x<0].sum()
 return float(x[x>0].sum()/neg) if neg>0 else (float("inf") if (x>0).any() else float("nan"))
def stats(g):
 if g.empty:return {"n":0,"symbols":0,"avg_pct":np.nan,"sum_pct":0.0,"pf":np.nan,"mdd_additive_pct":np.nan}
 g=g.sort_values("signal_ts"); x=pd.to_numeric(g.net_pct,errors="coerce").fillna(0); eq=x.cumsum(); dd=eq-eq.cummax()
 return {"n":len(g),"symbols":g.symbol.nunique(),"avg_pct":float(x.mean()),"sum_pct":float(x.sum()),"pf":pf(x),"mdd_additive_pct":float(dd.min())}
def find_file(root, required):
 for f in Path(root).rglob("*.csv*"):
  try:q=pd.read_csv(f)
  except:continue
  if required.issubset(q.columns): return f,q
 raise RuntimeError(f"no compatible file under {root}")
def main():
 # Same AUTO50 research universe/date base for a meaningful portfolio combination.
 f,a=find_file("context",{"symbol","signal_ts","delay_min","direction","net_pct"})
 pct=next(c for c in a.columns if "btc_rv7d_pctile" in c)
 strat=next((c for c in ("base_strategy","strategy") if c in a.columns),None)
 if strat:a=a[a[strat].astype(str).eq("L1")]
 mid=a[(a[pct]>=45)&(a[pct]<55)&a.direction.eq("SHORT")].copy(); mid["leg"]="MID45_55_SHORT"
 rf,r=find_file("regime",{"symbol","signal_ts","delay_min","direction","net_pct","base_strategy","regime_60_40"})
 l3=r[r.base_strategy.astype(str).eq("L3") & r.direction.eq("SHORT") & r.regime_60_40.astype(str).eq("BEAR")].copy(); l3["leg"]="L3_BEAR_SHORT"
 print("MID source",f,"L3 source",rf)
 rows=[]; ovs=[]
 for d in (1,2,3):
  m=mid[mid.delay_min==d].copy(); s=l3[l3.delay_min==d].copy()
  sk=set(zip(s.symbol.astype(str),s.signal_ts.astype("int64")))
  keep=[(str(sym),int(ts)) not in sk for sym,ts in zip(m.symbol,m.signal_ts)]
  md=m.loc[keep]; combo=pd.concat([md,s],ignore_index=True)
  for name,g in (("MID45_55_SHORT",m),("L3_BEAR_SHORT",s),("COMBINED",combo)):
   rows.append({"delay":d,"scope":name,**stats(g)})
  ovs.append({"delay":d,"mid_trades":len(m),"l3_trades":len(s),"exact_symbol_time_overlap":len(m)-len(md),"combined_trades":len(combo)})
 sm=pd.DataFrame(rows); ov=pd.DataFrame(ovs)
 sm.to_csv(OUT/"portfolio_summary.csv",index=False); ov.to_csv(OUT/"overlap.csv",index=False)
 print("\n=== FINAL AUTO50 MID45-55 + L3 BEAR SHORT ==="); print(sm.to_string(index=False))
 print("\n=== OVERLAP ==="); print(ov.to_string(index=False))
 print("\nNOTE: 45-55 is a post-hoc diagnostic candidate, not independently validated. L3 is rare/small-n.")
if __name__=="__main__":main()
