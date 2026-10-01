"""Download the predeclared 15m USD-M basket; record missing archives and hashes."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

UNIVERSE = tuple(sorted({
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT",
    "SOLUSDT", "LTCUSDT", "BCHUSDT", "LINKUSDT", "ETCUSDT", "TRXUSDT",
    "XLMUSDT", "EOSUSDT", "DOTUSDT", "UNIUSDT", "AAVEUSDT", "AVAXUSDT",
}))
HEADER = ["open_time", "open", "high", "low", "close", "volume", "quote_volume"]
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"


def months():
    return [f"{y:04d}-{m:02d}" for y in range(2021, 2027) for m in range(1, 13)
            if "2021-08" <= f"{y:04d}-{m:02d}" <= "2026-08"]


def fetch_one(symbol, ym, archives):
    name = f"{symbol}-15m-{ym}.zip"
    path = archives / name
    url = f"{BASE}/{symbol}/15m/{name}"
    raw = None
    error = None
    if path.exists():
        raw = path.read_bytes()
    else:
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    raw = response.read()
                path.write_bytes(raw)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return {"symbol": symbol, "month": ym, "status": "MISSING_404", "url": url}
                error = str(exc)
            except Exception as exc:
                error = str(exc)
            if attempt < 2:
                time.sleep(2 ** attempt)
    if raw is None:
        return {"symbol": symbol, "month": ym, "status": "DOWNLOAD_ERROR", "error": error, "url": url}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError("expected one CSV")
            rows = list(csv.reader(io.TextIOWrapper(archive.open(names[0]), encoding="utf-8")))
        rows = [r for r in rows if len(r) >= 8 and str(r[0]).isdigit()]
        if not rows:
            raise ValueError("empty archive")
        ts = [int(r[0]) for r in rows]
        if any(t % 900000 for t in ts) or any(b <= a for a, b in zip(ts, ts[1:])):
            raise ValueError("noninteger/misaligned/nonincreasing 15m time")
        return {"symbol": symbol, "month": ym, "status": "OK", "url": url,
                "zip_sha256": hashlib.sha256(raw).hexdigest(), "rows": len(rows),
                "first": ts[0], "last": ts[-1], "path": str(path)}
    except Exception as exc:
        return {"symbol": symbol, "month": ym, "status": "DATA_ERROR", "error": str(exc), "url": url}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--archives", type=Path, default=Path("cache/15m"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--symbols", nargs="*", default=list(UNIVERSE))
    args = ap.parse_args()
    if not set(args.symbols).issubset(UNIVERSE):
        raise ValueError("symbol outside frozen universe")
    args.out.mkdir(parents=True, exist_ok=True)
    args.archives.mkdir(parents=True, exist_ok=True)
    jobs = [(s, ym) for s in sorted(args.symbols) for ym in months()]
    manifest = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch_one, s, ym, args.archives) for s, ym in jobs]
        for n, future in enumerate(as_completed(futures), 1):
            manifest.append(future.result())
            if n % 32 == 0 or n == len(jobs):
                print(f"DOWNLOAD {n}/{len(jobs)}", flush=True)
    manifest.sort(key=lambda r: (r["symbol"], r["month"]))
    (args.out / "download_manifest.json").write_text(json.dumps(manifest, indent=2))
    errors = [r for r in manifest if r["status"] not in {"OK", "MISSING_404"}]
    if errors:
        raise RuntimeError(f"{len(errors)} archive errors; manifest saved")
    for symbol in sorted(args.symbols):
        files = [r for r in manifest if r["symbol"] == symbol and r["status"] == "OK"]
        if not files:
            print(f"NO_DATA {symbol}", flush=True)
            continue
        out = args.out / f"{symbol}.csv.gz"
        prev = None
        with gzip.open(out, "wt", newline="", compresslevel=3) as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(HEADER)
            for item in files:
                with zipfile.ZipFile(item["path"]) as archive:
                    name = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
                    reader = csv.reader(io.TextIOWrapper(archive.open(name), encoding="utf-8"))
                    for row in reader:
                        if len(row) < 8 or not str(row[0]).isdigit():
                            continue
                        ts = int(row[0])
                        if prev is not None and ts <= prev:
                            raise ValueError(f"overlap/source ordering {symbol}")
                        prev = ts
                        writer.writerow(row[:6] + [row[7]])
        print(f"ASSEMBLED {symbol} months={len(files)}", flush=True)
    print("DATA_READY", len(args.symbols), flush=True)


if __name__ == "__main__":
    main()
