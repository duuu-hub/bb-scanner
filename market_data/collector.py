from __future__ import annotations

import argparse
import csv
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

import requests

API_URL = "https://api.bitget.com/api/v2/mix/market/history-candles"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
PRODUCT_TYPE = "usdt-futures"
GRANULARITY = "15m"
INTERVAL_MS = 15 * 60 * 1000
PAGE_LIMIT = 200
PAGE_SPAN_MS = (PAGE_LIMIT - 1) * INTERVAL_MS
DATA_ROOT = Path("market_data_store/bitget/15m")
COLUMNS = [
    "timestamp_ms",
    "datetime_utc",
    "open",
    "high",
    "low",
    "close",
    "base_volume",
    "quote_volume",
]


@dataclass(frozen=True)
class Candle:
    timestamp_ms: int
    open: str
    high: str
    low: str
    close: str
    base_volume: str
    quote_volume: str

    @property
    def datetime_utc(self) -> str:
        return datetime.fromtimestamp(self.timestamp_ms / 1000, tz=timezone.utc).isoformat()

    def row(self) -> list[str]:
        return [
            str(self.timestamp_ms),
            self.datetime_utc,
            self.open,
            self.high,
            self.low,
            self.close,
            self.base_volume,
            self.quote_volume,
        ]


class BitgetHistoryClient:
    def __init__(self, timeout: int = 20, min_delay: float = 0.07) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "bb-scanner-market-data/1.0"})
        self.timeout = timeout
        self.min_delay = min_delay
        self._last_call = 0.0

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)

    def fetch_window(self, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        params = {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "granularity": GRANULARITY,
            "startTime": str(start_ms),
            "endTime": str(end_ms),
            "limit": str(PAGE_LIMIT),
        }

        last_error: Exception | None = None
        for attempt in range(6):
            try:
                self._pace()
                response = self.session.get(API_URL, params=params, timeout=self.timeout)
                self._last_call = time.monotonic()
                if response.status_code == 429:
                    raise RuntimeError("HTTP 429 rate limited")
                response.raise_for_status()
                payload = response.json()
                if payload.get("code") != "00000":
                    raise RuntimeError(f"Bitget error {payload.get('code')}: {payload.get('msg')}")
                raw = payload.get("data") or []
                candles: list[Candle] = []
                for item in raw:
                    if len(item) < 7:
                        continue
                    candles.append(
                        Candle(
                            timestamp_ms=int(item[0]),
                            open=str(item[1]),
                            high=str(item[2]),
                            low=str(item[3]),
                            close=str(item[4]),
                            base_volume=str(item[5]),
                            quote_volume=str(item[6]),
                        )
                    )
                candles.sort(key=lambda c: c.timestamp_ms)
                return candles
            except Exception as exc:
                last_error = exc
                if attempt == 5:
                    break
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"Failed to fetch {symbol} {start_ms}-{end_ms}: {last_error}")


def floor_completed_candle_start(now_ms: int | None = None) -> int:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    current_start = (now_ms // INTERVAL_MS) * INTERVAL_MS
    return current_start - INTERVAL_MS


def month_path(symbol: str, timestamp_ms: int) -> Path:
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return DATA_ROOT / symbol / f"{dt.year:04d}-{dt.month:02d}.csv"


def iter_symbol_files(symbol: str) -> list[Path]:
    root = DATA_ROOT / symbol
    if not root.exists():
        return []
    return sorted(root.glob("*.csv"))


def load_symbol(symbol: str) -> dict[int, Candle]:
    records: dict[int, Candle] = {}
    for path in iter_symbol_files(symbol):
        with path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                ts = int(row["timestamp_ms"])
                records[ts] = Candle(
                    timestamp_ms=ts,
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    base_volume=row["base_volume"],
                    quote_volume=row["quote_volume"],
                )
    return records


def write_symbol(symbol: str, records: dict[int, Candle]) -> None:
    grouped: dict[Path, list[Candle]] = {}
    for candle in records.values():
        grouped.setdefault(month_path(symbol, candle.timestamp_ms), []).append(candle)

    root = DATA_ROOT / symbol
    root.mkdir(parents=True, exist_ok=True)

    expected_paths = set(grouped)
    for stale in iter_symbol_files(symbol):
        if stale not in expected_paths:
            stale.unlink()

    for path, candles in grouped.items():
        candles.sort(key=lambda c: c.timestamp_ms)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".csv.tmp")
        with tmp.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(COLUMNS)
            for candle in candles:
                writer.writerow(candle.row())
        os.replace(tmp, path)


