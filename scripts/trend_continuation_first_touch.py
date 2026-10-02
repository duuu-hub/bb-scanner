#!/usr/bin/env python3
"""5Y Binance USD-M trend-continuation first-touch study.

Pre-registered discovery design:
- Data: frozen Binance USD-M 15m artifacts from run 36095439671.
- Signal information: completed 15m candle only.
- Entry: next 15m candle OPEN.
- Strength: cross-sectional winner percentile plus absolute trailing return.
- Signal only on a fresh transition into the qualifying state (no repeated signal every 15m).
- TP fixed at +1.0%.
- SL sweep: -1.00%, -1.50%, -2.00%, -2.50%, -3.00%, -4.00%, -5.00%.
- Time limits: 1h, 2h, 4h, 6h.
- Costs: 20bp and 40bp round trip.
- Train: <= 2024-12-31. Holdout: >= 2025-01-01.
- Same-symbol overlap is blocked until the prior simulated trade exits.
- If TP and SL are both reachable in one 15m bar, official Binance 1m archive
  resolves chronology. Same 1m both-touch => LOSS. Missing/misaligned 1m => DATA_GAP.
"""
from __future__ import annotations
import argparse, csv, glob, io, json, math, os, urllib.error, urllib.request, zipfile
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

BAR_MS = 15 * 60 * 1000
MIN_MS = 60 * 1000
DAY_MS = 24 * 60 * 60 * 1000
TP_PCT = 1.0
TAILS = (0.01, 0.03, 0.05, 0.10)
ABS_THRESH = {
    "1h": (2.0, 3.0, 5.0),
    "2h": (3.0, 5.0, 8.0),
    "4h": (5.0, 8.0, 12.0),
    "8h": (8.0, 12.0, 20.0),
    "24h": (12.0, 20.0, 30.0),
}
SLS = (1.00, 1.50, 2.00, 2.50, 3.00, 4.00, 5.00)
TIME_LIMITS = {"1h": 4, "2h": 8, "4h": 16, "6h": 24}
COST_BPS = (20, 40)
MIN_UNIVERSE = 20
BASE_1M = "https://data.binance.vision/data/futures/um/daily/klines"

STATS = Counter()

def sym_from_path(p: str) -> str:
    b = os.path.basename(p)
    return b[:-7].upper() if b.endswith(".csv.gz") else os.path.splitext(b)[0].upper()

def split_name(ts_ms: int) -> str:
    y = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).year
    return "TRAIN" if y <= 2024 else "HOLDOUT"

def safe_pf(vals):
    pos = sum(x for x in vals if x > 0)
    neg = -sum(x for x in vals if x < 0)
    if neg <= 0:
        return None
    return pos / neg

def max_losing_streak(vals):
    best = cur = 0
    for x in vals:
        if x < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best

def normalize_ts(v):
    x = int(v)
    if x > 10**14:
        x //= 1000
    return x

