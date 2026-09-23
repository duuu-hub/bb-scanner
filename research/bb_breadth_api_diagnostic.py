from __future__ import annotations
import pandas as pd, numpy as np
from backtest import fetch_range, rows_to_df
BAR15=15*60_000
tests=[("SPELL_2024Q4",1731163500000),("SPELL_2025Q1",1737155700000),("ANKR_2025Q4",1762852500000),("GUN_2026Q1",1768366800000)]
for label,ts in tests:
 print("\n===",label,pd.to_datetime(ts,unit="ms",utc=True),"===")
 for sym in ["BTCUSDT","ETHUSDT","DOGEUSDT","ADAUSDT","SPELLUSDT","ANKRUSDT","GUNUSDT"]:
  start=ts-17*BAR15
  for mode,end in [("old_end",ts-BAR15),("signal_end",ts),("plus15_end",ts+BAR15)]:
   try:
    rows=fetch_range(sym,"15m",15,start,end)
    df=rows_to_df(rows,15)
    q=df[(df["close_ts"]<=ts)&(df["ts"]>=start)].drop_duplicates("ts").sort_values("ts")
    ok=len(q)>=17 and (len(q.tail(17))==17) and np.all(np.diff(q.tail(17)["ts"].to_numpy(dtype=np.int64))==BAR15)
    print(sym,mode,"raw",len(df),"filtered",len(q),"ok17",ok,
          "first",int(q.ts.min()) if len(q) else None,
          "last",int(q.ts.max()) if len(q) else None,
          "last_close",int(q.close_ts.max()) if len(q) else None)
   except Exception as e:
    print(sym,mode,"ERR",type(e).__name__,str(e))
