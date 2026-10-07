#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, glob, io, json, math, os, urllib.error, urllib.request, zipfile
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

BAR = 15 * 60 * 1000
MIN = 60 * 1000
HOLD = 4 * 60 * 60 * 1000
DAY = 24 * 60 * 60 * 1000
WEEK_OFFSET = 4 * DAY  # Monday 1970-01-05 00:00 UTC
TP_PCT = 10.0
SL_PCT = 4.0
DELAYS = (0, 1, 2)
TF = {
    "1W": (7 * DAY, WEEK_OFFSET),
    "1D": (DAY, 0),
    "12H": (12 * 60 * 60 * 1000, 0),
    "4H": (4 * 60 * 60 * 1000, 0),
    "1H": (60 * 60 * 1000, 0),
    "30M": (30 * 60 * 1000, 0),
    "15M": (BAR, 0),
}
BASE_1M = "https://data.binance.vision/data/futures/um/daily/klines"

STATS = Counter()

def sym_from_path(path: str) -> str:
    b = os.path.basename(path)
    return b[:-7].upper() if b.endswith(".csv.gz") else os.path.splitext(b)[0].upper()

def norm_ts(v) -> int:
    x = int(v)
    if x > 10**14:
        x //= 1000
    return x

@lru_cache(maxsize=1024)
def load_1m_day(symbol: str, day: str):
    url = f"{BASE_1M}/{symbol}/1m/{symbol}-1m-{day}.zip"
    req = urllib.request.Request(url, headers={"User-Agent": "s2-5y-replay/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except Exception:
        return None
    try:
        out = []
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = [n for n in zf.namelist() if n.endswith(".csv")]
            if not names:
                return None
            with io.TextIOWrapper(zf.open(names[0]), encoding="utf-8") as fh:
                for row in csv.reader(fh):
                    if len(row) < 5 or not str(row[0]).isdigit():
                        continue
                    out.append((norm_ts(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])))
        return out
    except Exception:
        return None

def minute_row(symbol: str, ts_ms: int):
    day = datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    rows = load_1m_day(symbol, day)
    if rows is None:
        return None
    for r in rows:
        if r[0] == ts_ms:
            return r
    return None

def day_rows(symbol: str, ts_ms: int):
    day = datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return load_1m_day(symbol, day)

def load_raw(path: str):
    d = pd.read_csv(
        path, compression="gzip",
        usecols=["open_time","open","high","low","close"],
        dtype={"open_time":"int64","open":"float64","high":"float64","low":"float64","close":"float64"},
    ).sort_values("open_time").drop_duplicates("open_time")
    return {
        "ts": d.open_time.to_numpy(np.int64),
        "open": d.open.to_numpy(float),
        "high": d.high.to_numpy(float),
        "low": d.low.to_numpy(float),
        "close": d.close.to_numpy(float),
    }

def complete_tf(ts, close, dur, offset):
    bins = ((ts - offset) // dur) * dur + offset
    uniq, first, counts = np.unique(bins, return_index=True, return_counts=True)
    last = first + counts - 1
    need = dur // BAR
    good = (
        (counts == need)
        & (ts[first] == uniq)
        & (ts[last] == uniq + dur - BAR)
    )
    return uniq[good] + dur, close[last[good]]

def bb_state_vec(eval_ts, live, close_times, closes, dur, offset):
    idx = np.searchsorted(close_times, eval_ts, side="right")
    valid = idx >= 19
    above = np.zeros(len(eval_ts), dtype=bool)
    dist = np.full(len(eval_ts), np.nan, dtype=float)
    if not np.any(valid):
        return valid, above, dist

    pref = np.zeros(len(closes)+1, dtype=float)
    pref2 = np.zeros(len(closes)+1, dtype=float)
    np.cumsum(closes, out=pref[1:])
    np.cumsum(closes*closes, out=pref2[1:])

    vi = np.where(valid)[0]
    j = idx[vi]
    first_j = j - 19
    last_j = j - 1

    # Require the 19 completed TF candles to be contiguous and end at the
    # expected most-recent completed TF boundary.
    expected_last = ((eval_ts[vi] - offset) // dur) * dur + offset
    contiguous = (
        (close_times[last_j] == expected_last)
        & ((close_times[last_j] - close_times[first_j]) == 18 * dur)
    )
    vi = vi[contiguous]
    j = idx[vi]
    if len(vi) == 0:
        valid[:] = False
        return valid, above, dist

    sums = pref[j] - pref[j-19]
    sums2 = pref2[j] - pref2[j-19]
    total = sums + live[vi]
    total2 = sums2 + live[vi]*live[vi]
    basis = total / 20.0
    var = np.maximum(0.0, total2/20.0 - basis*basis)
    upper = basis + 2.0*np.sqrt(var)
    ok = upper > 0

    final_valid = np.zeros(len(eval_ts), dtype=bool)
    final_valid[vi[ok]] = True
    dist[vi[ok]] = (live[vi[ok]] / upper[ok] - 1.0) * 100.0
    above[vi[ok]] = live[vi[ok]] > upper[ok]
    return final_valid, above, dist

def build_signals(raw):
    ts = raw["ts"]; live = raw["open"]; close = raw["close"]
    n = len(ts)
    if n < 14_000:
        return pd.DataFrame()

    states = {}
    all_valid = np.ones(n, dtype=bool)
    exact = np.zeros(n, dtype=np.int8)

    for name, (dur, offset) in TF.items():
        if name == "15M":
            ctimes = ts + BAR
            cclose = close
        else:
            ctimes, cclose = complete_tf(ts, close, dur, offset)
        valid, above, dist = bb_state_vec(ts, live, ctimes, cclose, dur, offset)
        states[name] = (valid, above, dist)
        all_valid &= valid
        exact += above.astype(np.int8)

    v15, a15, d15 = states["15M"]
    raw_cond = all_valid & (exact == 6) & (~a15) & (d15 >= -1.0)

    prev_valid = np.roll(all_valid, 1)
    prev_cond = np.roll(raw_cond, 1)
    prev_contig = np.roll(ts, 1) == ts - BAR
    prev_valid[0] = False; prev_cond[0] = False; prev_contig[0] = False
    fresh = raw_cond & prev_valid & prev_contig & (~prev_cond)

    ix = np.where(fresh)[0]
    if len(ix) == 0:
        return pd.DataFrame()
    return pd.DataFrame({
        "signal_ts": ts[ix],
        "entry0": live[ix],
        "d15": d15[ix],
        "exact": exact[ix],
    })

def inspect_parent_1m(symbol, parent_ts, tp, sl, entry_minute_ts=None):
    rows = day_rows(symbol, parent_ts)
    if rows is None:
        return {"status":"DATA_GAP"}
    seg = [r for r in rows if parent_ts <= r[0] < parent_ts + BAR]
    if len(seg) != 15 or seg[0][0] != parent_ts or any(seg[i][0]-seg[i-1][0] != MIN for i in range(1,15)):
        return {"status":"DATA_GAP"}

    started = entry_minute_ts is None
    for t,o,h,l,c in seg:
        if entry_minute_ts is not None:
            if t < entry_minute_ts:
                continue
            if t == entry_minute_ts:
                started = True
                th = l <= tp
                sh = h >= sl
                # Mandatory repository rule: any entry-minute TP/SL touch is LOSS.
                if th or sh:
                    return {"status":"SL","exit_ts":t+MIN,"via":"entry_minute_conservative_loss"}
                continue
        if not started:
            continue
        th = l <= tp
        sh = h >= sl
        if th and sh:
            return {"status":"SL","exit_ts":t+MIN,"via":"same_1m_both_loss"}
        if sh:
            return {"status":"SL","exit_ts":t+MIN,"via":"1m"}
        if th:
            return {"status":"TP","exit_ts":t+MIN,"via":"1m"}
    return {"status":"NONE"}

def short_outcome(symbol, raw, base_ts, delay_min):
    ts=raw["ts"]; op=raw["open"]; hi=raw["high"]; lo=raw["low"]
    if delay_min == 0:
        entry_ts = int(base_ts)
        i = int(np.searchsorted(ts, entry_ts))
        if i >= len(ts) or int(ts[i]) != entry_ts:
            return {"status":"ENTRY_MISMATCH"}
        entry = float(op[i])
        parent = entry_ts
    else:
        entry_ts = int(base_ts + delay_min*MIN)
        mr = minute_row(symbol, entry_ts)
        if mr is None:
            return {"status":"DATA_GAP"}
        _, entry, _, _, _ = mr
        parent = (entry_ts // BAR) * BAR
        i = int(np.searchsorted(ts, parent))
        if i >= len(ts) or int(ts[i]) != parent:
            return {"status":"DATA_GAP"}

    if entry <= 0:
        return {"status":"ENTRY_MISMATCH"}

    tp = entry * (1.0 - TP_PCT/100.0)
    sl = entry * (1.0 + SL_PCT/100.0)
    deadline = entry_ts + HOLD

    # Entry parent: if any parent-level touch is possible, resolve from 1m.
    if float(lo[i]) <= tp or float(hi[i]) >= sl or delay_min > 0:
        r = inspect_parent_1m(symbol, parent, tp, sl, entry_ts if delay_min > 0 else entry_ts)
        if r["status"] in {"TP","SL","DATA_GAP"}:
            gp = TP_PCT if r["status"]=="TP" else (-SL_PCT if r["status"]=="SL" else None)
            return {"status":r["status"],"gross_pct":gp,"entry":entry,"entry_ts":entry_ts,"exit_ts":r.get("exit_ts"),"via":r.get("via")}
        # Parent OHLC claimed touch but official 1m did not reproduce.
        if float(lo[i]) <= tp or float(hi[i]) >= sl:
            return {"status":"EXIT_MISMATCH"}

    # Full 15m bars after the entry parent.
    next_bar = parent + BAR
    j = int(np.searchsorted(ts, next_bar))
    expected = next_bar
    while expected + BAR <= deadline:
        if j >= len(ts) or int(ts[j]) != expected:
            return {"status":"DATA_GAP"}
        th = float(lo[j]) <= tp
        sh = float(hi[j]) >= sl
        if th and sh:
            r = inspect_parent_1m(symbol, expected, tp, sl, None)
            if r["status"] == "TP":
                return {"status":"TP","gross_pct":TP_PCT,"entry":entry,"entry_ts":entry_ts,"exit_ts":r["exit_ts"],"via":"15m_collision"}
            if r["status"] == "SL":
                return {"status":"SL","gross_pct":-SL_PCT,"entry":entry,"entry_ts":entry_ts,"exit_ts":r["exit_ts"],"via":"15m_collision"}
            return {"status":r["status"]}
        if sh:
            return {"status":"SL","gross_pct":-SL_PCT,"entry":entry,"entry_ts":entry_ts,"exit_ts":expected+BAR,"via":"15m"}
        if th:
            return {"status":"TP","gross_pct":TP_PCT,"entry":entry,"entry_ts":entry_ts,"exit_ts":expected+BAR,"via":"15m"}
        expected += BAR
        j += 1

    # Partial tail for delayed entries, from last full-bar boundary to deadline.
    if expected < deadline:
        rows = day_rows(symbol, expected)
        if rows is None:
            return {"status":"DATA_GAP"}
        # Tail can cross UTC day; handle in minute steps with direct lookup fallback.
        t = expected
        while t < deadline:
            mr = minute_row(symbol, t)
            if mr is None:
                return {"status":"DATA_GAP"}
            _,o,h,l,c = mr
            th = l <= tp; sh = h >= sl
            if th and sh:
                return {"status":"SL","gross_pct":-SL_PCT,"entry":entry,"entry_ts":entry_ts,"exit_ts":t+MIN,"via":"tail_1m_both_loss"}
            if sh:
                return {"status":"SL","gross_pct":-SL_PCT,"entry":entry,"entry_ts":entry_ts,"exit_ts":t+MIN,"via":"tail_1m"}
            if th:
                return {"status":"TP","gross_pct":TP_PCT,"entry":entry,"entry_ts":entry_ts,"exit_ts":t+MIN,"via":"tail_1m"}
            t += MIN

    # First executable price at deadline.
    if deadline % BAR == 0:
        k = int(np.searchsorted(ts, deadline))
        if k >= len(ts) or int(ts[k]) != deadline:
            return {"status":"DATA_GAP"}
        exit_px = float(op[k])
    else:
        mr = minute_row(symbol, deadline)
        if mr is None:
            return {"status":"DATA_GAP"}
        exit_px = float(mr[1])
    gross = (entry - exit_px) / entry * 100.0
    return {"status":"TIME","gross_pct":gross,"entry":entry,"entry_ts":entry_ts,"exit_ts":deadline,"via":"deadline_open"}

def run_symbol(symbol, path):
    raw = load_raw(path)
    sig = build_signals(raw)
    if sig.empty:
        return [], {"symbol":symbol,"signals":0,"min_ts":int(raw["ts"][0]) if len(raw["ts"]) else None,"max_ts":int(raw["ts"][-1]) if len(raw["ts"]) else None}

    rows=[]
    excluded=Counter()
    for delay in DELAYS:
        busy=-1
        for r in sig.itertuples(index=False):
            actual_et = int(r.signal_ts + delay*MIN)
            if actual_et < busy:
                STATS[f"overlap_delay{delay}"] += 1
                continue
            out = short_outcome(symbol, raw, int(r.signal_ts), delay)
            st = out.get("status")
            if st in {"DATA_GAP","ENTRY_MISMATCH","EXIT_MISMATCH"} or out.get("gross_pct") is None:
                excluded[f"{delay}:{st}"] += 1
                STATS[f"excluded_{st}"] += 1
                continue
            busy = int(out["exit_ts"])
            rows.append({
                "symbol":symbol,
                "signal_ts":int(r.signal_ts),
                "entry_ts":int(out["entry_ts"]),
                "exit_ts":int(out["exit_ts"]),
                "delay_min":delay,
                "status":st,
                "gross_pct":float(out["gross_pct"]),
                "entry":float(out["entry"]),
                "d15":float(r.d15),
                "via":out.get("via"),
            })
    meta={
        "symbol":symbol,
        "signals":int(len(sig)),
        "trade_rows":int(len(rows)),
        "excluded":dict(excluded),
        "min_ts":int(raw["ts"][0]) if len(raw["ts"]) else None,
        "max_ts":int(raw["ts"][-1]) if len(raw["ts"]) else None,
    }
    return rows,meta

def self_test():
    assert abs(((100-90)/100*100)-10.0) < 1e-12
    assert abs(((100-104)/100*100)+4.0) < 1e-12
    c=np.array([False,True,True,False,True])
    prev=np.roll(c,1); prev[0]=False
    assert np.where(c & ~prev)[0].tolist()==[1,4]

    parent=1_800_000_000_000
    tp=90.0; sl=104.0
    original=globals()["day_rows"]

    def make_rows(mods=None, count=15):
        mods=mods or {}
        rows=[]
        for i in range(count):
            o,h,l,cl=100.0,101.0,99.0,100.0
            if i in mods:
                h,l=mods[i]
            rows.append((parent+i*MIN,o,h,l,cl))
        return rows

    try:
        # 1 pre-entry TP ignored.
        globals()["day_rows"]=lambda symbol,ts: make_rows({0:(101.0,89.0)})
        r=inspect_parent_1m("XUSDT",parent,tp,sl,parent+2*MIN)
        assert r["status"]=="NONE", r

        # 2 entry-minute TP-only => conservative LOSS.
        globals()["day_rows"]=lambda symbol,ts: make_rows({2:(101.0,89.0)})
        r=inspect_parent_1m("XUSDT",parent,tp,sl,parent+2*MIN)
        assert r["status"]=="SL" and r["via"]=="entry_minute_conservative_loss", r

        # 3 entry-minute SL-only => LOSS.
        globals()["day_rows"]=lambda symbol,ts: make_rows({2:(105.0,99.0)})
        r=inspect_parent_1m("XUSDT",parent,tp,sl,parent+2*MIN)
        assert r["status"]=="SL", r

        # 4 entry-minute both => LOSS.
        globals()["day_rows"]=lambda symbol,ts: make_rows({2:(105.0,89.0)})
        r=inspect_parent_1m("XUSDT",parent,tp,sl,parent+2*MIN)
        assert r["status"]=="SL", r

        # 5 established-position same-minute both => LOSS.
        globals()["day_rows"]=lambda symbol,ts: make_rows({0:(105.0,89.0)})
        r=inspect_parent_1m("XUSDT",parent,tp,sl,None)
        assert r["status"]=="SL" and r["via"]=="same_1m_both_loss", r

        # 6 entry then later TP => WIN.
        globals()["day_rows"]=lambda symbol,ts: make_rows({3:(101.0,89.0)})
        r=inspect_parent_1m("XUSDT",parent,tp,sl,parent+2*MIN)
        assert r["status"]=="TP", r

        # 7 entry then later SL => LOSS.
        globals()["day_rows"]=lambda symbol,ts: make_rows({3:(105.0,99.0)})
        r=inspect_parent_1m("XUSDT",parent,tp,sl,parent+2*MIN)
        assert r["status"]=="SL", r

        # 8 incomplete minute archive => DATA_GAP.
        globals()["day_rows"]=lambda symbol,ts: make_rows(count=14)
        r=inspect_parent_1m("XUSDT",parent,tp,sl,parent+2*MIN)
        assert r["status"]=="DATA_GAP", r
    finally:
        globals()["day_rows"]=original

    print("S2_SELF_TEST_PASS chronology=8/8")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--raw",required=True)
    ap.add_argument("--out",required=True)
    ap.add_argument("--self-test",action="store_true")
    args=ap.parse_args()
    if args.self_test:
        self_test()
        return
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    files=sorted(glob.glob(os.path.join(args.raw,"**","*USDT.csv.gz"),recursive=True))
    if not files:
        raise RuntimeError("no raw files")
    all_rows=[]; metas=[]
    for n,p in enumerate(files,1):
        sym=sym_from_path(p)
        rows,meta=run_symbol(sym,p)
        all_rows.extend(rows); metas.append(meta)
        print("PROGRESS",n,"/",len(files),sym,"signals",meta["signals"],"trades",len(rows),flush=True)
    pd.DataFrame(all_rows).to_csv(out/"trades.csv.gz",index=False,compression="gzip")
    Path(out/"meta.json").write_text(json.dumps({
        "experiment_id":"S2_CURRENT_RULE_5Y_V1",
        "files":len(files),
        "symbols":metas,
        "stats":dict(STATS),
        "one_minute_cache":str(load_1m_day.cache_info()),
    },indent=2))
    print("S2_SHARD_DONE",len(all_rows),flush=True)

if __name__=="__main__":
    main()
