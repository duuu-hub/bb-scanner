#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, os
import pandas as pd

LOOKBACKS = {"1h":4, "2h":8, "4h":16, "8h":32, "24h":96}

def symbol_from_path(p: str) -> str:
    b = os.path.basename(p)
    return b[:-7].upper() if b.endswith(".csv.gz") else os.path.splitext(b)[0].upper()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    frames = []
    files = sorted(glob.glob(os.path.join(a.data, "**", "*USDT.csv.gz"), recursive=True))
    if not files:
        raise RuntimeError("no *USDT.csv.gz input files")

    for i, p in enumerate(files, 1):
        d = pd.read_csv(
            p, compression="gzip",
            usecols=["open_time", "open", "close"],
            dtype={"open_time":"int64", "open":"float64", "close":"float64"},
        ).sort_values("open_time").drop_duplicates("open_time")
        if len(d) < max(LOOKBACKS.values()) + 2:
            continue

        close = d["close"].astype("float64")
        x = pd.DataFrame({
            "ts": d["open_time"].astype("int64"),
            "symbol": symbol_from_path(p),
            "entry_ts": d["open_time"].shift(-1),
            "entry": d["open"].shift(-1),
        })
        for name, bars in LOOKBACKS.items():
            x["r_" + name] = close.pct_change(bars)
        x = x.iloc[:-1].dropna(subset=["entry_ts","entry"]).copy()
        x["entry_ts"] = x["entry_ts"].astype("int64")
        frames.append(x)

        if i % 25 == 0 or i == len(files):
            print(f"prepared {i}/{len(files)} symbols", flush=True)

    if not frames:
        raise RuntimeError("no prepared rows")
    z = pd.concat(frames, ignore_index=True)
    z.to_csv(a.out, index=False, compression="gzip")
    print("PREP_PASS", a.out, "symbols", z.symbol.nunique(), "rows", len(z), flush=True)

if __name__ == "__main__":
    main()
