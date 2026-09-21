import json
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://api.bitget.com"
PRODUCT_TYPE = "usdt-futures"
BB_PERIOD = 20
BB_STD = 2.0
STATE_PATH = Path("state.json")

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

REQUEST_INTERVAL_SEC = float(os.getenv("REQUEST_INTERVAL_SEC", "0.07"))
REQUEST_TIMEOUT_SEC = int(os.getenv("REQUEST_TIMEOUT_SEC", "12"))
RE_ALERT_PRICE_MOVE_PCT = float(os.getenv("RE_ALERT_PRICE_MOVE_PCT", "5.0"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
MANUAL_RUN = os.getenv("GITHUB_EVENT_NAME", "") in ("workflow_dispatch", "push")

session = requests.Session()
session.headers.update({"User-Agent": "bb-scanner/1.0"})


def api_get(path, params=None, retries=3):
    url = BASE_URL + path
    last_error = None
    for attempt in range(retries):
        try:
            time.sleep(REQUEST_INTERVAL_SEC)
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


def bollinger_for_latest_completed(symbol, granularity):
    # Bitget documents this endpoint as returning finished mark-price K-lines.
    rows = api_get(
        "/api/v2/mix/market/history-mark-candles",
        {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "granularity": granularity,
            "limit": 30,
        },
    ) or []

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
    if len(parsed) < BB_PERIOD:
        return None

    window = [x[1] for x in parsed[-BB_PERIOD:]]
    close = window[-1]
    basis = sum(window) / BB_PERIOD
    std = statistics.pstdev(window)
    upper = basis + BB_STD * std
    lower = basis - BB_STD * std
    distance_pct = (close / upper - 1.0) * 100.0 if upper > 0 else 0.0

    return {
        "close": close,
        "basis": basis,
        "upper": upper,
        "lower": lower,
        "above": close > upper,
        "distance_pct": distance_pct,
        "candle_ts": parsed[-1][0],
    }


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
        return {
            "last_price": float(last_price) if last_price not in (None, "") else None,
            "change24h_pct": float(change24h) * 100.0 if change24h not in (None, "") else None,
        }
    except Exception as exc:
        print(f"[WARN] ticker {symbol}: {exc}")
        return {"last_price": None, "change24h_pct": None}


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
        "BB(20,2) latest completed candles:",
    ]
    for tf, _ in TIMEFRAMES:
        r = tf_results.get(tf)
        if not r:
            lines.append(f"{tf}: ?")
            continue
        mark = "✅" if r["above"] else "❌"
        lines.append(f"{tf}: {mark} {fmt_pct(r['distance_pct'])}")

    unmet = next_unmet_tf(stage)
    if unmet and tf_results.get(unmet):
        upper = tf_results[unmet]["upper"]
        live = ticker.get("last_price")
        if live and upper > 0:
            live_dist = (live / upper - 1.0) * 100.0
            lines += [
                "",
                f"Next: {unmet}",
                f"Live vs last completed {unmet} upper BB: {fmt_pct(live_dist)}",
            ]

    if stage == 7:
        lines += ["", f"7/7 reference price: {fmt_price(ticker.get('last_price'))}"]

    return "\n".join(lines)


def scan_symbol(symbol):
    tf_results = {}

    # Gate from 1W -> 4H. If any upper timeframe fails, it cannot be 4/7.
    for tf, granularity in TIMEFRAMES[:4]:
        result = bollinger_for_latest_completed(symbol, granularity)
        if result is None:
            return None
        tf_results[tf] = result
        if not result["above"]:
            return None

    # It passed the 4/7 gate. Fetch all lower TFs so the status table is complete.
    for tf, granularity in TIMEFRAMES[4:]:
        result = bollinger_for_latest_completed(symbol, granularity)
        if result is None:
            return None
        tf_results[tf] = result

    stage = determine_stage(tf_results)
    ticker = get_ticker(symbol)
    return {
        "symbol": symbol,
        "stage": stage,
        "tf_results": tf_results,
        "ticker": ticker,
    }


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
    print(f"[INFO] scanning {len(symbols)} active USDT perpetual symbols")

    candidates = []
    errors = 0

    for index, symbol in enumerate(symbols, start=1):
        try:
            candidate = scan_symbol(symbol)
            if candidate and candidate["stage"] >= 4:
                candidates.append(candidate)
        except Exception as exc:
            errors += 1
            print(f"[WARN] {symbol}: {exc}")

        if index % 50 == 0:
            print(f"[INFO] progress {index}/{len(symbols)}, candidates={len(candidates)}, errors={errors}")

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
