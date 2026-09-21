import json
import math
import os
import statistics
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://api.bitget.com"
PRODUCT_TYPE = "usdt-futures"
BB_PERIOD = 20
BB_STD = 2.0
STATE_PATH = Path("state.json")
DEBUG_SYMBOLS = {"龙虾USDT"}

# Upper timeframes first. We stop early until the 4h gate is reached.
TIMEFRAMES = [
    ("1W", "1W"),
    ("1D", "1D"),
    ("12H", "12H"),
    ("4H", "4H"),
    ("1H", "1H"),
    ("30M", "30m"),
    ("15M", "15m"),
]

GATE_TFS = ["1W", "1D", "12H", "4H"]
LOWER_TFS = ["1H", "30M", "15M"]

REQUEST_TIMEOUT_SEC = int(os.getenv("REQUEST_TIMEOUT_SEC", "12"))
API_STARTS_PER_SEC = float(os.getenv("API_STARTS_PER_SEC", "18"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "24"))
_rate_lock = threading.Lock()
_last_api_start = 0.0
RE_ALERT_PRICE_MOVE_PCT = float(os.getenv("RE_ALERT_PRICE_MOVE_PCT", "5.0"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
MANUAL_RUN = os.getenv("GITHUB_EVENT_NAME", "") in ("workflow_dispatch", "push")
DEBUG_SYMBOL = "龙虾USDT"

session = requests.Session()
session.headers.update({"User-Agent": "bb-scanner/1.0"})


def api_get(path, params=None, retries=3):
    url = BASE_URL + path
    last_error = None
    global _last_api_start
    for attempt in range(retries):
        try:
            # Global rate limiter shared by all worker threads.
            with _rate_lock:
                now = time.monotonic()
                min_gap = 1.0 / API_STARTS_PER_SEC
                wait = min_gap - (now - _last_api_start)
                if wait > 0:
                    time.sleep(wait)
                _last_api_start = time.monotonic()
            response = session.get(url, params=params or {}, timeout=REQUEST_TIMEOUT_SEC)
            response.raise_for_status()
            payload = response.json()
            if str(payload.get("code")) != "00000":
                raise RuntimeError(f"Bitget error {payload.get('code')}: {payload.get('msg')}")
            return payload.get("data")
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"GET {path} failed: {last_error}")


def load_state():
    if not STATE_PATH.exists():
        return {"symbols": {}, "last_scan": None}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"symbols": {}, "last_scan": None}


def save_state(state):
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def get_symbols():
    data = api_get(
        "/api/v2/mix/market/contracts",
        {"productType": PRODUCT_TYPE},
    ) or []
    symbols = []
    for item in data:
        if item.get("symbolType") != "perpetual":
            continue
        if item.get("symbolStatus") != "normal":
            continue
        symbol = item.get("symbol")
        if symbol and str(item.get("quoteCoin", "")).upper() == "USDT":
            symbols.append(symbol)
    return sorted(set(symbols))



def fetch_market_candles(symbol, granularity):
    """Fetch enough market candles for BB(20,2).

    Bitget's long-range candle queries are effectively bounded by a time
    window. Weekly BB20 needs about 140 days, so 1W is fetched in two
    non-overlapping 89-day chunks and merged by timestamp.
    """
    if granularity != "1W":
        return api_get(
            "/api/v2/mix/market/candles",
            {
                "symbol": symbol,
                "productType": PRODUCT_TYPE,
                "granularity": granularity,
                "limit": 30,
            },
        ) or []

    now_ms = int(time.time() * 1000)
    day_ms = 24 * 60 * 60 * 1000
    chunk_ms = 89 * day_ms
    merged = {}

    # Two 89-day chunks = 178 days, comfortably enough for
    # 19 completed weekly closes + the current week.
    for chunk_index in range(2):
        end_ms = now_ms - chunk_index * chunk_ms
        start_ms = end_ms - chunk_ms
        rows = api_get(
            "/api/v2/mix/market/candles",
            {
                "symbol": symbol,
                "productType": PRODUCT_TYPE,
                "granularity": granularity,
                "startTime": str(start_ms),
                "endTime": str(end_ms),
                "limit": 30,
            },
        ) or []
        for row in rows:
            try:
                merged[int(row[0])] = row
            except (ValueError, TypeError, IndexError):
                continue

    return [merged[ts] for ts in sorted(merged)]

