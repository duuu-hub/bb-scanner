from __future__ import annotations

import csv
import json
import math
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

PAPER = Path("paper_signals.csv")
BASE = "https://api.bitget.com"
PRIORITY = {"L1": 0, "L2": 1, "L3": 2}
HOLD_MIN = {"L1": 720, "L2": 60, "L3": 720}
SCENARIOS = [
    ("IDEAL", 0, 0.0),
    ("NOISE_1M_025", 1, 0.25),
    ("NOISE_2M_050", 2, 0.50),
    ("NOISE_3M_050", 3, 0.50),
]

session = requests.Session()
session.headers.update({"User-Agent": "bb-scanner-signal-replay/1.0"})
_last = 0.0


def api_get(path, params):
    global _last
    gap = 0.07 - (time.monotonic() - _last)
    if gap > 0:
        time.sleep(gap)
    _last = time.monotonic()
    r = session.get(BASE + path, params=params, timeout=15)
    r.raise_for_status()
    p = r.json()
    if str(p.get("code")) != "00000":
        raise RuntimeError(f"Bitget {p.get('code')}: {p.get('msg')}")
    return p.get("data") or []


def ms(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def ceil_minute(x):
    return ((x + 59_999) // 60_000) * 60_000


def boundary_15m(x):
    return (x // 900_000) * 900_000


def load_signals():
    with PAPER.open("r", encoding="utf-8", newline="") as fp:
        rows = list(csv.DictReader(fp))
    rows = [r for r in rows if (r.get("strategy") or "").upper() in PRIORITY]
    for r in rows:
        r["strategy"] = r["strategy"].upper()
        r["_ms"] = ms(r["timestamp_utc"])
        r["_boundary"] = boundary_15m(r["_ms"])
    groups = {}
    for r in rows:
        k = (r["symbol"].upper(), r["_boundary"])
        groups.setdefault(k, []).append(r)
    selected = []
    for (_, _), members in groups.items():
        members.sort(key=lambda r: (PRIORITY[r["strategy"]], r["_ms"]))
        selected.append(members[0])
    selected.sort(key=lambda r: r["_ms"])
    return rows, selected


def candles(symbol, start_ms, end_ms):
    data = api_get(
        "/api/v2/mix/market/candles",
        {
            "symbol": symbol,
            "productType": "usdt-futures",
            "granularity": "1m",
            "startTime": str(start_ms),
            "endTime": str(end_ms),
            "limit": "1000",
        },
    )
    out = []
    for r in data:
        try:
            out.append(
                {
                    "ts": int(r[0]),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                }
            )
        except Exception:
            pass
    out.sort(key=lambda x: x["ts"])
    return out


def outcome(row, cs, name, delay_min, slip_pct, now_ms):
    signal_ms = row["_ms"]
    hold = HOLD_MIN[row["strategy"]]
    deadline = signal_ms + hold * 60_000
    tp = float(row["tp_price"])
    sl = float(row["sl_price"])
    detected = float(row["entry_price"])

    if name == "IDEAL":
        entry_ms = signal_ms
        entry = detected
        # Avoid using the pre-signal portion of the partial 1m candle.
        track_from = ceil_minute(signal_ms)
    else:
        entry_ms = ceil_minute(signal_ms) + delay_min * 60_000
        entry_candle = next((c for c in cs if c["ts"] >= entry_ms), None)
        if entry_candle is None or entry_ms > min(deadline, now_ms):
            return {
                "status": "NO_ENTRY_DATA",
                "entry": None,
                "exit": None,
                "ret": None,
                "entry_ms": entry_ms,
            }
        entry = entry_candle["open"] * (1.0 + slip_pct / 100.0)
        track_from = entry_candle["ts"]

    if entry >= tp:
        return {"status": "MISSED_TP_BEFORE_ENTRY", "entry": entry, "exit": None, "ret": None, "entry_ms": entry_ms}
    if entry <= sl:
        return {"status": "BELOW_SL_AT_ENTRY", "entry": entry, "exit": None, "ret": None, "entry_ms": entry_ms}

    end = min(deadline, now_ms)
    relevant = [c for c in cs if track_from <= c["ts"] < end]
    for c in relevant:
        th = c["high"] >= tp
        sh = c["low"] <= sl
        if th and sh:
            return {"status": "AMBIGUOUS", "entry": entry, "exit": None, "ret": None, "entry_ms": entry_ms, "at": c["ts"]}
        if th:
            return {"status": "TP", "entry": entry, "exit": tp, "ret": (tp / entry - 1.0) * 100.0, "entry_ms": entry_ms, "at": c["ts"]}
        if sh:
            return {"status": "SL", "entry": entry, "exit": sl, "ret": (sl / entry - 1.0) * 100.0, "entry_ms": entry_ms, "at": c["ts"]}

    if now_ms >= deadline:
        last = next((c for c in reversed(cs) if c["ts"] < deadline), None)
        if last is None:
            return {"status": "NO_EXIT_DATA", "entry": entry, "exit": None, "ret": None, "entry_ms": entry_ms}
        ex = last["close"]
        return {"status": "TIME", "entry": entry, "exit": ex, "ret": (ex / entry - 1.0) * 100.0, "entry_ms": entry_ms, "at": deadline}

    last = next((c for c in reversed(cs) if c["ts"] <= now_ms), None)
    if last is None:
        return {"status": "OPEN_NO_MARK", "entry": entry, "exit": None, "ret": None, "entry_ms": entry_ms}
    mark = last["close"]
    return {"status": "OPEN", "entry": entry, "exit": mark, "ret": (mark / entry - 1.0) * 100.0, "entry_ms": entry_ms}


def pf(vals):
    win = sum(x for x in vals if x > 0)
    loss = abs(sum(x for x in vals if x < 0))
    if loss == 0:
        return math.inf if win > 0 else None
    return win / loss


def summarize(results):
    counts = Counter(x["status"] for x in results)
    closed = [x["ret"] for x in results if x["status"] in {"TP", "SL", "TIME"} and x["ret"] is not None]
    open_vals = [x["ret"] for x in results if x["status"] == "OPEN" and x["ret"] is not None]
    equity = 1.0
    for r in closed:
        equity *= 1.0 + (0.30 * r) / 100.0
    return {
        "n": len(results),
        "counts": dict(counts),
        "closed_n": len(closed),
        "win_rate_pct": (sum(x > 0 for x in closed) / len(closed) * 100.0) if closed else None,
        "avg_closed_return_pct": (sum(closed) / len(closed)) if closed else None,
        "pf": pf(closed),
        "sum_closed_return_pct": sum(closed),
        "weighted30_compounded_pct": (equity - 1.0) * 100.0,
        "open_n": len(open_vals),
        "open_mtm_avg_pct": (sum(open_vals) / len(open_vals)) if open_vals else None,
    }


def main():
    raw, selected = load_signals()
    now_ms = int(time.time() * 1000)
    print(f"[META] raw_long_signals={len(raw)} dedup_signals={len(selected)} first={selected[0]['timestamp_utc']} last={selected[-1]['timestamp_utc']}")
    all_results = {name: [] for name, _, _ in SCENARIOS}
    detail = []

    for i, row in enumerate(selected, 1):
        start = max(0, row["_ms"] - 60_000)
        end = min(now_ms, row["_ms"] + HOLD_MIN[row["strategy"]] * 60_000 + 60_000)
        cs = candles(row["symbol"], start, end)
        item = {
            "n": i,
            "time": row["timestamp_utc"],
            "symbol": row["symbol"],
            "strategy": row["strategy"],
            "detected": float(row["entry_price"]),
        }
        for name, d, s in SCENARIOS:
            res = outcome(row, cs, name, d, s, now_ms)
            all_results[name].append(res)
            item[name] = {
                "status": res["status"],
                "entry": res.get("entry"),
                "ret": res.get("ret"),
            }
        detail.append(item)

    for name, _, _ in SCENARIOS:
        print("[SUMMARY] " + name + " " + json.dumps(summarize(all_results[name]), ensure_ascii=False, sort_keys=True))

    print("[DETAIL_BEGIN]")
    for item in detail:
        print(json.dumps(item, ensure_ascii=False, sort_keys=True))
    print("[DETAIL_END]")


if __name__ == "__main__":
    main()
