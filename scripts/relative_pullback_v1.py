"""Frozen BTC-relative trend/pullback signals and canonical independent exits."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd

from scripts import external_breakout_replay as chronology
from scripts.psar_1d_canonical_engine import load_raw, segments
from scripts.relative_pullback_data import UNIVERSE

BAR = 900_000
MINUTE = 60_000
HOUR = 3_600_000
DAY = 86_400_000
START = int(pd.Timestamp("2021-09-01", tz="UTC").timestamp() * 1000)
CUT = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp() * 1000)
END = int(pd.Timestamp("2026-09-01", tz="UTC").timestamp() * 1000)
MAX_HOLD = 48
RELATIVE_MIN = .01
MIN_STOP = .005
MAX_STOP = .03
TP_R = 2.0
SL_ATR_BUFFER = .1
MINUTE_CACHE_DIR = None
_archive_loader = chronology.one_min


def _checked_minutes(symbol, ts):
    """Reuse the repo's official loader, with reproducible on-disk caching."""
    ym = pd.Timestamp(ts, unit="ms", tz="UTC").strftime("%Y-%m")
    path = None if MINUTE_CACHE_DIR is None else MINUTE_CACHE_DIR / f"{symbol}-{ym}.npz"
    key = (symbol, ym)
    if key in chronology.CACHE:
        return chronology.CACHE[key]
    if path is not None and path.exists():
        with np.load(path, allow_pickle=False) as z:
            data = tuple(z[k].copy() for k in ("t", "o", "h", "l"))
    else:
        data = _archive_loader(symbol, ts)
    if len(data) == 2 and data[0] == "data_gap":
        chronology.CACHE[key] = data
        return data
    t, o, h, l = data
    month_start = int(pd.Timestamp(ym + "-01", tz="UTC").timestamp() * 1000)
    month_end = int((pd.Timestamp(ym + "-01", tz="UTC") + pd.offsets.MonthBegin(1)).timestamp() * 1000)
    if (not len(t) or len({len(t), len(o), len(h), len(l)}) != 1
            or np.any(t % MINUTE) or np.any(np.diff(t) != MINUTE)
            or t[0] < month_start or t[-1] >= month_end
            or any(np.any(~np.isfinite(a)) or np.any(a <= 0) for a in (o, h, l))
            or np.any(h < np.maximum(o, l)) or np.any(l > np.minimum(o, h))):
        data = ("data_gap", "invalid official 1m geometry/time")
    elif path is not None and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, t=t, o=o, h=h, l=l)
    chronology.CACHE[key] = data
    return data


def ema(values, n):
    return pd.Series(values).ewm(span=n, adjust=False, min_periods=n).mean().to_numpy()


def features(raw):
    """Compute indicators within contiguous segments, as-of each 15m CLOSE."""
    t, o, h, l, c = raw
    result = {k: np.full(len(t), np.nan) for k in
              ("ema15", "return4h", "hour_close", "hour_ema20", "hour_ema50",
               "hour_ema200", "hour_slope50", "hour_atr")}
    for a, b in segments(t):
        tt, oo, hh, ll, cc = [x[a:b] for x in raw]
        result["ema15"][a:b] = ema(cc, 20)
        if len(cc) > 16:
            result["return4h"][a + 16:b] = cc[16:] / cc[:-16] - 1
        # Only complete UTC-aligned 1H candles are eligible.
        buckets = tt // HOUR
        boundaries = np.r_[0, np.flatnonzero(np.diff(buckets)) + 1, len(tt)]
        st, en = boundaries[:-1], boundaries[1:]
        ok = ((en - st) == 4) & (tt[st] % HOUR == 0)
        st, en = st[ok], en[ok]
        if not len(st):
            continue
        ht = tt[st]
        hc = cc[en - 1]
        hh1 = np.array([hh[x:y].max() for x, y in zip(st, en)])
        ll1 = np.array([ll[x:y].min() for x, y in zip(st, en)])
        previous = np.r_[np.nan, hc[:-1]]
        tr = np.maximum(hh1 - ll1, np.maximum(abs(hh1 - previous), abs(ll1 - previous)))
        atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().to_numpy()
        e20, e50, e200 = [ema(hc, n) for n in (20, 50, 200)]
        slope = e50 - np.r_[np.full(6, np.nan), e50[:-6]] if len(e50) >= 6 else np.full(len(e50), np.nan)
        hi = np.searchsorted(ht + HOUR, tt + BAR, side="right") - 1
        valid = hi >= 0
        for key, values in (("hour_close", hc), ("hour_ema20", e20),
                            ("hour_ema50", e50), ("hour_ema200", e200),
                            ("hour_slope50", slope), ("hour_atr", atr)):
            view = result[key][a:b]
            view[valid] = values[hi[valid]]
    return result


