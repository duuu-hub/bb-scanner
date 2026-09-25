#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
Path(a.out).mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"])
# frozen core only
c=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1.0)&(t.rr==3.0)].copy()
assert len(c)>1000
def stats(g):
 w=g.r_net[g.r_net>0].sum();l=-g.r_net[g.r_net<0].sum()
 return pd.Series({"trades":len(g),"symbols":g.symbol.nunique(),"wr":(g.r_net>0).mean(),"pf":w/l if l else np.nan,"expectancy_r":g.r_net.mean(),"median_r":g.r_net.median(),"net_return_sum":g.net_return.sum()})
stats(c).to_frame().T.to_csv(Path(a.out)/"core_overall.csv",index=False)
c.groupby("year").apply(stats,include_groups=False).reset_index().to_csv(Path(a.out)/"core_yearly.csv",index=False)
c.groupby("symbol").apply(stats,include_groups=False).reset_index().to_csv(Path(a.out)/"core_symbol.csv",index=False)
# chronological 60/40 OOS by time
cut=c.entry_time.quantile(.60); ins=c[c.entry_time<=cut];oos=c[c.entry_time>cut]
pd.concat([stats(ins).rename("IS"),stats(oos).rename("OOS")],axis=1).T.reset_index(names="split").assign(cutoff=str(cut)).to_csv(Path(a.out)/"core_oos.csv",index=False)
# cost stress: original net has 8bp; add extra to reach 12/16/24bp total
rows=[]
for cost in (.0008,.0012,.0016,.0024):
 x=c.copy();x["ret_stress"]=x.net_return-(cost-.0008);riskfrac=(x.net_return/x.r_net).replace([np.inf,-np.inf],np.nan)
 x["r_stress"]=x.ret_stress/riskfrac
 x=x[np.isfinite(x.r_stress)]
 w=x.r_stress[x.r_stress>0].sum();l=-x.r_stress[x.r_stress<0].sum()
 rows.append({"roundtrip_cost":cost,"trades":len(x),"wr":(x.r_stress>0).mean(),"pf":w/l if l else np.nan,"expectancy_r":x.r_stress.mean()})
pd.DataFrame(rows).to_csv(Path(a.out)/"core_cost_stress.csv",index=False)
# concentration
s=c.groupby("symbol").r_net.sum().sort_values(ascending=False); total=s.sum()
pd.DataFrame([{"top1_share":s.iloc[:1].sum()/total,"top5_share":s.iloc[:5].sum()/total,"top10_share":s.iloc[:10].sum()/total,"top20_share":s.iloc[:20].sum()/total,"positive_symbols":(s>0).sum(),"symbols":len(s)}]).to_csv(Path(a.out)/"core_concentration.csv",index=False)
# chronological portfolio slot simulations; fixed 1R per accepted trade, no compounding
pr=[]
for slots in (1,3,5,10,20,50):
 active=[]; eq=0.;peak=0.;mdd=0.;accepted=0
 for row in c.sort_values(["entry_time","symbol"]).itertuples():
  active=[z for z in active if z>row.entry_time]
  if len(active)>=slots: continue
  active.append(row.exit_time);eq+=row.r_net;accepted+=1;peak=max(peak,eq);mdd=max(mdd,peak-eq)
 pr.append({"slots":slots,"accepted":accepted,"accept_rate":accepted/len(c),"net_r":eq,"mdd_r":mdd,"avg_r":eq/accepted if accepted else np.nan})
pd.DataFrame(pr).to_csv(Path(a.out)/"core_portfolio_slots.csv",index=False)
print("CORE",stats(c).to_dict());print("OOS",stats(oos).to_dict());print(pd.DataFrame(rows).to_string(index=False));print(pd.DataFrame(pr).to_string(index=False))