def bollinger_live(symbol, granularity, live_price):
    """Calculate BB(20,2) for the CURRENT market candle.

    We use normal market/trade-price candles so the result tracks the Bitget
    chart. The live ticker price is used as the current candle close.
    """
    rows = fetch_market_candles(symbol, granularity)

    duration_ms = {
        "15m": 15 * 60 * 1000,
        "30m": 30 * 60 * 1000,
        "1H": 60 * 60 * 1000,
        "4H": 4 * 60 * 60 * 1000,
        "12H": 12 * 60 * 60 * 1000,
        "1D": 24 * 60 * 60 * 1000,
        "1W": 7 * 24 * 60 * 60 * 1000,
    }[granularity]

    now_ms = int(time.time() * 1000)
    parsed = []
    for row in rows:
        try:
            ts = int(row[0])
            close = float(row[4])
            if math.isfinite(close) and close > 0:
                parsed.append((ts, close))
        except (ValueError, TypeError, IndexError):
            continue

    parsed.sort(key=lambda x: x[0])
    if not parsed:
        return None

    # The regular candles endpoint normally includes the in-progress candle.
    # Detect and remove it from the completed history before rebuilding the
    # current BB with the freshest ticker price.
    has_current_row = parsed[-1][0] + duration_ms > now_ms
    completed = parsed[:-1] if has_current_row else parsed

    if len(completed) < BB_PERIOD - 1:
        return None

    if live_price is None or not math.isfinite(live_price) or live_price <= 0:
        return None

    # Current BB = previous 19 completed closes + current live trade price.
    live_window = [x[1] for x in completed[-(BB_PERIOD - 1):]] + [live_price]
    basis = sum(live_window) / BB_PERIOD
    std = statistics.pstdev(live_window)
    upper = basis + BB_STD * std
    lower = basis - BB_STD * std
    distance_pct = (live_price / upper - 1.0) * 100.0 if upper > 0 else 0.0

    completed_close = completed[-1][1]
    completed_above = False
    completed_upper = None
    if len(completed) >= BB_PERIOD:
        completed20 = [x[1] for x in completed[-BB_PERIOD:]]
        completed_basis = sum(completed20) / BB_PERIOD
        completed_std = statistics.pstdev(completed20)
        completed_upper = completed_basis + BB_STD * completed_std
        completed_above = completed_close > completed_upper

    return {
        "close": live_price,
        "basis": basis,
        "upper": upper,
        "lower": lower,
        "above": live_price > upper,
        "distance_pct": distance_pct,
        "completed_close": completed_close,
        "completed_upper": completed_upper,
        "completed_above": completed_above,
        "candle_ts": completed[-1][0],
        "rows_received": len(parsed),
    }


def get_all_tickers():
    """Fetch all USDT-futures tickers in one request."""
    data = api_get(
        "/api/v2/mix/market/tickers",
        {"productType": PRODUCT_TYPE},
    ) or []
    ticker_map = {}
    for item in data:
        try:
            symbol = item.get("symbol")
            if not symbol:
                continue
            last_price = item.get("lastPr") or item.get("last") or item.get("markPrice")
            mark_price = item.get("markPrice")
            change24h = item.get("change24h")
            ticker_map[symbol] = {
                "last_price": float(last_price) if last_price not in (None, "") else None,
                "mark_price": float(mark_price) if mark_price not in (None, "") else None,
                "change24h_pct": float(change24h) * 100.0 if change24h not in (None, "") else None,
            }
        except (ValueError, TypeError):
            continue
    return ticker_map

