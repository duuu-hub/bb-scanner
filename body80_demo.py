from __future__ import annotations

import argparse
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import requests

from bitget_demo_lifecycle_test import (
    BitgetDemoClassic,
    MARGIN_COIN,
    PRODUCT_TYPE,
    q_nearest,
    q_up,
    wait_for_fill,
)
from market_data.contract_filters import active_symbols_from_contracts

BASE_URL = "https://api.bitget.com"
BAR_MS = 15 * 60 * 1000
CONFIG_PATH = Path("config/body80_demo_config.json")
PUBLIC_RATE = float(os.getenv("BODY80_PUBLIC_RPS", "18"))
PUBLIC_WORKERS = int(os.getenv("BODY80_PUBLIC_WORKERS", "32"))
REQUEST_TIMEOUT = int(os.getenv("BODY80_REQUEST_TIMEOUT", "12"))

_rate_lock = threading.Lock()
_last_start = 0.0
session = requests.Session()
session.headers.update({"User-Agent": "bb-scanner-body80-demo/1.0"})


def utc_iso_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


def now_ms() -> int:
    return int(time.time() * 1000)


def decimal_or_zero(v) -> Decimal:
    try:
        return Decimal(str(v or "0").replace(",", ""))
    except Exception:
        return Decimal("0")


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    hard = {
        "trading_mode": "DEMO",
        "live_trading_enabled": False,
        "direction": "LONG",
        "entry_order_type": "MARKET",
        "tp_pct": 3.0,
        "sl_pct": 5.0,
        "max_hold_minutes": 360,
        "position_size_pct": 30.0,
        "max_open_positions": 6,
        "max_total_exposure_pct": 200.0,
    }
    for k, expected in hard.items():
        if cfg.get(k) != expected:
            raise RuntimeError(f"BODY80 demo safety/config mismatch {k}: {cfg.get(k)!r} != {expected!r}")
    if str(cfg.get("experiment_id")) != "BODY80_90_UTC12_17_FORWARD_V1":
        raise RuntimeError("Unexpected BODY80 experiment_id")
    return cfg


def state_default() -> dict:
    return {
        "processed_signal_ids": [],
        "open_trades": [],
        "closed_trades": [],
        "counterfactual_signals": [],
        "last_boundary_ms": None,
    }


def load_state(cfg: dict) -> dict:
    p = Path(cfg["state_path"])
    if not p.exists():
        return state_default()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    s = state_default()
    if isinstance(d, dict):
        s.update(d)
    for k in ("processed_signal_ids", "open_trades", "closed_trades", "counterfactual_signals"):
        if not isinstance(s.get(k), list):
            s[k] = []
    return s


