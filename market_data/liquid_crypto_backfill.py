from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from market_data.universe_collector import BitgetClient, find_internal_gaps

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]
INTERVAL_MS = 15 * 60 * 1000
DEFAULT_ROOT = Path("market_data_store/bitget/liquid_crypto_15m")
COLUMNS = [
    "timestamp_ms", "datetime_utc", "open", "high", "low", "close",
    "base_volume", "quote_volume",
]


def fetch_range(client: BitgetClient, symbol: str, start_ms: int, end_ms: int):
    records = {
        c.timestamp_ms: c
        for c in client.fetch_exact_range(symbol, start_ms, end_ms)
    }
    for _ in range(3):
        ordered = [records[ts] for ts in sorted(records)]
        gaps = find_internal_gaps(ordered)
        if not gaps:
            break
        before = len(records)
        for gap_start, gap_end in gaps:
            for candle in client.fetch_exact_range(symbol, gap_start, gap_end):
                records[candle.timestamp_ms] = candle
        if len(records) == before:
            break
    ordered = [records[ts] for ts in sorted(records)]
    remaining = find_internal_gaps(ordered)
    if remaining:
        raise RuntimeError(f"{symbol}: unrepaired gaps {remaining[:5]}")
    return ordered


def write_symbol(path: Path, candles) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="", compresslevel=6) as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(COLUMNS)
        for c in candles:
            writer.writerow([
                c.timestamp_ms,
                c.datetime_utc,
                c.open, c.high, c.low, c.close,
                c.base_volume, c.quote_volume,
            ])
    os.replace(tmp, path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=730)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--root", default=str(DEFAULT_ROOT))
    args = p.parse_args()

    if args.days < 30:
        raise SystemExit("--days must be >= 30")

    now = datetime.now(timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(minutes=15)
    start = end - timedelta(days=args.days) + timedelta(minutes=15)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    client = BitgetClient()

    print(f"[LIQUID] {len(SYMBOLS)} symbols {start.isoformat()}..{end.isoformat()}", flush=True)

    results = {}
    errors = {}

    def job(symbol):
        return symbol, fetch_range(client, symbol, start_ms, end_ms)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(job, s): s for s in SYMBOLS}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                _, candles = future.result()
                path = root / f"{symbol}.csv.gz"
                write_symbol(path, candles)
                results[symbol] = {
                    "rows": len(candles),
                    "first_timestamp_ms": candles[0].timestamp_ms if candles else None,
                    "last_timestamp_ms": candles[-1].timestamp_ms if candles else None,
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
                print(f"[OK] {symbol}: {len(candles)} rows {path.stat().st_size/1024/1024:.2f} MiB", flush=True)
            except Exception as exc:
                errors[symbol] = str(exc)
                print(f"[ERROR] {symbol}: {exc}", flush=True)

    if errors:
        raise RuntimeError(f"failed symbols: {errors}")

    meta = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Bitget USDT perpetual history-candles",
        "granularity": "15m",
        "requested_days": args.days,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "symbols": SYMBOLS,
        "coverage": results,
    }
    (root / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print("[DONE] liquid crypto backfill complete", flush=True)


if __name__ == "__main__":
    main()
