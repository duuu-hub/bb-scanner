from __future__ import annotations

import csv
import json
import math
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://api.bitget.com"
PRODUCT_TYPE = "usdt-futures"
INTERVAL_MS = 15 * 60 * 1000
MAX_WORKERS = int(os.getenv("REGIME_MAX_WORKERS", "28"))
API_STARTS_PER_SEC = float(os.getenv("REGIME_API_STARTS_PER_SEC", "18"))
REQUEST_TIMEOUT_SEC = int(os.getenv("REGIME_REQUEST_TIMEOUT_SEC", "12"))

STATE_PATH = Path("market_regime_state.json")
HISTORY_PATH = Path("market_regime_history.csv")

# BTC/ETH are intentionally excluded because this layer is meant to describe
# the altcoin market. Stablecoin-base perpetuals are also excluded if present.
EXCLUDED_BASES = {
    "BTC",
    "ETH",
    "USDT",
    "USDC",
    "FDUSD",
    "DAI",
    "TUSD",
    "USDE",
    "USDS",
    "USDD",
    "PYUSD",
}

session = requests.Session()
session.headers.update({"User-Agent": "bb-scanner-market-regime/1.0"})
_rate_lock = threading.Lock()
_last_api_start = 0.0

HISTORY_FIELDS = [
    "timestamp_utc",
    "bucket_start_utc",
    "regime",
    "score",
    "bull_votes",
    "bear_votes",
    "universe_count",
    "sample_1h_4h_count",
    "sample_24h_count",
    "positive_1h_pct",
    "positive_4h_pct",
    "positive_24h_pct",
    "median_1h_pct",
    "median_4h_pct",
    "median_24h_pct",
    "q25_4h_pct",
    "q75_4h_pct",
]


def api_get(path: str, params: dict | None = None, retries: int = 4):
    global _last_api_start
    url = BASE_URL + path
    last_error = None

    for attempt in range(retries):
        try:
            with _rate_lock:
                now = time.monotonic()
                min_gap = 1.0 / API_STARTS_PER_SEC
                wait = min_gap - (now - _last_api_start)
                if wait > 0:
                    time.sleep(wait)
                _last_api_start = time.monotonic()

            response = session.get(
                url,
                params=params or {},
                timeout=REQUEST_TIMEOUT_SEC,
            )
            response.raise_for_status()
            payload = response.json()
            if str(payload.get("code")) != "00000":
                raise RuntimeError(
                    f"Bitget error {payload.get('code')}: {payload.get('msg')}"
                )
            return payload.get("data")
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 8))

    raise RuntimeError(f"GET {path} failed: {last_error}")


def contract_universe() -> list[str]:
    rows = api_get(
        "/api/v2/mix/market/contracts",
        {"productType": PRODUCT_TYPE},
    ) or []

    symbols: list[str] = []
    for row in rows:
        if row.get("symbolType") != "perpetual":
            continue
        if row.get("symbolStatus") != "normal":
            continue

        symbol = str(row.get("symbol") or "").upper()
        quote = str(row.get("quoteCoin") or "").upper()
        base = str(row.get("baseCoin") or "").upper()

        if not symbol or quote != "USDT":
            continue
        if base in EXCLUDED_BASES:
            continue
        symbols.append(symbol)

    return sorted(set(symbols))


def bulk_24h_changes() -> dict[str, float]:
    rows = api_get(
        "/api/v2/mix/market/tickers",
        {"productType": PRODUCT_TYPE},
    ) or []

    result: dict[str, float] = {}
    for row in rows:
        try:
            symbol = str(row.get("symbol") or "").upper()
            value = row.get("change24h")
            if not symbol or value in (None, ""):
                continue
            pct = float(value) * 100.0
            if math.isfinite(pct):
                result[symbol] = pct
        except (TypeError, ValueError):
            continue

    return result


def fetch_1h_4h_returns(symbol: str) -> tuple[str, float | None, float | None]:
    """Return completed-candle 1h and 4h returns from Bitget 15m candles."""
    try:
        rows = api_get(
            "/api/v2/mix/market/candles",
            {
                "symbol": symbol,
                "productType": PRODUCT_TYPE,
                "granularity": "15m",
                "limit": "25",
            },
        ) or []

        parsed: list[tuple[int, float]] = []
        now_ms = int(time.time() * 1000)
        for row in rows:
            try:
                ts = int(row[0])
                close = float(row[4])
                if ts + INTERVAL_MS <= now_ms and close > 0 and math.isfinite(close):
                    parsed.append((ts, close))
            except (TypeError, ValueError, IndexError):
                continue

        parsed.sort(key=lambda item: item[0])
        if len(parsed) < 17:
            return symbol, None, None

        latest_ts, latest_close = parsed[-1]
        close_by_ts = {ts: close for ts, close in parsed}

        base_1h = close_by_ts.get(latest_ts - 4 * INTERVAL_MS)
        base_4h = close_by_ts.get(latest_ts - 16 * INTERVAL_MS)

        ret_1h = (
            (latest_close / base_1h - 1.0) * 100.0
            if base_1h and base_1h > 0
            else None
        )
        ret_4h = (
            (latest_close / base_4h - 1.0) * 100.0
            if base_4h and base_4h > 0
            else None
        )
        return symbol, ret_1h, ret_4h
    except Exception as exc:
        print(f"[WARN] {symbol}: {exc}")
        return symbol, None, None


