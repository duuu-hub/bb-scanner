from __future__ import annotations
import pandas as pd
from backtest import fetch_range, rows_to_df

TESTS=[
 ("2024Q4",1731163500000),
 ("2025Q1",1737155700000),
 ("2025Q4",1762852500000),
 ("2026Q1",1768366800000),
]
SYMS=["BTCUSDT","ETHUSDT","DOGEUSDT","ADAUSDT","SPELLUSDT","ANKRUSDT","GUNUSDT"]
CASES=[("15m",15,17),("1H",60,6),("4H",240,3)]

for label,ts in TESTS:
 print(f"\n=== {label} {pd.to_datetime(ts,unit='ms',utc=True)} ===",flush=True)
 for sym in SYMS:
  for gran,dur,bars in CASES:
   start=ts-bars*dur*60_000
   end=ts-dur*60_000
   try:
    rows=fetch_range(sym,gran,dur,start,end)
    df=rows_to_df(rows,dur)
    print(f"OK {sym} {gran} rows={len(df)} first={df.ts.min() if len(df) else None} last={df.ts.max() if len(df) else None}",flush=True)
   except Exception as e:
    print(f"ERR {sym} {gran}: {type(e).__name__}: {e}",flush=True)
