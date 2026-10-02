#!/usr/bin/env python3
"""Validation-only study for BODY70 continuation candidate.

Candidate is frozen:
- signal: 8h return >= +20%, cross-sectional top 10%, fresh transition
- signal candle bullish body ratio >= 0.70 of full high-low range
- entry: next 15m open
- TP +3%, SL -5%, max hold 6h
- same-symbol overlap blocked
- costs 20/30/40bp
- Train <=2024, Holdout >=2025

Validation dimensions only (NO retuning):
- calendar-year stability
- symbol concentration / top contributors
- recent 120d / 180d
- immediate vs +1m / +2m delayed entry
- max losing streak, trade-sequence drawdown
- concurrency distribution and fixed-risk portfolio proxy
"""
from __future__ import annotations
import argparse, csv, glob, io, json, math, os, urllib.error, urllib.request, zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import trend_continuation_first_touch as base

BAR_MS=base.BAR_MS
MIN_MS=base.MIN_MS
DAY_MS=base.DAY_MS
TP_PCT=3.0
SL_PCT=5.0
HOLD_MS=6*60*60*1000
COSTS=(20,30,40)
DELAYS=(0,1,2)
BASE_1M="https://data.binance.vision/data/futures/um/daily/klines"
base.TP_PCT=TP_PCT

def max_losing_streak(vals):
    best=cur=0
    for x in vals:
        if x<0:
            cur+=1; best=max(best,cur)
        else:
            cur=0
    return best

def trade_sequence_mdd(vals):
    eq=0.0; peak=0.0; mdd=0.0
    for x in vals:
        eq+=x
        peak=max(peak,eq)
        mdd=min(mdd,eq-peak)
    return mdd

@lru_cache(maxsize=1024)
def load_1m_day_ohlc(symbol,day):
    url=f"{BASE_1M}/{symbol}/1m/{symbol}-1m-{day}.zip"
    req=urllib.request.Request(url,headers={"User-Agent":"body70-validation/1.0"})
    try:
        with urllib.request.urlopen(req,timeout=60) as r:
            raw=r.read()
    except urllib.error.HTTPError as e:
        if e.code==404: return None
        return None
    except Exception:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names=[n for n in zf.namelist() if n.endswith(".csv")]
            if not names: return None
            rows=[]
            with io.TextIOWrapper(zf.open(names[0]),encoding="utf-8") as fh:
                for r in csv.reader(fh):
                    if len(r)<5 or not str(r[0]).isdigit(): continue
                    t=base.normalize_ts(r[0])
                    rows.append((t,float(r[1]),float(r[2]),float(r[3]),float(r[4])))
            return rows
    except Exception:
        return None

def minute_row(symbol,ts_ms):
    day=datetime.fromtimestamp(ts_ms/1000,tz=timezone.utc).strftime("%Y-%m-%d")
    rows=load_1m_day_ohlc(symbol,day)
    if rows is None: return None
    for r in rows:
        if r[0]==ts_ms: return r
    return None

