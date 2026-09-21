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
session.headers.update({"User-Agent": "bb-scanner-backtest/2.0"})
_last_call = 0.0


def api_get(path, params, retries=4):
    """Bitget public GET with a conservative global request pace."""
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
                raise RuntimeError(
                    f"Bitget {body.get('code')}: {body.get('msg')} "
                    f"path={path} params={params}"
                )
            return body.get("data") or []
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(0.8 * (attempt + 1))

    raise RuntimeError(f"GET {path} failed: {last_error}")


def fetch_history_page(symbol, granularity, start_ms, end_ms):
    """Fetch one page from Bitget's *historical* market-candle endpoint.

    Important: /market/candles is a recent-candle endpoint and can silently
    truncate long tests. /market/history-candles is the correct endpoint for
    paged historical research and returns up to 200 rows per call.
    """
    return api_get(
        "/api/v2/mix/market/history-candles",
        {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "granularity": granularity,
            "startTime": str(int(start_ms)),
            "endTime": str(int(end_ms)),
            "limit": "200",
        },
    )


def fetch_range(symbol, granularity, duration_min, start_ms, end_ms):
    """Fetch the complete requested range, deduplicated and sorted.

    Bitget limits one requested time span to <=90 days. We use 89-day chunks,
    then page backward inside each chunk until its beginning is reached.
    """
    if end_ms <= start_ms:
        return []

    day_ms = 24 * 60 * 60 * 1000
    duration_ms = duration_min * 60_000
    chunk_ms = 89 * day_ms
    merged = {}

    chunk_start = start_ms
    while chunk_start <= end_ms:
        chunk_end = min(end_ms, chunk_start + chunk_ms)
        cursor_end = chunk_end
        previous_oldest = None

        for _ in range(500):
            rows = fetch_history_page(
                symbol,
                granularity,
                chunk_start,
                cursor_end,
            )
            if not rows:
                break

            timestamps = []
            for row in rows:
                try:
                    ts = int(row[0])
                    timestamps.append(ts)
                    if start_ms - duration_ms <= ts <= end_ms + duration_ms:
                        merged[ts] = row
                except (TypeError, ValueError, IndexError):
                    continue

            if not timestamps:
                break

            oldest = min(timestamps)

            # We have reached the start of this chunk.
            if oldest <= chunk_start + duration_ms:
                break

            # A short page means there is no older page in this requested span.
            if len(rows) < 200:
                break

            # Defensive progress guard in case server-side boundary rounding
            # returns the same oldest candle again.
            if previous_oldest is not None and oldest >= previous_oldest:
                cursor_end = oldest - duration_ms
            else:
                cursor_end = oldest - 1
            previous_oldest = oldest

            if cursor_end <= chunk_start:
                break
        else:
            raise RuntimeError(
                f"pagination guard hit: {symbol} {granularity} "
                f"{chunk_start}..{chunk_end}"
            )

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
            continue

    df = pd.DataFrame(out, columns=["ts", "open", "high", "low", "close"])
    if df.empty:
        return df

    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df["close_ts"] = df["ts"] + duration_min * 60_000
    return df


def requested_start(test_start_ms, tf_name, duration_min):
    warmup_ms = (BB_PERIOD + 3) * duration_min * 60_000

    # Fetch deeper 1D history as a listing/coverage reference. This lets us
    # distinguish a genuinely young contract from an accidentally truncated
    # lower-timeframe download.
    if tf_name == "1D":
        weekly_warmup_ms = (BB_PERIOD + 3) * TF["1W"][1] * 60_000
        warmup_ms = max(warmup_ms, weekly_warmup_ms)

    return test_start_ms - warmup_ms


