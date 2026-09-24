from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from market_data.contract_filters import (
    RESEARCH_CORE_SYMBOLS,
    active_symbols_from_contracts,
    deterministic_symbol_sample,
)

BASE_URL = "https://api.bitget.com"
PRODUCT_TYPE = "usdt-futures"
TARGET_AUTO100 = 100
AUTO100_SEED = "bb-research-v1"
INTERVAL_SEC = 15 * 60
REQUEST_TIMEOUT_SEC = int(os.getenv("DERIVATIVES_REQUEST_TIMEOUT_SEC", "15"))

ROOT = Path("market_data_store/bitget/derivatives")
CORE_ROOT = ROOT / "core_15m"
AGG_ROOT = ROOT / "auto100_aggregate_15m"
UNIVERSE_ROOT = ROOT / "universes"

CORE_FIELDS = [
    "bucket_start_utc",
    "observed_at_utc",
    "exchange_ts_ms",
    "symbol",
    "last_price",
    "mark_price",
    "bid_price",
    "ask_price",
    "spread_bps",
    "open_interest_base",
    "open_interest_notional_usdt",
    "funding_rate",
    "base_volume_24h",
    "quote_volume_24h",
]

AGG_FIELDS = [
    "bucket_start_utc",
    "observed_at_utc",
    "universe_id",
    "universe_count",
    "symbols_with_valid_oi",
    "symbols_with_valid_funding",
    "total_open_interest_notional_usdt",
    "median_open_interest_notional_usdt",
    "median_funding_rate",
    "oi_weighted_funding_rate",
    "positive_funding_pct",
    "negative_funding_pct",
    "median_spread_bps",
    "total_quote_volume_24h",
]


def api_get(path: str, params: dict | None = None, retries: int = 5):
    last_error = None
    for attempt in range(retries):
        try:
            response = requests.get(
                BASE_URL + path,
                params=params or {},
                timeout=REQUEST_TIMEOUT_SEC,
                headers={"User-Agent": "bb-scanner-derivatives-snapshot/1.0"},
            )
            if response.status_code == 429:
                raise RuntimeError("HTTP 429 rate limited")
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


