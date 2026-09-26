#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd, numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"])
c=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1.0)&(t.rr==3.0)].copy()
assert len(c)>10000 and c.symbol.nunique()>500 and c.entry_time.notna().all()
# deterministic strength ranking: more negative entry move if available, else symbol order only as explicit neutral control
rankcols=[x for x in ["break_strength","distance_atr","signal_strength"] if x in c.columns]
c["year"]=c.entry_time.dt.year
ev=c.groupby("entry_time").symbol.nunique().rename("signals")
c=c.join(ev,on="entry_time")
thresholds=[51,61,71,81,91,101]; baskets=[5,10]
# expanding walk-forward: evaluate each calendar year using threshold selected ONLY from prior years among thresholds, by event mean R
rows=[]; controls=[]
years=sorted(c.year.unique())
for testy in years[1:]:
 train=c[c.year<testy]; test=c[c.year==testy]
 train_ev=train.groupby("entry_time").agg(signals=("symbol","nunique"),event_r=("r_net","mean")).reset_index()
 cand=[]
 for th in thresholds:
  z=train_ev[train_ev.signals>=th]
  cand.append((z.event_r.mean() if len(z)>=3 else -np.inf,th,len(z)))
 _,chosen,ntrain=max(cand)
 for th in thresholds:
  for b in baskets:
   z=test[test.signals>=th].copy()
   if rankcols: z=z.sort_values(["entry_time",rankcols[0],"symbol"],ascending=[True,False,True])
   else: z=z.sort_values(["entry_time","symbol"])
   z=z.groupby("entry_time",group_keys=False).head(b)
   er=z.groupby("entry_time").r_net.mean()
   gp=er[er>0].sum();gl=-er[er<0].sum()
   controls.append({"test_year":testy,"threshold":th,"basket":b,"events":len(er),"trades":len(z),"event_wr":(er>0).mean() if len(er) else np.nan,"event_pf":gp/gl if gl>0 else np.nan,"avg_event_r":er.mean() if len(er) else np.nan})
 for b in baskets:
  z=test[test.signals>=chosen].copy()
  if rankcols: z=z.sort_values(["entry_time",rankcols[0],"symbol"],ascending=[True,False,True])
  else: z=z.sort_values(["entry_time","symbol"])
  z=z.groupby("entry_time",group_keys=False).head(b);er=z.groupby("entry_time").r_net.mean()
  gp=er[er>0].sum();gl=-er[er<0].sum()
  rows.append({"test_year":testy,"chosen_threshold":chosen,"train_events":ntrain,"basket":b,"events":len(er),"trades":len(z),"event_wr":(er>0).mean() if len(er) else np.nan,"event_pf":gp/gl if gl>0 else np.nan,"avg_event_r":er.mean() if len(er) else np.nan})
pd.DataFrame(rows).to_csv(O/"walkforward.csv",index=False);pd.DataFrame(controls).to_csv(O/"threshold_grid.csv",index=False)
pd.DataFrame([{"core_rows":len(c),"symbols":c.symbol.nunique(),"events":c.entry_time.nunique(),"rank_field":rankcols[0] if rankcols else "NONE_NEUTRAL_SYMBOL"}]).to_csv(O/"sanity.csv",index=False)
print("SANITY",len(c),c.symbol.nunique(),c.entry_time.nunique(),rankcols);print(pd.DataFrame(rows).to_string(index=False))
