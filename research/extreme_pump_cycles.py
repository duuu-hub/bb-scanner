#!/usr/bin/env python3
"""Measure confirmed low->high pump cycles from the frozen 5Y Binance UM 15m dataset.

Pre-registered definitions:
- Ignore the first 90 calendar days of each symbol (reduce listing/launch distortion).
- A cycle starts from the lowest 15m low after the prior confirmed cycle.
- Track the highest later 15m high.
- A cycle is confirmed/closed when a later 15m low is <= 50% of that peak.
- If a new low undercuts the trough before a 50% peak drawdown, restart the trough there
  (the earlier rise was not a completed cycle).
- Open/unconfirmed cycles at the dataset end are excluded.
- "Low liquidity" = bottom quartile of symbols by median daily quote volume over their
  available history in this frozen dataset.
"""
from __future__ import annotations
import argparse, csv, gzip, json, math, statistics
from pathlib import Path

DAY_MS=86_400_000
WARMUP_MS=90*DAY_MS

def pctile(vals,q):
    if not vals: return float("nan")
    xs=sorted(vals)
    if len(xs)==1: return xs[0]
    pos=(len(xs)-1)*q
    lo=math.floor(pos); hi=math.ceil(pos)
    if lo==hi: return xs[lo]
    w=pos-lo
    return xs[lo]*(1-w)+xs[hi]*w

def summarize(cycles):
    vals=[x["rise_pct"] for x in cycles]
    if not vals:
        return {"n":0}
    vals_sorted=sorted(vals,reverse=True)
    k=max(1,math.ceil(len(vals_sorted)*0.10))
    top=vals_sorted[:k]
    return {
        "n":len(vals),
        "mean_pct":sum(vals)/len(vals),
        "median_pct":statistics.median(vals),
        "p90_cut_pct":pctile(vals,0.90),
        "top10pct_n":k,
        "top10pct_mean_pct":sum(top)/len(top),
        "max_pct":max(vals),
        "max_x":1+max(vals)/100.0,
    }

def analyze_symbol(path: Path):
    daily={}
    cycles=[]
    first_ts=None
    trough=None; trough_ts=None
    peak=None; peak_ts=None
    rows=0
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        r=csv.DictReader(f)
        for row in r:
            try:
                ts=int(row["open_time"]); hi=float(row["high"]); lo=float(row["low"])
                qv=float(row.get("quote_volume") or 0.0)
            except Exception:
                continue
            if not (hi>0 and lo>0): continue
            rows+=1
            if first_ts is None: first_ts=ts
            d=ts//DAY_MS
            daily[d]=daily.get(d,0.0)+max(qv,0.0)
            if ts < first_ts+WARMUP_MS:
                continue
            if trough is None:
                trough=lo; trough_ts=ts
                continue
            # If a previously established peak has now suffered a 50% drawdown,
            # close the cycle BEFORE using this bar's high (conservative same-bar handling).
            if peak is not None and lo <= 0.5*peak:
                if peak_ts is not None and peak_ts>trough_ts and peak>trough:
                    rise=(peak/trough-1.0)*100.0
                    cycles.append({
                        "symbol":path.stem.replace(".csv",""),
                        "trough_ts":trough_ts,
                        "peak_ts":peak_ts,
                        "trough":trough,
                        "peak":peak,
                        "rise_pct":rise,
                        "rise_x":peak/trough,
                    })
                trough=lo; trough_ts=ts; peak=None; peak_ts=None
                continue
            # A fresh lower low invalidates any small, unconfirmed rise that preceded it.
            if lo < trough:
                trough=lo; trough_ts=ts; peak=None; peak_ts=None
                continue
            if hi > trough and (peak is None or hi>peak):
                peak=hi; peak_ts=ts
    med_daily=statistics.median(daily.values()) if daily else 0.0
    active_days=len(daily)
    return {
        "symbol":path.stem.replace(".csv",""),
        "rows":rows,
        "active_days":active_days,
        "median_daily_quote_volume":med_daily,
        "cycles":cycles,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-root",required=True)
    ap.add_argument("--out",required=True)
    args=ap.parse_args()
    paths=sorted(Path(args.data_root).rglob("*USDT.csv.gz"))
    if not paths: raise SystemExit("no *USDT.csv.gz files found")
    symbols=[]; all_cycles=[]
    for i,p in enumerate(paths,1):
        s=analyze_symbol(p)
        symbols.append({k:v for k,v in s.items() if k!="cycles"})
        all_cycles.extend(s["cycles"])
        if i%25==0 or i==len(paths):
            print(f"processed {i}/{len(paths)} symbols cycles={len(all_cycles)}",flush=True)
    liq_vals=[s["median_daily_quote_volume"] for s in symbols if s["active_days"]>0]
    q25=pctile(liq_vals,0.25)
    low_symbols={s["symbol"] for s in symbols if s["median_daily_quote_volume"]<=q25}
    low_cycles=[c for c in all_cycles if c["symbol"] in low_symbols]
    all_sorted=sorted(all_cycles,key=lambda x:x["rise_pct"],reverse=True)
    low_sorted=sorted(low_cycles,key=lambda x:x["rise_pct"],reverse=True)
    result={
        "definition":{
            "market":"Binance USD-M USDT perpetual universe from frozen 5Y artifact run 36095439671",
            "interval":"15m",
            "listing_warmup_days":90,
            "cycle_end_drawdown_pct":50,
            "open_cycles_excluded":True,
            "low_liquidity_rule":"bottom 25% of symbols by median daily quote volume",
            "low_liquidity_cutoff_median_daily_quote_volume":q25,
        },
        "symbols_total":len(symbols),
        "symbols_low_liquidity":len(low_symbols),
        "all":summarize(all_cycles),
        "low_liquidity":summarize(low_cycles),
        "top20_all":all_sorted[:20],
        "top20_low_liquidity":low_sorted[:20],
    }
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    (out/"summary.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    with (out/"cycles.csv").open("w",newline="",encoding="utf-8") as f:
        fields=["symbol","trough_ts","peak_ts","trough","peak","rise_pct","rise_x"]
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(all_sorted)
    with (out/"symbol_liquidity.csv").open("w",newline="",encoding="utf-8") as f:
        fields=["symbol","rows","active_days","median_daily_quote_volume"]
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(sorted(symbols,key=lambda x:x["median_daily_quote_volume"]))
    print("RESULT_JSON")
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
