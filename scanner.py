import csv
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

from market_data.contract_filters import active_symbols_from_contracts

BASE_URL = "https://api.bitget.com"
PRODUCT_TYPE = "usdt-futures"
BB_PERIOD = 20
BB_STD = 2.0
STATE_PATH = Path("state.json")
PAPER_LOG_PATH = Path("paper_signals.csv")
SCAN_RUNTIME_PATH = Path("scan_runtime.json")
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

# Candidate 1 live-paper strategy (six sub-strategies).
# Success rates are stress-inclusive +1m TEST30 results with
# LSK/TUT/LAB/ALLO included. Priority follows TRAIN70 profit factor so
# recommendation order is not chosen from the test set.
STRATEGY_RULES = {
    "L1": {
        "name": "모멘텀 LONG",
        "direction": "LONG",
        "priority": 3,
        "horizon_min": 720,
        "tp_pct": 10.0,
        "sl_pct": 5.0,
        "rr": 2.0,
        "bt_win_rate": 53.3,
        "bt_n": 45,
        "entry_low_pct": -0.82,
        "entry_high_pct": 0.95,
    },
    "L2": {
        "name": "폭발추세 LONG",
        "direction": "LONG",
        "priority": 1,
        "horizon_min": 60,
        "tp_pct": 10.0,
        "sl_pct": 2.5,
        "rr": 4.0,
        "bt_win_rate": 42.1,
        "bt_n": 19,
        "entry_low_pct": -0.69,
        "entry_high_pct": 1.81,
    },
    "L3": {
        "name": "4H 지연 LONG",
        "direction": "LONG",
        "priority": 2,
        "horizon_min": 720,
        "tp_pct": 10.0,
        "sl_pct": 4.0,
        "rr": 2.5,
        "bt_win_rate": 46.7,
        "bt_n": 15,
        "entry_low_pct": -0.43,
        "entry_high_pct": 0.43,
    },
    "S1": {
        "name": "7/7 극단반전 SHORT",
        "direction": "SHORT",
        "priority": 5,
        "horizon_min": 720,
        "tp_pct": 10.0,
        "sl_pct": 4.0,
        "rr": 2.5,
        "bt_win_rate": 36.0,
        "bt_n": 25,
        "entry_low_pct": -0.98,
        "entry_high_pct": 1.05,
    },
    "S2": {
        "name": "15M 지연 SHORT",
        "direction": "SHORT",
        "priority": 6,
        "horizon_min": 240,
        "tp_pct": 10.0,
        "sl_pct": 4.0,
        "rr": 2.5,
        "bt_win_rate": 50.0,
        "bt_n": 34,
        "entry_low_pct": -0.42,
        "entry_high_pct": 0.46,
    },
    "S3": {
        "name": "8회 지속 SHORT",
        "direction": "SHORT",
        "priority": 4,
        "horizon_min": 60,
        "tp_pct": 10.0,
        "sl_pct": 5.0,
        "rr": 2.0,
        "bt_win_rate": 47.8,
        "bt_n": 23,
        "entry_low_pct": -0.13,
        "entry_high_pct": 0.35,
    },
}

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


