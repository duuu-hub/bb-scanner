from __future__ import annotations

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from market_data.contract_filters import (
    RESEARCH_CORE_SYMBOLS,
    deterministic_symbol_sample,
)
from market_data.universe_collector import (
    BitgetClient,
    ensure_universe_snapshot,
    find_internal_gaps,
    is_rwa_contract,
    sha256_file,
    write_manifest_atomic,
    write_partition_atomic,
)

DEFAULT_ROOT = Path("market_data_store/bitget/research_auto100_15m")
INTERVAL_MS = 15 * 60 * 1000


def day_bounds(day: date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    return start_ms, start_ms + 24 * 60 * 60 * 1000 - INTERVAL_MS


def fetch_symbol_range(
    client: BitgetClient,
    symbol: str,
    start_ms: int,
    end_ms: int,
):
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
        raise RuntimeError(f"{symbol}: unrepaired range gaps: {remaining[:3]}")
    return ordered


def main() -> None:
    p = argparse.ArgumentParser(
        description="Efficient fixed-universe historical backfill for research."
    )
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--sample-size", type=int, default=100)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--out-root", default=str(DEFAULT_ROOT))
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    if args.days < 1 or args.sample_size < 1 or args.workers < 1:
        raise SystemExit("days/sample-size/workers must be >= 1")

    client = BitgetClient()
    contracts = [
        x for x in client.active_usdt_perpetuals()
        if not is_rwa_contract(x)
    ]
    by_symbol = {str(x["symbol"]): x for x in contracts}
    sampled = deterministic_symbol_sample(
        by_symbol,
        args.sample_size,
        core_symbols=RESEARCH_CORE_SYMBOLS,
        seed="bb-research-v1",
    )
    selected_contracts = [by_symbol[s] for s in sampled]

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    first_day = yesterday - timedelta(days=args.days - 1)
    start_ms, _ = day_bounds(first_day)
    _, end_ms = day_bounds(yesterday)
    root = Path(args.out_root)
    root.mkdir(parents=True, exist_ok=True)

    universe_id, universe_path = ensure_universe_snapshot(
        root, selected_contracts, "research_auto_sample"
    )
    selection = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": "bb-research-v1",
        "sample_size": len(sampled),
        "source_active_crypto_count": len(contracts),
        "days": args.days,
        "first_day_utc": first_day.isoformat(),
        "last_day_utc": yesterday.isoformat(),
        "symbols": sampled,
        "core_symbols_requested": list(RESEARCH_CORE_SYMBOLS),
        "universe_id": universe_id,
        "universe_path": str(universe_path),
        "survivorship_note": (
            "Universe selected from contracts active at backfill time. "
            "Historical tests must treat this as a current-survivor sample."
        ),
    }
    (root / "selection.json").write_text(
        json.dumps(selection, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(
        f"[BACKFILL] crypto_active={len(contracts)} sample={len(sampled)} "
        f"days={args.days} {first_day}..{yesterday} workers={args.workers}",
        flush=True,
    )
    print("[SAMPLE] " + ",".join(sampled), flush=True)

    by_day: dict[date, dict[str, list]] = defaultdict(dict)
    failures: dict[str, str] = {}

    def job(symbol: str):
        return symbol, fetch_symbol_range(client, symbol, start_ms, end_ms)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(job, symbol): symbol for symbol in sampled}
        done = 0
        for future in as_completed(futures):
            symbol = futures[future]
            done += 1
            try:
                _, candles = future.result()
                buckets: dict[date, list] = defaultdict(list)
                for candle in candles:
                    d = datetime.fromtimestamp(
                        candle.timestamp_ms / 1000, tz=timezone.utc
                    ).date()
                    if first_day <= d <= yesterday:
                        buckets[d].append(candle)
                for d, rows in buckets.items():
                    by_day[d][symbol] = rows
                print(
                    f"[FETCH] {done}/{len(sampled)} {symbol}: {len(candles)} candles",
                    flush=True,
                )
            except Exception as exc:
                failures[symbol] = str(exc)
                print(f"[ERROR] {symbol}: {exc}", flush=True)

    if failures:
        raise RuntimeError(
            f"Historical backfill failed for {len(failures)} symbols: "
            f"{list(failures.items())[:5]}"
        )

    written_days = 0
    total_rows = 0
    for offset in range(args.days):
        day = first_day + timedelta(days=offset)
        data_path = root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.isoformat()}.csv.gz"
        meta_path = root / "manifests" / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.isoformat()}.json"
        if data_path.exists() and meta_path.exists() and not args.force:
            continue

        rows_by_symbol = by_day.get(day, {})
        counts = {s: len(rows_by_symbol.get(s, [])) for s in sampled}
        full = sum(1 for n in counts.values() if n == 96)
        partial = {s: n for s, n in counts.items() if 0 < n < 96}
        empty = [s for s, n in counts.items() if n == 0]

        path = write_partition_atomic(root, day, rows_by_symbol)
        rows = sum(counts.values())
        total_rows += rows
        manifest = {
            "schema_version": 3,
            "day_utc": day.isoformat(),
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": {
                "exchange": "Bitget",
                "market": "usdt-futures",
                "granularity": "15m",
                "endpoint": "/api/v2/mix/market/history-candles",
            },
            "selection": {
                "scope": "research_auto_sample",
                "sample_seed": "bb-research-v1",
                "symbol_count_requested": len(sampled),
                "universe_id": universe_id,
                "selection_path": str(root / "selection.json"),
            },
            "coverage": {
                "total_rows": rows,
                "symbols_with_96_candles": full,
                "partial_symbols": partial,
                "empty_symbol_count": len(empty),
                "empty_symbols": empty,
            },
            "file": {
                "path": str(path),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            },
        }
        write_manifest_atomic(root, day, manifest)
        written_days += 1
        if written_days == 1 or written_days % 30 == 0 or offset == args.days - 1:
            print(
                f"[WRITE] {day} rows={rows} full={full} partial={len(partial)} "
                f"empty={len(empty)} size={path.stat().st_size / 1024 / 1024:.2f}MiB",
                flush=True,
            )

    print(
        f"[DONE] wrote={written_days} days total_new_rows={total_rows} root={root}",
        flush=True,
    )


if __name__ == "__main__":
    main()
