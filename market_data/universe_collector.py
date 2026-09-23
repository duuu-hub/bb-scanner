from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

import requests

BASE_URL = "https://api.bitget.com"
CONTRACTS_PATH = "/api/v2/mix/market/contracts"
HISTORY_PATH = "/api/v2/mix/market/history-candles"
PRODUCT_TYPE = "usdt-futures"
GRANULARITY = "15m"
INTERVAL_MS = 15 * 60 * 1000
PAGE_LIMIT = 200
PAGE_SPAN_MS = (PAGE_LIMIT - 1) * INTERVAL_MS
DEFAULT_DATA_ROOT = Path("market_data_store/bitget/universe_15m")
COLUMNS = [
    "symbol",
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

    def row(self, symbol: str) -> list[str]:
        return [
            symbol,
            str(self.timestamp_ms),
            self.datetime_utc,
            self.open,
            self.high,
            self.low,
            self.close,
            self.base_volume,
            self.quote_volume,
        ]


class BitgetClient:
    """Public Bitget client with one global request-start budget across workers."""

    def __init__(self, timeout: int = 20, min_delay: float = 0.07) -> None:
        self.timeout = timeout
        self.min_delay = min_delay
        self._last_call = 0.0
        self._rate_lock = threading.Lock()
        self._thread_local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": "bb-scanner-universe-data/2.1"})
            self._thread_local.session = session
        return session

    def _pace(self) -> None:
        with self._rate_lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_delay:
                time.sleep(self.min_delay - elapsed)
            self._last_call = time.monotonic()

    def _get(self, path: str, params: dict[str, str], retries: int = 6):
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                self._pace()
                response = self._session().get(
                    BASE_URL + path,
                    params=params,
                    timeout=self.timeout,
                )
                if response.status_code == 429:
                    raise RuntimeError("HTTP 429 rate limited")
                response.raise_for_status()
                body = response.json()
                if str(body.get("code")) != "00000":
                    raise RuntimeError(f"Bitget error {body.get('code')}: {body.get('msg')}")
                return body.get("data") or []
            except Exception as exc:
                last_error = exc
                if attempt == retries - 1:
                    break
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"GET {path} failed after {retries} attempts: {last_error}")

    def active_usdt_perpetuals(self) -> list[dict]:
        rows = self._get(CONTRACTS_PATH, {"productType": PRODUCT_TYPE})
        out = []
        for item in rows:
            if item.get("symbolType") != "perpetual":
                continue
            if item.get("symbolStatus") != "normal":
                continue
            if str(item.get("quoteCoin", "")).upper() != "USDT":
                continue
            symbol = item.get("symbol")
            if symbol:
                out.append(item)
        out.sort(key=lambda x: str(x["symbol"]))
        return out

    def fetch_window(self, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        raw = self._get(
            HISTORY_PATH,
            {
                "symbol": symbol,
                "productType": PRODUCT_TYPE,
                "granularity": GRANULARITY,
                "startTime": str(start_ms),
                "endTime": str(end_ms),
                "limit": str(PAGE_LIMIT),
            },
        )
        candles: list[Candle] = []
        for item in raw:
            if len(item) < 7:
                continue
            candle = Candle(
                timestamp_ms=int(item[0]),
                open=str(item[1]),
                high=str(item[2]),
                low=str(item[3]),
                close=str(item[4]),
                base_volume=str(item[5]),
                quote_volume=str(item[6]),
            )
            validate_candle(candle)
            candles.append(candle)
        candles.sort(key=lambda x: x.timestamp_ms)
        return candles

    def fetch_exact_range(self, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
        if start_ms > end_ms:
            return []
        collected: dict[int, Candle] = {}
        cursor = start_ms
        while cursor <= end_ms:
            logical_end = min(end_ms, cursor + PAGE_SPAN_MS)
            request_end = logical_end + INTERVAL_MS
            for candle in self.fetch_window(symbol, cursor, request_end):
                if start_ms <= candle.timestamp_ms <= end_ms:
                    collected[candle.timestamp_ms] = candle
            cursor = logical_end + INTERVAL_MS
        return [collected[ts] for ts in sorted(collected)]


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


def utc_day_bounds(day: date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    return start_ms, start_ms + 24 * 60 * 60 * 1000 - INTERVAL_MS


def find_internal_gaps(candles: list[Candle]) -> list[tuple[int, int]]:
    if len(candles) < 2:
        return []
    gaps = []
    timestamps = [x.timestamp_ms for x in candles]
    for prev, cur in zip(timestamps, timestamps[1:]):
        if cur - prev == INTERVAL_MS:
            continue
        if cur <= prev or (cur - prev) % INTERVAL_MS != 0:
            raise ValueError(f"bad timestamp sequence {prev} -> {cur}")
        gaps.append((prev + INTERVAL_MS, cur - INTERVAL_MS))
    return gaps


def fetch_day(client: BitgetClient, symbol: str, day: date) -> list[Candle]:
    start_ms, end_ms = utc_day_bounds(day)
    records = {
        c.timestamp_ms: c
        for c in client.fetch_exact_range(symbol, start_ms, end_ms)
    }

    for _ in range(3):
        ordered = [records[ts] for ts in sorted(records)]
        gaps = find_internal_gaps(ordered)
        if not gaps:
            break
        for gap_start, gap_end in gaps:
            for candle in client.fetch_exact_range(symbol, gap_start, gap_end):
                records[candle.timestamp_ms] = candle

    ordered = [records[ts] for ts in sorted(records)]
    remaining = find_internal_gaps(ordered)
    if remaining:
        raise RuntimeError(f"{symbol} {day}: unrepaired internal gaps: {remaining[:3]}")
    return ordered


def partition_path(root: Path, day: date) -> Path:
    return root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.isoformat()}.csv.gz"


def manifest_path(root: Path, day: date) -> Path:
    return root / "manifests" / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.isoformat()}.json"


def write_partition_atomic(
    root: Path,
    day: date,
    rows_by_symbol: dict[str, list[Candle]],
) -> Path:
    path = partition_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    with gzip.open(tmp, "wt", encoding="utf-8", newline="", compresslevel=6) as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(COLUMNS)
        for symbol in sorted(rows_by_symbol):
            for candle in rows_by_symbol[symbol]:
                writer.writerow(candle.row(symbol))

    os.replace(tmp, path)
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest_atomic(root: Path, day: date, payload: dict) -> Path:
    path = manifest_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def is_rwa(contract: dict) -> bool:
    return str(contract.get("isRwa", "")).upper() == "YES"


def contract_meta(contract: dict) -> dict:
    return {
        "symbol": str(contract.get("symbol", "")),
        "baseCoin": contract.get("baseCoin"),
        "quoteCoin": contract.get("quoteCoin"),
        "isRwa": contract.get("isRwa"),
        "launchTime": contract.get("launchTime"),
        "offTime": contract.get("offTime"),
        "symbolStatus": contract.get("symbolStatus"),
        "symbolType": contract.get("symbolType"),
    }


def select_contracts(
    client: BitgetClient,
    explicit_symbols: list[str] | None,
    max_symbols: int,
    scope: str,
) -> list[dict]:
    contracts = client.active_usdt_perpetuals()
    by_symbol = {str(x["symbol"]): x for x in contracts}

    if explicit_symbols:
        missing = [s for s in explicit_symbols if s not in by_symbol]
        if missing:
            raise RuntimeError(f"Requested symbol(s) are not active USDT perpetuals: {missing}")
        selected = [by_symbol[s] for s in explicit_symbols]
    else:
        if scope == "crypto":
            selected = [x for x in contracts if not is_rwa(x)]
        elif scope == "rwa":
            selected = [x for x in contracts if is_rwa(x)]
        else:
            selected = contracts

    if max_symbols > 0:
        selected = selected[:max_symbols]
    return selected


def collect_one_day(
    client: BitgetClient,
    contracts: list[dict],
    day: date,
    root: Path,
    force: bool,
    workers: int,
    scope: str,
) -> dict:
    out_path = partition_path(root, day)
    meta_path = manifest_path(root, day)
    if out_path.exists() and meta_path.exists() and not force:
        print(f"[SKIP] {day}: partition already exists")
        return {"day": day.isoformat(), "skipped": True, "path": str(out_path)}

    rows_by_symbol: dict[str, list[Candle]] = {}
    counts: dict[str, int] = {}
    empty_symbols: list[str] = []
    errors: dict[str, str] = {}

    def job(contract: dict) -> tuple[str, list[Candle]]:
        symbol = str(contract["symbol"])
        return symbol, fetch_day(client, symbol, day)

    total = len(contracts)
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(job, contract): contract for contract in contracts}
        for future in as_completed(futures):
            contract = futures[future]
            symbol = str(contract["symbol"])
            completed += 1
            try:
                _, candles = future.result()
                rows_by_symbol[symbol] = candles
                counts[symbol] = len(candles)
                if not candles:
                    empty_symbols.append(symbol)
            except Exception as exc:
                errors[symbol] = str(exc)
                print(f"[ERROR] {day} {symbol}: {exc}", flush=True)

            if completed == 1 or completed % 25 == 0 or completed == total:
                current_count = counts.get(symbol, 0)
                print(
                    f"[DAY {day}] {completed}/{total} {symbol}: "
                    f"{current_count} candles",
                    flush=True,
                )

    if errors:
        raise RuntimeError(
            f"{day}: collection failed for {len(errors)} symbol(s); "
            f"no partition written. First errors: {list(errors.items())[:5]}"
        )

    data_path = write_partition_atomic(root, day, rows_by_symbol)
    total_rows = sum(counts.values())
    full_day_symbols = sum(1 for n in counts.values() if n == 96)
    partial_symbols = {s: n for s, n in counts.items() if 0 < n < 96}

    manifest = {
        "schema_version": 2,
        "day_utc": day.isoformat(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "exchange": "Bitget",
            "market": PRODUCT_TYPE,
            "granularity": GRANULARITY,
            "endpoint": HISTORY_PATH,
        },
        "selection": {
            "scope": scope,
            "basis": "active normal USDT perpetual contracts at collection run",
            "symbol_count_requested": len(contracts),
            "rwa_count": sum(1 for x in contracts if is_rwa(x)),
            "crypto_count": sum(1 for x in contracts if not is_rwa(x)),
            "contracts": [contract_meta(x) for x in contracts],
        },
        "coverage": {
            "total_rows": total_rows,
            "symbols_with_96_candles": full_day_symbols,
            "partial_symbols": partial_symbols,
            "empty_symbols": empty_symbols,
            "per_symbol_rows": counts,
        },
        "file": {
            "path": str(data_path),
            "sha256": sha256_file(data_path),
            "bytes": data_path.stat().st_size,
        },
        "historical_backfill_note": (
            "For backfilled dates the symbol universe is the set active when the backfill was run, "
            "not a point-in-time historical universe. Use manifests to avoid survivorship-bias mistakes."
        ),
    }
    write_manifest_atomic(root, day, manifest)

    print(
        f"[OK] {day}: scope={scope}, {len(contracts)} symbols, {total_rows} rows, "
        f"{full_day_symbols} full-day symbols, {data_path.stat().st_size / 1024 / 1024:.2f} MiB"
    )
    return manifest