def coverage_row(
    symbol,
    tf_name,
    df,
    req_start,
    req_end,
    duration_min,
    listing_proxy_ms,
):
    duration_ms = duration_min * 60_000
    if df.empty:
        raise RuntimeError(f"{symbol} {tf_name}: empty history")

    actual_start = int(df["ts"].min())
    actual_end = int(df["ts"].max())

    # If 1D history starts later than requested, the contract likely did not
    # exist yet. Give intraday data up to one day of listing-day slack.
    listing_limited = listing_proxy_ms > req_start + duration_ms
    effective_start = max(req_start, listing_proxy_ms)
    start_slack = max(2 * duration_ms, 24 * 60 * 60 * 1000 if listing_limited else 0)

    if actual_start > effective_start + start_slack:
        raise RuntimeError(
            f"{symbol} {tf_name}: history starts too late; "
            f"requested={pd.to_datetime(req_start, unit='ms', utc=True)}, "
            f"effective={pd.to_datetime(effective_start, unit='ms', utc=True)}, "
            f"actual={pd.to_datetime(actual_start, unit='ms', utc=True)}"
        )

    # Historical endpoint should cover almost to the requested end. Allow two
    # candle widths because endpoint boundary rounding can add/remove an edge.
    if actual_end < req_end - 2 * duration_ms:
        raise RuntimeError(
            f"{symbol} {tf_name}: history ends too early; "
            f"requested_end={pd.to_datetime(req_end, unit='ms', utc=True)}, "
            f"actual_end={pd.to_datetime(actual_end, unit='ms', utc=True)}"
        )

    count_start = max(effective_start, actual_start)
    span_ms = max(0, min(req_end, actual_end) - count_start)
    expected = max(1, int(span_ms // duration_ms) + 1)
    actual = int(
        ((df["ts"] >= count_start) & (df["ts"] <= min(req_end, actual_end))).sum()
    )
    ratio = min(1.0, actual / expected) if expected else 1.0

    # Perpetual futures are effectively continuous. A large internal gap is a
    # data-quality problem, so stop rather than producing a misleading result.
    if expected >= 20 and ratio < 0.95:
        raise RuntimeError(
            f"{symbol} {tf_name}: candle coverage only {ratio:.1%} "
            f"({actual}/{expected})"
        )

    return {
        "symbol": symbol,
        "tf": tf_name,
        "requested_start_utc": pd.to_datetime(req_start, unit="ms", utc=True),
        "requested_end_utc": pd.to_datetime(req_end, unit="ms", utc=True),
        "actual_start_utc": pd.to_datetime(actual_start, unit="ms", utc=True),
        "actual_end_utc": pd.to_datetime(actual_end, unit="ms", utc=True),
        "candles": len(df),
        "coverage_pct": ratio * 100.0,
        "listing_limited": listing_limited,
    }


def bb_state_at(live_price, eval_ts, tf_df):
    """Rebuild live BB exactly as the scanner concept: 19 closed + live price."""
    close_times = tf_df["close_ts"].to_numpy(dtype=np.int64)
    closes = tf_df["close"].to_numpy(dtype=float)

    # At eval_ts, candles whose close time is <= eval_ts are already closed.
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
    coverage = []

    # 1D first: its deep fetch gives us a rough listing-time proxy used to
    # validate that lower-TF histories were not silently truncated.
    one_day_duration = TF["1D"][1]
    one_day_start = requested_start(test_start_ms, "1D", one_day_duration)
    one_day_rows = fetch_range(
        symbol,
        TF["1D"][0],
        one_day_duration,
        one_day_start,
        test_end_ms,
    )
    one_day_df = rows_to_df(one_day_rows, one_day_duration)
    if one_day_df.empty:
        raise RuntimeError(f"{symbol}: no 1D history")
    data["1D"] = one_day_df
    listing_proxy_ms = int(one_day_df["ts"].min())

    for tf_name, (granularity, duration_min) in TF.items():
        start_ms = requested_start(test_start_ms, tf_name, duration_min)
        extra_ms = 24 * 60 * 60 * 1000 if tf_name == "15M" else 0
        end_ms = test_end_ms + extra_ms

        if tf_name == "1D":
            df = one_day_df
            # 1D was intentionally fetched deeper than its normal warm-up.
            coverage_start = one_day_start
        else:
            rows = fetch_range(
                symbol,
                granularity,
                duration_min,
                start_ms,
                end_ms,
            )
            df = rows_to_df(rows, duration_min)
            data[tf_name] = df
            coverage_start = start_ms

        c = coverage_row(
            symbol,
            tf_name,
            df,
            coverage_start,
            end_ms,
            duration_min,
            listing_proxy_ms,
        )
        coverage.append(c)
        print(
            f"[DATA] {symbol} {tf_name}: {len(df)} candles | "
            f"coverage={c['coverage_pct']:.1f}% | "
            f"{c['actual_start_utc']} -> {c['actual_end_utc']}"
        )

    return data, coverage


def build_snapshots(symbol, data, test_start_ms, test_end_ms):
    base = data["15M"]

    # Phase-1 research evaluates at the exact 15m boundary using the new
    # candle's OPEN. That price is known at that instant and therefore does
    # not introduce look-ahead. A later precision pass can use 1m data to
    # emulate the live scanner's boundary+1m timing.
    eval_df = base[
        (base["ts"] >= test_start_ms) & (base["ts"] <= test_end_ms)
    ].copy()
    records = []

    base_ts = base["ts"].to_numpy(dtype=np.int64)

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
            rec[f"within_{int(threshold)}pct_count"] = sum(
                rec[f"{tf_name}_dist"] >= -threshold for tf_name in TF
            )

        # Index by timestamp without repeated DataFrame scans.
        i = int(np.searchsorted(base_ts, eval_ts))
        if i >= len(base) or int(base_ts[i]) != eval_ts:
            continue

        # Future path from the signal price. max_up is adverse excursion for a
        # short; max_drop is favorable excursion.
        for label, bars in HORIZONS.items():
            future = base.iloc[i:i + bars]
            if len(future) < bars:
                rec[f"{label}_max_up_pct"] = np.nan
                rec[f"{label}_max_drop_pct"] = np.nan
                rec[f"{label}_close_ret_pct"] = np.nan
                continue

            max_up = (float(future["high"].max()) / live - 1.0) * 100.0
            max_drop = (1.0 - float(future["low"].min()) / live) * 100.0
            close_ret = (
                float(future.iloc[-1]["close"]) / live - 1.0
            ) * 100.0

            rec[f"{label}_max_up_pct"] = max_up
            rec[f"{label}_max_drop_pct"] = max_drop
            rec[f"{label}_close_ret_pct"] = close_ret

        records.append(rec)

    return pd.DataFrame(records)


def first_touch_events(snapshots):
    """Create distinct threshold-cross events for exact 4/7, 5/7, 6/7, 7/7."""
    if snapshots.empty:
        return snapshots.copy()

    all_events = []
    for _, g in snapshots.groupby("symbol", sort=False):
        g = g.sort_values("ts").copy()
        prev = g["exact_count"].shift(1).fillna(0)

        for level in (4, 5, 6, 7):
            mask = (g["exact_count"] >= level) & (prev < level)
            e = g.loc[mask].copy()
            e["signal_level"] = level
            all_events.append(e)

    if not all_events:
        return pd.DataFrame()

    return pd.concat(all_events, ignore_index=True).sort_values(
        ["ts", "symbol", "signal_level"]
    )


def add_split(events):
    if events.empty:
        return events

    events = events.copy()
    # Strict time-order split: earlier 70% for exploration, later 30% untouched
    # for out-of-sample validation.
    unique_times = np.sort(events["ts"].unique())
    cutoff_index = max(0, int(len(unique_times) * 0.70) - 1)
    cutoff = unique_times[cutoff_index]
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
            "symbols": g["symbol"].nunique(),
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


def near_profile(events):
    """Research table for reverse-engineering useful 'near BB' thresholds."""
    if events.empty:
        return pd.DataFrame()

    rows = []
    for split in ("train70", "test30"):
        subset = events[events["split"] == split]
        for level in (4, 5, 6):
            base = subset[subset["signal_level"] == level]
            for threshold in NEAR_THRESHOLDS:
                # All seven TFs are either broken or within threshold.
                col = f"within_{int(threshold)}pct_count"
                g = base[base[col] == 7]
                if g.empty:
                    continue
                rows.append(
                    {
                        "split": split,
                        "signal_level": level,
                        "near_pct": threshold,
                        "n": len(g),
                        "symbols": g["symbol"].nunique(),
                        "4h_med_up": g["4h_max_up_pct"].median(),
                        "4h_med_drop": g["4h_max_drop_pct"].median(),
                        "12h_med_up": g["12h_max_up_pct"].median(),
                        "12h_med_drop": g["12h_max_drop_pct"].median(),
                        "24h_med_up": g["24h_max_up_pct"].median(),
                        "24h_med_drop": g["24h_max_drop_pct"].median(),
                        "24h_drop10_rate": (
                            g["24h_max_drop_pct"] >= 10
                        ).mean() * 100,
                        "24h_pump10_rate": (
                            g["24h_max_up_pct"] >= 10
                        ).mean() * 100,
                    }
                )

    return pd.DataFrame(rows)


def parse_args():
    p = argparse.ArgumentParser(
        description="Bitget multi-timeframe BB research backtest"
    )
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
    if not symbols:
        raise SystemExit("No symbols supplied.")
    if args.days < 7:
        raise SystemExit("--days must be >= 7")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    now_ms = int(time.time() * 1000)
    fifteen_ms = 15 * 60 * 1000
    now_boundary = (now_ms // fifteen_ms) * fifteen_ms

    # Hold out the latest 24h so every included signal can have a complete
    # 24-hour forward path without using unfinished candles.
    test_end_ms = now_boundary - 24 * 60 * 60 * 1000
    test_start_ms = test_end_ms - args.days * 24 * 60 * 60 * 1000

    print(
        "[CONFIG] "
        f"symbols={len(symbols)} days={args.days} "
        f"test={pd.to_datetime(test_start_ms, unit='ms', utc=True)} "
        f"-> {pd.to_datetime(test_end_ms, unit='ms', utc=True)}"
    )

    all_snapshots = []
    all_coverage = []

    for symbol in symbols:
        print(f"\n=== {symbol}: {args.days}d research window ===")
        try:
            data, coverage = fetch_symbol_data(
                symbol,
                test_start_ms,
                test_end_ms,
            )
            snapshots = build_snapshots(
                symbol,
                data,
                test_start_ms,
                test_end_ms,
            )
            print(
                f"[SNAP] {symbol}: {len(snapshots)} valid 15m snapshots "
                f"(target about {args.days * 96})"
            )
            all_snapshots.append(snapshots)
            all_coverage.extend(coverage)
        except Exception as exc:
            print(f"[ERROR] {symbol}: {exc}")

    if not all_snapshots:
        raise SystemExit("No backtest data produced.")

    snapshots = pd.concat(all_snapshots, ignore_index=True)
    events = add_split(first_touch_events(snapshots))
    summary = summarize(events)
    near = near_profile(events)
    coverage_df = pd.DataFrame(all_coverage)

    snapshots.to_csv(outdir / "snapshots.csv", index=False)
    events.to_csv(outdir / "events.csv", index=False)
    summary.to_csv(outdir / "summary.csv", index=False)
    near.to_csv(outdir / "near_profile.csv", index=False)
    coverage_df.to_csv(outdir / "coverage.csv", index=False)

    print("\n=== DATA COVERAGE ===")
    if not coverage_df.empty:
        print(
            coverage_df[
                ["symbol", "tf", "candles", "coverage_pct", "listing_limited"]
            ].to_string(index=False)
        )

    print("\n=== FIRST-TOUCH EVENT SUMMARY ===")
    if summary.empty:
        print("No 4/7+ events in selected window.")
    else:
        with pd.option_context(
            "display.max_columns",
            100,
            "display.width",
            240,
        ):
            print(summary.to_string(index=False))

    print("\n=== NEAR-BB PROFILE ===")
    if near.empty:
        print("No near-profile rows in selected window.")
    else:
        with pd.option_context(
            "display.max_columns",
            100,
            "display.width",
            220,
        ):
            print(near.to_string(index=False))

    print(f"\nSaved: {outdir}/coverage.csv")
    print(f"Saved: {outdir}/summary.csv")
    print(f"Saved: {outdir}/near_profile.csv")
    print(f"Saved: {outdir}/events.csv")
    print(f"Saved: {outdir}/snapshots.csv")


if __name__ == "__main__":
    main()