def signals(symbol, raw, f, btc_raw, btc_features, start, end):
    t, o, h, l, c = raw
    bt = btc_raw[0]
    bi = np.searchsorted(bt, t)
    matched = bi < len(bt)
    matched[matched] &= bt[bi[matched]] == t[matched]
    bf = {}
    for key, values in btc_features.items():
        arr = np.full(len(t), np.nan)
        arr[matched] = values[bi[matched]]
        bf[key] = arr
    relative = f["return4h"] - bf["return4h"]
    previous_close = np.r_[np.nan, c[:-1]]
    previous_ema = np.r_[np.nan, f["ema15"][:-1]]
    previous_low = np.r_[np.nan, l[:-1]]
    previous_high = np.r_[np.nan, h[:-1]]
    long = ((bf["hour_close"] > bf["hour_ema200"])
            & (bf["hour_slope50"] > 0)
            & (f["hour_ema20"] > f["hour_ema50"])
            & (f["hour_close"] > f["hour_ema50"])
            & (relative >= RELATIVE_MIN)
            & (previous_low <= previous_ema) & (previous_close <= previous_ema)
            & (c > f["ema15"]) & (c > previous_high) & (c > o))
    short = ((bf["hour_close"] < bf["hour_ema200"])
             & (bf["hour_slope50"] < 0)
             & (f["hour_ema20"] < f["hour_ema50"])
             & (f["hour_close"] < f["hour_ema50"])
             & (relative <= -RELATIVE_MIN)
             & (previous_high >= previous_ema) & (previous_close >= previous_ema)
             & (c < f["ema15"]) & (c < previous_low) & (c < o))
    candidates = []
    for i in np.flatnonzero(long | short):
        j = i + 1
        if i == 0 or j >= len(t) or t[j] != t[i] + BAR or t[i] != t[i - 1] + BAR:
            continue
        if not start <= t[j] < end:
            continue
        side = 1 if long[i] else -1
        atr = float(f["hour_atr"][i])
        entry = float(o[j])
        sl = (min(l[i - 1], l[i]) - SL_ATR_BUFFER * atr if side == 1
              else max(h[i - 1], h[i]) + SL_ATR_BUFFER * atr)
        risk = side * (entry - sl) / entry
        if not np.isfinite(risk) or not MIN_STOP <= risk <= MAX_STOP or sl <= 0:
            continue
        tp = entry + side * TP_R * abs(entry - sl)
        if tp <= 0:
            continue
        candidates.append({"symbol": symbol, "signal_time": int(t[i]),
                           "decision_time": int(t[i] + BAR), "entry_time": int(t[j]),
                           "entry_index": int(j), "side": int(side), "score": float(abs(relative[i])),
                           "relative4h_pct": float(relative[i] * 100), "entry": entry,
                           "sl": float(sl), "tp": float(tp), "risk_pct": float(risk)})
    return candidates


def resolve_minutes(symbol, ts, entry, tp, sl, side, entry_bar=False):
    """Authoritative chronology. No invented ordering inside a 1m candle."""
    data = chronology.w1m(symbol, ts)
    if len(data) == 2 and data[0] == "data_gap":
        return "DATA_GAP", None, None
    mt, mo, mh, ml = data
    if entry_bar and not np.isclose(mo[0], entry, rtol=1e-7, atol=1e-10):
        return "ENTRY_MISMATCH", None, None
    for j in range(15):
        ht = mh[j] >= tp if side == 1 else ml[j] <= tp
        hs = ml[j] <= sl if side == 1 else mh[j] >= sl
        if not (ht or hs):
            continue
        if (entry_bar and j == 0) or hs:
            price = min(sl, mo[j]) if side == 1 else max(sl, mo[j])
            return "SL", int(mt[j] + MINUTE), float(price)
        return "TP", int(mt[j] + MINUTE), float(tp)
    return "EXIT_MISMATCH", None, None


