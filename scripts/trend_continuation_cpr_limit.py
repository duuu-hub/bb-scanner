#!/usr/bin/env python3
"""CPR v2: buy the pullback directly with a limit order.

Frozen signal:
- 8h return >= +20%, cross-sectional top 10%, fresh transition only.
- Original next-15m-open is the reference price/time.

Only change:
- During the next 2h, place a buy limit at -0.5/-1.0/-1.5/-2.0% from reference.
- First touch fills at the limit price.
- No reclaim/confirmation.
- TP +3%, SL -5%, max hold <=6h.
- 20/40bp round-trip cost.
- Train/Holdout split by original signal year.
- One active setup/trade per symbol.

Chronology:
- A 15m bar that touches the limit is resolved with official Binance 1m.
- Fill is the first 1m low <= limit.
- Entry-minute SL touch => LOSS.
- Entry-minute TP-only is ambiguous post-entry chronology and is treated as LOSS,
  matching repository conservative execution rules.
- Subsequent 1m in the fill 15m bar is scanned in order.
- Later 15m TP/SL collisions use the canonical Binance 1m resolver.
- Missing/misaligned required 1m data => DATA_GAP/excluded.
- Time exit uses the last completed 15m close no later than 6h after actual fill,
  so the holding period never exceeds 6h.
"""
from __future__ import annotations
import argparse, glob, json, os
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
import trend_continuation_first_touch as base

BAR_MS=base.BAR_MS
MIN_MS=base.MIN_MS
DAY_MS=base.DAY_MS
PULLBACKS=(0.5,1.0,1.5,2.0)
SETUP_BARS=8
TP_PCT=3.0
SL_PCT=5.0
COST_BPS=(20,40)
HOLD_MS=6*60*60*1000

base.TP_PCT=TP_PCT

def first_fill_1m(symbol, bar_ts, limit_px):
    day=base.datetime.fromtimestamp(bar_ts/1000,tz=base.timezone.utc).strftime("%Y-%m-%d")
    rows=base.load_1m_day(symbol,day)
    if rows is None:
        return {"status":"DATA_GAP"}
    seg=[(t,h,l) for (t,h,l) in rows if bar_ts<=t<bar_ts+BAR_MS]
    if len(seg)!=15 or seg[0][0]!=bar_ts or any(seg[i][0]-seg[i-1][0]!=MIN_MS for i in range(1,len(seg))):
        return {"status":"DATA_GAP"}
    for i,(t,h,l) in enumerate(seg):
        if l<=limit_px:
            return {"status":"FILL","fill_ts":int(t),"fill_i":i,"seg":seg}
    return {"status":"FILL_MISMATCH"}

def find_limit_fill(symbol, raw, ref_ts, ref_px, depth):
    ts, lo=raw["ts"],raw["low"]
    idx=int(np.searchsorted(ts,ref_ts))
    if idx>=len(ts) or int(ts[idx])!=int(ref_ts):
        return {"status":"ENTRY_MISMATCH"}
    if ref_px<=0 or abs(float(raw["open"][idx])/ref_px-1.0)>1e-9:
        return {"status":"ENTRY_MISMATCH"}
    limit_px=ref_px*(1.0-depth/100.0)
    for k in range(SETUP_BARS):
        j=idx+k
        expected=ref_ts+k*BAR_MS
        if j>=len(ts) or int(ts[j])!=int(expected):
            return {"status":"DATA_GAP"}
        if float(lo[j])<=limit_px:
            q=first_fill_1m(symbol,int(ts[j]),limit_px)
            q.update({"limit_px":float(limit_px),"fill_bar_idx":j,"bars_to_fill":k})
            return q
    return {"status":"NO_FILL"}

