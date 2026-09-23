from __future__ import annotations
import argparse, json, math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd

from backtest import TF, fetch_range, rows_to_df
from precision_backtest import (
    build_signals, fetch_all_minutes, one_trade, calc_pf
)

LONG_PRIORITY={
    "L1_MOMENTUM_1H10":0,
    "L2_EXPLOSIVE_4H30":1,
    "L3_4H_LAG":2,
}
TF_NAMES=["1W","1D","12H","4H","1H","30M","15M"]
MIN=60_000

def parse_args():
    p=argparse.ArgumentParser()
    p.add_argument("--source",required=True)
    p.add_argument("--workers",type=int,default=6)
    p.add_argument("--outdir",default="survival_120d_results")
    return p.parse_args()

def dedupe_long3(signals):
    x=signals[signals["strategy"].isin(LONG_PRIORITY)].copy()
    x["_priority"]=x["strategy"].map(LONG_PRIORITY)
    x=x.sort_values(["ts","symbol","_priority","strategy"])
    x=x.drop_duplicates(["ts","symbol"],keep="first")
    return x.drop(columns=["_priority"]).reset_index(drop=True)

def local_bases(symbol,ts):
    completed={}
    p15=None
    for tf_name,(gran,duration_min) in TF.items():
        dur_ms=duration_min*MIN
        start=ts-25*dur_ms
        rows=fetch_range(symbol,gran,duration_min,start,ts)
        df=rows_to_df(rows,duration_min)
        hist=df[df["close_ts"]<=ts].sort_values("ts")
        if len(hist)<19:
            raise RuntimeError(f"{symbol} {tf_name} insufficient={len(hist)}")
        completed[tf_name]=hist["close"].tail(19).astype(float).tolist()
        if tf_name=="15M":
            p15=df.copy()
    if p15 is None:
        raise RuntimeError("no 15m")
    opens=dict(zip(p15["ts"].astype(int),p15["open"].astype(float)))
    b1=opens.get(int(ts)-4*15*MIN)
    b4=opens.get(int(ts)-16*15*MIN)
    if not b1 or not b4:
        raise RuntimeError(f"{symbol} missing momentum bases")
    return {"completed":completed,"b1":float(b1),"b4":float(b4)}

def classify(bases,price):
    above={}
    for tf in TF_NAMES:
        arr=np.asarray(bases["completed"][tf]+[float(price)],dtype=float)
        basis=float(arr.mean())
        std=float(arr.std(ddof=0))
        upper=basis+2.0*std
        above[tf]=bool(price>upper)
    exact=sum(above.values())
    missing=[tf for tf in TF_NAMES if not above[tf]]
    rank=7 if exact==7 else (6 if exact==6 else 0)
    ret1=(price/bases["b1"]-1.0)*100.0
    ret4=(price/bases["b4"]-1.0)*100.0
    matches=[]
    if rank>=6 and ret1>=10.0:
        matches.append("L1_MOMENTUM_1H10")
    if rank>=6 and ret4>=30.0:
        matches.append("L2_EXPLOSIVE_4H30")
    if exact==6 and missing==["4H"]:
        matches.append("L3_4H_LAG")
    matches.sort(key=lambda s:LONG_PRIORITY[s])
    return {
        "selected":matches[0] if matches else None,
        "matches":matches,"exact":exact,"missing":missing,
        "ret1":ret1,"ret4":ret4,
    }

def summarize(trades):
    if trades.empty:
        return {"n":0}
    ret=trades["net_pct"].astype(float)
    eq=1.0
    for v in ret:
        eq*=1.0+(0.30*v)/100.0
    return {
        "n":len(trades),
        "wins":int((ret>0).sum()),
        "win_rate_pct":float((ret>0).mean()*100.0),
        "avg_net_pct":float(ret.mean()),
        "median_net_pct":float(ret.median()),
        "pf":float(calc_pf(ret)) if len(ret) else None,
        "outcomes":dict(Counter(trades["outcome"])),
        "weighted30_sequential_pct":float((eq-1.0)*100.0),
    }

