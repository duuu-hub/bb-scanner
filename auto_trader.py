from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests

from bitget_demo_lifecycle_test import (
    BitgetDemoClassic,
    MARGIN_COIN,
    PRODUCT_TYPE,
    q_nearest,
    q_up,
    wait_for_fill,
)
from signal_io import load_signal_jsonl
from trade_guard import Candle, GuardConfig, Signal, validate_signal
from trade_state import load_trading_state, save_trading_state


CONFIG_PATH = Path("config/trading_config.json")
SIGNALS_PATH = Path("signals/pending.jsonl")
STATE_PATH = Path("state/trading_state.json")
EXECUTION_LOG = Path("logs/executions.jsonl")


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ms() -> int:
    return int(time.time() * 1000)


def log_event(event: dict) -> None:
    EXECUTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    event = {"timestamp_utc": utc_iso(), **event}
    with EXECUTION_LOG.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    print("[EVENT] " + json.dumps(event, ensure_ascii=False, sort_keys=True))


def telegram_send(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("[INFO] Telegram secrets not configured; notification skipped.")
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    r.raise_for_status()
    return True


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if str(cfg.get("trading_mode", "")).upper() != "DEMO":
        raise RuntimeError("Only DEMO mode is permitted in this branch.")
    if bool(cfg.get("live_trading_enabled")):
        raise RuntimeError("live_trading_enabled must remain false.")
    active = str(cfg.get("active_strategy", "OFF")).upper()
    allowed = {str(x).upper() for x in cfg.get("allowed_strategies", [])}
    if active != "OFF" and active not in allowed:
        raise RuntimeError(f"active_strategy={active} is not in allowed_strategies.")
    return cfg


def decimal_or_zero(value) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(",", ""))
    except Exception:
        return Decimal("0")


def account_equity(client: BitgetDemoClassic) -> Decimal:
    rows = client.private_get(
        "/api/v2/mix/account/accounts",
        {"productType": PRODUCT_TYPE},
    ) or []
    for row in rows:
        if str(row.get("marginCoin", "")).upper() == MARGIN_COIN:
            equity = decimal_or_zero(row.get("accountEquity") or row.get("usdtEquity"))
            if equity > 0:
                return equity
    raise RuntimeError("Could not obtain positive USDT futures account equity.")


