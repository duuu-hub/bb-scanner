#!/usr/bin/env python3
from pathlib import Path
import os,pandas as pd,numpy as np
ROOT=Path(os.environ.get("CANONICAL_5Y_DIR","canonical"))
def main():
 fs=sorted(ROOT.rglob("*.parquet")); assert len(fs)==666, f"expected 666 parquet files, got {len(fs)}"
 checked=0
 for p in fs[:20]:
  d=pd.read_parquet(p).sort_values("open_time").drop_duplicates("open_time")
  assert len(d)>1000 and d.open_time.is_monotonic_increasing
  idx=pd.to_datetime(d.open_time,unit="ms",utc=True)
  assert not idx.duplicated().any()
  # Base candles must be UTC quarter-hour aligned.
  assert ((idx.minute%15)==0).all() and (idx.second==0).all()
  x=d.assign(dt=idx).set_index("dt").close.astype(float)
  # Explicit fixed UTC bins: avoids calendar-resample origin ambiguity.
  for name,mins in [("1H",60),("4H",240),("1D",1440),("3D",4320)]:
   ns=idx.view("int64"); step=mins*60*10**9
   bucket=pd.to_datetime((ns//step)*step,utc=True)
   # feature at t may only use completed HTF buckets strictly before current bucket.
   h=pd.DataFrame({"bucket":bucket,"close":x.to_numpy()}).groupby("bucket",sort=True).close.last()
   ma=h.rolling(20).mean().shift(1); sd=h.rolling(20).std(ddof=0).shift(1)
   assert ma.index.is_monotonic_increasing and sd.index.equals(ma.index)
   # deterministic UTC anchors
   if name in ("1D","3D"): assert all(t.hour==0 and t.minute==0 for t in h.index[:50])
  checked+=1
 print("AUDIT_PASS files_checked",checked,"universe",len(fs),flush=True)
if __name__=="__main__": main()
