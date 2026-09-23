from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from signal_io import append_signal_dict

PAPER_LOG = Path("paper_signals.csv")
SIGNALS = Path("signals/pending.jsonl")
REGIME_STATE = Path("market_regime_state.json")
PRODUCT_TYPE = "usdt-futures"
BASE_URL = "https://api.bitget.com"
LONG3_PRIORITY = ["L1", "L2", "L3"]
HOLD_MIN = {"L1": 720, "L2": 60, "L3": 720}


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def floor_15m(ts_ms: int) -> int:
    return (ts_ms // 900_000) * 900_000


def existing_ids() -> set[str]:
    if not SIGNALS.exists():
        return set()
    ids = set()
    for line in SIGNALS.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if row.get("signal_id"):
                ids.add(str(row["signal_id"]))
        except Exception:
            pass
    return ids


def public_get(path: str, params: dict):
    r = requests.get(BASE_URL + path, params=params, timeout=12)
    p = r.json()
    if not r.ok or str(p.get("code")) != "00000":
        raise RuntimeError(f"public GET failed: HTTP={r.status_code} code={p.get('code')} msg={p.get('msg')}")
    return p.get("data") or []


def asset_snapshot(symbol: str) -> dict:
    try:
        tick = public_get("/api/v2/mix/market/ticker", {"symbol": symbol, "productType": PRODUCT_TYPE})
        item = tick[0] if isinstance(tick, list) and tick else {}
        last = float(item.get("lastPr") or 0)
        ret24 = float(item.get("change24h") or 0) * 100.0
        rows = public_get("/api/v2/mix/market/candles", {
            "symbol": symbol, "productType": PRODUCT_TYPE, "granularity": "15m", "limit": "25"
        })
        parsed = []
        for row in rows:
            try:
                parsed.append((int(row[0]), float(row[1]), float(row[4])))
            except Exception:
                pass
        parsed.sort()
        now_ms = int(time.time() * 1000)
        current_boundary = (now_ms // 900_000) * 900_000
        open_by_ts = {ts: op for ts, op, _ in parsed}
        b1 = open_by_ts.get(current_boundary - 4 * 900_000)
        b4 = open_by_ts.get(current_boundary - 16 * 900_000)
        ret1 = (last / b1 - 1) * 100 if last > 0 and b1 else None
        ret4 = (last / b4 - 1) * 100 if last > 0 and b4 else None
        return {"price": last or None, "ret_1h_pct": ret1, "ret_4h_pct": ret4, "ret_24h_pct": ret24}
    except Exception:
        return {"price": None, "ret_1h_pct": None, "ret_4h_pct": None, "ret_24h_pct": None}


def market_snapshot() -> dict:
    btc = asset_snapshot("BTCUSDT")
    eth = asset_snapshot("ETHUSDT")
    snap = {
        "btc_1h_pct": btc["ret_1h_pct"], "btc_4h_pct": btc["ret_4h_pct"], "btc_24h_pct": btc["ret_24h_pct"],
        "btc_volatility_state": None,
        "eth_1h_pct": eth["ret_1h_pct"], "eth_4h_pct": eth["ret_4h_pct"], "eth_24h_pct": eth["ret_24h_pct"],
        "eth_btc_relative_strength_24h_pct": None,
        "total3_4h_pct": None, "total3_24h_pct": None,
        "total3es_4h_pct": None, "total3es_24h_pct": None,
        "alt_positive_4h_pct": None, "alt_median_4h_pct": None,
        "market_regime": None,
    }
    if btc["ret_1h_pct"] is not None:
        a = abs(btc["ret_1h_pct"])
        snap["btc_volatility_state"] = "HIGH" if a >= 3.0 else ("ELEVATED" if a >= 1.5 else "NORMAL")
    if btc["ret_24h_pct"] is not None and eth["ret_24h_pct"] is not None:
        snap["eth_btc_relative_strength_24h_pct"] = eth["ret_24h_pct"] - btc["ret_24h_pct"]
    if REGIME_STATE.exists():
        try:
            r = json.loads(REGIME_STATE.read_text(encoding="utf-8"))
            snap.update({
                "total3_4h_pct": r.get("total3_4h_pct"),
                "total3_24h_pct": r.get("total3_24h_pct"),
                "total3es_4h_pct": r.get("total3es_4h_pct"),
                "total3es_24h_pct": r.get("total3es_24h_pct"),
                "alt_positive_4h_pct": r.get("positive_4h_pct"),
                "alt_median_4h_pct": r.get("median_4h_pct"),
                "market_regime": r.get("regime"),
                "regime_snapshot_time": r.get("timestamp_utc"),
            })
        except Exception:
            pass
    return snap


def load_recent_rows(max_age_sec: int = 900) -> list[dict]:
    if not PAPER_LOG.exists():
        return []
    now = int(time.time() * 1000)
    out = []
    with PAPER_LOG.open("r", encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            try:
                ts = parse_iso_ms(row["timestamp_utc"])
            except Exception:
                continue
            if now - ts <= max_age_sec * 1000:
                row["_created_ms"] = ts
                out.append(row)
    return out


def choose_group(rows: list[dict]) -> dict | None:
    matches = set()
    for row in rows:
        for code in str(row.get("all_matches") or "").split("+"):
            if code:
                matches.add(code.upper())
        if row.get("strategy"):
            matches.add(str(row["strategy"]).upper())
    selected = next((x for x in LONG3_PRIORITY if x in matches), None)
    if not selected:
        return None
    selected_rows = [r for r in rows if str(r.get("strategy", "")).upper() == selected]
    if not selected_rows:
        return None
    base = selected_rows[-1]
    return {
        "base": base,
        "matches": sorted(matches, key=lambda x: (LONG3_PRIORITY.index(x) if x in LONG3_PRIORITY else 99, x)),
        "selected": selected,
    }


def main() -> int:
    rows = load_recent_rows()
    groups: dict[tuple[str, int], list[dict]] = {}
    for row in rows:
        strategy = str(row.get("strategy") or "").upper()
        if strategy not in LONG3_PRIORITY:
            continue
        symbol = str(row.get("symbol") or "").upper()
        boundary = floor_15m(int(row["_created_ms"]))
        groups.setdefault((symbol, boundary), []).append(row)

    seen = existing_ids()
    snap = market_snapshot()
    created = 0
    for (symbol, boundary), group in sorted(groups.items(), key=lambda kv: kv[0][1]):
        chosen = choose_group(group)
        if not chosen:
            continue
        row = chosen["base"]
        strategy = chosen["selected"]
        sid = f"LONG3:{strategy}:{symbol}:{boundary}"
        if sid in seen:
            continue
        signal = {
            "signal_id": sid,
            "portfolio": "LONG3",
            "strategy": strategy,
            "symbol": symbol,
            "side": "LONG",
            "signal_time_ms": boundary,
            "detected_price": float(row["entry_price"]),
            "entry_min": float(row["entry_low"]),
            "entry_max": float(row["entry_high"]),
            "tp": float(row["tp_price"]),
            "sl": float(row["sl_price"]),
            "max_hold_minutes": HOLD_MIN[strategy],
            "scan_started_at": row["timestamp_utc"],
            "signal_created_at": utc_iso(),
            "matched_strategies": chosen["matches"],
            "selected_strategy": strategy,
            "market_snapshot": snap,
        }
        append_signal_dict(SIGNALS, signal)
        seen.add(sid)
        created += 1
        print("[LONG3_SIGNAL] " + json.dumps(signal, ensure_ascii=False, sort_keys=True))
    print(f"[DONE] LONG3 adapter created={created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