def pct_positive(values: list[float]) -> float | None:
    if not values:
        return None
    return 100.0 * sum(1 for value in values if value > 0) / len(values)


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * q
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return ordered[low]
    weight = index - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def classify_regime(metrics: dict) -> dict:
    """Transparent initial regime rule for data collection and research.

    This is deliberately simple and symmetric. It is NOT wired to order
    placement. Thresholds create a neutral dead-band so one noisy reading
    does not flip the regime every 15 minutes.
    """
    bull_votes = 0
    bear_votes = 0

    breadth_pairs = [
        ("positive_1h_pct", 55.0, 45.0),
        ("positive_4h_pct", 55.0, 45.0),
        ("positive_24h_pct", 55.0, 45.0),
    ]
    for key, bull_threshold, bear_threshold in breadth_pairs:
        value = metrics.get(key)
        if value is None:
            continue
        if value >= bull_threshold:
            bull_votes += 1
        elif value <= bear_threshold:
            bear_votes += 1

    median_keys = ("median_1h_pct", "median_4h_pct", "median_24h_pct")
    for key in median_keys:
        value = metrics.get(key)
        if value is None:
            continue
        if value > 0:
            bull_votes += 1
        elif value < 0:
            bear_votes += 1

    score = bull_votes - bear_votes
    if score >= 3:
        regime = "BULLISH"
    elif score <= -3:
        regime = "BEARISH"
    else:
        regime = "NEUTRAL"

    return {
        "regime": regime,
        "score": score,
        "bull_votes": bull_votes,
        "bear_votes": bear_votes,
    }


def build_snapshot() -> dict:
    symbols = contract_universe()
    changes_24h = bulk_24h_changes()

    ret_1h_by_symbol: dict[str, float] = {}
    ret_4h_by_symbol: dict[str, float] = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(fetch_1h_4h_returns, symbol) for symbol in symbols]
        for future in as_completed(futures):
            symbol, ret_1h, ret_4h = future.result()
            if ret_1h is not None and math.isfinite(ret_1h):
                ret_1h_by_symbol[symbol] = ret_1h
            if ret_4h is not None and math.isfinite(ret_4h):
                ret_4h_by_symbol[symbol] = ret_4h

    common = sorted(set(ret_1h_by_symbol) & set(ret_4h_by_symbol))
    values_1h = [ret_1h_by_symbol[symbol] for symbol in common]
    values_4h = [ret_4h_by_symbol[symbol] for symbol in common]
    values_24h = [
        changes_24h[symbol]
        for symbol in symbols
        if symbol in changes_24h and math.isfinite(changes_24h[symbol])
    ]

    metrics = {
        "universe_count": len(symbols),
        "sample_1h_4h_count": len(common),
        "sample_24h_count": len(values_24h),
        "positive_1h_pct": pct_positive(values_1h),
        "positive_4h_pct": pct_positive(values_4h),
        "positive_24h_pct": pct_positive(values_24h),
        "median_1h_pct": median(values_1h),
        "median_4h_pct": median(values_4h),
        "median_24h_pct": median(values_24h),
        "q25_4h_pct": quantile(values_4h, 0.25),
        "q75_4h_pct": quantile(values_4h, 0.75),
    }
    metrics.update(classify_regime(metrics))

    now = datetime.now(timezone.utc)
    bucket_epoch = int(now.timestamp()) // (15 * 60) * (15 * 60)
    bucket = datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)

    return {
        "timestamp_utc": now.isoformat(),
        "bucket_start_utc": bucket.isoformat(),
        **metrics,
    }


def write_state(snapshot: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def upsert_history(snapshot: dict) -> None:
    rows: list[dict[str, str]] = []
    if HISTORY_PATH.exists():
        with HISTORY_PATH.open("r", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

    row = {
        field: "" if snapshot.get(field) is None else snapshot.get(field)
        for field in HISTORY_FIELDS
    }

    replaced = False
    for index, existing in enumerate(rows):
        if existing.get("bucket_start_utc") == snapshot["bucket_start_utc"]:
            rows[index] = row
            replaced = True
            break
    if not replaced:
        rows.append(row)

    rows.sort(key=lambda item: item.get("bucket_start_utc", ""))

    with HISTORY_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def main() -> None:
    snapshot = build_snapshot()
    write_state(snapshot)
    upsert_history(snapshot)

    print(
        "[REGIME] "
        f"{snapshot['regime']} score={snapshot['score']:+d} "
        f"bull_votes={snapshot['bull_votes']} bear_votes={snapshot['bear_votes']} "
        f"universe={snapshot['universe_count']} sample={snapshot['sample_1h_4h_count']}"
    )
    print(
        "[BREADTH] "
        f"up1h={fmt(snapshot['positive_1h_pct'])} "
        f"up4h={fmt(snapshot['positive_4h_pct'])} "
        f"up24h={fmt(snapshot['positive_24h_pct'])} "
        f"med1h={fmt(snapshot['median_1h_pct'])} "
        f"med4h={fmt(snapshot['median_4h_pct'])} "
        f"med24h={fmt(snapshot['median_24h_pct'])}"
    )


if __name__ == "__main__":
    main()