def finite_float(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def bucket_start(now: datetime) -> datetime:
    epoch = int(now.timestamp())
    floored = epoch // INTERVAL_SEC * INTERVAL_SEC
    return datetime.fromtimestamp(floored, tz=timezone.utc)


def month_path(root: Path, bucket: datetime) -> Path:
    return root / f"{bucket.year:04d}-{bucket.month:02d}.csv"


def load_active_crypto_symbols() -> list[str]:
    contracts = api_get(
        "/api/v2/mix/market/contracts",
        {"productType": PRODUCT_TYPE},
    ) or []
    return active_symbols_from_contracts(contracts, include_rwa=False)


def load_tickers() -> dict[str, dict]:
    rows = api_get(
        "/api/v2/mix/market/tickers",
        {"productType": PRODUCT_TYPE},
    ) or []
    return {
        str(row.get("symbol") or "").upper(): row
        for row in rows
        if row.get("symbol")
    }


def build_row(symbol: str, ticker: dict, bucket: datetime, observed: datetime) -> dict:
    last = finite_float(ticker.get("lastPr"))
    mark = finite_float(ticker.get("markPrice"))
    bid = finite_float(ticker.get("bidPr"))
    ask = finite_float(ticker.get("askPr"))
    oi_base = finite_float(ticker.get("holdingAmount"))
    funding = finite_float(ticker.get("fundingRate"))
    base_volume = finite_float(ticker.get("baseVolume"))
    quote_volume = finite_float(ticker.get("quoteVolume"))
    exchange_ts = ticker.get("ts")

    oi_notional = (
        oi_base * last
        if oi_base is not None and last is not None and oi_base >= 0 and last > 0
        else None
    )
    spread_bps = (
        (ask - bid) / ((ask + bid) / 2.0) * 10000.0
        if bid is not None and ask is not None and bid > 0 and ask >= bid
        else None
    )

    return {
        "bucket_start_utc": bucket.isoformat(),
        "observed_at_utc": observed.isoformat(),
        "exchange_ts_ms": "" if exchange_ts in (None, "") else exchange_ts,
        "symbol": symbol,
        "last_price": last,
        "mark_price": mark,
        "bid_price": bid,
        "ask_price": ask,
        "spread_bps": spread_bps,
        "open_interest_base": oi_base,
        "open_interest_notional_usdt": oi_notional,
        "funding_rate": funding,
        "base_volume_24h": base_volume,
        "quote_volume_24h": quote_volume,
    }


def universe_id(symbols: list[str]) -> str:
    joined = "\n".join(symbols).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()[:20]


def ensure_universe(symbols: list[str], observed: datetime) -> str:
    uid = universe_id(symbols)
    path = UNIVERSE_ROOT / f"{uid}.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "universe_id": uid,
                    "basis": "deterministic crypto-only AUTO100 current active universe",
                    "seed": AUTO100_SEED,
                    "target": TARGET_AUTO100,
                    "created_at_utc": observed.isoformat(),
                    "symbols": symbols,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return uid


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def upsert_core(rows: list[dict], bucket: datetime) -> Path:
    path = month_path(CORE_ROOT, bucket)
    existing = read_csv(path)
    bucket_text = bucket.isoformat()
    keep = [row for row in existing if row.get("bucket_start_utc") != bucket_text]
    keep.extend(rows)
    keep.sort(key=lambda row: (row.get("bucket_start_utc", ""), row.get("symbol", "")))
    write_csv(path, CORE_FIELDS, keep)
    return path


def aggregate(rows: list[dict], uid: str, bucket: datetime, observed: datetime) -> dict:
    oi_values = [
        row["open_interest_notional_usdt"]
        for row in rows
        if isinstance(row.get("open_interest_notional_usdt"), (int, float))
    ]
    funding_values = [
        row["funding_rate"]
        for row in rows
        if isinstance(row.get("funding_rate"), (int, float))
    ]
    weighted_pairs = [
        (row["funding_rate"], row["open_interest_notional_usdt"])
        for row in rows
        if isinstance(row.get("funding_rate"), (int, float))
        and isinstance(row.get("open_interest_notional_usdt"), (int, float))
        and row["open_interest_notional_usdt"] > 0
    ]
    spreads = [
        row["spread_bps"]
        for row in rows
        if isinstance(row.get("spread_bps"), (int, float))
    ]
    quote_volumes = [
        row["quote_volume_24h"]
        for row in rows
        if isinstance(row.get("quote_volume_24h"), (int, float))
        and row["quote_volume_24h"] >= 0
    ]

    weight_sum = sum(weight for _, weight in weighted_pairs)
    weighted_funding = (
        sum(rate * weight for rate, weight in weighted_pairs) / weight_sum
        if weight_sum > 0
        else None
    )

    return {
        "bucket_start_utc": bucket.isoformat(),
        "observed_at_utc": observed.isoformat(),
        "universe_id": uid,
        "universe_count": len(rows),
        "symbols_with_valid_oi": len(oi_values),
        "symbols_with_valid_funding": len(funding_values),
        "total_open_interest_notional_usdt": sum(oi_values) if oi_values else None,
        "median_open_interest_notional_usdt": statistics.median(oi_values) if oi_values else None,
        "median_funding_rate": statistics.median(funding_values) if funding_values else None,
        "oi_weighted_funding_rate": weighted_funding,
        "positive_funding_pct": (
            100.0 * sum(1 for value in funding_values if value > 0) / len(funding_values)
            if funding_values else None
        ),
        "negative_funding_pct": (
            100.0 * sum(1 for value in funding_values if value < 0) / len(funding_values)
            if funding_values else None
        ),
        "median_spread_bps": statistics.median(spreads) if spreads else None,
        "total_quote_volume_24h": sum(quote_volumes) if quote_volumes else None,
    }


def upsert_aggregate(row: dict, bucket: datetime) -> Path:
    path = month_path(AGG_ROOT, bucket)
    existing = read_csv(path)
    bucket_text = bucket.isoformat()
    keep = [x for x in existing if x.get("bucket_start_utc") != bucket_text]
    keep.append(row)
    keep.sort(key=lambda x: x.get("bucket_start_utc", ""))
    write_csv(path, AGG_FIELDS, keep)
    return path


def main() -> None:
    observed = datetime.now(timezone.utc)
    bucket = bucket_start(observed)

    active = load_active_crypto_symbols()
    auto100 = deterministic_symbol_sample(
        active,
        TARGET_AUTO100,
        core_symbols=RESEARCH_CORE_SYMBOLS,
        seed=AUTO100_SEED,
    )
    tickers = load_tickers()
    uid = ensure_universe(auto100, observed)

    auto_rows = [
        build_row(symbol, tickers[symbol], bucket, observed)
        for symbol in auto100
        if symbol in tickers
    ]
    if len(auto_rows) < max(10, int(len(auto100) * 0.8)):
        raise RuntimeError(
            f"Ticker coverage too low: {len(auto_rows)}/{len(auto100)} AUTO100 symbols"
        )

    core_set = set(RESEARCH_CORE_SYMBOLS)
    core_rows = [row for row in auto_rows if row["symbol"] in core_set]
    if "BTCUSDT" not in {row["symbol"] for row in core_rows}:
        raise RuntimeError("BTCUSDT missing from derivatives core snapshot")
    if "ETHUSDT" not in {row["symbol"] for row in core_rows}:
        raise RuntimeError("ETHUSDT missing from derivatives core snapshot")

    core_path = upsert_core(core_rows, bucket)
    agg_row = aggregate(auto_rows, uid, bucket, observed)
    agg_path = upsert_aggregate(agg_row, bucket)

    print(
        "[DERIVATIVES] "
        f"bucket={bucket.isoformat()} auto100={len(auto_rows)} core={len(core_rows)} "
        f"oi_valid={agg_row['symbols_with_valid_oi']} "
        f"funding_valid={agg_row['symbols_with_valid_funding']} "
        f"universe={uid}"
    )
    print(f"[DERIVATIVES] core_path={core_path} aggregate_path={agg_path}")


if __name__ == "__main__":
    main()