def simulate_after_fill(symbol, raw, fill):
    ts,hi,lo,cl=raw["ts"],raw["high"],raw["low"],raw["close"]
    entry=float(fill["limit_px"])
    tp=entry*(1+TP_PCT/100.0)
    sl=entry*(1-SL_PCT/100.0)
    j0=int(fill["fill_bar_idx"])
    fill_ts=int(fill["fill_ts"])
    seg=fill["seg"]
    fi=int(fill["fill_i"])

    # Entry minute. Low reached the limit. If the same minute also makes the SL,
    # the path from above must cross entry first. TP-only is chronology-ambiguous.
    t,h,l=seg[fi]
    if l<=sl:
        return {"status":"SL","gross_pct":-SL_PCT,"exit_ts":int(t+MIN_MS),"via":"entry_1m_sl"}
    if h>=tp:
        base.STATS["entry_minute_tp_ambiguous_loss"]+=1
        return {"status":"SL","gross_pct":-SL_PCT,"exit_ts":int(t+MIN_MS),"via":"entry_1m_tp_ambiguous_loss"}

    # Remaining one-minute bars in the fill 15m candle.
    for t,h,l in seg[fi+1:]:
        th=h>=tp; sh=l<=sl
        if th and sh:
            base.STATS["same_1m_both_loss"]+=1
            return {"status":"SL","gross_pct":-SL_PCT,"exit_ts":int(t+MIN_MS),"via":"1m_same_both"}
        if sh:
            return {"status":"SL","gross_pct":-SL_PCT,"exit_ts":int(t+MIN_MS),"via":"1m"}
        if th:
            return {"status":"TP","gross_pct":TP_PCT,"exit_ts":int(t+MIN_MS),"via":"1m"}

    deadline=fill_ts+HOLD_MS

    # Later complete 15m candles. Stop before a candle whose close would exceed deadline.
    j=j0+1
    while j<len(ts):
        bar_ts=int(ts[j])
        if bar_ts+BAR_MS>deadline:
            break
        expected=int(ts[j0]+(j-j0)*BAR_MS)
        if bar_ts!=expected:
            return {"status":"DATA_GAP","exit_ts":bar_ts}
        th=float(hi[j])>=tp; sh=float(lo[j])<=sl
        if th and sh:
            r=base.resolve_collision_1m(symbol,bar_ts,tp,sl)
            st=r["status"]
            if st=="TP":
                return {"status":"TP","gross_pct":TP_PCT,"exit_ts":int(r["exit_ts"]),"via":"collision_1m"}
            if st=="SL":
                return {"status":"SL","gross_pct":-SL_PCT,"exit_ts":int(r["exit_ts"]),"via":"collision_1m"}
            return {"status":st,"exit_ts":int(r.get("exit_ts",bar_ts))}
        if sh:
            return {"status":"SL","gross_pct":-SL_PCT,"exit_ts":bar_ts+BAR_MS,"via":"15m"}
        if th:
            return {"status":"TP","gross_pct":TP_PCT,"exit_ts":bar_ts+BAR_MS,"via":"15m"}
        j+=1

    # Last completed 15m close at or before the 6h deadline.
    exit_j=max(j0,min(j-1,len(ts)-1))
    exit_end=int(ts[exit_j])+BAR_MS
    if exit_end>deadline:
        exit_j-=1
    if exit_j<j0:
        # fill-bar close is necessarily after fill but <=15m later and well before 6h
        exit_j=j0
    gross=(float(cl[exit_j])/entry-1.0)*100.0
    return {"status":"TIME","gross_pct":gross,"exit_ts":int(ts[exit_j])+BAR_MS,"via":"completed_15m_before_deadline"}

def replay_depth(events, raw_paths, depth, split):
    a=events[events["split"].eq(split)].sort_values(["symbol","entry_ts"])
    gross=[]; statuses=Counter(); misc=Counter(); bars=[]; accepted=[]
    for sym,g in a.groupby("symbol",sort=True):
        p=raw_paths.get(sym)
        if p is None:
            misc["ENTRY_MISMATCH"]+=len(g); continue
        raw=base.load_raw_symbol(p)
        busy=-1
        for row in g.itertuples(index=False):
            ref_ts=int(row.entry_ts)
            if ref_ts<busy:
                misc["overlap_skips"]+=1; continue
            busy=ref_ts+SETUP_BARS*BAR_MS
            fill=find_limit_fill(sym,raw,ref_ts,float(row.entry),depth)
            st=fill["status"]
            if st=="NO_FILL":
                misc["no_fill"]+=1; continue
            if st!="FILL":
                misc[st]+=1; continue
            rec=simulate_after_fill(sym,raw,fill)
            rst=rec["status"]
            if rst in ("DATA_GAP","ENTRY_MISMATCH","EXIT_MISMATCH") or "gross_pct" not in rec:
                misc[rst]+=1; continue
            busy=int(rec["exit_ts"])
            gross.append(float(rec["gross_pct"]))
            statuses[rst]+=1
            bars.append(int(fill["bars_to_fill"]))
            accepted.append(int(fill["fill_ts"]))
    return gross,statuses,misc,bars,accepted

