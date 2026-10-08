#!/usr/bin/env python3
"""Exact max/range of completed PSAR flip-to-flip streak lengths from frozen 2026-10-08 event ledger."""
import argparse, glob, json, os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import numpy as np

RUN=37726795614
STABLE={"USDCUSDT","FDUSDUSDT","TUSDUSDT","USDPUSDT","DAIUSDT","BUSDUSDT","USDEUSDT","PYUSDUSDT","EURCUSDT","USD1USDT","USDDUSDT","USDXUSDT"}
SHARDS=4
def stamp(ms, tz=timezone.utc):
    return datetime.fromtimestamp(int(ms)/1000,tz=tz).isoformat()
def summarize(v):
    x=v.length.astype(int).to_numpy()
    out={"n":int(len(v)),"mean":round(float(x.mean()),3),"median":float(np.median(x)),
        "p90":float(np.quantile(x,.90)),"p95":float(np.quantile(x,.95)),
        "p99":float(np.quantile(x,.99)),"p999":float(np.quantile(x,.999)),
        "max_bars":int(x.max()),
        "longer_count":{str(k):int((x>=k).sum()) for k in (16,24,32,48,50,64,80,100,128,200,300)},
        "longer_pct":{str(k):round(float(np.mean(x>=k)*100),5) for k in (16,24,32,48,50,64,80,100,128,200,300)},
        "length_hist":{},
        "top10":[]}
    for lo,hi in [(1,4),(5,8),(9,16),(17,32),(33,64),(65,99),(100,199),(200,999999)]:
        out["length_hist"][f"{lo}-{hi}"]=int(((x>=lo)&(x<=hi)).sum())
    for _,r in v.sort_values(["length","start_ts"],ascending=[False,True]).head(10).iterrows():
        out["top10"].append({"symbol":str(r.symbol),"direction":str(r.side),
            "length_bars":int(r.length),"start_utc":stamp(r.start_ts),
            "end_flip_utc":stamp(r.end_ts),
            "start_kst":stamp(r.start_ts,timezone(timedelta(hours=9))),
            "end_flip_kst":stamp(r.end_ts,timezone(timedelta(hours=9))),
            "peak_at_bar":int(r.peak_age)})
    return out

def main():
    a=argparse.ArgumentParser();a.add_argument("--root",default="artifacts");a.add_argument("--out",default="psar-max-trend.json")
    args=a.parse_args()
    report={"status":"EXPLORATORY","dataset_run":36095439671,"source_event_run":RUN,
      "branch":"research-rank5-binance-15m-5y",
      "psar":"open-projection canonical at TF candle OPEN, prev completed history, AF=.02 step=.02 max=.20",
      "scope":"completed flip-to-flip runs only, excludes left-censored burn-in 100 bars and incomplete final trend, splits at data gaps",
      "excluded":"event runs spanning dataset edges; range limited to observed data not mathematical PSAR maximum",
      "frames":{}}
    for tf in ("1h","4h"):
        paths=sorted(glob.glob(args.root+f"/**/psar-peak-age-{tf}-*.csv",recursive=True))
        assert len(paths)==SHARDS, (tf,paths)
        meta=[json.loads(open(f+".meta.json").read()) for f in paths]
        assert {m["shard"] for m in meta}==set(range(SHARDS))
        assert len({m["canonical_engine_sha256"] for m in meta})==1
        assert len({m["commit_sha"] for m in meta})==1
        assert sum(m["files"] for m in meta)==meta[0]["all_files"]
        df=pd.concat([pd.read_csv(x) for x in paths],ignore_index=True)
        assert len(df)==sum(m["n"] for m in meta)
        assert not df.duplicated(["symbol","start_ts"]).any()
        assert ((df.peak_age>=1)&(df.peak_age<=df.length)).all()
        rep={"N":len(df),"source_sha":meta[0]["commit_sha"],"engine_sha256":meta[0]["canonical_engine_sha256"]}
        for side in ("BULL","BEAR"):
            for split in ("ALL","TRAIN","SEEN_VALIDATION"):
                x=df[(df.side==side)&((df.split==split) if split!="ALL" else True)]
                assert len(x)>0
                rep[side+"_"+split]=summarize(x)
        for side in ("BULL","BEAR"):
            # Exploratory post-hoc sensitivity, not the primary result.
            x=df[(df.side==side)&(~df.symbol.isin(STABLE))]
            assert len(x)>0
            rep[side+"_NON_STABLE_ALL"]=summarize(x)
        report["frames"][tf]=rep
        for side in ("BULL","BEAR"):
            x=rep[side+"_ALL"]
            print("RESULT",tf,side,"N",x["n"],"MAX",x["max_bars"],
                  "p99",x["p99"],"maxcase",x["top10"][0],flush=True)
    Path(args.out).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    for tf,fr in report["frames"].items():
        for side in ("BULL","BEAR"):
            v=fr[side+"_NON_STABLE_ALL"]
            print("NON_STABLE_MAX",tf,side,"N",v["n"],"MAX",v["max_bars"],"P99",v["p99"],"case",v["top10"][0],flush=True)
    print("MAX_TRENDS_AUDIT_PASS 8/8 shards, no overlap, metadata and source checksum consistent")

if __name__=="__main__":main()