def all_positions(client: BitgetDemoClassic) -> list[dict]:
    return client.private_get(
        "/api/v2/mix/position/all-position",
        {"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN},
    ) or []


def active_positions(rows: list[dict]) -> list[dict]:
    return [r for r in rows if decimal_or_zero(r.get("total")) > 0]


def position_exposure_usdt(rows: list[dict]) -> Decimal:
    total = Decimal("0")
    for row in active_positions(rows):
        qty = decimal_or_zero(row.get("total"))
        mark = decimal_or_zero(row.get("markPrice") or row.get("openPriceAvg"))
        total += abs(qty * mark)
    return total


def matching_position(rows: list[dict], symbol: str, side: str) -> dict | None:
    hold = side.lower()
    for row in active_positions(rows):
        if str(row.get("symbol", "")).upper() == symbol.upper() and str(row.get("holdSide", "")).lower() == hold:
            return row
    return None


def wait_for_position(
    client: BitgetDemoClassic,
    symbol: str,
    side: str,
    attempts: int = 10,
) -> dict | None:
    last = None
    for _ in range(attempts):
        rows = client.private_get(
            "/api/v2/mix/position/single-position",
            {
                "symbol": symbol,
                "productType": PRODUCT_TYPE,
                "marginCoin": MARGIN_COIN,
            },
        ) or []
        last = matching_position(rows, symbol, side)
        if last:
            return last
        time.sleep(0.5)
    return last


def order_has_preset_protection(client: BitgetDemoClassic, symbol: str, order_id: str) -> bool:
    detail = client.private_get(
        "/api/v2/mix/order/detail",
        {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "orderId": order_id,
        },
    ) or {}
    return bool(detail.get("presetStopSurplusPrice") and detail.get("presetStopLossPrice"))


def symbol_has_position(rows: list[dict], symbol: str) -> bool:
    return any(str(r.get("symbol", "")).upper() == symbol.upper() for r in active_positions(rows))


def ticker_price(client: BitgetDemoClassic, symbol: str) -> Decimal:
    data = client.public_get(
        "/api/v2/mix/market/ticker",
        {"productType": "usdt-futures", "symbol": symbol},
    ) or []
    if not data:
        raise RuntimeError(f"No ticker for {symbol}")
    price = decimal_or_zero(data[0].get("lastPr"))
    if price <= 0:
        raise RuntimeError(f"Invalid ticker for {symbol}")
    return price


def contract_config(client: BitgetDemoClassic, symbol: str) -> dict:
    data = client.public_get(
        "/api/v2/mix/market/contracts",
        {"productType": "usdt-futures", "symbol": symbol},
    ) or []
    if not data:
        raise RuntimeError(f"No contract config for {symbol}")
    return data[0]


def candles_since_signal(client: BitgetDemoClassic, signal: Signal, until_ms: int) -> list[Candle]:
    # TTL is intentionally short. A small 1m window is enough to detect whether
    # TP/SL was already consumed before our delayed executor reaches the signal.
    rows = client.public_get(
        "/api/v2/mix/market/candles",
        {
            "symbol": signal.symbol,
            "productType": "usdt-futures",
            "granularity": "1m",
            "startTime": str(signal.signal_time_ms),
            "endTime": str(until_ms),
            "limit": 20,
        },
    ) or []
    out = []
    for row in rows:
        try:
            ts = int(row[0])
            if ts + 60_000 < signal.signal_time_ms:
                continue
            out.append(Candle(open_time_ms=ts, high=float(row[2]), low=float(row[3])))
        except Exception:
            continue
    return sorted(out, key=lambda c: c.open_time_ms)


def order_size(contract: dict, price: Decimal, notional: Decimal) -> str:
    min_qty = decimal_or_zero(contract.get("minTradeNum"))
    step = decimal_or_zero(contract.get("sizeMultiplier"))
    min_usdt = decimal_or_zero(contract.get("minTradeUSDT"))
    volume_place = int(contract.get("volumePlace") or 8)
    target = max(notional, min_usdt)
    qty = max(min_qty, target / price)
    if step > 0:
        qty = q_up(qty, step)
    return f"{qty:.{volume_place}f}"


def normalize_price(contract: dict, price: float | Decimal) -> str:
    place = int(contract.get("pricePlace") or 8)
    end_step = decimal_or_zero(contract.get("priceEndStep") or "1")
    step = Decimal(1).scaleb(-place) * end_step
    q = q_nearest(Decimal(str(price)), step)
    return f"{q:.{place}f}"


def close_tracked_trade(client: BitgetDemoClassic, trade: dict, reason: str) -> str:
    side = str(trade["side"]).upper()
    close_side = "buy" if side == "LONG" else "sell"
    payload = {
        "symbol": trade["symbol"],
        "productType": PRODUCT_TYPE,
        "marginMode": "crossed",
        "marginCoin": MARGIN_COIN,
        "size": str(trade["qty"]),
        "side": close_side,
        "tradeSide": "close",
        "orderType": "market",
        "clientOid": f"mgrclose_{int(time.time())}"[:32],
    }
    placed = client.private_post("/api/v2/mix/order/place-order", payload)
    order_id = str(placed.get("orderId") or "")
    if not order_id:
        raise RuntimeError(f"Close order missing orderId: {placed}")
    detail = wait_for_fill(client, trade["symbol"], order_id)
    log_event({
        "event": "CLOSE",
        "reason": reason,
        "signal_id": trade["signal_id"],
        "symbol": trade["symbol"],
        "side": side,
        "order_id": order_id,
        "avg_price": detail.get("priceAvg"),
    })
    return order_id


def manage_open_trades(client: BitgetDemoClassic, cfg: dict, state: dict, ts_ms: int) -> None:
    exchange_positions = all_positions(client)
    kept = []
    for trade in state.get("open_trades", []):
        pos = matching_position(exchange_positions, trade["symbol"], trade["side"])
        if not pos:
            trade["closed_at_ms"] = ts_ms
            trade["close_reason"] = "EXCHANGE_POSITION_GONE"
            state["closed_trades"].append(trade)
            log_event({
                "event": "TRACKED_POSITION_CLOSED",
                "signal_id": trade["signal_id"],
                "symbol": trade["symbol"],
                "side": trade["side"],
                "reason": "EXCHANGE_POSITION_GONE",
            })
            continue

        if bool(cfg.get("require_exchange_tp_sl", True)):
            position_protected = bool(pos.get("takeProfit") and pos.get("stopLoss"))
            order_protected = False
            if not position_protected and trade.get("entry_order_id"):
                try:
                    order_protected = order_has_preset_protection(
                        client, trade["symbol"], str(trade["entry_order_id"])
                    )
                except Exception:
                    order_protected = False
            if not position_protected and not order_protected:
                close_tracked_trade(client, trade, "TP_SL_MISSING")
                trade["closed_at_ms"] = ts_ms
                trade["close_reason"] = "TP_SL_MISSING"
                state["closed_trades"].append(trade)
                continue

        if ts_ms >= int(trade["deadline_ms"]):
            close_tracked_trade(client, trade, "MAX_HOLD")
            trade["closed_at_ms"] = ts_ms
            trade["close_reason"] = "MAX_HOLD"
            state["closed_trades"].append(trade)
            continue

        kept.append(trade)

    state["open_trades"] = kept


def reject(state: dict, signal: Signal, reason: str, details: dict | None = None) -> None:
    state["processed_signal_ids"].append(signal.signal_id)
    log_event({
        "event": "SKIP",
        "signal_id": signal.signal_id,
        "strategy": signal.strategy,
        "symbol": signal.symbol,
        "side": signal.side,
        "reason": reason,
        **(details or {}),
    })


def execute_signal(client: BitgetDemoClassic, cfg: dict, state: dict, signal: Signal, ts_ms: int) -> bool:
    processed = set(state.get("processed_signal_ids", []))
    price = ticker_price(client, signal.symbol)
    candle_path = candles_since_signal(client, signal, ts_ms)

    guard_cfg = GuardConfig(
        active_strategy=str(cfg.get("active_strategy", "OFF")),
        signal_ttl_seconds=int(cfg.get("signal_ttl_seconds", 300)),
        sl_recovery_policy=str(cfg.get("sl_recovery_policy", "skip")),
    )
    decision = validate_signal(
        signal=signal,
        current_price=float(price),
        candles_since_signal=candle_path,
        config=guard_cfg,
        seen_signal_ids=processed,
        now_ms=ts_ms,
    )
    if not decision.allowed:
        reject(state, signal, decision.reason, {"current_price": float(price)})
        return False

    positions = all_positions(client)
    active = active_positions(positions)

    if not bool(cfg.get("allow_hedge_same_symbol", False)) and symbol_has_position(active, signal.symbol):
        reject(state, signal, "SYMBOL_POSITION_EXISTS")
        return False

    if len(active) >= int(cfg.get("max_open_positions", 3)):
        reject(state, signal, "MAX_OPEN_POSITIONS")
        return False

    equity = account_equity(client)
    min_equity = Decimal(str(cfg.get("min_equity_usdt", 0)))
    if equity < min_equity:
        reject(state, signal, "EQUITY_BELOW_MINIMUM", {"equity": float(equity)})
        return False

    size_pct = Decimal(str(cfg.get("position_size_pct", 0)))
    if size_pct <= 0:
        reject(state, signal, "INVALID_POSITION_SIZE_PCT")
        return False

    planned_notional = equity * size_pct / Decimal("100")
    max_order = Decimal(str(cfg.get("max_order_notional_usdt", planned_notional)))
    planned_notional = min(planned_notional, max_order)

    current_exposure = position_exposure_usdt(active)
    max_exposure = equity * Decimal(str(cfg.get("max_total_exposure_pct", 100))) / Decimal("100")
    if current_exposure + planned_notional > max_exposure:
        reject(
            state,
            signal,
            "MAX_TOTAL_EXPOSURE",
            {
                "equity": float(equity),
                "current_exposure": float(current_exposure),
                "planned_notional": float(planned_notional),
                "max_exposure": float(max_exposure),
            },
        )
        return False

    if not bool(cfg.get("demo_auto_execute", False)):
        reject(
            state,
            signal,
            "DEMO_AUTO_EXECUTE_DISABLED",
            {"planned_notional": float(planned_notional), "current_price": float(price)},
        )
        return False

    contract = contract_config(client, signal.symbol)
    qty = order_size(contract, price, planned_notional)
    tp = normalize_price(contract, signal.tp)
    sl = normalize_price(contract, signal.sl)

    if signal.side == "LONG":
        order_side = "buy"
    else:
        order_side = "sell"

    payload = {
        "symbol": signal.symbol,
        "productType": PRODUCT_TYPE,
        "marginMode": "crossed",
        "marginCoin": MARGIN_COIN,
        "size": qty,
        "side": order_side,
        "tradeSide": "open",
        "orderType": "market",
        "clientOid": ("sig_" + signal.signal_id.replace(":", "_"))[-32:],
        "presetStopSurplusPrice": tp,
        "presetStopLossPrice": sl,
    }

    placed = client.private_post("/api/v2/mix/order/place-order", payload)
    order_id = str(placed.get("orderId") or "")
    if not order_id:
        raise RuntimeError(f"Entry missing orderId: {placed}")

    detail = wait_for_fill(client, signal.symbol, order_id)
    state["processed_signal_ids"].append(signal.signal_id)

    # Verify that the filled entry retained the exchange-side preset TP/SL,
    # then separately verify that the position actually appeared.
    require_tp_sl = bool(cfg.get("require_exchange_tp_sl", True))
    detail_protected = bool(
        detail.get("presetStopSurplusPrice") and detail.get("presetStopLossPrice")
    )
    pos = wait_for_position(client, signal.symbol, signal.side)
    if not pos or (require_tp_sl and not detail_protected):
        emergency_trade = {
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "side": signal.side,
            "qty": qty,
        }
        close_tracked_trade(client, emergency_trade, "ENTRY_PROTECTION_VERIFY_FAILED")
        log_event({
            "event": "ENTRY_ABORTED",
            "signal_id": signal.signal_id,
            "symbol": signal.symbol,
            "side": signal.side,
            "reason": "ENTRY_PROTECTION_VERIFY_FAILED",
        })
        return False

    trade = {
        "signal_id": signal.signal_id,
        "strategy": signal.strategy,
        "symbol": signal.symbol,
        "side": signal.side,
        "qty": qty,
        "entry_order_id": order_id,
        "entry_avg_price": detail.get("priceAvg"),
        "tp": tp,
        "sl": sl,
        "opened_at_ms": ts_ms,
        "deadline_ms": ts_ms + int(signal.max_hold_minutes) * 60_000,
    }
    state["open_trades"].append(trade)
    log_event({
        "event": "ENTRY",
        **trade,
        "equity": float(equity),
        "planned_notional": float(planned_notional),
    })
    return True


def main() -> int:
    cfg = load_config()
    state = load_trading_state(STATE_PATH)
    client = BitgetDemoClassic()
    ts_ms = now_ms()

    # Position management always runs, even when active_strategy=OFF.
    manage_open_trades(client, cfg, state, ts_ms)

    signals = load_signal_jsonl(SIGNALS_PATH)
    processed = set(state.get("processed_signal_ids", []))
    fresh = [s for s in signals if s.signal_id not in processed]

    entries = 0
    for signal in fresh:
        try:
            if execute_signal(client, cfg, state, signal, ts_ms):
                entries += 1
        except Exception as exc:
            log_event({
                "event": "ERROR",
                "signal_id": signal.signal_id,
                "strategy": signal.strategy,
                "symbol": signal.symbol,
                "side": signal.side,
                "error": str(exc),
            })

    state["last_run_ms"] = ts_ms
    save_trading_state(STATE_PATH, state)

    summary = (
        f"🤖 Demo auto-trader run\n"
        f"active={cfg.get('active_strategy')} | auto={cfg.get('demo_auto_execute')}\n"
        f"signals_new={len(fresh)} | entries={entries} | tracked_open={len(state.get('open_trades', []))}"
    )
    print(summary)
    if bool(cfg.get("telegram_trade_notifications", True)) and (fresh or entries):
        try:
            telegram_send(summary)
        except Exception as exc:
            print(f"[WARN] Telegram trade notification failed: {exc}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[FATAL] {exc}")
        sys.exit(1)
