from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path


def parse_timestamp(value: str) -> int:
    text = (value or "").strip()
    if not text:
        raise ValueError("empty timestamp")

    try:
        numeric = float(text)
        if numeric > 10_000_000_000:
            return int(numeric)
        if numeric > 1_000_000_000:
            return int(numeric * 1000)
    except ValueError:
        pass

    normalized = text.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def find_column(fieldnames: list[str], candidates: tuple[str, ...]) -> str:
    lookup = {name.lower().strip(): name for name in fieldnames}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    raise ValueError(f"missing one of columns: {candidates}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize a TradingView TOTAL3/TOTAL3ES chart export"
    )
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--symbol", choices=("TOTAL3", "TOTAL3ES"), required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("market_data_store/tradingview"),
    )
    args = parser.parse_args()

    with args.input_csv.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        time_col = find_column(
            fieldnames,
            ("time", "timestamp", "date", "datetime", "time utc"),
        )
        close_col = find_column(fieldnames, ("close",))
        records: dict[int, float] = {}

        for row in reader:
            try:
                ts = parse_timestamp(row.get(time_col, ""))
                close = float(row.get(close_col, ""))
                if close > 0:
                    records[ts] = close
            except (TypeError, ValueError):
                continue

    if not records:
        raise SystemExit("No usable rows found in TradingView export.")

    args.output_root.mkdir(parents=True, exist_ok=True)
    output = args.output_root / f"{args.symbol}.csv"
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["timestamp_ms", "datetime_utc", "close"])
        for ts in sorted(records):
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
            writer.writerow([ts, dt, records[ts]])

    first = min(records)
    last = max(records)
    print(
        f"{args.symbol}: {len(records)} rows -> {output} | "
        f"{datetime.fromtimestamp(first/1000, tz=timezone.utc).isoformat()} -> "
        f"{datetime.fromtimestamp(last/1000, tz=timezone.utc).isoformat()}"
    )


if __name__ == "__main__":
    main()