@lru_cache(maxsize=512)
def load_1m_day(symbol: str, day: str):
    STATS["one_min_day_requests"] += 1
    url = f"{BASE_1M}/{symbol}/1m/{symbol}-1m-{day}.zip"
    req = urllib.request.Request(url, headers={"User-Agent":"trend-continuation-research/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            STATS["one_min_404"] += 1
            return None
        STATS["one_min_http_error"] += 1
        return None
    except Exception:
        STATS["one_min_other_error"] += 1
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = [n for n in zf.namelist() if n.endswith(".csv")]
            if not names:
                return None
            rows = []
            with io.TextIOWrapper(zf.open(names[0]), encoding="utf-8") as fh:
                for r in csv.reader(fh):
                    if len(r) < 5 or not str(r[0]).isdigit():
                        continue
                    rows.append((normalize_ts(r[0]), float(r[2]), float(r[3])))
            return rows
    except Exception:
        STATS["one_min_parse_error"] += 1
        return None

def resolve_collision_1m(symbol, bar_ts, tp, sl):
    STATS["collisions_15m"] += 1
    day = datetime.fromtimestamp(bar_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    rows = load_1m_day(symbol, day)
    if rows is None:
        STATS["collision_data_gap"] += 1
        return {"status":"DATA_GAP", "exit_ts":bar_ts}
    seg = [(t,h,l) for (t,h,l) in rows if bar_ts <= t < bar_ts + BAR_MS]
    if len(seg) != 15 or seg[0][0] != bar_ts or any(seg[i][0] - seg[i-1][0] != MIN_MS for i in range(1, len(seg))):
        STATS["collision_data_gap"] += 1
        return {"status":"DATA_GAP", "exit_ts":bar_ts}
    for t, hi, lo in seg:
        th = hi >= tp
        sh = lo <= sl
        if th and sh:
            STATS["same_1m_both_loss"] += 1
            return {"status":"SL", "exit_ts":t + MIN_MS, "via":"1m_same_bar_both"}
        if sh:
            return {"status":"SL", "exit_ts":t + MIN_MS, "via":"1m"}
        if th:
            return {"status":"TP", "exit_ts":t + MIN_MS, "via":"1m"}
    STATS["collision_exit_mismatch"] += 1
    return {"status":"EXIT_MISMATCH", "exit_ts":bar_ts}

def load_raw_symbol(path):
    d = pd.read_csv(
        path, compression="gzip",
        usecols=["open_time","open","high","low","close"],
        dtype={"open_time":"int64","open":"float64","high":"float64","low":"float64","close":"float64"},
    ).sort_values("open_time").drop_duplicates("open_time")
    return {
        "ts": d.open_time.to_numpy(dtype=np.int64),
        "open": d.open.to_numpy(dtype=np.float64),
        "high": d.high.to_numpy(dtype=np.float64),
        "low": d.low.to_numpy(dtype=np.float64),
        "close": d.close.to_numpy(dtype=np.float64),
    }

def event_outcomes(symbol, raw, entry_ts, entry, sl_pct):
    ts = raw["ts"]; op = raw["open"]; hi = raw["high"]; lo = raw["low"]; cl = raw["close"]
    idx = int(np.searchsorted(ts, entry_ts))
    if idx >= len(ts) or int(ts[idx]) != int(entry_ts):
        return {tl: {"status":"ENTRY_MISMATCH"} for tl in TIME_LIMITS}
    if entry <= 0 or abs(op[idx] / entry - 1.0) > 1e-9:
        return {tl: {"status":"ENTRY_MISMATCH"} for tl in TIME_LIMITS}

    tp = entry * (1.0 + TP_PCT / 100.0)
    sl = entry * (1.0 - sl_pct / 100.0)
    max_bars = max(TIME_LIMITS.values())
    first = None

    for k in range(max_bars):
        j = idx + k
        expected = entry_ts + k * BAR_MS
        if j >= len(ts) or int(ts[j]) != int(expected):
            first = {"status":"DATA_GAP", "bar_ts":expected, "exit_ts":expected}
            break
        th = hi[j] >= tp
        sh = lo[j] <= sl
        if not th and not sh:
            continue
        if th and sh:
            r = resolve_collision_1m(symbol, int(ts[j]), tp, sl)
            first = {"status":r["status"], "bar_ts":int(ts[j]), "exit_ts":int(r.get("exit_ts", int(ts[j])+BAR_MS))}
        elif sh:
            first = {"status":"SL", "bar_ts":int(ts[j]), "exit_ts":int(ts[j])+BAR_MS}
        else:
            first = {"status":"TP", "bar_ts":int(ts[j]), "exit_ts":int(ts[j])+BAR_MS}
        break

    out = {}
    for tl, bars in TIME_LIMITS.items():
        deadline = entry_ts + bars * BAR_MS
        if first is not None and first["bar_ts"] < deadline:
            st = first["status"]
            if st == "TP":
                out[tl] = {"status":"TP", "gross_pct":TP_PCT, "exit_ts":first["exit_ts"]}
            elif st == "SL":
                out[tl] = {"status":"SL", "gross_pct":-sl_pct, "exit_ts":first["exit_ts"]}
            else:
                out[tl] = {"status":st, "exit_ts":first["exit_ts"]}
            continue

        j = idx + bars - 1
        expected = entry_ts + (bars - 1) * BAR_MS
        if j >= len(ts) or int(ts[j]) != int(expected):
            out[tl] = {"status":"DATA_GAP", "exit_ts":deadline}
            continue
        gross = (float(cl[j]) / entry - 1.0) * 100.0
        out[tl] = {"status":"TIME", "gross_pct":gross, "exit_ts":deadline}
    return out

def build_signal_events(parts_root):
    part_files = sorted(glob.glob(os.path.join(parts_root, "**", "*.csv.gz"), recursive=True))
    if len(part_files) < 8:
        raise RuntimeError(f"expected >=8 prepared parts, got {len(part_files)}")
    event_frames = []
    meta = {}

    for lb, thresholds in ABS_THRESH.items():
        col = "r_" + lb
        frames = []
        for p in part_files:
            d = pd.read_csv(p, compression="gzip", usecols=["ts","symbol","entry_ts","entry",col])
            frames.append(d)
        z = pd.concat(frames, ignore_index=True).dropna(subset=[col,"entry","entry_ts"])
        z["u_n"] = z.groupby("ts")["symbol"].transform("count")
        z = z[z["u_n"] >= MIN_UNIVERSE].copy()
        z["pct"] = z.groupby("ts")[col].rank(pct=True, method="average")
        z = z.sort_values(["symbol","ts"]).reset_index(drop=True)
        meta[lb] = {"rows":int(len(z)), "symbols":int(z.symbol.nunique()), "min_ts":int(z.ts.min()), "max_ts":int(z.ts.max())}

        for tail in TAILS:
            for thr in thresholds:
                cond = (z["pct"] >= 1.0-tail) & (z[col] >= thr/100.0)
                prev = cond.groupby(z["symbol"], sort=False).shift(1, fill_value=False)
                e = z.loc[cond & ~prev, ["ts","symbol","entry_ts","entry",col,"pct"]].copy()
                e["lookback"] = lb
                e["tail"] = tail
                e["threshold_pct"] = thr
                e["strength_ret_pct"] = e[col] * 100.0
                e = e.drop(columns=[col])
                event_frames.append(e)
                print("SIGNALS", lb, tail, thr, len(e), flush=True)
        del z, frames

    events = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame()
    if events.empty:
        raise RuntimeError("no qualifying signal events")
    events["entry_ts"] = events["entry_ts"].astype("int64")
    events["ts"] = events["ts"].astype("int64")
    events["split"] = events["ts"].map(split_name)
    return events, meta

def precompute_outcomes(events, raw_root):
    raw_paths = {}
    for p in glob.glob(os.path.join(raw_root, "**", "*USDT.csv.gz"), recursive=True):
        raw_paths[sym_from_path(p)] = p
    if not raw_paths:
        raise RuntimeError("no raw 15m files")

    uniq = events[["symbol","entry_ts","entry"]].drop_duplicates().sort_values(["symbol","entry_ts"])
    outcomes = {}
    total = len(uniq); done = 0
    for symbol, g in uniq.groupby("symbol", sort=True):
        p = raw_paths.get(symbol)
        if p is None:
            for row in g.itertuples(index=False):
                for slp in SLS:
                    for tl in TIME_LIMITS:
                        outcomes[(symbol,int(row.entry_ts),slp,tl)] = {"status":"ENTRY_MISMATCH"}
            continue
        raw = load_raw_symbol(p)
        for row in g.itertuples(index=False):
            for slp in SLS:
                rs = event_outcomes(symbol, raw, int(row.entry_ts), float(row.entry), slp)
                for tl, rec in rs.items():
                    outcomes[(symbol,int(row.entry_ts),slp,tl)] = rec
            done += 1
            if done % 1000 == 0 or done == total:
                print(f"OUTCOMES {done}/{total} collisions={STATS['collisions_15m']} 1m_days={STATS['one_min_day_requests']}", flush=True)
    return outcomes

def summarize_cell(e, outcomes, slp, tl, split, cost_bp):
    a = e[e["split"] == split].sort_values("entry_ts")
    busy = {}
    vals = []
    statuses = Counter()
    excluded = Counter()
    overlap_skips = 0
    accepted_ts = []
    for row in a.itertuples(index=False):
        sym = row.symbol
        et = int(row.entry_ts)
        if et < busy.get(sym, -1):
            overlap_skips += 1
            continue
        rec = outcomes.get((sym, et, slp, tl), {"status":"ENTRY_MISMATCH"})
        st = rec.get("status")
        if st in ("DATA_GAP","ENTRY_MISMATCH","EXIT_MISMATCH") or "gross_pct" not in rec:
            excluded[st] += 1
            continue
        busy[sym] = int(rec["exit_ts"])
        gross = float(rec["gross_pct"])
        net = gross - cost_bp / 100.0
        vals.append(net)
        statuses[st] += 1
        accepted_ts.append(et)

    n = len(vals)
    if accepted_ts:
        span_days = max(1.0, (max(accepted_ts)-min(accepted_ts))/DAY_MS + 1.0)
    else:
        span_days = None
    return {
        "split":split,
        "sl_pct":slp,
        "time_limit":tl,
        "cost_bp":cost_bp,
        "n":n,
        "raw_signals":int(len(a)),
        "overlap_skips":int(overlap_skips),
        "excluded":int(sum(excluded.values())),
        "excluded_detail":json.dumps(dict(excluded), sort_keys=True),
        "tp_n":int(statuses["TP"]),
        "sl_n":int(statuses["SL"]),
        "time_n":int(statuses["TIME"]),
        "tp_rate_pct":(statuses["TP"]/n*100.0) if n else None,
        "win_n":int(sum(x>0 for x in vals)),
        "win_rate_net_pct":(sum(x>0 for x in vals)/n*100.0) if n else None,
        "sum_net_pct":float(sum(vals)) if n else 0.0,
        "gross_profit_net_pct":float(sum(x for x in vals if x>0)) if n else 0.0,
        "gross_loss_abs_net_pct":float(-sum(x for x in vals if x<0)) if n else 0.0,
        "avg_net_pct":(sum(vals)/n) if n else None,
        "median_net_pct":float(np.median(vals)) if n else None,
        "pf_net":safe_pf(vals) if n else None,
        "max_losing_streak":max_losing_streak(vals) if n else None,
        "trades_per_day":(n/span_days) if n and span_days else None,
        "diag_daily_ev_pct":((sum(vals)/n)*(n/span_days)) if n and span_days else None,
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--restrict-to-raw-symbols", action="store_true")
    ap.add_argument("--events-in", default="")
    ap.add_argument("--tp-pct", type=float, default=1.0)
    a = ap.parse_args()
    global TP_PCT
    TP_PCT=float(a.tp_pct)
    outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)

    if a.events_in:
        events=pd.read_csv(a.events_in,compression="infer")
        events["ts"]=events["ts"].astype("int64"); events["entry_ts"]=events["entry_ts"].astype("int64")
        data_meta={"source":"prebuilt_events","path":a.events_in}
    else:
        events, data_meta = build_signal_events(a.parts)
    print("EVENT_BASE_ALL", len(events), "unique", len(events[["symbol","entry_ts"]].drop_duplicates()), flush=True)
    if a.restrict_to_raw_symbols:
        raw_syms={sym_from_path(p) for p in glob.glob(os.path.join(a.raw, "**", "*USDT.csv.gz"), recursive=True)}
        events=events[events["symbol"].isin(raw_syms)].copy()
        print("EVENT_BASE_SHARD", len(events), "symbols", events.symbol.nunique(), "unique", len(events[["symbol","entry_ts"]].drop_duplicates()), flush=True)
    if not a.events_in:
        events.to_csv(outdir/"signal_events.csv.gz", index=False, compression="gzip")

    outcomes = precompute_outcomes(events, a.raw)

    rows = []
    group_cols = ["lookback","tail","threshold_pct"]
    for key, e in events.groupby(group_cols, sort=True):
        lb, tail, thr = key
        for slp in SLS:
            for tl in TIME_LIMITS:
                for cost in COST_BPS:
                    for split in ("TRAIN","HOLDOUT"):
                        s = summarize_cell(e, outcomes, slp, tl, split, cost)
                        s.update({"lookback":lb,"tail":tail,"threshold_pct":thr,"tp_pct":TP_PCT})
                        rows.append(s)
    cells = pd.DataFrame(rows)
    cells.to_csv(outdir/"cells.csv", index=False)

    keycols = ["lookback","tail","threshold_pct","sl_pct","time_limit","cost_bp"]
    tr = cells[(cells["split"]=="TRAIN") & (cells["n"]>=100) & cells["avg_net_pct"].notna()].copy()
    tr = tr.sort_values(["avg_net_pct","pf_net","n"], ascending=[False,False,False]).head(30)
    paired = []
    for row in tr.to_dict("records"):
        mask = np.ones(len(cells), dtype=bool)
        for k in keycols:
            mask &= (cells[k] == row[k]).to_numpy()
        h = cells[mask & (cells["split"]=="HOLDOUT").to_numpy()]
        paired.append({"train":row, "holdout":(h.iloc[0].to_dict() if len(h) else None)})

    summary = {
        "design":{
            "dataset_run":36095439671,
            "market":"Binance USD-M USDT perpetual",
            "interval":"15m",
            "entry":"next 15m OPEN after completed signal bar",
            "tp_pct":TP_PCT,
            "tails":TAILS,
            "absolute_thresholds_pct":ABS_THRESH,
            "sl_pct":SLS,
            "time_limits":TIME_LIMITS,
            "cost_bp":COST_BPS,
            "train":"signal year <= 2024",
            "holdout":"signal year >= 2025",
            "fresh_transition_only":True,
            "same_symbol_overlap_blocked":True,
            "collision_rule":"15m both=>official Binance 1m; same 1m both=>LOSS; unusable 1m=>DATA_GAP/excluded",
            "selection_note":"top_train list is selected using TRAIN only; HOLDOUT is paired after selection",
        },
        "data_meta":data_meta,
        "signal_rows":int(len(events)),
        "unique_symbol_entry_events":int(len(events[["symbol","entry_ts"]].drop_duplicates())),
        "integrity_stats":dict(STATS),
        "top_train_paired_holdout":paired,
    }
    (outdir/"summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print("RESULT_JSON")
    print(json.dumps(summary, default=str), flush=True)

if __name__ == "__main__":
    main()
