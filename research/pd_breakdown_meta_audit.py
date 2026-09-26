#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--wf",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["signal_time","entry_time","exit_time"]);t=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1)&(t.rr==3)].copy();t["year"]=t.entry_time.dt.year
w=pd.read_csv(a.wf)[["test_year","chosen_threshold"]].drop_duplicates();w=w[(w.test_year>=2023)&(w.test_year<=2026)]
# duplicates/timing/source identities
dup=t.duplicated(["symbol","entry_time"]).sum();timing=((t.entry_time-t.signal_time)==pd.Timedelta(hours=4)).mean();exit_ok=(t.exit_time>=t.entry_time).mean();maxhold=(t.exit_time-t.entry_time).max()/pd.Timedelta(hours=1)
gross=(t.entry-t.exit)/t.entry;fee_err=(gross-t.net_return-.0008).abs().max();risk=t.net_return/t.r_net
# selected event universe
t["signals"]=t.groupby("entry_time").symbol.transform("nunique");zs=[]
for y,th in w.itertuples(index=False):zs.append(t[(t.year==y)&(t.signals>=th)])
z=pd.concat(zs);times=pd.Series(sorted(z.entry_time.unique()));gaps=times.diff()/pd.Timedelta(hours=1)
overlap24=int((gaps<=24).sum());overlap48=int((gaps<=48).sum())
# cooldown count vs transitive episode cluster count
def cooldown(h):
 keep=[];last=None
 for ts in times:
  if last is None or ts-last>pd.Timedelta(hours=h):keep.append(ts);last=ts
 return len(keep)
def cluster(h):
 if len(times)==0:return 0
 n=1
 for a,b in zip(times.iloc[:-1],times.iloc[1:]):
  if b-a>pd.Timedelta(hours=h):n+=1
 return n
# threshold provenance and train-only check
wf=[]
for y,th in w.itertuples(index=False):
 train=t[t.year<y];ev=train.groupby("entry_time").agg(signals=("symbol","nunique"),r=("r_net","mean")).reset_index();cand=[]
 for q in [51,61,71,81,91,101]:
  a=ev[ev.signals>=q];cand.append((a.r.mean() if len(a)>=3 else -np.inf,q,len(a)))
 best=max(cand);wf.append(dict(year=y,stored=int(th),recalc=int(best[1]),train_events=int(best[2]),match=int(th)==int(best[1])))
S=dict(rows=len(t),symbols=t.symbol.nunique(),duplicate_symbol_entry=int(dup),timing_4h_rate=timing,exit_order_rate=exit_ok,max_exit_timestamp_gap_h=maxhold,fee_identity_maxerr=fee_err,risk_valid_rate=np.isfinite(risk).mean(),selected_events=len(times),gaps_le24=overlap24,gaps_le48=overlap48,cooldown24=cooldown(24),cluster24=cluster(24),cooldown48=cooldown(48),cluster48=cluster(48))
pd.DataFrame([S]).to_csv(O/"audit_summary.csv",index=False);pd.DataFrame(wf).to_csv(O/"wf_recalc.csv",index=False);print("AUDIT",S);print(pd.DataFrame(wf).to_string(index=False))
