#!/usr/bin/env python3
"""Causal 20x~60x extreme-extension SHORT sweep using executable 15m OPEN.

Final comparison semantics:
- Binance USD-M USDT perpetual frozen 5Y 15m dataset (run 36095439671).
- Ignore first 90 calendar days per symbol.
- At each 15m OPEN, use ONLY state known from fully completed prior bars.
- Current cycle trough is the causal low from prior completed bars.
- A cycle resets after a completed bar has low <= 50% of the prior running peak.
- A fresh lower low before reset restarts the trough after that bar closes.
- Thresholds: 20x,25x,...,60x.
- Enter SHORT at the FIRST executable 15m OPEN whose price is >= threshold * causal trough.
- One entry per threshold per cycle. Thus higher-threshold counts must be <= lower-threshold counts.
- Fixed exits at exact +1d ... +7d 15m OPEN.
- Gross short return = (entry - exit) / entry.
- Net20/net40 subtract 20bp/40bp total round-trip cost.
- MAE = maximum adverse HIGH from entry through exit versus entry.
- Funding excluded in this screening run.
"""
from __future__ import annotations
import argparse,csv,gzip,json,math,statistics
from pathlib import Path

DAY_MS=86_400_000
WARMUP_MS=90*DAY_MS
THRESHOLDS=tuple(range(20,61,5))

def pctile(vals,q):
    if not vals:return None
    xs=sorted(vals)
    if len(xs)==1:return xs[0]
    pos=(len(xs)-1)*q
    lo=math.floor(pos); hi=math.ceil(pos)
    if lo==hi:return xs[lo]
    w=pos-lo
    return xs[lo]*(1-w)+xs[hi]*w

def load_symbol(path):
    rows=[]; daily={}
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        r=csv.DictReader(f)
        for x in r:
            try:
                ts=int(x["open_time"]); op=float(x["open"]); hi=float(x["high"]); lo=float(x["low"]); cl=float(x["close"])
                qv=float(x.get("quote_volume") or 0.0)
            except Exception:
                continue
            if min(op,hi,lo,cl)<=0: continue
            rows.append((ts,op,hi,lo,cl))
            d=ts//DAY_MS
            daily[d]=daily.get(d,0.0)+max(qv,0.0)
    med=statistics.median(daily.values()) if daily else 0.0
    return rows,med,len(daily)

def signals_for_symbol(symbol,rows,median_qv):
    if len(rows)<2:return []
    first_ts=rows[0][0]; start_ts=first_ts+WARMUP_MS
    ts_to_idx={r[0]:i for i,r in enumerate(rows)}
    trough=None; trough_ts=None; peak=None
    fired=set()
    signals=[]

    for i,(ts,op,hi,lo,cl) in enumerate(rows):
        if ts < start_ts:
            continue

        # OPEN-time decision uses only state built from PRIOR completed bars.
        if trough is not None:
            entry_x=op/trough
            for thr in THRESHOLDS:
                if thr not in fired and entry_x >= thr:
                    fired.add(thr)
                    signals.append({
                        "symbol":symbol,"threshold_x":thr,
                        "trough":trough,"trough_ts":trough_ts,
                        "entry_ts":ts,"entry":op,"entry_vs_trough_x":entry_x,
                        "median_daily_quote_volume":median_qv,
                        "_entry_idx":i,
                    })

        # After the bar completes, update causal state for the NEXT open.
        if trough is None:
            trough=lo; trough_ts=ts; peak=hi; fired=set()
            continue

        prior_peak=peak
        # Completed-bar reset after >=50% drawdown from prior running peak.
        if prior_peak is not None and lo <= 0.5*prior_peak:
            trough=lo; trough_ts=ts; peak=hi; fired=set()
            continue

        # Fresh causal lower low restarts the trough after this bar closes.
        if lo < trough:
            trough=lo; trough_ts=ts; peak=hi; fired=set()
            continue

        if peak is None or hi>peak:
            peak=hi

    for s in signals:
        ei=s.pop("_entry_idx"); entry=s["entry"]
        for d in range(1,8):
            target_ts=s["entry_ts"]+d*DAY_MS
            xi=ts_to_idx.get(target_ts)
            if xi is None or xi<=ei:
                for k in ("gross_pct","net20_pct","net40_pct","mae_pct"):
                    s[f"d{d}_{k}"]=None
                continue
            exit_open=rows[xi][1]
            gross=(entry-exit_open)/entry*100.0
            max_high=max(r[2] for r in rows[ei:xi+1])
            mae=max(0.0,(max_high/entry-1.0)*100.0)
            s[f"d{d}_gross_pct"]=gross
            s[f"d{d}_net20_pct"]=gross-0.20
            s[f"d{d}_net40_pct"]=gross-0.40
            s[f"d{d}_mae_pct"]=mae
    return signals

