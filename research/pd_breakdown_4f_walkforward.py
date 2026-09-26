#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--trades",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
ss=[]
for fn in glob.glob(a.input+"/**/*.csv.gz",recursive=True):
 sym=Path(fn).name.replace(".csv.gz","");d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms"
 d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True);d["close"]=pd.to_numeric(d.close,errors="coerce");d=d.dropna(subset=["dt","close"]).sort_values("dt").drop_duplicates("dt")
 x=d.set_index("dt").resample("4h",label="left",closed="left").agg(close=("close","last"),bars=("close","count"));x=x[x.bars==16]
 ss.append(x.close.pct_change().rename(sym))
R=pd.concat(ss,axis=1).sort_index();bd=(R<0).mean(axis=1);bd2=(R<-.02).mean(axis=1);cssd=R.std(axis=1)
F=pd.DataFrame({"breadth_down2_prev":bd2,"breadth_accel":bd-bd.shift(1),"cssd_accel":cssd-cssd.shift(1)})
t=pd.read_csv(a.trades,parse_dates=["entry_time"]);c=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1)&(t.rr==3)].sort_values(["entry_time","symbol"])
cut=c.entry_time.quantile(.60);o=c[c.entry_time>cut]
e=o.groupby("entry_time").agg(signal_count=("symbol","size"),event_r=("r_net","mean")).reset_index();e["feature_time"]=e.entry_time-pd.Timedelta(hours=4)
e=e.merge(F,left_on="feature_time",right_index=True,how="left").sort_values("entry_time").reset_index(drop=True)
# hard sanity gates BEFORE any walk-forward
assert len(e)==929, f"OOS events {len(e)} != 929"
assert int((e.signal_count>=51).sum())==30, f"51+ events {(e.signal_count>=51).sum()} != 30"
assert bool((e.feature_time==e.entry_time-pd.Timedelta(hours=4)).all())
assert R.shape[1]>=600
assert e[["breadth_down2_prev","breadth_accel","cssd_accel"]].notna().all(axis=1).mean()>.98
e=e.dropna(subset=["breadth_down2_prev","breadth_accel","cssd_accel"]).reset_index(drop=True)
# Expanding chronological WF: 40% initial train then four 15% test folds.
bounds=[int(len(e)*x) for x in [.40,.55,.70,.85,1.0]]
count_q=[.50,.65,.75]; cap_q=[.50,.65,.80]; rows=[];kept=[];bases=[]
for fold in range(4):
 end=bounds[fold];te=bounds[fold+1];train=e.iloc[:end];test=e.iloc[end:te];best=None
 for cq in count_q:
  cmin=train.signal_count.quantile(cq)
  for bq in cap_q:
   bmax=train.breadth_down2_prev.quantile(bq)
   for aq in cap_q:
    amax=train.breadth_accel.quantile(aq)
    for dq in cap_q:
     dmax=train.cssd_accel.quantile(dq)
     z=train[(train.signal_count>=cmin)&(train.breadth_down2_prev<=bmax)&(train.breadth_accel<=amax)&(train.cssd_accel<=dmax)]
     if len(z)<max(20,int(.05*len(train))):continue
     key=(z.event_r.mean(),len(z))
     if best is None or key>best[0]:best=(key,cmin,bmax,amax,dmax)
 assert best is not None
 _,cmin,bmax,amax,dmax=best;mask=(test.signal_count>=cmin)&(test.breadth_down2_prev<=bmax)&(test.breadth_accel<=amax)&(test.cssd_accel<=dmax);z=test[mask].copy()
 kept.append(z.assign(fold=fold+1));bases.append(test)
 def met(x):
  gp=x.loc[x.event_r>0,"event_r"].sum();gl=-x.loc[x.event_r<0,"event_r"].sum();eq=x.event_r.cumsum();dd=(eq.cummax()-eq).max()
  return len(x),x.event_r.mean(),gp/gl if gl>0 else np.nan,dd
 bn,ba,bp,bd_=met(test);kn,ka,kp,kd=met(z)
 rows.append(dict(fold=fold+1,train_events=len(train),test_events=bn,kept_events=kn,retain=kn/bn,baseline_avg_r=ba,filtered_avg_r=ka,baseline_pf=bp,filtered_pf=kp,baseline_event_mdd_r=bd_,filtered_event_mdd_r=kd,count_min=cmin,breadth2_max=bmax,breadth_accel_max=amax,cssd_accel_max=dmax,test_start=test.entry_time.min(),test_end=test.entry_time.max()))
res=pd.DataFrame(rows);res.to_csv(O/"walkforward_folds.csv",index=False);k=pd.concat(kept).sort_values("entry_time");base=pd.concat(bases).sort_values("entry_time")
def agg(x,label):
 gp=x.loc[x.event_r>0,"event_r"].sum();gl=-x.loc[x.event_r<0,"event_r"].sum();eq=x.event_r.cumsum();dd=(eq.cummax()-eq).max()
 return dict(sample=label,events=len(x),avg_event_r=x.event_r.mean(),event_pf=gp/gl if gl>0 else np.nan,event_mdd_r=dd,positive_rate=(x.event_r>0).mean())
pd.DataFrame([agg(base,"baseline_same_windows"),agg(k,"filtered_walkforward")]).to_csv(O/"aggregate.csv",index=False)
pd.DataFrame([dict(raw_oos_events=929,events_51plus=30,usable_events=len(e),symbols=R.shape[1],timing_ok=True)]).to_csv(O/"sanity.csv",index=False)
print(pd.read_csv(O/"sanity.csv").to_string(index=False));print(res.to_string(index=False));print(pd.read_csv(O/"aggregate.csv").to_string(index=False))
