#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--trades",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();P=Path(a.out);P.mkdir(parents=True,exist_ok=True)
# Build strictly lagged 4h market features from canonical 15m candles.
series=[];btc=None
for fn in glob.glob(a.input+"/**/*.csv.gz",recursive=True):
 sym=Path(fn).name.replace(".csv.gz","")
 d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms"
 d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True);d["close"]=pd.to_numeric(d.close,errors="coerce")
 d=d.dropna(subset=["dt","close"]).sort_values("dt").drop_duplicates("dt")
 x=d.set_index("dt").resample("4h",label="left",closed="left").agg(close=("close","last"),bars=("close","count"))
 x=x[x.bars==16];r=x.close.pct_change()
 series.append(r.rename(sym))
 if sym=="BTCUSDT": btc=x.close
R=pd.concat(series,axis=1).sort_index()
breadth_down=(R<0).mean(axis=1); breadth_down2=(R<-0.02).mean(axis=1); medret=R.median(axis=1)
F=pd.DataFrame({"breadth_down_prev":breadth_down,"breadth_down2_prev":breadth_down2,"market_medret_prev":medret})
F["breadth_accel"]=breadth_down-breadth_down.shift(1)
if btc is not None:
 br=btc.pct_change();F["btc_ret_prev"]=br;F["btc_ret_3_prev"]=btc.pct_change(3);F["btc_vol_6_prev"]=br.rolling(6).std()
# Features indexed by signal candle open; event entry at next 4h => join entry_time-4h only.
t=pd.read_csv(a.trades,parse_dates=["entry_time","signal_time"])
c=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1.0)&(t.rr==3.0)].sort_values(["entry_time","symbol"])
cut=c.entry_time.quantile(.60);o=c[c.entry_time>cut]
e=o.groupby("entry_time").agg(signals=("symbol","size"),event_avg_r=("r_net","mean")).reset_index()
e["feature_time"]=e.entry_time-pd.Timedelta(hours=4)
e=e.merge(F,left_on="feature_time",right_index=True,how="left")
z=e[e.signals>=51].copy();z["success"]=z.event_avg_r>0
features=[x for x in F.columns]
rows=[]
for f in features:
 good=z.loc[z.success,f].dropna();bad=z.loc[~z.success,f].dropna()
 rows.append(dict(feature=f,n_good=len(good),n_bad=len(bad),good_mean=good.mean(),bad_mean=bad.mean(),diff=good.mean()-bad.mean(),good_median=good.median(),bad_median=bad.median()))
pd.DataFrame(rows).to_csv(P/"feature_separation.csv",index=False);z.to_csv(P/"events_features.csv",index=False)
# transparent one-feature threshold scan; report only, no claim of independent OOS.
scan=[]
for f in features:
 q=np.unique(z[f].dropna().quantile([.2,.4,.6,.8]).values)
 for th in q:
  for side in ["le","ge"]:
   k=z[z[f]<=th] if side=="le" else z[z[f]>=th]
   if len(k)<5:continue
   scan.append(dict(feature=f,side=side,threshold=th,events=len(k),avg_event_r=k.event_avg_r.mean(),positive_rate=(k.event_avg_r>0).mean(),sum_event_r=k.event_avg_r.sum()))
pd.DataFrame(scan).to_csv(P/"threshold_scan_EXPLORATORY.csv",index=False)
summary=pd.DataFrame([dict(symbols=R.shape[1],oos_events=len(e),events_51plus=len(z),feature_rows_complete=int(z[features].notna().all(axis=1).sum()),feature_time_integrity=bool((z.feature_time==z.entry_time-pd.Timedelta(hours=4)).all()))])
summary.to_csv(P/"summary.csv",index=False);print(summary.to_string(index=False));print(pd.DataFrame(rows).to_string(index=False))
