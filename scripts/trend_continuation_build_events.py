#!/usr/bin/env python3
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trend_continuation_first_touch import build_signal_events

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--parts",required=True)
    ap.add_argument("--out",required=True)
    a=ap.parse_args()
    out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True)
    events,meta=build_signal_events(a.parts)
    events.to_csv(out,index=False,compression="gzip")
    print("EVENT_BUILD_PASS rows",len(events),"symbols",events.symbol.nunique(),"unique",len(events[["symbol","entry_ts"]].drop_duplicates()),flush=True)

if __name__=="__main__":
    main()
