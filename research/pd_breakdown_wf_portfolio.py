#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd, numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"])
t=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1.0)&(t.rr==3.0)].copy()
w=pd.read_csv(a.wf)
t["year"]=t.entry_time.dt.year
assert len(t)>10000 and t.symbol.nunique()>500
# WF choices are fixed by prior-year data. 2022 has no usable prior sample and is excluded.
choices=w[["test_year","chosen_threshold"]].drop_duplicates()
choices=choices[(choices.test_year>=2023)&(choices.test_year<=2026)]
assert choices.test_year.is_unique and len(choices)==4
# neutral deterministic basket control retained from prior validation
sel=[]
for y,th in choices.itertuples(index=False):
 z=t[(t.year==y)&(t.groupby("entry_time").symbol.transform("nunique")>=th)].copy()
 z=z.sort_values(["entry_time","symbol"])
 for b in [5,10]:
  q=z.groupby("entry_time",group_keys=False).head(b).copy();q["basket"]=b;q["threshold"]=th;sel.append(q)
x=pd.concat(sel,ignore_index=True)
assert len(x)>0
# Equal risk allocation inside each event; 0.25% account risk per symbol at baseline.
# r_net already contains source 8bp cost. Additional cost stress converts extra bp to R using source return/R relation unavailable,
# so recompute conservative R haircut proportional to extra roundtrip cost / stop distance proxy from raw fields if available.
# If exact price/ATR fields unavailable, do NOT invent conversion: report baseline only and flag.
need={"entry_price","stop_price"}
can_cost=need.issubset(x.columns)
costs=[8,12,16,24] if can_cost else [8]
rows=[]; annual=[]
for b in [5,10]:
 z=x[x.basket==b].copy()
 for bp in costs:
  q=z.copy()
  if bp==8: q["r_adj"]=q.r_net
  else:
   risk=(q.stop_price-q.entry_price).abs()/q.entry_price
   q["r_adj"]=q.r_net-((bp-8)/10000)/risk
  ev=q.groupby("entry_time").agg(r=("r_adj","mean"),year=("year","first")).reset_index().sort_values("entry_time")
  # one event risks b*0.25% spread equally across selected symbols => event account return = meanR * b*0.25%
  rets=ev.r*(b*0.0025); eq=(1+rets).cumprod(); peak=eq.cummax(); dd=eq/peak-1
  loss=(ev.r<=0).astype(int); grp=(loss.ne(loss.shift())).cumsum(); streak=loss.groupby(grp).cumsum().max()
  gp=ev.loc[ev.r>0,"r"].sum();gl=-ev.loc[ev.r<0,"r"].sum()
  rows.append({"basket":b,"cost_bp":bp,"events":len(ev),"trades":len(q),"event_pf":gp/gl if gl else np.nan,"avg_event_r":ev.r.mean(),"final_equity":eq.iloc[-1],"total_return":eq.iloc[-1]-1,"mdd":-dd.min(),"max_event_loss_streak":int(streak)})
  for y,g in ev.groupby("year"):
   gp=g.loc[g.r>0,"r"].sum();gl=-g.loc[g.r<0,"r"].sum()
   annual.append({"basket":b,"cost_bp":bp,"year":int(y),"events":len(g),"event_pf":gp/gl if gl else np.nan,"avg_event_r":g.r.mean(),"sum_event_r":g.r.sum()})
pd.DataFrame(rows).to_csv(O/"portfolio_summary.csv",index=False);pd.DataFrame(annual).to_csv(O/"annual.csv",index=False)
pd.DataFrame([{"core_rows":len(t),"symbols":t.symbol.nunique(),"wf_years":",".join(map(str,choices.test_year)),"cost_stress_exact":can_cost}]).to_csv(O/"sanity.csv",index=False)
print("SANITY",len(t),t.symbol.nunique(),choices.to_dict("records"),"cost_exact",can_cost)
print(pd.DataFrame(rows).to_string(index=False));print(pd.DataFrame(annual).to_string(index=False))