def parse_day(raw: str | None) -> date:
    if raw:
        return date.fromisoformat(raw)
    return (datetime.now(timezone.utc) - timedelta(days=1)).date()


def parse_symbols(raw: str) -> list[str] | None:
    xs = [x.strip().upper() for x in raw.split(",") if x.strip()]
    return xs or None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect immutable daily partitions of Bitget USDT-futures 15m OHLCV."
    )
    parser.add_argument("--mode", choices=("sync", "backfill"), default="sync")
    parser.add_argument(
        "--date",
        default="",
        help="UTC day for sync mode (YYYY-MM-DD). Defaults to the previous completed UTC day.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Backfill this many completed UTC days ending yesterday.",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Optional comma-separated active symbols. Empty uses the selected scope.",
    )
    parser.add_argument(
        "--scope",
        choices=("crypto", "rwa", "all"),
        default="crypto",
        help="Default crypto excludes Bitget contracts tagged isRwa=YES.",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=0,
        help="Optional deterministic cap after alphabetical sorting; intended for smoke tests only.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Concurrent symbol workers; request starts remain globally rate-limited.",
    )
    parser.add_argument(
        "--out-root",
        default=str(DEFAULT_DATA_ROOT),
        help="Output root for daily .csv.gz partitions and manifests.",
    )
    parser.add_argument("--force", action="store_true", help="Rebuild existing daily partitions.")
    args = parser.parse_args()

    if args.days < 1:
        raise SystemExit("--days must be >= 1")
    if args.max_symbols < 0:
        raise SystemExit("--max-symbols must be >= 0")
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")

    client = BitgetClient()
    explicit = parse_symbols(args.symbols)
    contracts = select_contracts(client, explicit, args.max_symbols, args.scope)
    if not contracts:
        raise SystemExit("No symbols selected.")

    all_contracts = client.active_usdt_perpetuals()
    total_rwa = sum(1 for x in all_contracts if is_rwa(x))
    print(
        f"[UNIVERSE] active_all={len(all_contracts)}, "
        f"active_crypto={len(all_contracts) - total_rwa}, active_rwa={total_rwa}, "
        f"selected={len(contracts)}, scope={args.scope}, "
        f"storage={args.out_root}, interval={GRANULARITY}, workers={args.workers}"
    )

    root = Path(args.out_root)
    if args.mode == "sync":
        days: Iterable[date] = [parse_day(args.date or None)]
    else:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        first = yesterday - timedelta(days=args.days - 1)
        days = (first + timedelta(days=i) for i in range(args.days))

    for day in days:
        collect_one_day(
            client,
            contracts,
            day,
            root,
            args.force,
            args.workers,
            args.scope,
        )


if __name__ == "__main__":
    main()
