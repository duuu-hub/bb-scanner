#!/usr/bin/env python3
import argparse, pandas as pd, numpy as np
from pathlib import Path
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--regime",required=True);ap.add_argument("--dispersion",required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time"])
t=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1.0)&(t.rr==3.0)].copy()
r=pd.read_csv(a.regime,parse_dates=["entry_time"])
d=pd.read_csv(a.dispersion,parse_dates=["entry_time"])
cols=["entry_time","breadth_down2_prev","breadth_accel"]
e=r[cols].drop_duplicates("entry_time").merge(d[["entry_time","cssd_accel"]].drop_duplicates("entry_time"),on="entry_time",how="inner")
cnt=t.groupby("entry_time").symbol.nunique().rename("signal_count").reset_index()
e=e.merge(cnt,on="entry_time"); e["event_r"]=t.groupby("entry_time").r_net.mean().reindex(e.entry_time).values
e=e.dropna().sort_values("entry_time").reset_index(drop=True)
# Strict expanding chronological WF. Quantile choices are selected on TRAIN only.
# Low-complexity grid: count floor + upper caps on panic-depth/acceleration features.
count_q=[0.50,0.65,0.75]; cap_q=[0.50,0.65,0.80]
cuts=np.quantile(np.arange(len(e)),[.40,.55,.70,.85]).astype(int)
rows=[]; kept=[]
for fold,end in enumerate(cuts,1):
    start=0 if fold==1 else cuts[fold-2]
    test_end=cuts[fold] if fold<len(cuts) else len(e)
    train=e.iloc[:end].copy(); test=e.iloc[end:test_end].copy()
    best=None
    for cq in count_q:
      cmin=train.signal_count.quantile(cq)
      for bq in cap_q:
       bmax=train.breadth_down2_prev.quantile(bq)
       for aq in cap_q:
        amax=train.breadth_accel.quantile(aq)
        for dq in cap_q:
         dmax=train.cssd_accel.quantile(dq)
         z=train[(train.signal_count>=cmin)&(train.breadth_down2_prev<=bmax)&(train.breadth_accel<=amax)&(train.cssd_accel<=dmax)]
         if len(z)<max(20,int(.03*len(train))): continue
         score=z.event_r.mean()
         key=(score,len(z),-cq,-bq,-aq,-dq)
         if best is None or key>best[0]: best=(key,cmin,bmax,amax,dmax)
    if best is None: continue
    _,cmin,bmax,amax,dmax=best
    mask=(test.signal_count>=cmin)&(test.breadth_down2_prev<=bmax)&(test.breadth_accel<=amax)&(test.cssd_accel<=dmax)
    z=test[mask].copy(); kept.append(z.assign(fold=fold))
    def met(x):
      gp=x.loc[x.event_r>0,"event_r"].sum(); gl=-x.loc[x.event_r<0,"event_r"].sum()
      eq=x.event_r.cumsum(); dd=(eq.cummax()-eq).max() if len(x) else np.nan
      return len(x),x.event_r.mean() if len(x) else np.nan,gp/gl if gl>0 else np.nan,dd
    bn,bavg,bpf,bdd=met(test); kn,kavg,kpf,kdd=met(z)
    rows.append(dict(fold=fold,train_events=len(train),test_events=bn,kept_events=kn,retain=kn/bn if bn else np.nan,baseline_avg_r=bavg,filtered_avg_r=kavg,baseline_pf=bpf,filtered_pf=kpf,baseline_event_mdd_r=bdd,filtered_event_mdd_r=kdd,count_min=cmin,breadth2_max=bmax,breadth_accel_max=amax,cssd_accel_max=dmax,test_start=test.entry_time.min(),test_end=test.entry_time.max()))
res=pd.DataFrame(rows);res.to_csv(O/"walkforward_folds.csv",index=False)
k=pd.concat(kept,ignore_index=True) if kept else e.iloc[:0]
# Aggregate strictly OOS folds vs same test windows baseline
idx=[]
for x in rows:
 idx.append(e[(e.entry_time>=pd.Timestamp(x["test_start"]))&(e.entry_time<=pd.Timestamp(x["test_end"]))])
base=pd.concat(idx).drop_duplicates("entry_time") if idx else e.iloc[:0]
def agg(x,label):
 gp=x.loc[x.event_r>0,"event_r"].sum();gl=-x.loc[x.event_r<0,"event_r"].sum();eq=x.event_r.cumsum();dd=(eq.cummax()-eq).max() if len(x) else np.nan
 return dict(sample=label,events=len(x),avg_event_r=x.event_r.mean(),event_pf=gp/gl if gl>0 else np.nan,event_mdd_r=dd,positive_rate=(x.event_r>0).mean())
pd.DataFrame([agg(base,"baseline_same_windows"),agg(k.sort_values("entry_time"),"filtered_walkforward")]).to_csv(O/"aggregate.csv",index=False)
pd.DataFrame([dict(events=len(e),start=e.entry_time.min(),end=e.entry_time.max(),timing_rule="features entry_time-4h only")]).to_csv(O/"sanity.csv",index=False)
print(res.to_string(index=False));print(pd.read_csv(O/"aggregate.csv").to_string(index=False))