def resolve_trade(candidate, raw, split_end):
    t, o, h, l, c = raw
    start = candidate["entry_index"]
    side, entry, tp, sl = [candidate[k] for k in ("side", "entry", "tp", "sl")]
    deadline = min(candidate["entry_time"] + MAX_HOLD * BAR, split_end)
    for k in range(start, min(start + MAX_HOLD, len(t))):
        if t[k] >= deadline:
            break
        if k > start and t[k] != t[k - 1] + BAR:
            return {"status": "DATA_GAP"}
        ht = h[k] >= tp if side == 1 else l[k] <= tp
        hs = l[k] <= sl if side == 1 else h[k] >= sl
        if not (ht or hs):
            continue
        if k == start or (ht and hs):
            reason, exit_time, price = resolve_minutes(candidate["symbol"], int(t[k]), entry, tp, sl,
                                                      side, entry_bar=(k == start))
            if reason in {"DATA_GAP", "ENTRY_MISMATCH", "EXIT_MISMATCH"}:
                return {"status": reason}
        elif hs:
            reason = "SL"
            price = float(min(sl, o[k]) if side == 1 else max(sl, o[k]))
            exit_time = int(t[k] if side * (o[k] - sl) <= 0 else t[k] + BAR)
        else:
            reason, price = "TP", float(tp)
            exit_time = int(t[k] if side * (o[k] - tp) >= 0 else t[k] + BAR)
        return {"status": "RESOLVED", "exit_time": exit_time, "exit": price, "reason": reason,
                "gross_return": float(side * (price - entry) / entry)}
    z = int(np.searchsorted(t, deadline))
    if z < len(t) and t[z] == deadline:
        price = float(o[z])
        if z > start and np.any(np.diff(t[start:z + 1]) != BAR):
            return {"status": "DATA_GAP"}
    elif z > 0 and t[z - 1] + BAR == deadline:
        price = float(c[z - 1])
        if np.any(np.diff(t[start:z]) != BAR):
            return {"status": "DATA_GAP"}
    else:
        return {"status": "DATA_GAP"}
    return {"status": "RESOLVED", "exit_time": int(deadline), "exit": price,
            "reason": "TIME" if deadline < split_end else "SPLIT_END",
            "gross_return": float(side * (price - entry) / entry)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("relative-pullback-results"))
    ap.add_argument("--minute-cache", type=Path, default=Path("cache/1m"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    global MINUTE_CACHE_DIR
    MINUTE_CACHE_DIR = args.minute_cache
    chronology.one_min = _checked_minutes
    btc_raw = load_raw(args.data / "BTCUSDT.csv.gz")
    btc_features = features(btc_raw)
    rows, counters = [], Counter()
    splits = (("TRAIN", START, CUT), ("HOLDOUT", CUT, END))
    symbols = [s for s in UNIVERSE if s != "BTCUSDT"]
    for n, symbol in enumerate(symbols, 1):
        path = args.data / f"{symbol}.csv.gz"
        if not path.exists():
            counters[f"{symbol}:NO_DATA"] += 1
            continue
        raw = load_raw(path)
        f = features(raw)
        for split, start, end in splits:
            candidates = signals(symbol, raw, f, btc_raw, btc_features, start, end)
            counters[f"{split}:SIGNALS"] += len(candidates)
            for candidate in candidates:
                resolved = resolve_trade(candidate, raw, end)
                counters[f"{split}:{resolved['status']}"] += 1
                if resolved["status"] == "RESOLVED":
                    row = {k: v for k, v in candidate.items() if k != "entry_index"}
                    rows.append({"split": split, **row, **resolved,
                                 "hold_min": (resolved["exit_time"] - candidate["entry_time"]) / MINUTE})
        chronology.CACHE.clear()
        print(f"SCAN {n}/{len(symbols)} {symbol} rows={len(rows)}", flush=True)
    columns = ["split", "symbol", "signal_time", "decision_time", "entry_time", "side", "score",
               "relative4h_pct", "entry", "sl", "tp", "risk_pct", "status", "exit_time", "exit",
               "reason", "gross_return", "hold_min"]
    ledger = pd.DataFrame(rows, columns=columns)
    if len(ledger) and ledger.duplicated(["split", "symbol", "entry_time"]).any():
        raise ValueError("duplicate candidate")
    ledger.to_csv(args.out / "independent_candidates.csv.gz", index=False, compression="gzip")
    plan = Path(__file__).resolve().parents[1] / "research/relative-pullback-v1/PLAN.md"
    meta = {"workflow_sha": os.environ.get("GITHUB_SHA", "local"),
            "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
            "start": START, "cutoff": CUT, "end": END, "universe": list(UNIVERSE),
            "counters": dict(counters), "resolved_independent_n": len(ledger),
            "chronology": "official 1m for entry-bar exits/parent collisions; entry-minute touch LOSS",
            "signal_family": "one frozen configuration; no result-based selection"}
    (args.out / "scan_meta.json").write_text(json.dumps(meta, indent=2))
    print("SCAN_COMPLETE", dict(counters), flush=True)


if __name__ == "__main__":
    main()