def summarize(gross,statuses,misc,bars,accepted,cost,signal_n):
    vals=[x-cost/100.0 for x in gross]
    n=len(vals); pos=sum(x for x in vals if x>0); neg=-sum(x for x in vals if x<0)
    span=None
    if accepted:
        span=max(1.0,(max(accepted)-min(accepted))/DAY_MS+1.0)
    return {
        "signal_n":int(signal_n),"n":n,"setup_fill_pct":(n/signal_n*100.0 if signal_n else None),
        "overlap_skips":int(misc["overlap_skips"]),"no_setup":int(misc["no_fill"]),
        "excluded":int(sum(v for k,v in misc.items() if k not in ("overlap_skips","no_fill"))),
        "excluded_detail":json.dumps({k:int(v) for k,v in misc.items() if k not in ("overlap_skips","no_fill")},sort_keys=True),
        "tp_n":int(statuses["TP"]),"sl_n":int(statuses["SL"]),"time_n":int(statuses["TIME"]),
        "win_n":int(sum(x>0 for x in vals)),"sum_net_pct":float(sum(vals)),
        "gross_profit_net_pct":float(pos),"gross_loss_abs_net_pct":float(neg),
        "win_rate_net_pct":(sum(x>0 for x in vals)/n*100.0 if n else None),
        "avg_net_pct":(sum(vals)/n if n else None),"pf_net":(pos/neg if neg>0 else None),
        "avg_bars_to_entry":(float(np.mean(bars)) if bars else None),
        "trades_per_day":(n/span if n and span else None),
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--events-in",required=True); ap.add_argument("--raw",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    events=pd.read_csv(a.events_in,compression="infer")
    events=events[
        events["lookback"].eq("8h") &
        np.isclose(events["tail"].astype(float),0.10) &
        np.isclose(events["threshold_pct"].astype(float),20.0)
    ].copy()
    events["entry_ts"]=events["entry_ts"].astype("int64"); events["ts"]=events["ts"].astype("int64")
    if "split" not in events: events["split"]=events["ts"].map(base.split_name)
    raw_paths={base.sym_from_path(p):p for p in glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True)}
    events=events[events["symbol"].isin(set(raw_paths))].copy()
    print("LIMIT_CPR_EVENTS",len(events),"symbols",events.symbol.nunique(),flush=True)

    rows=[]
    for d in PULLBACKS:
        for split in ("TRAIN","HOLDOUT"):
            eg=events[events["split"].eq(split)]
            gross,statuses,misc,bars,accepted=replay_depth(events,raw_paths,d,split)
            for cost in COST_BPS:
                s=summarize(gross,statuses,misc,bars,accepted,cost,len(eg))
                s.update({"pullback_pct":d,"split":split,"cost_bp":cost,"tp_pct":TP_PCT,"sl_pct":SL_PCT,
                          "time_limit":"6h","setup_window":"2h","reclaim":"NONE_limit_touch"})
                rows.append(s)
            print("DEPTH_DONE",d,split,"n",len(gross),"status",dict(statuses),"misc",dict(misc),flush=True)
    cells=pd.DataFrame(rows); cells.to_csv(out/"cells.csv",index=False)
    summary={"design":{"baseline":"8h>=20% top10 fresh transition","entry":"direct limit touch","pullbacks":PULLBACKS,
                       "setup_window":"2h","tp_pct":TP_PCT,"sl_pct":SL_PCT,"hold":"<=6h",
                       "cost_bp":COST_BPS,"entry_minute_tp_only":"LOSS per repo rule"},
             "integrity_stats":dict(base.STATS),"rows":rows}
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("LIMIT_CPR_RESULT_JSON"); print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__": main()
