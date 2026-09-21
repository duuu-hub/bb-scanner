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
DEBUG_SYMBOLS = {
    x.strip() for x in os.getenv("DEBUG_SYMBOLS", "").split(",") if x.strip()
}
TF_KR = {
    "1W": "주봉",
    "1D": "일봉",
    "12H": "12시간",
    "4H": "4시간",
    "1H": "1시간",
    "30M": "30분",
    "15M": "15분",
}

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
API_STARTS_PER_SEC = float(os.getenv("API_STARTS_PER_SEC", "19"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "32"))
_rate_lock = threading.Lock()
_last_api_start = 0.0
RE_ALERT_PRICE_MOVE_PCT = float(os.getenv("RE_ALERT_PRICE_MOVE_PCT", "5.0"))
NEAR_BB_PCT = float(os.getenv("NEAR_BB_PCT", "3.0"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
MANUAL_RUN = os.getenv("GITHUB_EVENT_NAME", "") in ("workflow_dispatch", "push")

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


def score_candidate(tf_results):
    """7개 타임프레임 과열 점수.

    Exact pass = live price above the current upper BB.
    Near miss = live price below upper BB but within NEAR_BB_PCT.
    """
    exact = []
    near = []
    far = []
    for tf, _ in TIMEFRAMES:
        r = tf_results.get(tf)
        if not r:
            far.append(tf)
            continue
        if r.get("above"):
            exact.append(tf)
        elif r.get("distance_pct") is not None and r["distance_pct"] >= -NEAR_BB_PCT:
            near.append(tf)
        else:
            far.append(tf)

    exact_count = len(exact)

    if exact_count == 7:
        label = "EXTREME 7/7"
        rank = 7
    elif exact_count == 6:
        label = "HOT 6/7"
        rank = 6
    elif exact_count == 5 and len(far) == 0:
        label = "NEAR-HEAT 5/7"
        rank = 5
    elif exact_count == 4 and len(far) == 0:
        label = "WATCH 4/7"
        rank = 4
    else:
        label = "IGNORE"
        rank = 0

    return {
        "rank": rank,
        "label": label,
        "exact": exact,
        "near": near,
        "far": far,
        "exact_count": exact_count,
    }


def determine_stage(tf_results):
    return score_candidate(tf_results)["rank"]

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
    return {
        7: "🚨 전봉 돌파 7/7",
        6: "🔥 과열후보 6/7",
        5: "🟠 근접후보 5/7",
        4: "🟡 관찰후보 4/7",
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
    score = score_candidate(tf_results)

    exact_kr = [TF_KR[x] for x in score["exact"]]
    near_kr = [TF_KR[x] for x in score["near"]]
    far_kr = [TF_KR[x] for x in score["far"]]

    # Mobile-first Telegram layout: put the symbol on the very first line,
    # then the stage and actionable summary. Keep details below a visual divider.
    lines = [
        f"🪙 {symbol}",
        f"{stage_label(stage)}",
        f"현재가 {fmt_price(ticker.get('last_price'))}  |  24H {fmt_pct(ticker.get('change24h_pct'))}",
        f"사유 {reason}",
        "────────────",
        f"✅ 돌파 {len(exact_kr)}/7" + (f"  ·  {', '.join(exact_kr)}" if exact_kr else ""),
    ]

    if near_kr:
        lines.append(f"🟨 근접  ·  {', '.join(near_kr)}")
    if far_kr:
        lines.append(f"❌ 미달  ·  {', '.join(far_kr)}")

    lines.append("────────────")
    lines.append("BB 상단 대비")

    for tf, _ in TIMEFRAMES:
        r = tf_results.get(tf)
        name = TF_KR[tf]
        if not r:
            lines.append(f"{name}: ?")
            continue

        d = r.get("distance_pct")
        if r.get("above"):
            mark = "✅ 돌파"
        elif d is not None and d >= -NEAR_BB_PCT:
            mark = "🟨 근접"
        else:
            mark = "❌ 미달"

        lines.append(f"{name}: {mark}  {fmt_pct(d)}")

    if stage == 7:
        lines += ["", f"7/7 포착가: {fmt_price(ticker.get('last_price'))}"]

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



def telegram_send_batched(messages, max_chars=3800):
    """Send scan alerts in as few Telegram notifications as possible."""
    if not messages:
        return 0

    header = f"📡 BB 알림 {len(messages)}건"
    chunks = []
    current = header

    for message in messages:
        block = "\n\n" + message
        if len(current) + len(block) <= max_chars:
            current += block
            continue

        chunks.append(current)
        current = header + block

    if current:
        chunks.append(current)

    sent = 0
    for chunk in chunks:
        if telegram_send(chunk):
            sent += 1
    return sent


def should_alert(candidate, previous):
    stage = candidate["stage"]
    price = candidate["ticker"].get("last_price")

    if previous is None:
        return True, "신규 포착"

    prev_stage = int(previous.get("stage", 0))
    if stage > prev_stage:
        return True, f"단계 상승 {prev_stage}/7 → {stage}/7"

    last_alert_price = previous.get("last_alert_price")
    if (
        stage == prev_stage
        and price
        and last_alert_price
        and float(last_alert_price) > 0
    ):
        signed_move = (price / float(last_alert_price) - 1.0) * 100.0
        if abs(signed_move) >= RE_ALERT_PRICE_MOVE_PCT:
            return True, f"이전 알림가 대비 {signed_move:+.2f}%"

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

    # Flexible scan: cheap TFs first, but do NOT require hierarchical passes.
    # We keep symbols that still have a realistic path to >=4/7 heat.
    cheap_pipeline = [
        ("1D", "1D"),
        ("12H", "12H"),
        ("4H", "4H"),
        ("1H", "1H"),
        ("30M", "30m"),
        ("15M", "15m"),
    ]

    survivors = [sym for sym in symbols if sym in ticker_map]

    for idx, (tf, granularity) in enumerate(cheap_pipeline, start=1):
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
            ]
            for future in as_completed(futures):
                symbol, _, result = future.result()
                if result is not None:
                    tf_store.setdefault(symbol, {})[tf] = result

        # Exact feasibility pruning for our final rules:
        # - 6/7 may have only ONE non-exact TF (near or far).
        # - 5/7 and 4/7 are allowed only when every non-exact TF is NEAR.
        # Once a symbol can no longer satisfy either path, drop it immediately.
        remaining_after_this = len(cheap_pipeline) - idx + 1  # +1 for weekly
        kept = []
        for symbol in survivors:
            results = tf_store.get(symbol, {})
            exact_now = 0
            near_now = 0
            far_now = 0

            for r in results.values():
                if r.get("above"):
                    exact_now += 1
                elif (
                    r.get("distance_pct") is not None
                    and r["distance_pct"] >= -NEAR_BB_PCT
                ):
                    near_now += 1
                else:
                    far_now += 1

            # A missing API result in a timeframe already checked is treated
            # as a far miss so it cannot keep wasting requests downstream.
            far_now += max(0, idx - len(results))

            possible = False
            if far_now == 0:
                # 4/7 or better remains possible if enough unchecked TFs
                # can still become exact.
                possible = exact_now + remaining_after_this >= 4
            elif far_now == 1 and near_now == 0:
                # With one far miss, only HOT 6/7 remains possible.
                possible = exact_now + remaining_after_this >= 6

            if possible:
                kept.append(symbol)

        survivors = kept
        print(f"[INFO] {tf} 검사 후 잔존: {len(survivors)}개")

        if not survivors:
            break

    # Weekly is expensive: only query the symbols that survived the cheap scan.
    if survivors:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [
                pool.submit(
                    fetch_tf_for_symbol,
                    symbol,
                    "1W",
                    "1W",
                    ticker_map[symbol],
                )
                for symbol in survivors
            ]
            for future in as_completed(futures):
                symbol, _, result = future.result()
                if result is not None:
                    tf_store.setdefault(symbol, {})["1W"] = result

    for symbol in survivors:
        results = tf_store.get(symbol, {})
        if not all(tf in results for tf, _ in TIMEFRAMES):
            continue
        score = score_candidate(results)
        if score["rank"] >= 4:
            candidate = {
                "symbol": symbol,
                "stage": score["rank"],
                "tf_results": results,
                "ticker": ticker_map[symbol],
            }
            candidates.append(candidate)

    gate_stats = {tf: 0 for tf, _ in TIMEFRAMES}
    for symbol, results in tf_store.items():
        for tf, _ in TIMEFRAMES:
            if results.get(tf, {}).get("above"):
                gate_stats[tf] += 1

    candidates.sort(key=lambda x: (-x["stage"], x["symbol"]))

    alerts_sent = 0
    telegram_messages_sent = 0
    pending_alerts = []

    for candidate in candidates:
        symbol = candidate["symbol"]
        previous = previous_symbols.get(symbol)
        alert, reason = should_alert(candidate, previous)

        price = candidate["ticker"].get("last_price")
        prev_alert_price = previous.get("last_alert_price") if previous else None

        if alert:
            message = build_alert(candidate, reason)
            print("\n" + message + "\n")
            pending_alerts.append(message)
            if price:
                prev_alert_price = price

        new_symbols_state[symbol] = {
            "stage": candidate["stage"],
            "last_price": price,
            "last_alert_price": prev_alert_price,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    if pending_alerts:
        try:
            telegram_messages_sent = telegram_send_batched(pending_alerts)
            if telegram_messages_sent:
                alerts_sent = len(pending_alerts)
        except Exception as exc:
            print(f"[ERROR] Telegram batch send failed: {exc}")

    # Manual runs always send a compact completion message, which doubles as a Telegram test.
    if MANUAL_RUN:
        stage_counts = {4: 0, 5: 0, 6: 0, 7: 0}
        for candidate in candidates:
            stage_counts[candidate["stage"]] += 1
        summary = (
            "✅ BB 스캔 완료\n"
            f"전체: {len(symbols)}종목 | 후보: {len(candidates)}종목\n"
            f"관찰4={stage_counts[4]} / 근접5={stage_counts[5]} / "
            f"과열6={stage_counts[6]} / 전봉7={stage_counts[7]}\n"
            f"실제 돌파 수: 주봉 {gate_stats['1W']} | 일봉 {gate_stats['1D']} | "
            f"12시간 {gate_stats['12H']} | 4시간 {gate_stats['4H']} | "
            f"1시간 {gate_stats['1H']} | 30분 {gate_stats['30M']} | 15분 {gate_stats['15M']}\n"
            f"API 오류: {errors}"
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
        f"[DONE] candidates={len(candidates)}, alerts_sent={alerts_sent}, "
        f"telegram_messages={telegram_messages_sent}, errors={errors}"
    )


if __name__ == "__main__":
    main()