def demo_universe_only() -> bool:
    return os.getenv("LONG3_DEMO_UNIVERSE_ONLY", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def get_symbols():
    """Return the symbol universe used by this scanner run.

    Normal/manual research scans use the live crypto USDT-perpetual catalog.
    LONG3 Demo forward runs set LONG3_DEMO_UNIVERSE_ONLY=1 and intentionally
    scan only contracts that the authenticated Bitget Demo environment exposes.

    This is both a correctness and latency control: the Demo account cannot
    place orders on symbols absent from its contract catalog, and scanning
    those symbols only delays executable signals.
    """
    if demo_universe_only():
        from bitget_demo_lifecycle_test import BitgetDemoClassic

        demo = BitgetDemoClassic()
        data = demo.private_get(
            "/api/v2/mix/market/contracts",
            {"productType": PRODUCT_TYPE},
        ) or []
        symbols = active_symbols_from_contracts(data)
        if not symbols:
            raise RuntimeError(
                "Bitget Demo contract universe is empty; refusing to fall back "
                "to the live universe for Demo forward execution."
            )
        return symbols

    data = api_get(
        "/api/v2/mix/market/contracts",
        {"productType": PRODUCT_TYPE},
    ) or []
    return active_symbols_from_contracts(data)


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
            open_price = float(row[1])
            close = float(row[4])
            if (
                math.isfinite(open_price)
                and open_price > 0
                and math.isfinite(close)
                and close > 0
            ):
                parsed.append((ts, open_price, close))
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
    live_window = [x[2] for x in completed[-(BB_PERIOD - 1):]] + [live_price]
    basis = sum(live_window) / BB_PERIOD
    std = statistics.pstdev(live_window)
    upper = basis + BB_STD * std
    lower = basis - BB_STD * std
    distance_pct = (live_price / upper - 1.0) * 100.0 if upper > 0 else 0.0

    completed_close = completed[-1][2]
    completed_above = False
    completed_upper = None
    if len(completed) >= BB_PERIOD:
        completed20 = [x[2] for x in completed[-BB_PERIOD:]]
        completed_basis = sum(completed20) / BB_PERIOD
        completed_std = statistics.pstdev(completed20)
        completed_upper = completed_basis + BB_STD * completed_std
        completed_above = completed_close > completed_upper

    ret_1h_pct = None
    ret_4h_pct = None
    if granularity == "15m":
        # Backtest momentum uses the 15m boundary price versus the boundary
        # price 4/16 candles earlier. In live mode the freshest ticker is used
        # as the current price and the historical candle OPEN anchors the
        # earlier 15m boundary.
        current_candle_ts = (
            parsed[-1][0]
            if has_current_row
            else (now_ms // duration_ms) * duration_ms
        )
        open_by_ts = {ts: open_price for ts, open_price, _ in parsed}
        base_1h = open_by_ts.get(current_candle_ts - 4 * duration_ms)
        base_4h = open_by_ts.get(current_candle_ts - 16 * duration_ms)
        if base_1h and base_1h > 0:
            ret_1h_pct = (live_price / base_1h - 1.0) * 100.0
        if base_4h and base_4h > 0:
            ret_4h_pct = (live_price / base_4h - 1.0) * 100.0

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
        "ret_1h_pct": ret_1h_pct,
        "ret_4h_pct": ret_4h_pct,
    }


def fetch_boundary_price(symbol, boundary_ms):
    """Return the exact 15m-boundary trade price from the current 15m candle open."""
    try:
        rows = api_get(
            "/api/v2/mix/market/candles",
            {
                "symbol": symbol,
                "productType": PRODUCT_TYPE,
                "granularity": "15m",
                "startTime": str(int(boundary_ms)),
                "endTime": str(int(boundary_ms) + 15 * 60 * 1000 - 1),
                "limit": 2,
            },
        ) or []
        for row in rows:
            try:
                if int(row[0]) == int(boundary_ms):
                    price = float(row[1])
                    if math.isfinite(price) and price > 0:
                        return symbol, price
            except (ValueError, TypeError, IndexError):
                continue
    except Exception as exc:
        print(f"[WARN] boundary snapshot {symbol}: {exc}")
    return symbol, None


def get_boundary_prices(symbols, boundary_ms):
    """Fetch exact price snapshots for one common 15m boundary.

    This deliberately costs one 15m-candle lookup per symbol so a delayed
    GitHub runner evaluates the same boundary state instead of the later live
    ticker state.
    """
    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [
            pool.submit(fetch_boundary_price, symbol, boundary_ms)
            for symbol in symbols
        ]
        for future in as_completed(futures):
            symbol, price = future.result()
            if price is not None:
                out[symbol] = price
    return out


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


def fmt_horizon(minutes):
    minutes = int(minutes)
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours}H"
    return f"{minutes}M"


def stage_label(stage):
    return {
        7: "🚨 전봉 돌파 7/7",
        6: "🔥 과열후보 6/7",
        5: "🟠 근접후보 5/7",
        4: "🟡 관찰후보 4/7",
    }.get(stage, f"{stage}/7")



