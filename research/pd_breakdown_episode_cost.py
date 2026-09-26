#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"]);t=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1)&(t.rr==3)].copy();t["year"]=t.entry_time.dt.year
w=pd.read_csv(a.wf);ch=w[["test_year","chosen_threshold"]].drop_duplicates();ch=ch[(ch.test_year>=2023)&(ch.test_year<=2026)]
assert len(t)>10000 and {"entry","exit","net_return","r_net"}.issubset(t.columns)
# exact risk fraction from source identity net=gross-8bp and r_net=net/risk_fraction
gross=(t.entry-t.exit)/t.entry
t["risk_frac"]=t.net_return/t.r_net
mask=t.r_net.abs()<1e-12;t.loc[mask,"risk_frac"]=np.nan
chk=(gross-t.net_return-0.0008).abs().dropna();assert chk.max()<1e-8
t["signals"]=t.groupby("entry_time").symbol.transform("nunique")
selected=[]
for y,th in ch.itertuples(index=False):
 z=t[(t.year==y)&(t.signals>=th)].sort_values(["entry_time","symbol"])
 for b in [5,10]:
  q=z.groupby("entry_time",group_keys=False).head(b).copy();q["basket"]=b;selected.append(q)
x=pd.concat(selected)
rows=[]
for gap in [0,12,24,48]:
 for b in [5,10]:
  z=x[x.basket==b].copy();times=sorted(z.entry_time.unique());keep=[];last=None
  for ts in times:
   ts=pd.Timestamp(ts)
   if last is None or gap==0 or ts-last>pd.Timedelta(hours=gap): keep.append(ts);last=ts
  q=z[z.entry_time.isin(keep)].copy()
  for bp in [8,12,16,24]:
   q["r_adj"]=q.r_net-((bp-8)/10000)/q.risk_frac
   ev=q.groupby("entry_time").r_adj.mean().sort_index();gp=ev[ev>0].sum();gl=-ev[ev<0].sum()
   rets=ev*(b*.0025);eq=(1+rets).cumprod();dd=eq/eq.cummax()-1
   rows.append(dict(episode_gap_h=gap,basket=b,cost_bp=bp,events=len(ev),trades=len(q),event_pf=gp/gl if gl else np.nan,avg_event_r=ev.mean(),total_return=eq.iloc[-1]-1,mdd=-dd.min()))
pd.DataFrame(rows).to_csv(O/"episode_cost.csv",index=False)
print("SANITY",len(t),t.symbol.nunique(),"fee_identity_maxerr",chk.max(),"risk_valid",t.risk_frac.notna().mean());print(pd.DataFrame(rows).to_string(index=False))
