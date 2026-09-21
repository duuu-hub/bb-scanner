import argparse
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE_URL = "https://api.bitget.com"
PRODUCT_TYPE = "usdt-futures"
BB_PERIOD = 20
BB_STD = 2.0

TF = {
    "1W": ("1W", 7 * 24 * 60),
    "1D": ("1D", 24 * 60),
    "12H": ("12H", 12 * 60),
    "4H": ("4H", 4 * 60),
    "1H": ("1H", 60),
    "30M": ("30m", 30),
    "15M": ("15m", 15),
}
HORIZONS = {
    "15m": 1,
    "1h": 4,
    "4h": 16,
    "12h": 48,
    "24h": 96,
}
NEAR_THRESHOLDS = [1.0, 3.0, 5.0, 7.0, 10.0]

session = requests.Session()
session.headers.update({"User-Agent": "bb-scanner-backtest/1.0"})
_last_call = 0.0


def api_get(path, params, retries=4):
    global _last_call
    url = BASE_URL + path
    last_error = None
    for attempt in range(retries):
        try:
            gap = 1.0 / 18.0
            now = time.monotonic()
            wait = gap - (now - _last_call)
            if wait > 0:
                time.sleep(wait)
            _last_call = time.monotonic()

            r = session.get(url, params=params, timeout=20)
            r.raise_for_status()
            body = r.json()
            if str(body.get("code")) != "00000":
                raise RuntimeError(f"Bitget {body.get('code')}: {body.get('msg')}")
            return body.get("data") or []
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"GET {path} failed: {last_error}")


def _fetch_page(symbol, granularity, start_ms, end_ms, limit):
    return api_get(
        "/api/v2/mix/market/candles",
        {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "granularity": granularity,
            "startTime": str(int(start_ms)),
            "endTime": str(int(end_ms)),
            "limit": str(limit),
        },
    )


def fetch_range(symbol, granularity, start_ms, end_ms):
    """Fetch market-price candles in <=89-day windows and page backward.

    The live scanner also uses /api/v2/mix/market/candles, so this keeps the
    backtest price source consistent with live scanning.
    """
    day_ms = 24 * 60 * 60 * 1000
    chunk_ms = 89 * day_ms
    merged = {}

    chunk_start = start_ms
    while chunk_start < end_ms:
        chunk_end = min(end_ms, chunk_start + chunk_ms)
        cursor_end = chunk_end
        page_guard = 0

        while cursor_end > chunk_start:
            page_guard += 1
            if page_guard > 200:
                raise RuntimeError(f"pagination guard hit: {symbol} {granularity}")

            try:
                rows = _fetch_page(symbol, granularity, chunk_start, cursor_end, 1000)
            except Exception:
                rows = _fetch_page(symbol, granularity, chunk_start, cursor_end, 200)

            if not rows:
                break

            timestamps = []
            for row in rows:
                try:
                    ts = int(row[0])
                    if start_ms <= ts <= end_ms:
                        merged[ts] = row
                    timestamps.append(ts)
                except (TypeError, ValueError, IndexError):
                    pass

            if not timestamps:
                break

            oldest = min(timestamps)
            if oldest <= chunk_start:
                break
            if oldest >= cursor_end:
                break
            cursor_end = oldest - 1

            # Small pages normally mean the range was exhausted.
            if len(rows) < 200:
                break

        chunk_start = chunk_end + 1

    rows = [merged[k] for k in sorted(merged)]
    if not rows:
        raise RuntimeError(f"no candles: {symbol} {granularity}")
    return rows


def rows_to_df(rows, duration_min):
    out = []
    for row in rows:
        try:
            ts = int(row[0])
            o, h, l, c = map(float, row[1:5])
            if all(math.isfinite(x) and x > 0 for x in (o, h, l, c)):
                out.append((ts, o, h, l, c))
        except (TypeError, ValueError, IndexError):
            pass

    df = pd.DataFrame(out, columns=["ts", "open", "high", "low", "close"])
    if df.empty:
        return df
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df["close_ts"] = df["ts"] + duration_min * 60_000
    return df


