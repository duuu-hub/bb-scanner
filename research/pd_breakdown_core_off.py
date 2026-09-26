import pandas as pd, numpy as np
from pathlib import Path
OUT=Path("out"); OUT.mkdir(exist_ok=True)
sel=pd.read_csv("pd/selected_trades.csv")
core=pd.read_csv("core/binance_daily_strategy.csv")
# tolerate selected output schema
for c in ["entry_time","entry_ts","datetime_utc"]:
    if c in sel.columns: sel["_ts"]=pd.to_datetime(sel[c],utc=True); break
else: raise ValueError("no PD entry timestamp")
core["datetime_utc"]=pd.to_datetime(core["datetime_utc"],utc=True)
core["date"]=core["datetime_utc"].dt.floor("D")
# 제1전략 = LONG-only; short/flat days are available to complementary strategy
core["core_long_on"]=pd.to_numeric(core["position"],errors="coerce").fillna(0)>0
m=sel.merge(core[["date","core_long_on"]],left_on=sel["_ts"].dt.floor("D"),right_on="date",how="left")
assert m["core_long_on"].notna().mean()>.99
m["core_long_on"]=m["core_long_on"].fillna(False)
# event identity and event return
evtcol="_ts"
rcol="r_net" if "r_net" in m.columns else ("r" if "r" in m.columns else None)
if rcol is None: raise ValueError("no R column")
rows=[]
for state,label in [(False,"CORE_OFF"),(True,"CORE_ON")]:
 z=m[m.core_long_on==state].copy()
 ev=z.groupby(evtcol)[rcol].mean()
 pos=ev[ev>0].sum(); neg=-ev[ev<0].sum()
 rows.append(dict(segment=label,events=ev.size,trades=len(z),event_pf=pos/neg if neg else np.inf,avg_event_r=ev.mean(),median_event_r=ev.median(),positive_event_pct=(ev>0).mean()*100,sum_event_r=ev.sum()))
pd.DataFrame(rows).to_csv(OUT/"core_off_summary.csv",index=False)
# fixed risk portfolio approximation by event basket aggregate; exact selected trades, chronological compounding
for risk in [.0025,.005,.0075,.01,.0125,.015,.02,.025,.03]:
 for state,label in [(False,"CORE_OFF"),(True,"CORE_ON")]:
  z=m[m.core_long_on==state].sort_values(evtcol)
  eq=1.; peak=1.; mdd=0.
  for _,g in z.groupby(evtcol):
   pnl=risk*g[rcol].sum()
   eq*=max(0,1+pnl); peak=max(peak,eq); mdd=max(mdd,1-eq/peak)
  rows.append(dict(segment=label,risk=risk,events=z[evtcol].nunique(),trades=len(z),final_equity=eq,total_return=eq-1,mdd_pct=mdd))
pd.DataFrame([x for x in rows if "risk" in x]).to_csv(OUT/"core_off_risk.csv",index=False)
m.to_csv(OUT/"matched_trades.csv",index=False)
print(pd.read_csv(OUT/"core_off_summary.csv").to_string(index=False))
print(pd.read_csv(OUT/"core_off_risk.csv").to_string(index=False))