def subset_summary(trades):
    out={"all":summarize(trades)}
    for split in ("train70","test30"):
        out[split]=summarize(trades[trades["split"]==split])
    for st in LONG_PRIORITY:
        out[st]=summarize(trades[trades["strategy"]==st])
    return out

def main():
    args=parse_args()
    outdir=__import__("pathlib").Path(args.outdir)
    outdir.mkdir(parents=True,exist_ok=True)

    source=pd.read_csv(args.source)
    signals=dedupe_long3(build_signals(source))
    print("[META] "+json.dumps({
        "dedup_long3":len(signals),
        "symbols":int(signals["symbol"].nunique()),
        "strategies":signals["strategy"].value_counts().to_dict(),
        "splits":signals["split"].value_counts().to_dict(),
    },sort_keys=True))

    print("[STEP] fetch targeted 1m windows")
    minute_map,fail=fetch_all_minutes(signals,args.workers)
    if fail:
        print("[WARN] minute failures="+json.dumps(fail))

    unique=signals[["symbol","ts"]].drop_duplicates()
    base_map={}
    failures=[]
    print(f"[STEP] rebuild exact BB bases for {len(unique)} signal boundaries")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs={
            pool.submit(local_bases,r.symbol,int(r.ts)):(r.symbol,int(r.ts))
            for r in unique.itertuples(index=False)
        }
        done=0
        for fut in as_completed(futs):
            key=futs[fut]; done+=1
            try:
                base_map[key]=fut.result()
            except Exception as exc:
                failures.append((key[0],key[1],str(exc)))
            if done%20==0 or done==len(futs):
                print(f"[BASE] {done}/{len(futs)} ok={len(base_map)} fail={len(failures)}")

    all_rows=[]
    survival_rows=[]
    detail=[]
    for sig in signals.itertuples(index=False):
        m=minute_map.get(sig.symbol)
        bases=base_map.get((sig.symbol,int(sig.ts)))
        if m is None or m.empty or bases is None:
            continue
        for delay in (1,2,3):
            tr=one_trade(sig,m,delay)
            if tr is None:
                continue
            all_rows.append(tr)
            entry_price=float(tr["entry_price"])
            state=classify(bases,entry_price)
            survives=(state["selected"]==sig.strategy)
            d={
                "delay_min":delay,"symbol":sig.symbol,"ts":int(sig.ts),
                "strategy":sig.strategy,"split":sig.split,
                "entry_price":entry_price,"checkpoint_selected":state["selected"],
                "checkpoint_matches":"+".join(state["matches"]),
                "survives":survives,"net_pct":float(tr["net_pct"]),
                "outcome":tr["outcome"],
            }
            detail.append(d)
            if survives:
                survival_rows.append(tr)

    all_df=pd.DataFrame(all_rows)
    surv_df=pd.DataFrame(survival_rows)
    pd.DataFrame(detail).to_csv(outdir/"survival_detail.csv",index=False)
    all_df.to_csv(outdir/"all_delayed_trades.csv",index=False)
    surv_df.to_csv(outdir/"survival_filtered_trades.csv",index=False)
    if failures:
        pd.DataFrame(failures,columns=["symbol","ts","error"]).to_csv(outdir/"base_failures.csv",index=False)

    for delay in (1,2,3):
        base=all_df[all_df["delay_min"]==delay].copy()
        flt=surv_df[surv_df["delay_min"]==delay].copy()
        payload={
            "delay_min":delay,
            "available":len(base),
            "survivors":len(flt),
            "survival_rate_pct":len(flt)/len(base)*100.0 if len(base) else None,
            "unfiltered":subset_summary(base),
            "survival_filtered":subset_summary(flt),
        }
        print("[RESULT] "+json.dumps(payload,sort_keys=True))

if __name__=="__main__":
    main()
