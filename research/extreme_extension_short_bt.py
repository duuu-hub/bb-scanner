#!/usr/bin/env python3
"""Causal backtest: short extreme pump extensions from frozen Binance UM 15m 5Y data.

No-lookahead rules:
- Ignore first 90 calendar days after each symbol's available history begins.
- Maintain the current cycle trough causally from information known so far.
- A cycle resets only after price has drawn down >=50% from the running peak.
- A lower low before reset updates the trough causally.
- Signal when a 15m HIGH first reaches 10x/20x/30x/40x/50x the current trough.
- Enter SHORT at the NEXT 15m bar OPEN (never at the crossing price/bar).
- One entry per threshold per cycle.
- Fixed exits at exact +1d ... +7d timestamps, using that bar OPEN.
- Gross short return = (entry - exit) / entry.
- Net20/net40 subtract 20bp/40bp total round-trip cost respectively.
- MAE for short = maximum HIGH after entry through the fixed exit versus entry.
"""
from __future__ import annotations
import argparse,csv,gzip,json,math,statistics
from pathlib import Path

DAY_MS=86_400_000
BAR_MS=15*60*1000
WARMUP_MS=90*DAY_MS
THRESHOLDS=(10,20,30,40,50)

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
    rows=[]
    daily={}
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        r=csv.DictReader(f)
        for x in r:
            try:
                ts=int(x["open_time"]); op=float(x["open"]); hi=float(x["high"]); lo=float(x["low"]); cl=float(x["close"])
                qv=float(x.get("quote_volume") or 0.0)
            except Exception:
                continue
            if min(op,hi,lo,cl)<=0:continue
            rows.append((ts,op,hi,lo,cl))
            d=ts//DAY_MS
            daily[d]=daily.get(d,0.0)+max(qv,0.0)
    med=statistics.median(daily.values()) if daily else 0.0
    return rows,med,len(daily)

def signals_for_symbol(symbol,rows,median_qv):
    if len(rows)<2:return []
    first_ts=rows[0][0]
    start_ts=first_ts+WARMUP_MS
    ts_to_idx={r[0]:i for i,r in enumerate(rows)}
    trough=None; trough_ts=None
    peak=None
    fired=set()
    signals=[]
    pending=[]  # (cross_idx, threshold, trough, trough_ts, cross_high)

    for i,(ts,op,hi,lo,cl) in enumerate(rows):
        # Fill pending signals strictly at next bar open.
        if pending:
            for cross_idx,thr,tr,tr_ts,cross_hi in pending:
                if i==cross_idx+1:
                    signals.append({
                        "symbol":symbol,"threshold_x":thr,
                        "trough":tr,"trough_ts":tr_ts,
                        "cross_ts":rows[cross_idx][0],"cross_high":cross_hi,
                        "entry_ts":ts,"entry":op,
                        "entry_vs_trough_x":op/tr,
                        "median_daily_quote_volume":median_qv,
                        "_entry_idx":i,
                    })
            pending=[]

        if ts < start_ts:
            continue
        if trough is None:
            trough=lo; trough_ts=ts; peak=hi; fired=set()
            continue

        # Confirmed 50% drawdown resets cycle. We do not signal on the reset bar
        # because intrabar order between its low and high is unknowable.
        if peak is not None and lo <= 0.5*peak:
            trough=lo; trough_ts=ts; peak=hi; fired=set()
            continue

        # New causal low before cycle reset: restart trough, no same-bar signal.
        if lo < trough:
            trough=lo; trough_ts=ts; peak=hi; fired=set()
            continue

        if peak is None or hi>peak:
            peak=hi

        newly=[]
        for thr in THRESHOLDS:
            if thr in fired: continue
            if hi >= thr*trough:
                fired.add(thr)
                newly.append((i,thr,trough,trough_ts,hi))
        if newly:
            pending.extend(newly)

    # Attach fixed-horizon outcomes.
    for s in signals:
        ei=s.pop("_entry_idx")
        entry=s["entry"]
        for d in range(1,8):
            target_ts=s["entry_ts"]+d*DAY_MS
            xi=ts_to_idx.get(target_ts)
            if xi is None or xi<=ei:
                s[f"d{d}_gross_pct"]=None
                s[f"d{d}_net20_pct"]=None
                s[f"d{d}_net40_pct"]=None
                s[f"d{d}_mae_pct"]=None
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
    n20=[x-0.20 for x in g]; n40=[x-0.40 for x in g]
    return {
        "n":len(g),
        "gross_mean_pct":sum(g)/len(g),
        "gross_median_pct":statistics.median(g),
        "gross_win_rate_pct":sum(x>0 for x in g)/len(g)*100.0,
        "net20_mean_pct":sum(n20)/len(n20),
        "net20_win_rate_pct":sum(x>0 for x in n20)/len(n20)*100.0,
        "net40_mean_pct":sum(n40)/len(n40),
        "net40_win_rate_pct":sum(x>0 for x in n40)/len(n40)*100.0,
        "worst_trade_gross_pct":min(g),
        "best_trade_gross_pct":max(g),
        "mae_mean_pct":sum(m)/len(m),
        "mae_median_pct":statistics.median(m),
        "mae_p90_pct":pctile(m,0.90),
        "mae_p95_pct":pctile(m,0.95),
        "mae_max_pct":max(m),
        "worst_account_impact_at_5pct_notional_pct":-0.05*max(m),
    }

def group_summary(trades):
    return {
        str(thr):{
            "signals":sum(t["threshold_x"]==thr for t in trades),
            "horizons":{f"d{d}":summarize([t for t in trades if t["threshold_x"]==thr],d) for d in range(1,8)}
        } for thr in THRESHOLDS
    }

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
        ts=signals_for_symbol(sym,rows,med_qv)
        all_trades.extend(ts)
        symbol_stats.append({"symbol":sym,"median_daily_quote_volume":med_qv,"active_days":active_days})
        if i%25==0 or i==len(paths):
            print(f"processed {i}/{len(paths)} symbols signals={len(all_trades)}",flush=True)

    liqs=[x["median_daily_quote_volume"] for x in symbol_stats if x["active_days"]>0]
    q25=pctile(liqs,0.25)
    low_syms={x["symbol"] for x in symbol_stats if x["median_daily_quote_volume"]<=q25}
    low_trades=[t for t in all_trades if t["symbol"] in low_syms]

    result={
        "definition":{
            "market":"Binance USD-M USDT perpetual universe; frozen 5Y artifact run 36095439671",
            "interval":"15m",
            "listing_warmup_days":90,
            "cycle_reset_drawdown_pct":50,
            "thresholds_x":list(THRESHOLDS),
            "entry":"next 15m open after first causal threshold crossing",
            "exit":"exact +N day 15m open, N=1..7",
            "cost20":"20bp total round trip",
            "cost40":"40bp total round trip",
            "low_liquidity_rule":"bottom 25% by symbol median daily quote volume",
            "low_liquidity_cutoff_median_daily_quote_volume":q25,
            "funding_included":False,
        },
        "symbols_total":len(symbol_stats),
        "signals_total":len(all_trades),
        "all":group_summary(all_trades),
        "low_liquidity":group_summary(low_trades),
    }
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    (out/"summary.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    fields=["symbol","threshold_x","trough","trough_ts","cross_ts","cross_high","entry_ts","entry","entry_vs_trough_x","median_daily_quote_volume"]
    for d in range(1,8):
        fields += [f"d{d}_gross_pct",f"d{d}_net20_pct",f"d{d}_net40_pct",f"d{d}_mae_pct"]
    with (out/"trades.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(all_trades)
    print("BACKTEST_JSON")
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