def bb_state_at(live_price, eval_ts, tf_df):
    # Candle is completed only when its close time is <= evaluation time.
    close_times = tf_df["close_ts"].to_numpy(dtype=np.int64)
    closes = tf_df["close"].to_numpy(dtype=float)
    idx = np.searchsorted(close_times, eval_ts, side="right")
    if idx < BB_PERIOD - 1:
        return None

    prev19 = closes[idx - (BB_PERIOD - 1):idx]
    window = np.append(prev19, live_price)
    basis = float(window.mean())
    std = float(window.std(ddof=0))
    upper = basis + BB_STD * std
    if upper <= 0:
        return None

    dist = (live_price / upper - 1.0) * 100.0
    return {
        "upper": upper,
        "dist": dist,
        "above": live_price > upper,
    }


def fetch_symbol_data(symbol, test_start_ms, test_end_ms):
    data = {}
    # Add exactly enough warm-up for 19 completed candles, plus margin.
    for tf_name, (granularity, duration_min) in TF.items():
        warmup_ms = (BB_PERIOD + 3) * duration_min * 60_000
        start_ms = test_start_ms - warmup_ms
        # 15m needs future bars for 24h outcome measurement.
        extra_ms = 24 * 60 * 60 * 1000 if tf_name == "15M" else 0
        rows = fetch_range(symbol, granularity, start_ms, test_end_ms + extra_ms)
        df = rows_to_df(rows, duration_min)
        data[tf_name] = df
        print(f"[DATA] {symbol} {tf_name}: {len(df)} candles")
    return data


def build_snapshots(symbol, data, test_start_ms, test_end_ms):
    base = data["15M"]
    # Signal is evaluated at the OPEN of each new 15m candle. This is a
    # no-lookahead approximation of the live scan at candle boundary +1m.
    eval_df = base[(base["ts"] >= test_start_ms) & (base["ts"] <= test_end_ms)].copy()
    records = []

    for row in eval_df.itertuples(index=False):
        eval_ts = int(row.ts)
        live = float(row.open)
        rec = {
            "symbol": symbol,
            "ts": eval_ts,
            "time_utc": pd.to_datetime(eval_ts, unit="ms", utc=True),
            "price": live,
        }

        exact = 0
        valid = True
        for tf_name in TF:
            state = bb_state_at(live, eval_ts, data[tf_name])
            if state is None:
                valid = False
                break
            rec[f"{tf_name}_dist"] = state["dist"]
            rec[f"{tf_name}_above"] = int(state["above"])
            exact += int(state["above"])

        if not valid:
            continue

        rec["exact_count"] = exact
        for threshold in NEAR_THRESHOLDS:
            near = 0
            for tf_name in TF:
                d = rec[f"{tf_name}_dist"]
                if d >= -threshold:
                    near += 1
            rec[f"within_{int(threshold)}pct_count"] = near

        # Future path measured from signal price. Positive max_up is adverse
        # for a short; positive max_drop is favorable for a short.
        base_idx = base.index[base["ts"] == eval_ts]
        if len(base_idx) == 0:
            continue
        i = int(base_idx[0])

        for label, bars in HORIZONS.items():
            future = base.iloc[i:i + bars]
            if len(future) < bars:
                rec[f"{label}_max_up_pct"] = np.nan
                rec[f"{label}_max_drop_pct"] = np.nan
                rec[f"{label}_close_ret_pct"] = np.nan
                continue
            max_up = (float(future["high"].max()) / live - 1.0) * 100.0
            max_drop = (1.0 - float(future["low"].min()) / live) * 100.0
            close_ret = (float(future.iloc[-1]["close"]) / live - 1.0) * 100.0
            rec[f"{label}_max_up_pct"] = max_up
            rec[f"{label}_max_drop_pct"] = max_drop
            rec[f"{label}_close_ret_pct"] = close_ret

        records.append(rec)

    return pd.DataFrame(records)