def strategy_matches(candidate):
    """Return Candidate-1 six-strategy matches in recommendation order."""
    tf_results = candidate["tf_results"]
    score = score_candidate(tf_results)
    r15 = tf_results.get("15M") or {}
    ret_1h = r15.get("ret_1h_pct")
    ret_4h = r15.get("ret_4h_pct")

    matches = []

    # L1: rank >= 6 and +10% or more over the latest 1h.
    if score["rank"] >= 6 and ret_1h is not None and ret_1h >= 10.0:
        matches.append("L1")

    # L2: rank >= 6 and +30% or more over the latest 4h.
    if score["rank"] >= 6 and ret_4h is not None and ret_4h >= 30.0:
        matches.append("L2")

    missing = [
        tf for tf, _ in TIMEFRAMES
        if not tf_results.get(tf, {}).get("above")
    ]

    # L3: exactly 6/7, only 4H is missing.
    if score["exact_count"] == 6 and missing == ["4H"]:
        matches.append("L3")

    # S1: original Candidate-1 rule: 7/7 plus 4h momentum >= +10%.
    # No +35% upper gate here; that gate belonged to Candidate 2.
    if (
        score["rank"] == 7
        and ret_4h is not None
        and ret_4h >= 10.0
    ):
        matches.append("S1")

    # S2: exactly 6/7, only 15M missing, and within 1% of its upper BB.
    d15 = r15.get("distance_pct")
    if (
        score["exact_count"] == 6
        and missing == ["15M"]
        and d15 is not None
        and d15 >= -1.0
    ):
        matches.append("S2")

    # S3: Candidate has persisted for exactly 8 consecutive scans.
    if score["rank"] >= 4 and int(candidate.get("streak", 0)) == 8:
        matches.append("S3")

    return sorted(matches, key=lambda code: STRATEGY_RULES[code]["priority"])


def primary_strategy(candidate):
    matches = candidate.get("strategies") or strategy_matches(candidate)
    return matches[0] if matches else None


def strategy_sort_key(candidate):
    code = primary_strategy(candidate)
    priority = STRATEGY_RULES[code]["priority"] if code else 99
    return (
        priority,
        -len(candidate.get("strategies", [])),
        -int(candidate.get("streak", 1)),
        -int(candidate.get("stage", 0)),
        candidate["symbol"],
    )


def trade_levels(candidate, strategy_code):
    cfg = STRATEGY_RULES[strategy_code]
    price = candidate["ticker"].get("last_price")
    if not price:
        return None

    entry_low = price * (1.0 + cfg["entry_low_pct"] / 100.0)
    entry_high = price * (1.0 + cfg["entry_high_pct"] / 100.0)

    if cfg["direction"] == "LONG":
        tp = price * (1.0 + cfg["tp_pct"] / 100.0)
        sl = price * (1.0 - cfg["sl_pct"] / 100.0)
    else:
        tp = price * (1.0 - cfg["tp_pct"] / 100.0)
        sl = price * (1.0 + cfg["sl_pct"] / 100.0)

    return {
        "entry_low": min(entry_low, entry_high),
        "entry_high": max(entry_low, entry_high),
        "tp": tp,
        "sl": sl,
    }