def summarize(trades,day):
    g=[t[f"d{day}_gross_pct"] for t in trades if t.get(f"d{day}_gross_pct") is not None]
    m=[t[f"d{day}_mae_pct"] for t in trades if t.get(f"d{day}_mae_pct") is not None]
    if not g:return {"n":0}
    return {
        "n":len(g),
        "gross_mean_pct":sum(g)/len(g),
        "gross_median_pct":statistics.median(g),
        "gross_win_rate_pct":sum(x>0 for x in g)/len(g)*100.0,
        "net20_mean_pct":sum(x-0.20 for x in g)/len(g),
        "net40_mean_pct":sum(x-0.40 for x in g)/len(g),
        "net40_win_rate_pct":sum(x>0.40 for x in g)/len(g)*100.0,
        "worst_trade_gross_pct":min(g),
        "best_trade_gross_pct":max(g),
        "mae_mean_pct":sum(m)/len(m),
        "mae_median_pct":statistics.median(m),
        "mae_p90_pct":pctile(m,0.90),
        "mae_p95_pct":pctile(m,0.95),
        "mae_max_pct":max(m),
        "worst_account_impact_at_5pct_notional_pct":-0.05*max(m),
    }

def build_group(trades):
    out={}
    for thr in THRESHOLDS:
        ts=[t for t in trades if t["threshold_x"]==thr]
        out[str(thr)]={
            "signals":len(ts),
            "horizons":{f"d{d}":summarize(ts,d) for d in range(1,8)},
            "symbols":sorted(set(t["symbol"] for t in ts)),
        }
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-root",required=True)
    ap.add_argument("--out",required=True)
    a=ap.parse_args()
    paths=sorted(Path(a.data_root).rglob("*USDT.csv.gz"))
    all_trades=[]; symbol_stats=[]
    for i,p in enumerate(paths,1):
        rows,med_qv,active_days=load_symbol(p)
        sym=p.name.replace(".csv.gz","")
        t=signals_for_symbol(sym,rows,med_qv)
        all_trades.extend(t)
        symbol_stats.append({"symbol":sym,"median_daily_quote_volume":med_qv,"active_days":active_days})
        if i%25==0 or i==len(paths):
            print(f"processed {i}/{len(paths)} signals={len(all_trades)}",flush=True)

    liqs=[x["median_daily_quote_volume"] for x in symbol_stats if x["active_days"]>0]
    q25=pctile(liqs,0.25)
    low_syms={x["symbol"] for x in symbol_stats if x["median_daily_quote_volume"]<=q25}
    low_t=[t for t in all_trades if t["symbol"] in low_syms]

    all_group=build_group(all_trades)
    # Integrity: threshold counts must be monotone non-increasing.
    counts=[all_group[str(thr)]["signals"] for thr in THRESHOLDS]
    assert all(counts[i]>=counts[i+1] for i in range(len(counts)-1)), counts

    result={
        "definition":{
            "market":"Binance USD-M USDT perpetual frozen 5Y artifact run 36095439671",
            "interval":"15m","listing_warmup_days":90,"cycle_reset_drawdown_pct":50,
            "thresholds_x":list(THRESHOLDS),
            "entry":"first 15m OPEN at/above threshold using only prior-bar causal trough",
            "exit":"exact +N day 15m OPEN, N=1..7",
            "costs":"gross plus 20bp/40bp round-trip stress",
            "funding_included":False,
            "low_liquidity_rule":"bottom 25% by median daily quote volume",
            "low_liquidity_cutoff":q25,
        },
        "symbols_total":len(symbol_stats),
        "signals_total":len(all_trades),
        "all":all_group,
        "low_liquidity":build_group(low_t),
    }
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    (out/"summary.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    fields=["symbol","threshold_x","trough","trough_ts","entry_ts","entry","entry_vs_trough_x","median_daily_quote_volume"]
    for d in range(1,8):
        fields += [f"d{d}_gross_pct",f"d{d}_net20_pct",f"d{d}_net40_pct",f"d{d}_mae_pct"]
    with (out/"trades.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(all_trades)
    print("FINAL_SWEEP_JSON")
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
