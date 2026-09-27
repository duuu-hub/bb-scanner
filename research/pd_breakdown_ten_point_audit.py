#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["signal_time","entry_time","exit_time"])
t=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1)&(t.rr==3)].copy()
t["year"]=t.entry_time.dt.year;t["signals"]=t.groupby("entry_time").symbol.transform("nunique")
w=pd.read_csv(a.wf);w=w[(w.basket==5)&w.test_year.between(2023,2026)][["test_year","chosen_threshold"]].drop_duplicates()
checks=[]
def ck(n,ok,detail): checks.append(dict(check=n,passed=bool(ok),detail=str(detail)))
ck("01_no_duplicate_symbol_entry",not t.duplicated(["symbol","entry_time"]).any(),t.duplicated(["symbol","entry_time"]).sum())
ck("02_signal_to_entry_exact4h",((t.entry_time-t.signal_time)==pd.Timedelta(hours=4)).all(),((t.entry_time-t.signal_time)/pd.Timedelta(hours=1)).value_counts().head().to_dict())
hold=(t.exit_time-t.entry_time)/pd.Timedelta(hours=1)
ck("03_exit_order_and_hold",((hold>=0)&(hold<=20)).all(),{"min":hold.min(),"max":hold.max()})
gross=(t.entry-t.exit)/t.entry; ferr=(gross-t.net_return-.0008).abs().max()
ck("04_fee_identity",ferr<1e-10,ferr)
rf=t.net_return/t.r_net
ck("05_risk_fraction_valid",np.isfinite(rf).all() and (rf>0).all(),{"min":rf.min(),"max":rf.max()})
wfrows=[]
for y,th in w.itertuples(index=False):
 train=t[t.year<y]; ev=train.groupby("entry_time").agg(signals=("symbol","nunique"),r=("r_net","mean")).reset_index()
 cand=[]
 for q in [51,61,71,81,91,101]:
  z=ev[ev.signals>=q];cand.append((z.r.mean() if len(z)>=3 else -np.inf,q,len(z)))
 best=max(cand);wfrows.append((y,int(th),int(best[1]),int(best[2])))
ck("06_walkforward_train_only",all(x[1]==x[2] for x in wfrows),wfrows)
z=pd.concat([t[(t.year==y)&(t.signals>=th)] for y,th in w.itertuples(index=False)])
ck("07_selected_event_count",z.entry_time.nunique()==42,{"events":z.entry_time.nunique(),"rows":len(z)})
# basket 5 deterministic event construction
b=z.sort_values(["entry_time","symbol"]).groupby("entry_time",group_keys=False).head(5)
ck("08_basket5_integrity",b.entry_time.nunique()==42 and b.groupby("entry_time").size().max()<=5,{"events":b.entry_time.nunique(),"trades":len(b),"max_per_event":b.groupby("entry_time").size().max()})
# collision policy audit from canonical reasons
amb=(t.reason=="SL_AMBIG").sum(); valid=t.reason.isin(["SL","TP","TIME","SL_AMBIG"]).all()
ck("09_exit_reason_domain_and_ambiguous_sl",valid,{"ambiguous_sl":int(amb),"reasons":t.reason.value_counts().to_dict()})
# source chronology / no overlapping self-trades for same symbol because generator advances next_i
q=t.sort_values(["symbol","entry_time"]); bad=0
for _,g in q.groupby("symbol"):
 bad+=int((g.entry_time.iloc[1:].reset_index(drop=True)<g.exit_time.iloc[:-1].reset_index(drop=True)).sum())
ck("10_same_symbol_nonoverlap",bad==0,bad)
R=pd.DataFrame(checks);R.to_csv(O/"ten_point_audit.csv",index=False);print(R.to_string(index=False));print("TEN_AUDIT_PASS",R.passed.all())
if not R.passed.all(): raise SystemExit(2)