def save_state(cfg: dict, state: dict) -> None:
    state["processed_signal_ids"] = list(dict.fromkeys(state.get("processed_signal_ids", [])))[-10000:]
    state["closed_trades"] = state.get("closed_trades", [])[-2000:]
    state["counterfactual_signals"] = state.get("counterfactual_signals", [])[-5000:]
    p = Path(cfg["state_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def log_event(cfg: dict, event: dict) -> None:
    p = Path(cfg["log_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"logged_at_utc": datetime.now(timezone.utc).isoformat(), **event}
    with p.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    print("[BODY80] " + json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def telegram(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    r.raise_for_status()


def public_get(path: str, params: dict | None = None, retries: int = 3):
    global _last_start
    last = None
    for attempt in range(retries):
        try:
            with _rate_lock:
                t = time.monotonic()
                gap = 1.0 / PUBLIC_RATE
                wait = gap - (t - _last_start)
                if wait > 0:
                    time.sleep(wait)
                _last_start = time.monotonic()
            r = session.get(BASE_URL + path, params=params or {}, timeout=REQUEST_TIMEOUT)
            p = r.json()
            if not r.ok or str(p.get("code")) != "00000":
                raise RuntimeError(f"HTTP={r.status_code} code={p.get('code')} msg={p.get('msg')}")
            return p.get("data")
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(0.35 * (attempt + 1))
    raise RuntimeError(f"public GET {path} failed: {last}")


def full_public_universe() -> list[str]:
    rows = public_get("/api/v2/mix/market/contracts", {"productType": "usdt-futures"}) or []
    return active_symbols_from_contracts(rows)


def fetch_signal_snapshot(symbol: str, boundary_ms: int) -> dict | None:
    rows = public_get(
        "/api/v2/mix/market/candles",
        {
            "symbol": symbol,
            "productType": "usdt-futures",
            "granularity": "15m",
            "endTime": str(boundary_ms + BAR_MS - 1),
            "limit": "40",
        },
    ) or []
    parsed = []
    boundary_open = None
    for row in rows:
        try:
            ts = int(row[0])
            o, h, l, c = map(float, row[1:5])
        except Exception:
            continue
        if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)):
            continue
        if ts == boundary_ms:
            boundary_open = o
        if ts + BAR_MS <= boundary_ms:
            parsed.append((ts, o, h, l, c))
    parsed.sort()
    if len(parsed) < 34:
        return None
    x = parsed[-34:]
    expected_start = boundary_ms - 34 * BAR_MS
    if x[0][0] != expected_start:
        return None
    if any(x[i][0] - x[i - 1][0] != BAR_MS for i in range(1, len(x))):
        return None
    signal = x[-1]
    if signal[0] != boundary_ms - BAR_MS:
        return None
    cur_ret = signal[4] / x[-33][4] - 1.0
    prev_ret = x[-2][4] / x[-34][4] - 1.0
    rng = signal[2] - signal[3]
    body_ratio = max(signal[4] - signal[1], 0.0) / rng if rng > 0 else 0.0
    bullish = signal[4] > signal[1]
    return {
        "symbol": symbol,
        "signal_ts": int(signal[0]),
        "signal_open": float(signal[1]),
        "signal_high": float(signal[2]),
        "signal_low": float(signal[3]),
        "signal_close": float(signal[4]),
        "body_ratio": float(body_ratio if bullish else 0.0),
        "ret8_cur": float(cur_ret),
        "ret8_prev": float(prev_ret),
        "boundary_open": boundary_open,
    }


def scan(cfg: dict, boundary_ms: int) -> tuple[list[dict], dict]:
    symbols = full_public_universe()
    found = []
    errors = 0
    started = now_ms()
    with ThreadPoolExecutor(max_workers=PUBLIC_WORKERS) as pool:
        futs = {pool.submit(fetch_signal_snapshot, s, boundary_ms): s for s in symbols}
        for f in as_completed(futs):
            try:
                row = f.result()
                if row is not None:
                    found.append(row)
            except Exception as exc:
                errors += 1
                print(f"[BODY80][WARN] candle {futs[f]}: {exc}", flush=True)

    if len(found) < 20:
        raise RuntimeError(f"insufficient point-in-time universe: {len(found)} valid symbols")

    d = pd.DataFrame(found)
    d["pct_cur"] = d["ret8_cur"].rank(pct=True, method="average")
    d["pct_prev"] = d["ret8_prev"].rank(pct=True, method="average")
    current = (d["ret8_cur"] >= cfg["min_8h_return_pct"] / 100.0) & (
        d["pct_cur"] >= 1.0 - cfg["cross_section_top_tail"]
    )
    previous = (d["ret8_prev"] >= cfg["min_8h_return_pct"] / 100.0) & (
        d["pct_prev"] >= 1.0 - cfg["cross_section_top_tail"]
    )
    fresh = current & ~previous if cfg["fresh_transition_only"] else current

    sig_hour = int(datetime.fromtimestamp((boundary_ms - BAR_MS) / 1000, tz=timezone.utc).hour)
    q = d[
        fresh
        & (d["body_ratio"] >= cfg["body_ratio_min"])
        & (d["body_ratio"] < cfg["body_ratio_max_exclusive"])
    ].copy()
    if not (cfg["signal_candle_utc_hour_min"] <= sig_hour <= cfg["signal_candle_utc_hour_max"]):
        q = q.iloc[0:0].copy()

    q["strength8h_pct"] = q["ret8_cur"] * 100.0
    q["cross_section_pct"] = q["pct_cur"] * 100.0
    q = q.sort_values(
        ["strength8h_pct", "cross_section_pct", "symbol"],
        ascending=[False, False, True],
        kind="mergesort",
    )
    candidates = q.to_dict("records")
    meta = {
        "boundary_ms": boundary_ms,
        "signal_ts": boundary_ms - BAR_MS,
        "signal_hour_utc": sig_hour,
        "public_symbols": len(symbols),
        "valid_universe": len(found),
        "fetch_errors": errors,
        "scan_started_ms": started,
        "scan_finished_ms": now_ms(),
    }
    return candidates, meta


def ticker_map() -> dict[str, dict]:
    rows = public_get("/api/v2/mix/market/tickers", {"productType": "usdt-futures"}) or []
    out = {}
    for x in rows:
        try:
            s = str(x["symbol"]).upper()
            last = decimal_or_zero(x.get("lastPr") or x.get("markPrice"))
            bid = decimal_or_zero(x.get("bidPr") or x.get("bidPrice") or last)
            ask = decimal_or_zero(x.get("askPr") or x.get("askPrice") or last)
            if last > 0 and bid > 0 and ask > 0:
                out[s] = {"last": last, "bid": bid, "ask": ask}
        except Exception:
            continue
    return out


def spread_pct(bid: Decimal, ask: Decimal) -> float:
    if bid <= 0 or ask <= 0 or ask < bid:
        return 999.0
    mid = (bid + ask) / Decimal("2")
    return float((ask - bid) / mid * Decimal("100")) if mid > 0 else 999.0


def active_positions(client: BitgetDemoClassic) -> list[dict]:
    rows = client.private_get(
        "/api/v2/mix/position/all-position",
        {"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN},
    ) or []
    return [r for r in rows if decimal_or_zero(r.get("total")) > 0]


def equity(client: BitgetDemoClassic) -> Decimal:
    rows = client.private_get("/api/v2/mix/account/accounts", {"productType": PRODUCT_TYPE}) or []
    for r in rows:
        if str(r.get("marginCoin", "")).upper() == MARGIN_COIN:
            q = decimal_or_zero(r.get("accountEquity") or r.get("usdtEquity"))
            if q > 0:
                return q
    raise RuntimeError("positive demo equity unavailable")


def exposure_usdt(positions: list[dict]) -> Decimal:
    total = Decimal("0")
    for r in positions:
        qty = decimal_or_zero(r.get("total"))
        px = decimal_or_zero(r.get("markPrice") or r.get("openPriceAvg"))
        total += abs(qty * px)
    return total


def demo_contracts(client: BitgetDemoClassic) -> dict[str, dict]:
    rows = client.private_get("/api/v2/mix/market/contracts", {"productType": "usdt-futures"}) or []
    return {str(r.get("symbol", "")).upper(): r for r in rows if r.get("symbol")}


def order_size(contract: dict, price: Decimal, notional: Decimal) -> str:
    min_qty = decimal_or_zero(contract.get("minTradeNum"))
    step = decimal_or_zero(contract.get("sizeMultiplier"))
    min_usdt = decimal_or_zero(contract.get("minTradeUSDT"))
    place = int(contract.get("volumePlace") or 8)
    target = max(notional, min_usdt)
    qty = max(min_qty, target / price)
    if step > 0:
        qty = q_up(qty, step)
    return f"{qty:.{place}f}"


def norm_price(contract: dict, price: Decimal) -> str:
    place = int(contract.get("pricePlace") or 8)
    end = decimal_or_zero(contract.get("priceEndStep") or "1")
    step = Decimal(1).scaleb(-place) * end
    q = q_nearest(price, step)
    return f"{q:.{place}f}"


def matching_position(positions: list[dict], symbol: str) -> dict | None:
    for r in positions:
        if str(r.get("symbol", "")).upper() == symbol.upper() and decimal_or_zero(r.get("total")) > 0:
            return r
    return None


def close_trade(client: BitgetDemoClassic, cfg: dict, state: dict, trade: dict, reason: str, ts_ms: int) -> None:
    positions = active_positions(client)
    pos = matching_position(positions, trade["symbol"])
    if not pos:
        trade["closed_at_ms"] = ts_ms
        trade["close_reason"] = "EXCHANGE_POSITION_GONE"
        state["closed_trades"].append(trade)
        log_event(cfg, {"event": "CLOSE_OBSERVED", "signal_id": trade["signal_id"], "symbol": trade["symbol"], "reason": trade["close_reason"]})
        return

    qty = str(trade["qty"])
    placed = client.private_post(
        "/api/v2/mix/order/place-order",
        {
            "symbol": trade["symbol"],
            "productType": PRODUCT_TYPE,
            "marginMode": "crossed",
            "marginCoin": MARGIN_COIN,
            "size": qty,
            "side": "buy",
            "tradeSide": "close",
            "orderType": "market",
            "clientOid": f"b80close_{int(time.time())}_{trade['symbol']}"[:32],
        },
    )
    oid = str(placed.get("orderId") or "")
    detail = wait_for_fill(client, trade["symbol"], oid) if oid else {}
    exit_px = decimal_or_zero(detail.get("priceAvg"))
    entry_px = decimal_or_zero(trade.get("entry_avg_price"))
    ret = float((exit_px / entry_px - Decimal("1")) * Decimal("100")) if entry_px > 0 and exit_px > 0 else None
    trade.update({"closed_at_ms": ts_ms, "close_reason": reason, "exit_avg_price": str(exit_px) if exit_px > 0 else None, "return_pct": ret})
    state["closed_trades"].append(trade)
    log_event(cfg, {"event": "CLOSE", "signal_id": trade["signal_id"], "symbol": trade["symbol"], "reason": reason, "return_pct": ret})
    try:
        telegram(f"⏱ BODY80 Demo 종료\n{trade['symbol']}\nreason={reason} return={ret}")
    except Exception as exc:
        print(f"[BODY80][WARN] telegram close: {exc}", flush=True)


def manage_trades(client: BitgetDemoClassic, cfg: dict, state: dict, ts_ms: int) -> None:
    positions = active_positions(client)
    kept = []
    for trade in state.get("open_trades", []):
        pos = matching_position(positions, trade["symbol"])
        if not pos:
            trade["closed_at_ms"] = ts_ms
            trade["close_reason"] = "EXCHANGE_POSITION_GONE"
            state["closed_trades"].append(trade)
            log_event(cfg, {"event": "EXCHANGE_POSITION_GONE", "signal_id": trade["signal_id"], "symbol": trade["symbol"]})
            continue
        if ts_ms >= int(trade["deadline_ms"]):
            close_trade(client, cfg, state, trade, "MAX_HOLD_6H", ts_ms)
            continue
        kept.append(trade)
    state["open_trades"] = kept


def place_candidate(
    client: BitgetDemoClassic,
    cfg: dict,
    state: dict,
    candidate: dict,
    boundary_ms: int,
    tickers: dict,
    contracts: dict,
) -> None:
    symbol = str(candidate["symbol"]).upper()
    signal_id = f"{cfg['experiment_id']}:{symbol}:{int(candidate['signal_ts'])}"
    if signal_id in set(state.get("processed_signal_ids", [])):
        return

    observed_ms = now_ms()
    age_ms = observed_ms - boundary_ms
    base_event = {
        "signal_id": signal_id,
        "strategy": cfg["experiment_id"],
        "symbol": symbol,
        "signal_ts": int(candidate["signal_ts"]),
        "entry_boundary_ms": boundary_ms,
        "signal_age_ms": age_ms,
        "strength8h_pct": float(candidate["strength8h_pct"]),
        "cross_section_pct": float(candidate["cross_section_pct"]),
        "body_ratio": float(candidate["body_ratio"]),
        "boundary_open": candidate.get("boundary_open"),
    }

    def reject(reason: str, **extra):
        row = {**base_event, "event": "COUNTERFACTUAL", "reason": reason, **extra}
        state["counterfactual_signals"].append(row)
        state["processed_signal_ids"].append(signal_id)
        log_event(cfg, row)

    if age_ms < 0 or age_ms > int(cfg["signal_ttl_seconds"]) * 1000:
        reject("SIGNAL_TOO_LATE")
        return
    tick = tickers.get(symbol)
    if not tick:
        reject("TICKER_UNAVAILABLE")
        return
    sp = spread_pct(tick["bid"], tick["ask"])
    if sp > float(cfg["max_spread_pct"]):
        reject("SPREAD_TOO_WIDE", spread_pct=sp)
        return
    contract = contracts.get(symbol)
    if contract is None:
        reject("DEMO_SYMBOL_UNSUPPORTED", spread_pct=sp)
        return

    positions = active_positions(client)
    if matching_position(positions, symbol):
        reject("SAME_SYMBOL_POSITION_EXISTS", spread_pct=sp)
        return

    eq = equity(client)
    exp = exposure_usdt(positions)
    planned = eq * Decimal(str(cfg["position_size_pct"] / 100.0))
    if len(positions) >= int(cfg["max_open_positions"]):
        reject("MAX_OPEN_POSITIONS", spread_pct=sp)
        return
    max_exp = eq * Decimal(str(cfg["max_total_exposure_pct"] / 100.0))
    if exp + planned > max_exp:
        reject("MAX_TOTAL_EXPOSURE", spread_pct=sp)
        return

    ref = tick["ask"]
    qty = order_size(contract, ref, planned)
    tp = norm_price(contract, ref * (Decimal("1") + Decimal(str(cfg["tp_pct"])) / Decimal("100")))
    sl = norm_price(contract, ref * (Decimal("1") - Decimal(str(cfg["sl_pct"])) / Decimal("100")))

    payload = {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "marginMode": "crossed",
        "marginCoin": MARGIN_COIN,
        "size": qty,
        "side": "buy",
        "tradeSide": "open",
        "orderType": "market",
        "clientOid": f"b80_{int(time.time())}_{symbol}"[:32],
        "presetStopSurplusPrice": tp,
        "presetStopLossPrice": sl,
    }
    placed = client.private_post("/api/v2/mix/order/place-order", payload)
    oid = str(placed.get("orderId") or "")
    if not oid:
        reject("ORDER_NO_ID", spread_pct=sp)
        return
    detail = wait_for_fill(client, symbol, oid)
    avg = decimal_or_zero(detail.get("priceAvg"))
    if avg <= 0:
        raise RuntimeError(f"{symbol} demo market fill missing priceAvg")

    fill_ms = int(detail.get("uTime") or detail.get("cTime") or now_ms())
    boundary_open = decimal_or_zero(candidate.get("boundary_open"))
    slip_open = float((avg / boundary_open - Decimal("1")) * Decimal("100")) if boundary_open > 0 else None
    slip_detect = float((avg / ref - Decimal("1")) * Decimal("100")) if ref > 0 else None
    trade = {
        **base_event,
        "side": "LONG",
        "qty": qty,
        "entry_order_id": oid,
        "entry_avg_price": str(avg),
        "detected_ask": str(ref),
        "tp": tp,
        "sl": sl,
        "spread_pct": sp,
        "fill_ms": fill_ms,
        "fill_delay_ms": fill_ms - boundary_ms,
        "slippage_vs_boundary_open_pct": slip_open,
        "slippage_vs_detected_ask_pct": slip_detect,
        "deadline_ms": boundary_ms + int(cfg["max_hold_minutes"]) * 60_000,
        "planned_notional": str(planned),
    }
    state["open_trades"].append(trade)
    state["processed_signal_ids"].append(signal_id)
    log_event(cfg, {"event": "DEMO_MARKET_FILL", **trade})
    try:
        telegram(
            f"🟢 BODY80 Demo LONG {symbol}\n"
            f"진입 {avg}\nTP {tp}\nSL {sl}\nTime 6H"
        )
    except Exception as exc:
        print(f"[BODY80][WARN] telegram fill: {exc}", flush=True)


def run_boundary(boundary_ms: int, manage_only: bool = False) -> dict:
    cfg = load_config()
    state = load_state(cfg)
    client = BitgetDemoClassic()
    manage_trades(client, cfg, state, now_ms())

    result = {"boundary_ms": boundary_ms, "managed": True, "candidates": 0}
    if not manage_only:
        candidates, meta = scan(cfg, boundary_ms)
        result.update(meta)
        result["candidates"] = len(candidates)
        log_event(cfg, {"event": "SCAN_COMPLETE", **meta, "candidate_count": len(candidates)})

        ticks = ticker_map() if candidates else {}
        contracts = demo_contracts(client) if candidates else {}
        for c in candidates:
            place_candidate(client, cfg, state, c, boundary_ms, ticks, contracts)

    state["last_boundary_ms"] = boundary_ms
    save_state(cfg, state)
    result["open_trades"] = len(state["open_trades"])
    result["closed_trades"] = len(state["closed_trades"])
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boundary-ms", type=int, required=True)
    ap.add_argument("--manage-only", action="store_true")
    a = ap.parse_args()
    boundary = int(a.boundary_ms)
    if boundary % BAR_MS != 0:
        raise SystemExit("--boundary-ms must be an exact 15m UTC boundary")
    result = run_boundary(boundary, manage_only=a.manage_only)
    print("BODY80_DEMO_RESULT " + json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