def validate_candle(candle: Candle) -> None:
    if candle.timestamp_ms % INTERVAL_MS != 0:
        raise ValueError(f"unaligned timestamp {candle.timestamp_ms}")
    try:
        o = Decimal(candle.open)
        h = Decimal(candle.high)
        l = Decimal(candle.low)
        c = Decimal(candle.close)
        bv = Decimal(candle.base_volume)
        qv = Decimal(candle.quote_volume)
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric candle {candle}") from exc
    if h < max(o, c, l):
        raise ValueError(f"high invariant failed at {candle.timestamp_ms}")
    if l > min(o, c, h):
        raise ValueError(f"low invariant failed at {candle.timestamp_ms}")
    if bv < 0 or qv < 0:
        raise ValueError(f"negative volume at {candle.timestamp_ms}")


def validate_dataset(symbol: str, records: dict[int, Candle]) -> list[tuple[int, int]]:
    if not records:
        return []
    timestamps = sorted(records)
    for ts in timestamps:
        validate_candle(records[ts])
    gaps: list[tuple[int, int]] = []
    for prev, cur in zip(timestamps, timestamps[1:]):
        delta = cur - prev
        if delta == INTERVAL_MS:
            continue
        if delta <= 0 or delta % INTERVAL_MS != 0:
            raise ValueError(f"timestamp ordering/alignment problem for {symbol}: {prev} -> {cur}")
        gaps.append((prev + INTERVAL_MS, cur - INTERVAL_MS))
    return gaps


def fetch_backward_until(
    client: BitgetHistoryClient,
    symbol: str,
    stop_at_or_before: int | None,
    initial_end_ms: int,
    max_empty_windows: int = 3,
) -> list[Candle]:
    collected: dict[int, Candle] = {}
    end_ms = initial_end_ms
    empty_windows = 0

    while True:
        if stop_at_or_before is not None and end_ms <= stop_at_or_before:
            break
        start_ms = max(0, end_ms - PAGE_SPAN_MS)
        batch = client.fetch_window(symbol, start_ms, end_ms)
        if not batch:
            empty_windows += 1
            if empty_windows >= max_empty_windows:
                break
            end_ms = start_ms - INTERVAL_MS
            continue

        empty_windows = 0
        min_ts = min(c.timestamp_ms for c in batch)
        for candle in batch:
            if candle.timestamp_ms > initial_end_ms:
                continue
            if stop_at_or_before is not None and candle.timestamp_ms <= stop_at_or_before:
                continue
            collected[candle.timestamp_ms] = candle

        # Deliberately overlap one boundary candle between pages. Bitget's
        # history endpoint can treat endTime as exclusive, and stepping to
        # min_ts - one interval creates a one-candle hole at every page edge.
        # Overlap + timestamp de-duplication is safer and deterministic.
        next_end = min_ts
        if next_end >= end_ms:
            raise RuntimeError(f"pagination did not move backward for {symbol}")
        end_ms = next_end

    return sorted(collected.values(), key=lambda c: c.timestamp_ms)