def first_touch_events(snapshots):
    """Create distinct 4/7, 5/7, 6/7, 7/7 first-touch events.

    A new event occurs when exact_count crosses a level from below. It re-arms
    after the count falls below that level, avoiding 15-minute duplicate trades.
    """
    if snapshots.empty:
        return snapshots.copy()

    all_events = []
    for symbol, g in snapshots.groupby("symbol", sort=False):
        g = g.sort_values("ts").copy()
        prev = g["exact_count"].shift(1).fillna(0)
        for level in (4, 5, 6, 7):
            mask = (g["exact_count"] >= level) & (prev < level)
            e = g.loc[mask].copy()
            e["signal_level"] = level
            all_events.append(e)

    if not all_events:
        return pd.DataFrame()
    return pd.concat(all_events, ignore_index=True).sort_values(["ts", "symbol", "signal_level"])


def add_split(events):
    if events.empty:
        return events
    events = events.copy()
    cutoff = events["ts"].quantile(0.70)
    events["split"] = np.where(events["ts"] <= cutoff, "train70", "test30")
    return events


def summarize(events):
    if events.empty:
        return pd.DataFrame()

    rows = []
    for (split, level), g in events.groupby(["split", "signal_level"]):
        row = {
            "split": split,
            "level": int(level),
            "n": len(g),
        }
        for horizon in ("1h", "4h", "12h", "24h"):
            up = g[f"{horizon}_max_up_pct"].dropna()
            down = g[f"{horizon}_max_drop_pct"].dropna()
            close = g[f"{horizon}_close_ret_pct"].dropna()
            if len(up):
                row[f"{horizon}_med_up"] = up.median()
                row[f"{horizon}_p90_up"] = up.quantile(0.90)
                row[f"{horizon}_pump10_rate"] = (up >= 10).mean() * 100
            if len(down):
                row[f"{horizon}_med_drop"] = down.median()
                row[f"{horizon}_drop5_rate"] = (down >= 5).mean() * 100
                row[f"{horizon}_drop10_rate"] = (down >= 10).mean() * 100
            if len(close):
                row[f"{horizon}_med_close_ret"] = close.median()
        rows.append(row)

    return pd.DataFrame(rows).sort_values(["split", "level"])


def parse_args():
    p = argparse.ArgumentParser(description="Bitget multi-timeframe BB research backtest")
    p.add_argument(
        "--symbols",
        default="龙虾USDT,NILUSDT,INITUSDT,METISUSDT",
        help="comma-separated Bitget symbols",
    )
    p.add_argument("--days", type=int, default=60, help="test-window days")
    p.add_argument("--outdir", default="backtest_results")
    return p.parse_args()


def main():
    args = parse_args()
    symbols = [x.strip() for x in args.symbols.split(",") if x.strip()]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    now_ms = int(time.time() * 1000)
    # Leave the most recent 24h out so every event has a complete 24h outcome.
    test_end_ms = now_ms - 24 * 60 * 60 * 1000
    test_start_ms = test_end_ms - args.days * 24 * 60 * 60 * 1000

    all_snapshots = []
    for symbol in symbols:
        print(f"\n=== {symbol}: {args.days}d research window ===")
        try:
            data = fetch_symbol_data(symbol, test_start_ms, test_end_ms)
            snapshots = build_snapshots(symbol, data, test_start_ms, test_end_ms)
            print(f"[SNAP] {symbol}: {len(snapshots)} valid 15m snapshots")
            all_snapshots.append(snapshots)
        except Exception as exc:
            print(f"[ERROR] {symbol}: {exc}")

    if not all_snapshots:
        raise SystemExit("No backtest data produced.")

    snapshots = pd.concat(all_snapshots, ignore_index=True)
    events = first_touch_events(snapshots)
    events = add_split(events)
    summary = summarize(events)

    snapshots.to_csv(outdir / "snapshots.csv", index=False)
    events.to_csv(outdir / "events.csv", index=False)
    summary.to_csv(outdir / "summary.csv", index=False)

    print("\n=== FIRST-TOUCH EVENT SUMMARY ===")
    if summary.empty:
        print("No 4/7+ events in selected window.")
    else:
        with pd.option_context("display.max_columns", 100, "display.width", 220):
            print(summary.to_string(index=False))

    print(f"\nSaved: {outdir}/snapshots.csv")
    print(f"Saved: {outdir}/events.csv")
    print(f"Saved: {outdir}/summary.csv")


if __name__ == "__main__":
    main()
