#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd, numpy as np

ap=argparse.ArgumentParser()
ap.add_argument("--selected",required=True)
ap.add_argument("--accepted",required=True)
ap.add_argument("--out",required=True)
ap.add_argument("--seed",type=int,default=20260928)
a=ap.parse_args(); O=Path(a.out); O.mkdir(parents=True,exist_ok=True)

S=pd.read_csv(a.selected,parse_dates=["signal_time","entry_time","exit_time"])
A=pd.read_csv(a.accepted,parse_dates=["entry_time","exit_time"])
assert len(S)==420 and len(A)>300
assert S.entry_time.nunique()==42
assert S.groupby("entry_time").size().max()<=10

# 1) Event concentration on actually accepted 1% portfolio trades.
E=A.groupby("entry_time").agg(trades=("symbol","size"),sum_r=("r_net","sum"),mean_r=("r_net","mean")).reset_index().sort_values("entry_time")
total=E.sum_r.sum(); pos=E.loc[E.sum_r>0,"sum_r"].sum()
conc=[]
for k in [1,3,5,10]:
 z=E.nlargest(k,"sum_r")
 conc.append(dict(top_k=k,sum_r=z.sum_r.sum(),share_of_net=z.sum_r.sum()/total if total else np.nan,share_of_positive=z.sum_r.sum()/pos if pos else np.nan))
pd.DataFrame(conc).to_csv(O/"concentration.csv",index=False)
E.sort_values("sum_r",ascending=False).to_csv(O/"events_ranked.csv",index=False)

# 2) Year stability, event-level + trade-level.
yr=[]
for y,g in A.assign(year=A.entry_time.dt.year).groupby("year"):
 ev=g.groupby("entry_time").r_net.mean()
 gp=ev[ev>0].sum(); gl=-ev[ev<0].sum()
 tgp=g.loc[g.r_net>0,"r_net"].sum(); tgl=-g.loc[g.r_net<0,"r_net"].sum()
 yr.append(dict(year=int(y),events=ev.size,trades=len(g),trade_wr=(g.r_net>0).mean(),
                trade_pf=tgp/tgl if tgl else np.nan,avg_trade_r=g.r_net.mean(),
                event_pf=gp/gl if gl else np.nan,avg_event_r=ev.mean(),sum_r=g.r_net.sum()))
Y=pd.DataFrame(yr); Y.to_csv(O/"yearly.csv",index=False)

# 3) Cost stress. Original raw engine cost = 8 bp roundtrip.
# risk_pct = net_return / r_net is exact fee-adjusted R denominator (risk/entry).
risk_pct=S.net_return/S.r_net
assert np.isfinite(risk_pct).all() and (risk_pct>0).all()
rows=[]
for bps in [8,12,16,24,32]:
 new_net=S.net_return + 0.0008 - bps/10000.0
 q=S.copy(); q["r_cost"]=new_net/risk_pct
 ev=q.groupby("entry_time").r_cost.mean(); gp=ev[ev>0].sum(); gl=-ev[ev<0].sum()
 # exact slot admission and realized-equity replay at 1% risk/trade; exits unchanged by fee.
 cash=1.; peak=1.; mdd=0.; active=[]; accepted=0
 for r in q.sort_values(["entry_time","event_rank","symbol"]).itertuples():
  done=[p for p in active if p[0]<=r.entry_time]
  for ex,pnl in sorted(done,key=lambda x:x[0]):
   cash+=pnl; peak=max(peak,cash); mdd=max(mdd,(peak-cash)/peak)
  active=[p for p in active if p[0]>r.entry_time]
  if len(active)>=10: continue
  active.append((r.exit_time,cash*.01*r.r_cost)); accepted+=1
 for ex,pnl in sorted(active,key=lambda x:x[0]):
  cash+=pnl; peak=max(peak,cash); mdd=max(mdd,(peak-cash)/peak)
 rows.append(dict(cost_bps=bps,events=len(ev),accepted=accepted,event_pf=gp/gl if gl else np.nan,
                  avg_event_r=ev.mean(),final_equity_risk1=cash,realized_mdd_risk1=mdd))
C=pd.DataFrame(rows); C.to_csv(O/"cost_stress.csv",index=False)

# 4) Event-order permutation + bootstrap, intentionally in event-R space.
# This isolates whether historical event ordering/sample selection was unusually favorable.
er=E.set_index("entry_time").mean_r.sort_index().to_numpy(float)
def pf(v):
 gp=v[v>0].sum(); gl=-v[v<0].sum()
 return gp/gl if gl>0 else np.inf
def mdd_add(v):
 eq=np.cumsum(v); full=np.r_[0.0,eq]; peak=np.maximum.accumulate(full)
 return float(np.max(peak-full))
rng=np.random.default_rng(a.seed)
nmc=10000
perm_dd=np.empty(nmc)
boot_avg=np.empty(nmc); boot_pf=np.empty(nmc)
for i in range(nmc):
 perm_dd[i]=mdd_add(rng.permutation(er))
 b=rng.choice(er,size=len(er),replace=True); boot_avg[i]=b.mean(); boot_pf[i]=pf(b)
hist_dd=mdd_add(er)
mc=pd.DataFrame([dict(events=len(er),historical_avg_r=er.mean(),historical_event_pf=pf(er),historical_additive_mdd_r=hist_dd,
 perm_mdd_p05=np.quantile(perm_dd,.05),perm_mdd_p50=np.quantile(perm_dd,.50),perm_mdd_p95=np.quantile(perm_dd,.95),
 hist_mdd_percentile=(perm_dd<=hist_dd).mean(),
 boot_avg_p05=np.quantile(boot_avg,.05),boot_avg_p50=np.quantile(boot_avg,.50),boot_avg_p95=np.quantile(boot_avg,.95),
 boot_pf_p05=np.quantile(boot_pf,.05),boot_pf_p50=np.quantile(boot_pf,.50),boot_pf_p95=np.quantile(boot_pf,.95),
 prob_boot_avg_le0=(boot_avg<=0).mean(),prob_boot_pf_le1=(boot_pf<=1).mean())])
mc.to_csv(O/"event_mc.csv",index=False)

print("CONCENTRATION"); print(pd.DataFrame(conc).to_string(index=False))
print("YEARLY"); print(Y.to_string(index=False))
print("COST"); print(C.to_string(index=False))
print("MC"); print(mc.to_string(index=False))