def fetch_exact_range(
    client: BitgetHistoryClient,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> list[Candle]:
    if start_ms > end_ms:
        return []
    collected: dict[int, Candle] = {}
    cursor = start_ms
    while cursor <= end_ms:
        # Bitget rejects startTime == endTime. Request one extra interval so
        # even a single missing candle can be queried, then filter the result.
        logical_end = min(end_ms, cursor + PAGE_SPAN_MS)
        request_end = logical_end + INTERVAL_MS
        batch = client.fetch_window(symbol, cursor, request_end)
        for candle in batch:
            if start_ms <= candle.timestamp_ms <= end_ms:
                collected[candle.timestamp_ms] = candle
        cursor = logical_end + INTERVAL_MS
    return sorted(collected.values(), key=lambda c: c.timestamp_ms)


def repair_gaps(
    client: BitgetHistoryClient,
    symbol: str,
    records: dict[int, Candle],
    max_rounds: int = 3,
) -> dict[int, Candle]:
    for round_no in range(1, max_rounds + 1):
        gaps = validate_dataset(symbol, records)
        if not gaps:
            return records
        print(f"{symbol}: repair round {round_no}, {len(gaps)} gap(s)")
        before = len(records)
        for start_ms, end_ms in gaps:
            for candle in fetch_exact_range(client, symbol, start_ms, end_ms):
                records[candle.timestamp_ms] = candle
        if len(records) == before:
            break
    remaining = validate_dataset(symbol, records)
    if remaining:
        preview = ", ".join(f"{a}-{b}" for a, b in remaining[:5])
        raise RuntimeError(f"{symbol}: unrepaired internal gaps remain: {preview}")
    return records


def sync_symbol(client: BitgetHistoryClient, symbol: str, force_full_backfill: bool = False) -> None:
    records = load_symbol(symbol)
    latest_completed = floor_completed_candle_start()

    if force_full_backfill or not records:
        print(f"{symbol}: full historical backfill starting")
        fetched = fetch_backward_until(client, symbol, None, latest_completed)
        if not fetched:
            raise RuntimeError(f"{symbol}: no historical candles returned")
        for candle in fetched:
            records[candle.timestamp_ms] = candle
        print(
            f"{symbol}: backfill fetched {len(fetched)} candles, "
            f"{fetched[0].datetime_utc} -> {fetched[-1].datetime_utc}"
        )
    else:
        latest_stored = max(records)
        if latest_stored < latest_completed:
            fetched = fetch_backward_until(
                client,
                symbol,
                latest_stored,
                latest_completed,
                max_empty_windows=1,
            )
            if not fetched:
                raise RuntimeError(
                    f"{symbol}: expected new candles after {latest_stored}, but Bitget returned none"
                )
            for candle in fetched:
                records[candle.timestamp_ms] = candle
            print(f"{symbol}: incremental fetch added/updated {len(fetched)} candles")
        else:
            print(f"{symbol}: already current through latest completed candle")

    records = repair_gaps(client, symbol, records)
    validate_dataset(symbol, records)
    write_symbol(symbol, records)

    timestamps = sorted(records)
    print(
        f"{symbol}: OK {len(records)} candles, "
        f"{datetime.fromtimestamp(timestamps[0]/1000, tz=timezone.utc).isoformat()} -> "
        f"{datetime.fromtimestamp(timestamps[-1]/1000, tz=timezone.utc).isoformat()}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Maintain long-term Bitget BTC/ETH 15m market candles"
    )
    parser.add_argument(
        "--mode",
        choices=("sync", "backfill"),
        default="sync",
        help="sync updates existing data; backfill rebuilds as far back as Bitget returns data",
    )
    parser.add_argument(
        "--symbol",
        action="append",
        choices=SYMBOLS,
        help="optional symbol filter; may be supplied multiple times",
    )
    args = parser.parse_args()

    symbols: Iterable[str] = args.symbol or SYMBOLS
    client = BitgetHistoryClient()
    for symbol in symbols:
        sync_symbol(client, symbol, force_full_backfill=args.mode == "backfill")


if __name__ == "__main__":
    main()
