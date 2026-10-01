"""Causal 1D PSAR SHORT features and OPEN-to-OPEN outcomes.

Age follows the existing study's zero-based definition: age 0 is flip OPEN;
age 3 is flip OPEN plus three calendar days. No TP/SL or intrabar fill model.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

STEP = 900_000
DAY = 86_400_000
BURN = 100
MAX_HOLD = 8
HORIZONS = (4, 5, 6, 7, 8)
CUTOFF = 1_735_689_600_000  # 2025-01-01 UTC


def symbol(path):
    name = Path(path).name
    if not name.endswith(".csv.gz"):
        raise ValueError("expected .csv.gz")
    name = name[:-7].upper()
    if not re.fullmatch(r"[A-Z0-9]+USDT", name):
        raise ValueError("invalid symbol")
    return name


def validate_raw(t, o, h, l, c):
    if not len(t) or not all(len(v) == len(t) for v in (o, h, l, c)):
        raise ValueError("empty or mismatched arrays")
    if np.any(np.diff(t) <= 0):
        raise ValueError("timestamps not strictly increasing")
    if np.any(t % STEP):
        raise ValueError("timestamps not aligned to 15m")
    if not all(np.isfinite(v).all() for v in (o, h, l, c)):
        raise ValueError("nonfinite OHLC")
    if any(np.any(v <= 0) for v in (o, h, l, c)):
        raise ValueError("nonpositive OHLC")
    if np.any(h < np.maximum.reduce([o, l, c])) or np.any(l > np.minimum.reduce([o, h, c])):
        raise ValueError("invalid OHLC geometry")


def load_raw(path):
    # Preserve source ordering. Sorting first would conceal bad source input.
    df = pd.read_csv(path, compression="gzip", usecols=["open_time", "open", "high", "low", "close"])
    ts = df.open_time.to_numpy()
    if not np.isfinite(ts).all() or np.any(ts != np.floor(ts)):
        raise ValueError("noninteger timestamps")
    values = [ts.astype(np.int64)] + [df[x].to_numpy(float) for x in ("open", "high", "low", "close")]
    validate_raw(*values)
    return values


def segments(t):
    q = np.r_[0, np.flatnonzero(np.diff(t) != STEP) + 1, len(t)]
    return list(zip(q[:-1], q[1:]))


def resample_day(t, o, h, l, c):
    buckets = t // DAY
    q = np.r_[0, np.flatnonzero(buckets[1:] != buckets[:-1]) + 1, len(t)]
    start, end = q[:-1], q[1:]
    ok = (end - start == 96) & (t[start] % DAY == 0)
    start, end = start[ok], end[ok]
    if any(np.any(np.diff(t[a:b]) != STEP) for a, b in zip(start, end)):
        raise ValueError("gap inside complete day")
    if not len(start):
        return [np.array([], dtype=np.int64)] + [np.array([], dtype=float) for _ in range(4)]
    return [t[start], o[start], np.array([h[a:b].max() for a, b in zip(start, end)]),
            np.array([l[a:b].min() for a, b in zip(start, end)]), c[end - 1]]


def psar_open(h, l):
    """Legacy-equivalent state: output at i before consuming H/L at i."""
    n = len(h)
    sar, bull = np.full(n, np.nan), np.ones(n, dtype=bool)
    if n < 3:
        return sar, bull
    state, trend, extreme, acceleration = float(l[0]), True, float(h[1]), .02
    sar[1] = state
    for i in range(2, n):
        projected = state + acceleration * (extreme - state)
        projected = min(projected, l[i - 1], l[i - 2]) if trend else max(projected, h[i - 1], h[i - 2])
        sar[i], bull[i] = projected, trend
        if trend and l[i] < projected:
            state, trend, extreme, acceleration = extreme, False, l[i], .02
        elif not trend and h[i] > projected:
            state, trend, extreme, acceleration = extreme, True, h[i], .02
        else:
            state = projected
            if (trend and h[i] > extreme) or (not trend and l[i] < extreme):
                extreme = h[i] if trend else l[i]
                acceleration = min(acceleration + .02, .2)
    return sar, bull


def psar_prefix_reference(h, l, index):
    """Independent prefix replay: consume closed bars, project requested OPEN."""
    if index < 2:
        return (np.nan, True) if index == 0 else (l[0], True)
    state, ep, af, direction = l[0], h[1], .02, 1
    for closed in range(2, index):
        candidate = state + af * (ep - state)
        candidate = min(candidate, *l[closed - 2:closed]) if direction == 1 else max(candidate, *h[closed - 2:closed])
        if direction == 1:
            if l[closed] < candidate:
                state, ep, af, direction = ep, l[closed], .02, -1
            else:
                state = candidate
                if h[closed] > ep:
                    ep, af = h[closed], min(.2, af + .02)
        else:
            if h[closed] > candidate:
                state, ep, af, direction = ep, h[closed], .02, 1
            else:
                state = candidate
                if l[closed] < ep:
                    ep, af = l[closed], min(.2, af + .02)
    candidate = state + af * (ep - state)
    candidate = min(candidate, *l[index - 2:index]) if direction == 1 else max(candidate, *h[index - 2:index])
    return candidate, direction == 1


def atr_open(h, l, c):
    prev = np.r_[np.nan, c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - prev), abs(l - prev)))
    closed = pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()
    return np.r_[np.nan, closed[:-1]]


def event_features(t, o, h, l, c):
    sar, bull = psar_open(h, l)
    atr = atr_open(h, l, c)
    flip = np.r_[False, bull[1:] != bull[:-1]]
    age, frozen, event = np.full(len(t), -1, dtype=int), np.full(len(t), np.nan), np.full(len(t), -1, dtype=np.int64)
    last, d0 = -1, np.nan
    for i in range(BURN, len(t)):
        if not np.isfinite(sar[i]) or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if flip[i]:
            last, d0 = i, abs(c[i - 1] - sar[i]) / atr[i]
        if last >= 0:
            age[i], frozen[i], event[i] = i - last, d0, t[last]
    return sar, bull, atr, flip, age, frozen, event


def short_return(entry, exit_price):
    return 100 * (1 - exit_price / entry)


def first_exit(bull, entry_index, maximum):
    if entry_index + maximum >= len(bull):
        raise ValueError("horizon outside data")
    offsets = np.flatnonzero(bull[entry_index + 1:entry_index + maximum + 1]) + 1
    return int(offsets[0]) if len(offsets) else maximum


def validate_ranges(manifest):
    seen = {}
    for row in manifest:
        for old in seen.get(row["symbol"], []):
            if max(row["first"], old["first"]) <= min(row["last"], old["last"]):
                raise ValueError("overlapping symbol/time inputs")
        seen.setdefault(row["symbol"], []).append(row)


def digest_frame(frame, columns=None):
    if columns is None:
        columns = sorted(frame.columns)
    stable = frame.sort_values(["symbol", "ts"])[list(columns)]
    return hashlib.sha256(stable.to_csv(index=False, float_format="%.15g").encode()).hexdigest()


def collect(data_dir):
    files = sorted(Path(data_dir).rglob("*.csv.gz"))
    if not files:
        raise ValueError("no raw data files")
    rows, manifest, cases = [], [], []
    stats = {"files": len(files), "segments": 0, "usable_segments": 0, "gaps": 0, "full_days": 0}
    for file_index, path in enumerate(files):
        raw = load_raw(path)
        sym = symbol(path)
        t = raw[0]
        manifest.append({"path": str(path.relative_to(data_dir)), "symbol": sym,
                         "first": int(t[0]), "last": int(t[-1]), "bars": len(t),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        cuts = segments(t)
        stats["segments"] += len(cuts)
        stats["gaps"] += len(cuts) - 1
        for seg_index, (a, b) in enumerate(cuts):
            rt, ro, rh, rl, rc = resample_day(*(v[a:b] for v in raw))
            n = len(rt)
            stats["full_days"] += n
            if n and np.any(np.diff(rt) != DAY):
                raise ValueError("noncontiguous daily segment")
            if n <= BURN + MAX_HOLD:
                continue
            stats["usable_segments"] += 1
            sar, bull, atr, flip, age, d0, events = event_features(rt, ro, rh, rl, rc)
            # Audit all raw/daily OHLC identities once while raw source is present.
            for j in range(n):
                q = int(np.searchsorted(t, rt[j]))
                assert np.array_equal(t[q:q + 96], rt[j] + np.arange(96) * STEP)
                assert ro[j] == raw[1][q] and rh[j] == max(raw[2][q:q + 96])
                assert rl[j] == min(raw[3][q:q + 96]) and rc[j] == raw[4][q + 95]
            cases.append((sym, seg_index, rt, ro, rh, rl, rc))
            for i in np.flatnonzero((age == 3) & ~bull):
                if i + MAX_HOLD >= n:
                    continue
                row = {"symbol": sym, "ts": int(rt[i]), "flip_ts": int(events[i]),
                       "segment": seg_index, "entry": float(ro[i]), "d0": float(d0[i]),
                       "sar_open": float(sar[i]), "atr_open": float(atr[i]), "age": int(age[i]),
                       "max_exit_ts": int(rt[i + MAX_HOLD]), "remaining_days": n - 1 - i,
                       "legacy24": bool(b - a >= 96 * (BURN + 24 + 2) and n > BURN + 24 + 1 and i < n - 24),
                       "legacy12": bool(b - a >= 96 * (BURN + 12 + 2) and n > BURN + 12 + 1 and i < n - 12)}
                for k in range(1, MAX_HOLD + 1):
                    row[f"ret{k}"] = float(short_return(ro[i], ro[i + k]))
                    row[f"exit{k}"] = int(rt[i + k])
                    row[f"bull{k}"] = bool(bull[i + k])
                    row[f"mfe{k}"] = float(100 * (1 - rl[i:i + k].min() / ro[i]))
                    row[f"mae{k}"] = float(100 * (rh[i:i + k].max() / ro[i] - 1))
                for k in HORIZONS:
                    row[f"hold{k}"] = first_exit(bull, i, k)
                rows.append(row)
        if file_index % 50 == 0 or file_index + 1 == len(files):
            print(f"RAW {file_index + 1}/{len(files)} rows={len(rows)}", flush=True)
    validate_ranges(manifest)
    frame = pd.DataFrame(rows).sort_values(["symbol", "ts"]).reset_index(drop=True)
    if frame.duplicated(["symbol", "ts"]).any():
        raise ValueError("duplicate signal key")
    return frame, manifest, stats, cases


def train_pool(frame, cutoff=CUTOFF):
    # Purge labels that need price data after the freeze timestamp.
    return frame[(frame.ts < cutoff) & (frame.max_exit_ts <= cutoff)]


def freeze_threshold(frame, cutoff=CUTOFF):
    train = train_pool(frame, cutoff)
    if train.empty:
        raise ValueError("no eligible training data")
    return float(train.d0.quantile(1 / 3))


def select_d0(frame, threshold):
    # LOW exclusion has no upper D0 cap. Values beyond TRAIN maximum remain HIGH.
    return frame[frame.d0 > threshold].copy()


def metrics(values):
    v = np.asarray(values, dtype=float)
    if not len(v):
        return {"n": 0, "pf": None, "win_pct": None, "avg_pct": None}
    gp, gl = v[v > 0].sum(), -v[v < 0].sum()
    return {"n": len(v), "pf": float(gp / gl) if gl > 0 else None,
            "win_pct": float((v > 0).mean() * 100), "avg_pct": float(v.mean()),
            "sum_pct_points": float(v.sum()), "worst_pct": float(v.min()), "best_pct": float(v.max())}


def returns(frame, maximum, reflip=True):
    if not reflip:
        return frame[f"ret{maximum}"].to_numpy(float)
    ret = frame[[f"ret{k}" for k in range(1, MAX_HOLD + 1)]].to_numpy(float)
    return ret[np.arange(len(frame)), frame[f"hold{maximum}"].to_numpy(int) - 1]


def candidates(frame, cost=.4):
    out = {}
    for maximum in HORIZONS:
        hold = frame[f"hold{maximum}"].to_numpy(int)
        out[str(maximum)] = {"FIXED": metrics(returns(frame, maximum, False) - cost),
                             "REFLIP_OR_MAX": metrics(returns(frame, maximum) - cost),
                             "avg_hold_days": float(hold.mean()) if len(hold) else None,
                             "early_exit_pct": float((hold < maximum).mean() * 100) if len(hold) else None}
    return out


def choose_train_maximum(frame):
    scores = candidates(frame)
    # Declared deterministic tie rule: prefer shorter maximum hold.
    best = max(HORIZONS, key=lambda h: (scores[str(h)]["REFLIP_OR_MAX"]["pf"] or -1, -h))
    return best, scores
