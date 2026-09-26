#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
Path(a.out).mkdir(parents=True,exist_ok=True)
t=pd.read_csv(a.trades,parse_dates=["entry_time","exit_time","signal_time"])
c=t[(t.n==320)&(t.signal=="FIRST_BREAKDOWN")&(t.stop_atr==1.0)&(t.rr==3.0)].sort_values(["entry_time","symbol"]).copy()
cut=c.entry_time.quantile(.60);o=c[c.entry_time>cut].copy()
# duplicate audits
keys=["symbol","n","signal","stop_atr","rr","signal_time","entry_time"]
dup=o.duplicated(keys,keep=False)
same_symbol_time=o.duplicated(["symbol","entry_time"],keep=False)
# timestamp counts: rows vs unique symbols, and timing integrity
g=o.groupby("entry_time").agg(rows=("symbol","size"),unique_symbols=("symbol","nunique"),unique_signal_times=("signal_time","nunique")).reset_index()
g["duplicate_rows"]=g.rows-g.unique_symbols
g["expected_signal_time"]=g.entry_time-pd.Timedelta(hours=4)
# all rows for each entry time should have signal time exactly previous 4h candle
timing=o.assign(ok=lambda x:x.signal_time==x.entry_time-pd.Timedelta(hours=4)).groupby("entry_time").ok.all().rename("timing_ok")
g=g.merge(timing,on="entry_time")
g.sort_values(["rows","entry_time"],ascending=[False,True]).to_csv(Path(a.out)/"timestamp_counts.csv",index=False)
top=g.sort_values("rows",ascending=False).head(20)
top.to_csv(Path(a.out)/"top20_timestamps.csv",index=False)
times=set(top.entry_time)
o[o.entry_time.isin(times)].sort_values(["entry_time","symbol"])[keys+["r_net","reason"]].to_csv(Path(a.out)/"top20_signal_rows.csv",index=False)
summary=pd.DataFrame([{
 "oos_rows":len(o),"oos_unique_symbols":o.symbol.nunique(),"exact_duplicate_rows":int(dup.sum()),
 "same_symbol_entrytime_duplicate_rows":int(same_symbol_time.sum()),"max_rows_same_entrytime":int(g.rows.max()),
 "max_unique_symbols_same_entrytime":int(g.unique_symbols.max()),"timestamps_51plus":int((g.rows>=51).sum()),
 "rows_in_51plus":int(g.loc[g.rows>=51,"rows"].sum()),"all_timing_ok":bool(g.timing_ok.all()),
 "all_rows_equal_unique_symbols":bool((g.rows==g.unique_symbols).all())
}])
summary.to_csv(Path(a.out)/"audit_summary.csv",index=False)
print(summary.to_string(index=False));print(top.to_string(index=False))