def delayed_outcome(symbol,raw,base_entry_ts,delay_min):
    if delay_min==0:
        return None
    ets=base_entry_ts+delay_min*MIN_MS
    mr=minute_row(symbol,ets)
    if mr is None:
        return {"status":"DATA_GAP"}
    _,entry,hi0,lo0,cl0=mr
    tp=entry*(1+TP_PCT/100.0)
    sl=entry*(1-SL_PCT/100.0)

    # Entry-minute chronology from its OPEN.
    th=hi0>=tp; sh=lo0<=sl
    if th and sh:
        return {"status":"SL","gross_pct":-SL_PCT,"entry":entry,"entry_ts":ets,"exit_ts":ets+MIN_MS,"via":"entry_1m_both_loss"}
    if sh:
        return {"status":"SL","gross_pct":-SL_PCT,"entry":entry,"entry_ts":ets,"exit_ts":ets+MIN_MS,"via":"entry_1m"}
    if th:
        return {"status":"TP","gross_pct":TP_PCT,"entry":entry,"entry_ts":ets,"exit_ts":ets+MIN_MS,"via":"entry_1m"}

    # Remaining 1m bars in the parent 15m bar, exact chronology.
    parent=(ets//BAR_MS)*BAR_MS
    day=datetime.fromtimestamp(parent/1000,tz=timezone.utc).strftime("%Y-%m-%d")
    rows=load_1m_day_ohlc(symbol,day)
    if rows is None:
        return {"status":"DATA_GAP"}
    seg=[r for r in rows if ets+MIN_MS<=r[0]<parent+BAR_MS]
    expected_n=max(0,int((parent+BAR_MS-(ets+MIN_MS))//MIN_MS))
    if len(seg)!=expected_n:
        return {"status":"DATA_GAP"}
    for t,o,h,l,c in seg:
        th=h>=tp; sh=l<=sl
        if th and sh:
            return {"status":"SL","gross_pct":-SL_PCT,"entry":entry,"entry_ts":ets,"exit_ts":t+MIN_MS,"via":"1m_both_loss"}
        if sh:
            return {"status":"SL","gross_pct":-SL_PCT,"entry":entry,"entry_ts":ets,"exit_ts":t+MIN_MS,"via":"1m"}
        if th:
            return {"status":"TP","gross_pct":TP_PCT,"entry":entry,"entry_ts":ets,"exit_ts":t+MIN_MS,"via":"1m"}

    ts=raw["ts"]; hi=raw["high"]; lo=raw["low"]; cl=raw["close"]
    first_full=parent+BAR_MS
    idx=int(np.searchsorted(ts,first_full))
    deadline=ets+HOLD_MS

    j=idx
    while j<len(ts):
        bts=int(ts[j])
        if bts+BAR_MS>deadline: break
        if bts!=first_full+(j-idx)*BAR_MS:
            return {"status":"DATA_GAP"}
        th=float(hi[j])>=tp; sh=float(lo[j])<=sl
        if th and sh:
            r=base.resolve_collision_1m(symbol,bts,tp,sl)
            st=r.get("status")
            if st=="TP":
                return {"status":"TP","gross_pct":TP_PCT,"entry":entry,"entry_ts":ets,"exit_ts":int(r["exit_ts"]),"via":"15m_collision"}
            if st=="SL":
                return {"status":"SL","gross_pct":-SL_PCT,"entry":entry,"entry_ts":ets,"exit_ts":int(r["exit_ts"]),"via":"15m_collision"}
            return {"status":st}
        if sh:
            return {"status":"SL","gross_pct":-SL_PCT,"entry":entry,"entry_ts":ets,"exit_ts":bts+BAR_MS,"via":"15m"}
        if th:
            return {"status":"TP","gross_pct":TP_PCT,"entry":entry,"entry_ts":ets,"exit_ts":bts+BAR_MS,"via":"15m"}
        j+=1

    # last completed 15m close not exceeding deadline
    exit_j=j-1
    if exit_j<idx:
        # use end of fill parent candle if no later full bar fits
        pidx=int(np.searchsorted(ts,parent))
        if pidx>=len(ts) or int(ts[pidx])!=parent: return {"status":"DATA_GAP"}
        gross=(float(cl[pidx])/entry-1.0)*100.0
        return {"status":"TIME","gross_pct":gross,"entry":entry,"entry_ts":ets,"exit_ts":parent+BAR_MS,"via":"time_parent_close"}
    gross=(float(cl[exit_j])/entry-1.0)*100.0
    return {"status":"TIME","gross_pct":gross,"entry":entry,"entry_ts":ets,"exit_ts":int(ts[exit_j])+BAR_MS,"via":"time"}

def load_body70_events(events,raw_paths):
    rows=[]; raws={}
    for sym,g in events.sort_values(["symbol","entry_ts"]).groupby("symbol",sort=True):
        p=raw_paths.get(sym)
        if not p: continue
        d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"],
                      dtype={"open_time":"int64","open":"float64","high":"float64","low":"float64","close":"float64"})
        d=d.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
        raw={"ts":d.open_time.to_numpy(dtype=np.int64),"open":d.open.to_numpy(float),
             "high":d.high.to_numpy(float),"low":d.low.to_numpy(float),"close":d.close.to_numpy(float)}
        raws[sym]=raw
        ts=raw["ts"]
        for r in g.itertuples(index=False):
            si=int(np.searchsorted(ts,int(r.ts)))
            if si>=len(ts) or int(ts[si])!=int(r.ts): continue
            o=float(raw["open"][si]); h=float(raw["high"][si]); l=float(raw["low"][si]); c=float(raw["close"][si])
            rng=h-l
            body=max(c-o,0.0)
            ratio=(body/rng) if rng>0 else 0.0
            if c>o and ratio>=0.70:
                rows.append({"symbol":sym,"ts":int(r.ts),"entry_ts":int(r.entry_ts),"entry":float(r.entry),
                             "split":r.split if hasattr(r,"split") else base.split_name(int(r.ts)),
                             "body_ratio":ratio})
    return pd.DataFrame(rows),raws

def build_trades(cands,raws,delay):
    out=[]; busy={}
    for r in cands.sort_values(["entry_ts","symbol"]).itertuples(index=False):
        sym=r.symbol; base_et=int(r.entry_ts)
        actual_et=base_et+delay*MIN_MS
        if actual_et<busy.get(sym,-1):
            continue
        raw=raws[sym]
        if delay==0:
            rs=base.event_outcomes(sym,raw,base_et,float(r.entry),SL_PCT)
            rec=rs["6h"].copy()
            rec["entry"]=float(r.entry); rec["entry_ts"]=base_et
        else:
            rec=delayed_outcome(sym,raw,base_et,delay)
        st=rec.get("status")
        if st in ("DATA_GAP","ENTRY_MISMATCH","EXIT_MISMATCH") or "gross_pct" not in rec:
            continue
        busy[sym]=int(rec["exit_ts"])
        out.append({"symbol":sym,"signal_ts":int(r.ts),"entry_ts":int(rec["entry_ts"]),"exit_ts":int(rec["exit_ts"]),
                    "split":r.split,"delay_min":delay,"gross_pct":float(rec["gross_pct"]),"status":st})
    return pd.DataFrame(out)

def metrics(df,cost):
    if df.empty:
        return {"n":0}
    vals=(df.gross_pct-cost/100.0).to_numpy(float)
    pos=vals[vals>0].sum(); neg=-vals[vals<0].sum()
    return {
        "n":int(len(vals)),
        "win_rate_pct":float((vals>0).mean()*100),
        "avg_net_pct":float(vals.mean()),
        "median_net_pct":float(np.median(vals)),
        "pf_net":float(pos/neg) if neg>0 else None,
        "sum_net_pct":float(vals.sum()),
        "max_losing_streak":int(max_losing_streak(vals)),
        "trade_sequence_mdd_pctsum":float(trade_sequence_mdd(vals)),
    }

def concurrency_stats(df):
    if df.empty: return {}
    pts=[]
    for r in df.itertuples(index=False):
        pts.append((int(r.entry_ts),1)); pts.append((int(r.exit_ts),-1))
    # exits before entries at same timestamp
    pts.sort(key=lambda x:(x[0],x[1]))
    cur=mx=0; levels=Counter()
    last_t=None
    for t,d in pts:
        cur+=d; mx=max(mx,cur); levels[cur]+=1
    return {"max_concurrent":int(mx)}

def portfolio_proxy(df,cost,risk_pct):
    """Fixed fractional proxy: position notional = risk_pct/SL_PCT of equity at entry.
    PnL contribution per trade ~= net_pct * risk_pct / SL_PCT in account-%.
    Chronological trade-close accumulation; no exposure cap."""
    if df.empty: return {}
    x=df.copy()
    x["acct_pnl_pct"]=(x.gross_pct-cost/100.0)*(risk_pct/SL_PCT)
    x=x.sort_values("exit_ts")
    vals=x.acct_pnl_pct.to_numpy(float)
    return {
        "risk_pct":risk_pct,
        "sum_account_pct":float(vals.sum()),
        "trade_close_mdd_pct":float(trade_sequence_mdd(vals)),
        "max_losing_streak":int(max_losing_streak(vals)),
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--events-in",required=True); ap.add_argument("--raw",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)

    e=pd.read_csv(a.events_in,compression="infer")
    e=e[e["lookback"].eq("8h") & np.isclose(e["tail"].astype(float),0.10) & np.isclose(e["threshold_pct"].astype(float),20.0)].copy()
    e["ts"]=e["ts"].astype("int64"); e["entry_ts"]=e["entry_ts"].astype("int64")
    if "split" not in e: e["split"]=e["ts"].map(base.split_name)
    raw_paths={base.sym_from_path(p):p for p in glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True)}
    e=e[e["symbol"].isin(set(raw_paths))].copy()

    cands,raws=load_body70_events(e,raw_paths)
    print("BODY70_CANDIDATES",len(cands),"symbols",cands.symbol.nunique(),flush=True)

    all_trades=[]
    for delay in DELAYS:
        t=build_trades(cands,raws,delay)
        all_trades.append(t)
        print("DELAY_DONE",delay,"n",len(t),flush=True)
    trades=pd.concat(all_trades,ignore_index=True) if all_trades else pd.DataFrame()
    trades.to_csv(out/"trades.csv.gz",index=False,compression="gzip")

    summary_rows=[]
    for delay in DELAYS:
        td=trades[trades.delay_min.eq(delay)].copy()
        for split in ("TRAIN","HOLDOUT"):
            z=td[td.split.eq(split)].copy()
            for cost in COSTS:
                rec={"delay_min":delay,"split":split,"cost_bp":cost,**metrics(z,cost),**concurrency_stats(z)}
                rec["portfolio_risk_0_5"]=portfolio_proxy(z,cost,0.5)
                rec["portfolio_risk_1_0"]=portfolio_proxy(z,cost,1.0)
                summary_rows.append(rec)

    # year stability at canonical immediate/20bp
    canon=trades[trades.delay_min.eq(0)].copy()
    canon["year"]=pd.to_datetime(canon.signal_ts,unit="ms",utc=True).dt.year
    year_rows=[]
    for y,g in canon.groupby("year"):
        year_rows.append({"year":int(y),**metrics(g,20)})

    # recent windows, anchored to max signal timestamp in candidate data
    recent_rows=[]
    if len(canon):
        max_ts=int(canon.signal_ts.max())
        for days in (120,180):
            g=canon[canon.signal_ts>=max_ts-days*DAY_MS]
            recent_rows.append({"window_days":days,"anchor_max_ts":max_ts,**metrics(g,20)})

    # symbol concentration at canonical immediate/20bp
    sym_rows=[]
    if len(canon):
        canon=canon.copy(); canon["net20"]=canon.gross_pct-0.20
        for sym,g in canon.groupby("symbol"):
            sym_rows.append({"symbol":sym,"n":int(len(g)),"sum_net_pct":float(g.net20.sum()),"avg_net_pct":float(g.net20.mean())})
        sym_rows=sorted(sym_rows,key=lambda x:x["sum_net_pct"],reverse=True)
    top10_profit=sum(max(0,x["sum_net_pct"]) for x in sym_rows[:10])
    total_profit=sum(max(0,x["sum_net_pct"]) for x in sym_rows)
    concentration={"top10_positive_contribution_share":(top10_profit/total_profit if total_profit>0 else None),
                   "top10_symbols":sym_rows[:10],"bottom10_symbols":sym_rows[-10:]}

    pd.DataFrame(summary_rows).to_json(out/"summary_rows.json",orient="records",indent=2)
    pd.DataFrame(year_rows).to_csv(out/"yearly.csv",index=False)
    pd.DataFrame(recent_rows).to_csv(out/"recent_windows.csv",index=False)
    pd.DataFrame(sym_rows).to_csv(out/"symbol_stats.csv",index=False)

    summary={
        "candidate":{"signal":"8h>=20% top10 fresh transition + bullish body ratio>=0.70",
                     "tp_pct":TP_PCT,"sl_pct":SL_PCT,"hold":"6h","costs":COSTS,"delays_min":DELAYS},
        "summary_rows":summary_rows,
        "yearly_20bp_immediate":year_rows,
        "recent_20bp_immediate":recent_rows,
        "symbol_concentration_20bp_immediate":concentration,
        "integrity_stats":dict(base.STATS),
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("BODY70_VALIDATION_JSON"); print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__": main()