def append_paper_signal(candidate, reason, strategy_code):
    """Persist actual NEW Candidate-1 paper entries only."""
    if not strategy_code:
        return False
    if not reason.startswith("신규"):
        return False

    cfg = STRATEGY_RULES[strategy_code]
    levels = trade_levels(candidate, strategy_code)
    price = candidate["ticker"].get("last_price")
    r15 = candidate["tf_results"].get("15M") or {}
    score = score_candidate(candidate["tf_results"])

    row = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": candidate["symbol"],
        "strategy": strategy_code,
        "direction": cfg["direction"],
        "reason": reason,
        "streak": int(candidate.get("streak", 1)),
        "stage": int(candidate.get("stage", 0)),
        "exact_count": int(score["exact_count"]),
        "entry_price": price,
        "entry_low": levels["entry_low"] if levels else None,
        "entry_high": levels["entry_high"] if levels else None,
        "tp_price": levels["tp"] if levels else None,
        "sl_price": levels["sl"] if levels else None,
        "tp_pct": cfg["tp_pct"],
        "sl_pct": cfg["sl_pct"],
        "rr": cfg["rr"],
        "bt_win_rate": cfg["bt_win_rate"],
        "bt_n": cfg["bt_n"],
        "ret_1h_pct": r15.get("ret_1h_pct"),
        "ret_4h_pct": r15.get("ret_4h_pct"),
        "all_matches": "+".join(candidate.get("strategies", [])),
    }

    fieldnames = list(row.keys())
    write_header = not PAPER_LOG_PATH.exists()
    with PAPER_LOG_PATH.open("a", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    return True


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


def build_alert(candidate, reason, strategy_code=None):
    symbol = candidate["symbol"]
    stage = candidate["stage"]
    tf_results = candidate["tf_results"]
    ticker = candidate["ticker"]
    score = score_candidate(tf_results)

    exact_kr = [TF_KR[x] for x in score["exact"]]
    near_kr = [TF_KR[x] for x in score["near"]]
    far_kr = [TF_KR[x] for x in score["far"]]

    streak = int(candidate.get("streak", 1))
    strategy_code = strategy_code or primary_strategy(candidate)
    cfg = STRATEGY_RULES[strategy_code]
    levels = trade_levels(candidate, strategy_code)
    matches = candidate.get("strategies", [strategy_code])
    price = ticker.get("last_price")
    prev_scan_price = candidate.get("previous_scan_price")

    rank_emoji = {
        1: "🥇",
        2: "🥈",
        3: "🥉",
        4: "4️⃣",
        5: "5️⃣",
        6: "6️⃣",
    }.get(cfg["priority"], "🎯")

    lines = [
        f"🪙 {symbol}" + (f"  🔁 {streak}회 연속" if streak >= 2 else ""),
        f"{rank_emoji} 추천 {cfg['priority']}순위 · {cfg['direction']} · {strategy_code} {cfg['name']}",
        f"상태 {reason}",
        f"경계신호가 {fmt_price(price)}  |  24H {fmt_pct(ticker.get('change24h_pct'))}",
    ]

    if streak >= 2 and prev_scan_price and price:
        scan_move = (price / float(prev_scan_price) - 1.0) * 100.0
        lines.append(
            f"지난스캔 {fmt_price(float(prev_scan_price))} → "
            f"현재 {fmt_price(price)}  ({scan_move:+.2f}%)"
        )

    if levels:
        lines += [
            "────────────",
            f"진입권장 {fmt_price(levels['entry_low'])} ~ {fmt_price(levels['entry_high'])}",
            f"TP {fmt_price(levels['tp'])} ({cfg['tp_pct']:+.1f}%)"
            f"  |  SL {fmt_price(levels['sl'])} (-{cfg['sl_pct']:.1f}%)",
            f"손익비 1:{cfg['rr']:.1f}  |  BT성공률 {cfg['bt_win_rate']:.1f}% (검증 {cfg['bt_n']}회)",
            f"⏱ TIME LIMIT {fmt_horizon(cfg['horizon_min'])} · TP/SL 미도달 시 시간종료",
            "기준 비중 시드 30%",
        ]

    same_direction = [
        code for code in matches
        if code != strategy_code
        and STRATEGY_RULES[code]["direction"] == cfg["direction"]
    ]
    opposite_direction = [
        code for code in matches
        if STRATEGY_RULES[code]["direction"] != cfg["direction"]
    ]
    if same_direction:
        lines.append("동방향 동시신호 " + " + ".join(same_direction))
    if opposite_direction:
        lines.append(
            "⚠️ 반대방향 동시신호 " + " + ".join(opposite_direction)
            + " · 후보1은 별도 신호로 유지"
        )

    r15 = tf_results.get("15M") or {}
    ret_1h = r15.get("ret_1h_pct")
    ret_4h = r15.get("ret_4h_pct")
    lines += [
        "────────────",
        f"{stage_label(stage)}",
        f"최근 1H {fmt_pct(ret_1h)}  |  최근 4H {fmt_pct(ret_4h)}",
        f"✅ 돌파 {len(exact_kr)}/7" + (f" · {', '.join(exact_kr)}" if exact_kr else ""),
    ]

    if near_kr:
        lines.append(f"🟨 근접 · {', '.join(near_kr)}")
    if far_kr:
        lines.append(f"❌ 미달 · {', '.join(far_kr)}")

    lines.append("BB상단 대비")
    for tf, _ in TIMEFRAMES:
        r = tf_results.get(tf)
        if not r:
            continue
        d = r.get("distance_pct")
        if r.get("above"):
            mark = "✅"
        elif d is not None and d >= -NEAR_BB_PCT:
            mark = "🟨"
        else:
            mark = "❌"
        lines.append(f"{TF_KR[tf]} {mark} {fmt_pct(d)}")

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


def alert_events(candidate, previous):
    """Return Candidate-1 Telegram events.

    Every newly-active sub-strategy is emitted separately, matching the
    six-strategy backtest where duplicate/opposite-direction signals can coexist.
    Status-only repeats emit one update for the current primary strategy.
    """
    current_codes = candidate.get("strategies", [])
    if not current_codes:
        return []

    stage = candidate["stage"]
    price = candidate["ticker"].get("last_price")

    if previous is None:
        return [(code, "신규 진입신호") for code in current_codes]

    prev_codes = set(previous.get("active_strategies", []))
    new_codes = [code for code in current_codes if code not in prev_codes]
    if new_codes:
        return [(code, f"신규 전략 {code}") for code in new_codes]

    primary = primary_strategy(candidate)
    if not primary:
        return []

    prev_stage = int(previous.get("stage", 0))
    if stage > prev_stage:
        return [(primary, f"단계 상승 {prev_stage}/7 → {stage}/7")]

    if int(candidate.get("streak", 1)) == 2:
        return [(primary, "2회 연속 확인 · 추가진입 아님")]

    last_alert_price = previous.get("last_alert_price")
    if (
        stage == prev_stage
        and price
        and last_alert_price
        and float(last_alert_price) > 0
    ):
        signed_move = (price / float(last_alert_price) - 1.0) * 100.0
        if abs(signed_move) >= RE_ALERT_PRICE_MOVE_PCT:
            return [
                (
                    primary,
                    f"이전 알림가 대비 {signed_move:+.2f}% · 상태갱신",
                )
            ]

    return []


def wait_for_quarter_boundary():
    """Scheduled runs wake ~10m early and align the scan to :00/:15/:30/:45.

    When GitHub starts the job up to 4m after the intended boundary, scan
    immediately. If it starts in the pre-boundary phase (:05..:14 modulo 15),
    keep the runner alive and wait for the next exact quarter-hour.
    """
    if os.getenv("GITHUB_EVENT_NAME", "") != "schedule":
        return

    now = datetime.now(timezone.utc)
    phase_min = now.minute % 15

    # :00..:04 means the early trigger was delayed across the intended
    # boundary; scanning now is better than waiting another 15 minutes.
    if phase_min < 5:
        print(
            f"[SCHEDULE] boundary already passed by "
            f"{phase_min:02d}:{now.second:02d}; scanning immediately"
        )
        return

    seconds_into_phase = phase_min * 60 + now.second + now.microsecond / 1_000_000
    wait_sec = 15 * 60 - seconds_into_phase
    if wait_sec <= 0:
        return

    target_epoch = time.time() + wait_sec
    target = datetime.fromtimestamp(target_epoch, tz=timezone.utc)
    print(
        f"[SCHEDULE] early wake at {now.isoformat()} -> "
        f"waiting {wait_sec:.1f}s for {target.strftime('%H:%M:%S')} UTC"
    )
    time.sleep(wait_sec)
    print(f"[SCHEDULE] quarter boundary reached: {datetime.now(timezone.utc).isoformat()}")


def main():
    wait_for_quarter_boundary()
    scan_started_dt = datetime.now(timezone.utc)
    scan_started_at = scan_started_dt.isoformat()
    scan_started_ms = int(scan_started_dt.timestamp() * 1000)
    signal_boundary_ms = (scan_started_ms // (15 * 60 * 1000)) * (15 * 60 * 1000)
    boundary_lag_ms = scan_started_ms - signal_boundary_ms

    # Capture the full-market ticker immediately when the runner reaches the
    # boundary. This is the lowest-latency path. Only delayed starts need the
    # more expensive per-symbol historical 15m-open reconstruction.
    ticker_map = get_all_tickers()
    snapshot_source = "bulk_ticker_near_boundary"
    symbols = get_symbols()
    universe_source = (
        "bitget_demo_contracts" if demo_universe_only()
        else "bitget_live_crypto_contracts"
    )
    if boundary_lag_ms > 5_000:
        boundary_prices = get_boundary_prices(symbols, signal_boundary_ms)
        snapshot_source = "15m_candle_open_reconstructed"
        for symbol in list(ticker_map):
            boundary_price = boundary_prices.get(symbol)
            if boundary_price is None:
                ticker_map.pop(symbol, None)
                continue
            ticker_map[symbol]["live_last_price"] = ticker_map[symbol].get("last_price")
            ticker_map[symbol]["last_price"] = boundary_price

    for symbol in ticker_map:
        ticker_map[symbol]["signal_boundary_ms"] = signal_boundary_ms

    SCAN_RUNTIME_PATH.write_text(
        json.dumps(
            {
                "scan_started_at": scan_started_at,
                "signal_boundary_ms": signal_boundary_ms,
                "signal_boundary_utc": datetime.fromtimestamp(
                    signal_boundary_ms / 1000, tz=timezone.utc
                ).isoformat(),
                "boundary_lag_ms": boundary_lag_ms,
                "price_snapshot_source": snapshot_source,
                "symbol_universe_source": universe_source,
                "symbol_count": len(symbols),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    state = load_state()
    previous_symbols = state.get("symbols", {})
    new_symbols_state = {}

    print(
        f"[INFO] scanning {len(symbols)} active USDT perpetual symbols "
        f"universe={universe_source} "
        f"with {len(ticker_map)} boundary snapshots "
        f"at {datetime.fromtimestamp(signal_boundary_ms / 1000, tz=timezone.utc).isoformat()} "
        f"source={snapshot_source} lag={boundary_lag_ms}ms"
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

    for candidate in candidates:
        previous = previous_symbols.get(candidate["symbol"])
        candidate["streak"] = (
            int(previous.get("streak", 0)) + 1
            if previous is not None
            else 1
        )
        candidate["previous_scan_price"] = (
            previous.get("last_price") if previous else None
        )
        candidate["strategies"] = strategy_matches(candidate)

    # Actionable Candidate-1 signals first, ordered by TRAIN70-PF priority.
    # Non-actionable BB candidates stay in state only.
    candidates.sort(key=strategy_sort_key)

    alerts_sent = 0
    telegram_messages_sent = 0
    pending_alerts = []

    for candidate in candidates:
        symbol = candidate["symbol"]
        previous = previous_symbols.get(symbol)
        events = alert_events(candidate, previous)

        price = candidate["ticker"].get("last_price")
        prev_alert_price = previous.get("last_alert_price") if previous else None

        for strategy_code, reason in events:
            message = build_alert(candidate, reason, strategy_code)
            print("\n" + message + "\n")
            pending_alerts.append(message)
            try:
                append_paper_signal(candidate, reason, strategy_code)
            except Exception as exc:
                print(f"[WARN] paper log {symbol}/{strategy_code}: {exc}")

        if events and price:
            prev_alert_price = price

        new_symbols_state[symbol] = {
            "stage": candidate["stage"],
            "streak": int(candidate.get("streak", 1)),
            "last_price": price,
            "last_alert_price": prev_alert_price,
            "active_strategies": candidate.get("strategies", []),
            "primary_strategy": primary_strategy(candidate),
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
        actionable_count = sum(
            1 for candidate in candidates if candidate.get("strategies")
        )
        summary = (
            "✅ BB 스캔 완료\n"
            f"전체: {len(symbols)}종목 | BB후보: {len(candidates)}종목 | "
            f"후보1 신호: {actionable_count}종목\n"
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