def get_ticker(symbol):
    try:
        data = api_get(
            "/api/v2/mix/market/ticker",
            {"symbol": symbol, "productType": PRODUCT_TYPE},
        )
        if isinstance(data, list):
            item = data[0] if data else {}
        elif isinstance(data, dict):
            item = data
        else:
            item = {}

        last_price = item.get("lastPr") or item.get("last") or item.get("markPrice")
        change24h = item.get("change24h")
        mark_price = item.get("markPrice")
        return {
            "last_price": float(last_price) if last_price not in (None, "") else None,
            "mark_price": float(mark_price) if mark_price not in (None, "") else None,
            "change24h_pct": float(change24h) * 100.0 if change24h not in (None, "") else None,
        }
    except Exception as exc:
        print(f"[WARN] ticker {symbol}: {exc}")
        return {"last_price": None, "mark_price": None, "change24h_pct": None}


def determine_stage(tf_results):
    if not all(tf_results.get(tf, {}).get("above") for tf in GATE_TFS):
        return 0
    if not tf_results.get("1H", {}).get("above"):
        return 4
    if not tf_results.get("30M", {}).get("above"):
        return 5
    if not tf_results.get("15M", {}).get("above"):
        return 6
    return 7


def next_unmet_tf(stage):
    return {4: "1H", 5: "30M", 6: "15M"}.get(stage)


def fmt_pct(value, digits=2):
    if value is None:
        return "N/A"
    return f"{value:+.{digits}f}%"


def fmt_price(value):
    if value is None:
        return "N/A"
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{value:.10f}".rstrip("0").rstrip(".")


def stage_label(stage):
    if stage == 7:
        return "🚨 EXTREME 7/7"
    return {
        4: "🟡 PRE-HEAT 4/7",
        5: "🟠 PRE-HEAT 5/7",
        6: "🔥 PRE-HEAT 6/7",
    }.get(stage, f"{stage}/7")


