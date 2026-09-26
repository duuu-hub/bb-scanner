#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--trades",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();P=Path(a.out);P.mkdir(parents=True,exist_ok=True)
ss=[]
for fn in glob.glob(a.input+"/**/*.csv.gz",recursive=True):
 sym=Path(fn).name.replace(".csv.gz","");d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms"
 d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True);d["close"]=pd.to_numeric(d.close,errors="coerce");d=d.dropna(subset=["dt","close"]).sort_values("dt").drop_duplicates("dt")
 x=d.set_index("dt").resample("4h",label="left",closed="left").agg(close=("close","last"),bars=("close","count"));x=x[x.bars==16]
 ss.append(x.close.pct_change().rename(sym))
R=pd.concat(ss,axis=1).sort_index()
m=R.median(axis=1); csad=R.sub(m,axis=0).abs().mean(axis=1); cssd=R.std(axis=1)
# average pairwise correlation via standardized cross-section rolling 6 4h bars; practical common-movement proxy
# compute mean asset correlation to equal-weight market over trailing 6 bars
corr=[]
for i in range(len(R)):
 if i<5:corr.append(np.nan);continue
 w=R.iloc[i-5:i+1];mr=w.mean(axis=1);vals=w.corrwith(mr);corr.append(vals.mean())
F=pd.DataFrame({"csad_prev":csad,"cssd_prev":cssd,"avg_market_corr6_prev":corr,"market_abs_prev":m.abs()})
F["csad_accel"]=csad-csad.shift(1);F["cssd_accel"]=cssd-cssd.shift(1)
t=pd.read_csv(a.trades,parse_dates=["entry_time"]);c=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1)&(t.rr==3)].sort_values(["entry_time","symbol"]);cut=c.entry_time.quantile(.6);o=c[c.entry_time>cut]
e=o.groupby("entry_time").agg(signals=("symbol","size"),event_avg_r=("r_net","mean")).reset_index();e["ft"]=e.entry_time-pd.Timedelta(hours=4);e=e.merge(F,left_on="ft",right_index=True,how="left");z=e[e.signals>=51].copy();z["success"]=z.event_avg_r>0
rows=[]
for f in F.columns:
 g=z[z.success][f].dropna();b=z[~z.success][f].dropna();rows.append(dict(feature=f,good_n=len(g),bad_n=len(b),good_mean=g.mean(),bad_mean=b.mean(),diff=g.mean()-b.mean(),good_med=g.median(),bad_med=b.median()))
pd.DataFrame(rows).to_csv(P/"dispersion_separation.csv",index=False);z.to_csv(P/"events_dispersion.csv",index=False)
# quartile monotonicity across all 929 OOS events, avoiding 51+ only cherry-pick
qrows=[]
for f in F.columns:
 x=e.dropna(subset=[f]).copy();x["q"]=pd.qcut(x[f],4,duplicates="drop")
 for q,g in x.groupby("q",observed=True):qrows.append(dict(feature=f,quartile=str(q),events=len(g),avg_event_r=g.event_avg_r.mean(),positive_rate=(g.event_avg_r>0).mean(),avg_signals=g.signals.mean()))
pd.DataFrame(qrows).to_csv(P/"all_oos_quartiles.csv",index=False)
pd.DataFrame([dict(symbols=R.shape[1],oos_events=len(e),events51=len(z),complete51=int(z[list(F.columns)].notna().all(axis=1).sum()),timing_ok=bool((z.ft==z.entry_time-pd.Timedelta(hours=4)).all()))]).to_csv(P/"summary.csv",index=False)
print(pd.DataFrame(rows).to_string(index=False))