def telegram_send(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[INFO] Telegram secrets not configured; message not sent.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram error: {body}")
    return True


def build_alert(candidate, reason):
    symbol = candidate["symbol"]
    stage = candidate["stage"]
    tf_results = candidate["tf_results"]
    ticker = candidate["ticker"]
    lines = [
        f"{stage_label(stage)} — {symbol}",
        f"Reason: {reason}",
        f"Live: {fmt_price(ticker.get('last_price'))}",
        f"24H: {fmt_pct(ticker.get('change24h_pct'))}",
        "",
        "BB(20,2) CURRENT bars (19 closed + live trade price):",
    ]
    for tf, _ in TIMEFRAMES:
        r = tf_results.get(tf)
        if not r:
            lines.append(f"{tf}: ?")
            continue
        mark = "✅" if r["above"] else "❌"
        closed = "C✅" if r.get("completed_above") else "C·"
        lines.append(f"{tf}: {mark} {fmt_pct(r['distance_pct'])}  {closed}")

    unmet = next_unmet_tf(stage)
    if unmet and tf_results.get(unmet):
        upper = tf_results[unmet]["upper"]
        live = ticker.get("last_price")
        if live and upper > 0:
            live_dist = (live / upper - 1.0) * 100.0
            lines += [
                "",
                f"Next: {unmet}",
                f"Live vs current {unmet} upper BB: {fmt_pct(live_dist)}",
            ]

    if stage == 7:
        lines += ["", f"7/7 reference price: {fmt_price(ticker.get('last_price'))}"]

    return "\n".join(lines)


def fetch_tf_for_symbol(symbol, tf, granularity, ticker):
    live_price = ticker.get("last_price") or ticker.get("mark_price")
    if not live_price:
        return symbol, tf, None
    try:
        return symbol, tf, bollinger_live(symbol, granularity, live_price)
    except Exception as exc:
        print(f"[WARN] {symbol} {tf}: {exc}")
        return symbol, tf, None


def filter_stage(symbols, tf, granularity, ticker_map, tf_store):
    """Evaluate one timeframe in parallel and keep only upper-BB breakouts."""
    passed = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = []
        for symbol in symbols:
            ticker = ticker_map.get(symbol)
            if ticker:
                futures.append(
                    pool.submit(fetch_tf_for_symbol, symbol, tf, granularity, ticker)
                )
        for future in as_completed(futures):
            symbol, _, result = future.result()
            if result is None:
                continue
            tf_store.setdefault(symbol, {})[tf] = result
            if result["above"]:
                passed.append(symbol)
    return passed


def build_candidate(symbol, tf_results, ticker):
    stage = determine_stage(tf_results)
    return {
        "symbol": symbol,
        "stage": stage,
        "tf_results": tf_results,
        "ticker": ticker,
    }


def debug_symbol(symbol, ticker):
    """Print full BB diagnostics for a known visual-reference symbol."""
    live_price = ticker.get("last_price") or ticker.get("mark_price")
    print(f"[DEBUG] {symbol} live={live_price}")
    for tf, granularity in TIMEFRAMES:
        try:
            r = bollinger_live(symbol, granularity, live_price)
            if not r:
                raw_rows = fetch_market_candles(symbol, granularity)
                print(f"[DEBUG] {symbol} {tf}: NO_DATA raw_rows={len(raw_rows)}")
                continue
            print(
                f"[DEBUG] {symbol} {tf}: "
                f"price={r['close']} upper={r['upper']} "
                f"dist={r['distance_pct']:+.3f}% above={r['above']} "
                f"rows={r.get('rows_received')}"
            )
        except Exception as exc:
            print(f"[DEBUG] {symbol} {tf}: ERROR {exc}")


def should_alert(candidate, previous):
    stage = candidate["stage"]
    price = candidate["ticker"].get("last_price")

    if previous is None:
        return True, f"NEW {stage}/7"

    prev_stage = int(previous.get("stage", 0))
    if stage > prev_stage:
        return True, f"UPGRADE {prev_stage}/7 → {stage}/7"

    last_alert_price = previous.get("last_alert_price")
    if (
        stage == prev_stage
        and price
        and last_alert_price
        and float(last_alert_price) > 0
    ):
        move = abs(price / float(last_alert_price) - 1.0) * 100.0
        if move >= RE_ALERT_PRICE_MOVE_PCT:
            return True, f"PRICE MOVED {move:.2f}% since last alert"

    return False, ""


def main():
    state = load_state()
    previous_symbols = state.get("symbols", {})
    new_symbols_state = {}

    symbols = get_symbols()
    ticker_map = get_all_tickers()
    print(
        f"[INFO] scanning {len(symbols)} active USDT perpetual symbols "
        f"with {len(ticker_map)} bulk tickers"
    )

    for debug_symbol_name in DEBUG_SYMBOLS:
        if debug_symbol_name in symbols:
            debug_symbol(debug_symbol_name, ticker_map.get(debug_symbol_name, {}))
        else:
            print(f"[DEBUG] {debug_symbol_name}: NOT FOUND in contract list")

    candidates = []
    errors = 0
    tf_store = {}

    # Fast pipeline: cheap single-request TFs first. Weekly is the expensive
    # two-chunk fetch, so only symbols surviving 1D/12H/4H reach it.
    pipeline = [
        ("1D", "1D"),
        ("12H", "12H"),
        ("4H", "4H"),
        ("1W", "1W"),
    ]

    survivors = [s for s in symbols if s in ticker_map]
    pipeline_counts = []
    for tf, granularity in pipeline:
        started = time.monotonic()
        survivors = filter_stage(
            survivors, tf, granularity, ticker_map, tf_store
        )
        elapsed = time.monotonic() - started
        pipeline_counts.append((tf, len(survivors)))
        print(
            f"[INFO] gate {tf}: {len(survivors)} passed "
            f"({elapsed:.1f}s)"
        )
        if not survivors:
            break

    # Only the small 4/7 survivor set needs the lower timeframes.
    lower_pipeline = [("1H", "1H"), ("30M", "30m"), ("15M", "15m")]
    active = list(survivors)
    for tf, granularity in lower_pipeline:
        if not active:
            break
        # Evaluate this TF for ALL 4/7 survivors so stage can be determined
        # hierarchically without scanning the whole market.
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [
                pool.submit(
                    fetch_tf_for_symbol,
                    symbol,
                    tf,
                    granularity,
                    ticker_map[symbol],
                )
                for symbol in survivors
                if symbol in ticker_map
            ]
            for future in as_completed(futures):
                symbol, _, result = future.result()
                if result is not None:
                    tf_store.setdefault(symbol, {})[tf] = result

    for symbol in survivors:
        results = tf_store.get(symbol, {})
        # Missing lower TF data means skip rather than emit a bad stage.
        if not all(tf in results for tf, _ in TIMEFRAMES):
            continue
        candidate = build_candidate(symbol, results, ticker_map[symbol])
        if candidate["stage"] >= 4:
            candidates.append(candidate)

    gate_stats = {tf: 0 for tf, _ in TIMEFRAMES}
    # These are pipeline counts, not standalone-market counts.
    count_map = dict(pipeline_counts)
    gate_stats["1D"] = count_map.get("1D", 0)
    gate_stats["12H"] = count_map.get("12H", 0)
    gate_stats["4H"] = count_map.get("4H", 0)
    gate_stats["1W"] = count_map.get("1W", 0)
    for c in candidates:
        for tf in LOWER_TFS:
            if c["tf_results"].get(tf, {}).get("above"):
                gate_stats[tf] += 1

    candidates.sort(key=lambda x: (-x["stage"], x["symbol"]))

    alerts_sent = 0
    for candidate in candidates:
        symbol = candidate["symbol"]
        previous = previous_symbols.get(symbol)
        alert, reason = should_alert(candidate, previous)

        price = candidate["ticker"].get("last_price")
        prev_alert_price = previous.get("last_alert_price") if previous else None

        if alert:
            message = build_alert(candidate, reason)
            print("\n" + message + "\n")
            try:
                if telegram_send(message):
                    alerts_sent += 1
            except Exception as exc:
                print(f"[ERROR] Telegram send failed for {symbol}: {exc}")
            if price:
                prev_alert_price = price

        new_symbols_state[symbol] = {
            "stage": candidate["stage"],
            "last_price": price,
            "last_alert_price": prev_alert_price,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    # Manual runs always send a compact completion message, which doubles as a Telegram test.
    if MANUAL_RUN:
        stage_counts = {4: 0, 5: 0, 6: 0, 7: 0}
        for candidate in candidates:
            stage_counts[candidate["stage"]] += 1
        summary = (
            "✅ Bitget BB scanner manual run complete\n"
            f"Scanned: {len(symbols)} symbols\n"
            f"Candidates: {len(candidates)} "
            f"(4/7={stage_counts[4]}, 5/7={stage_counts[5]}, "
            f"6/7={stage_counts[6]}, 7/7={stage_counts[7]})\n"
            f"Gate pipeline: 1D={gate_stats['1D']} → 12H={gate_stats['12H']} "
            f"→ 4H={gate_stats['4H']} → 1W={gate_stats['1W']}\n"
            f"API symbol errors: {errors}"
        )
        print(summary)
        try:
            telegram_send(summary)
        except Exception as exc:
            print(f"[ERROR] Telegram manual summary failed: {exc}")

    state = {
        "symbols": new_symbols_state,
        "last_scan": datetime.now(timezone.utc).isoformat(),
        "last_symbol_count": len(symbols),
        "last_candidate_count": len(candidates),
        "last_error_count": errors,
    }
    save_state(state)

    print(
        f"[DONE] candidates={len(candidates)}, alerts_sent={alerts_sent}, errors={errors}"
    )


if __name__ == "__main__":
    main()
