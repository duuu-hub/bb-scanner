from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests

from bitget_demo_lifecycle_test import BitgetDemoClassic, MARGIN_COIN, PRODUCT_TYPE, q_nearest, q_up, wait_for_fill
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


def decimal_or_zero(value) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(",", ""))
    except Exception:
        return Decimal("0")


def log_event(event: dict) -> None:
    EXECUTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp_utc": utc_iso(), **event}
    with EXECUTION_LOG.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    print("[EVENT] " + json.dumps(payload, ensure_ascii=False, sort_keys=True))


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


def notify(cfg: dict, text: str) -> None:
    if not bool(cfg.get("telegram_trade_notifications", True)):
        return
    try:
        telegram_send(text)
    except Exception as exc:
        print(f"[WARN] Telegram trade notification failed: {exc}")


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if str(cfg.get("trading_mode", "")).upper() != "DEMO":
        raise RuntimeError("Only DEMO mode is permitted.")
    if bool(cfg.get("live_trading_enabled")):
        raise RuntimeError("live_trading_enabled must remain false.")
    if str(cfg.get("active_portfolio", "")).upper() != "LONG3":
        raise RuntimeError("active_portfolio must be LONG3.")
    enabled = [str(x).upper() for x in cfg.get("enabled_strategies", [])]
    if enabled != ["L1", "L2", "L3"]:
        raise RuntimeError("enabled_strategies must remain exactly [L1, L2, L3].")
    if int(cfg.get("max_open_positions", 0)) != 6:
        raise RuntimeError("max_open_positions must remain 6 for LONG3 forward test.")
    if float(cfg.get("position_size_pct", 0)) != 30.0:
        raise RuntimeError("position_size_pct must remain 30%.")
    if float(cfg.get("max_total_exposure_pct", 0)) != 200.0:
        raise RuntimeError("max_total_exposure_pct must remain 200%.")
    return cfg


def account_equity(client: BitgetDemoClassic) -> Decimal:
    rows = client.private_get("/api/v2/mix/account/accounts", {"productType": PRODUCT_TYPE}) or []
    for row in rows:
        if str(row.get("marginCoin", "")).upper() == MARGIN_COIN:
            equity = decimal_or_zero(row.get("accountEquity") or row.get("usdtEquity"))
            if equity > 0:
                return equity
    raise RuntimeError("Could not obtain positive USDT futures account equity.")


def all_positions(client: BitgetDemoClassic) -> list[dict]:
    rows = client.private_get(
        "/api/v2/mix/position/all-position",
        {"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN},
    )
    if rows is None:
        raise RuntimeError("Position snapshot returned null.")
    return rows or []


def active_positions(rows: list[dict]) -> list[dict]:
    return [r for r in rows if decimal_or_zero(r.get("total")) > 0]


def position_exposure_usdt(rows: list[dict]) -> Decimal:
    total = Decimal("0")
    for row in active_positions(rows):
        qty = decimal_or_zero(row.get("total"))
        mark = decimal_or_zero(row.get("markPrice") or row.get("openPriceAvg"))
        total += abs(qty * mark)
    return total


def symbol_has_position(rows: list[dict], symbol: str) -> bool:
    return any(str(r.get("symbol", "")).upper() == symbol.upper() for r in active_positions(rows))


def matching_position(rows: list[dict], symbol: str, side: str) -> dict | None:
    hold = side.lower()
    for row in active_positions(rows):
        if (
            str(row.get("symbol", "")).upper() == symbol.upper()
            and str(row.get("holdSide", "")).lower() == hold
        ):
            return row
    return None


def wait_for_position(client: BitgetDemoClassic, symbol: str, side: str, attempts: int = 10) -> dict | None:
    last = None
    for _ in range(attempts):
        rows = client.private_get(
            "/api/v2/mix/position/single-position",
            {"symbol": symbol, "productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN},
        ) or []
        last = matching_position(rows, symbol, side)
        if last:
            return last
        time.sleep(0.5)
    return last


def ticker(client: BitgetDemoClassic, symbol: str) -> tuple[Decimal, Decimal, Decimal]:
    data = client.public_get(
        "/api/v2/mix/market/ticker",
        {"productType": "usdt-futures", "symbol": symbol},
    ) or []
    if not data:
        raise RuntimeError(f"No ticker for {symbol}")
    item = data[0]
    price = decimal_or_zero(item.get("lastPr"))
    bid = decimal_or_zero(item.get("bidPr") or item.get("bidPrice") or price)
    ask = decimal_or_zero(item.get("askPr") or item.get("askPrice") or price)
    if price <= 0 or bid <= 0 or ask <= 0:
        raise RuntimeError(f"Invalid ticker for {symbol}")
    return price, bid, ask


def contract_config(client: BitgetDemoClassic, symbol: str) -> dict:
    data = client.public_get(
        "/api/v2/mix/market/contracts",
        {"productType": "usdt-futures", "symbol": symbol},
    ) or []
    if not data:
        raise RuntimeError(f"No contract config for {symbol}")
    return data[0]


def candles_since_signal(client: BitgetDemoClassic, signal: Signal, until_ms: int) -> list[Candle]:
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


def order_has_preset_protection(client: BitgetDemoClassic, symbol: str, order_id: str) -> bool:
    detail = client.private_get(
        "/api/v2/mix/order/detail",
        {"symbol": symbol, "productType": PRODUCT_TYPE, "orderId": order_id},
    ) or {}
    return bool(detail.get("presetStopSurplusPrice") and detail.get("presetStopLossPrice"))


def close_tracked_trade(client: BitgetDemoClassic, trade: dict, reason: str) -> dict:
    side = str(trade["side"]).upper()
    payload = {
        "symbol": trade["symbol"],
        "productType": PRODUCT_TYPE,
        "marginMode": "crossed",
        "marginCoin": MARGIN_COIN,
        "size": str(trade["qty"]),
        "side": "buy" if side == "LONG" else "sell",
        "tradeSide": "close",
        "orderType": "market",
        "clientOid": f"mgrclose_{int(time.time())}"[:32],
    }
    placed = client.private_post("/api/v2/mix/order/place-order", payload)
    order_id = str(placed.get("orderId") or "")
    if not order_id:
        raise RuntimeError(f"Close order missing orderId: {placed}")
    detail = wait_for_fill(client, trade["symbol"], order_id)
    entry = decimal_or_zero(trade.get("entry_avg_price"))
    exit_price = decimal_or_zero(detail.get("priceAvg"))
    ret = None
    if entry > 0 and exit_price > 0:
        ret = (
            float((exit_price / entry - Decimal("1")) * Decimal("100"))
            if side == "LONG"
            else float((entry / exit_price - Decimal("1")) * Decimal("100"))
        )
    log_event(
        {
            "event": "CLOSE",
            "reason": reason,
            "signal_id": trade["signal_id"],
            "strategy": trade.get("strategy"),
            "symbol": trade["symbol"],
            "side": side,
            "order_id": order_id,
            "avg_price": detail.get("priceAvg"),
            "return_pct": ret,
        }
    )
    return {"order_id": order_id, "avg_price": detail.get("priceAvg"), "return_pct": ret}


def resolve_exchange_close(client: BitgetDemoClassic, trade: dict, ts_ms: int) -> dict:
    """Best-effort reconstruction of an exchange-side TP/SL/other close."""
    opened_ms = int(trade.get("opened_at_ms") or 0)
    try:
        data = client.private_get(
            "/api/v2/mix/order/orders-history",
            {
                "productType": PRODUCT_TYPE,
                "symbol": trade["symbol"],
                "startTime": str(max(0, opened_ms - 5_000)),
                "endTime": str(ts_ms),
                "limit": "100",
            },
        ) or {}
        rows = data.get("entrustedList", []) if isinstance(data, dict) else []
        closes = []
        for row in rows:
            try:
                event_ms = int(row.get("uTime") or row.get("cTime") or 0)
            except (TypeError, ValueError):
                event_ms = 0
            if event_ms < opened_ms:
                continue
            if str(row.get("tradeSide") or "").lower() != "close":
                continue
            if str(row.get("status") or "").lower() not in {"filled", "full-fill", "full_fill"}:
                continue
            closes.append((event_ms, row))

        if not closes:
            return {"reason": "EXCHANGE_POSITION_GONE", "return_pct": None, "avg_price": None}

        _, row = min(closes, key=lambda item: item[0])
        source = str(row.get("orderSource") or "").lower()
        if source in {"profit_market", "profit_limit", "pos_profit_market", "pos_profit_limit"}:
            reason = "TAKE_PROFIT"
        elif source in {"loss_market", "loss_limit", "pos_loss_market", "pos_loss_limit"}:
            reason = "STOP_LOSS"
        else:
            reason = "EXCHANGE_CLOSE"

        entry = decimal_or_zero(trade.get("entry_avg_price"))
        exit_price = decimal_or_zero(row.get("priceAvg") or row.get("price"))
        side = str(trade.get("side") or "").upper()
        ret = None
        if entry > 0 and exit_price > 0:
            ret = (
                float((exit_price / entry - Decimal("1")) * Decimal("100"))
                if side == "LONG"
                else float((entry / exit_price - Decimal("1")) * Decimal("100"))
            )
        return {
            "reason": reason,
            "return_pct": ret,
            "avg_price": str(exit_price) if exit_price > 0 else None,
            "order_id": row.get("orderId"),
            "order_source": source or None,
        }
    except Exception as exc:
        log_event(
            {
                "event": "CLOSE_RESOLUTION_WARN",
                "signal_id": trade.get("signal_id"),
                "strategy": trade.get("strategy"),
                "symbol": trade.get("symbol"),
                "error": str(exc),
            }
        )
        return {"reason": "EXCHANGE_POSITION_GONE", "return_pct": None, "avg_price": None}


def manage_open_trades(client: BitgetDemoClassic, cfg: dict, state: dict, ts_ms: int) -> None:
    exchange_positions = all_positions(client)
    kept = []
    for trade in state.get("open_trades", []):
        pos = matching_position(exchange_positions, trade["symbol"], trade["side"])
        if not pos:
            close_info = resolve_exchange_close(client, trade, ts_ms)
            trade.update(
                {
                    "closed_at_ms": ts_ms,
                    "close_reason": close_info.get("reason", "EXCHANGE_POSITION_GONE"),
                    "return_pct": close_info.get("return_pct"),
                    "exit_avg_price": close_info.get("avg_price"),
                    "exit_order_id": close_info.get("order_id"),
                    "exit_order_source": close_info.get("order_source"),
                }
            )
            state["closed_trades"].append(trade)
            log_event(
                {
                    "event": "TRACKED_POSITION_CLOSED",
                    "signal_id": trade["signal_id"],
                    "strategy": trade.get("strategy"),
                    "symbol": trade["symbol"],
                    "side": trade["side"],
                    "reason": trade["close_reason"],
                    "avg_price": trade.get("exit_avg_price"),
                    "return_pct": trade.get("return_pct"),
                    "order_source": trade.get("exit_order_source"),
                }
            )
            notify(
                cfg,
                f"✅ Demo 포지션 종료 감지\n{trade.get('strategy')} {trade['symbol']}\n"
                f"reason={trade['close_reason']} return={trade.get('return_pct')}",
            )
            continue

        if bool(cfg.get("require_exchange_tp_sl", True)):
            protected = bool(pos.get("takeProfit") and pos.get("stopLoss"))
            if not protected and trade.get("entry_order_id"):
                try:
                    protected = order_has_preset_protection(
                        client, trade["symbol"], str(trade["entry_order_id"])
                    )
                except Exception:
                    protected = False
            if not protected:
                result = close_tracked_trade(client, trade, "TP_SL_MISSING")
                trade.update(
                    {
                        "closed_at_ms": ts_ms,
                        "close_reason": "TP_SL_MISSING",
                        "return_pct": result.get("return_pct"),
                    }
                )
                state["closed_trades"].append(trade)
                notify(
                    cfg,
                    f"🚨 Demo 강제청산\n{trade.get('strategy')} {trade['symbol']}\nreason=TP_SL_MISSING",
                )
                continue

        if ts_ms >= int(trade["deadline_ms"]):
            result = close_tracked_trade(client, trade, "MAX_HOLD")
            trade.update(
                {
                    "closed_at_ms": ts_ms,
                    "close_reason": "MAX_HOLD",
                    "return_pct": result.get("return_pct"),
                }
            )
            state["closed_trades"].append(trade)
            notify(
                cfg,
                f"⏱ Demo 시간청산\n{trade.get('strategy')} {trade['symbol']}\nreturn={result.get('return_pct')}",
            )
            continue

        kept.append(trade)

    state["open_trades"] = kept


def reject(cfg: dict, state: dict, signal: Signal, reason: str, details: dict | None = None) -> None:
    if signal.signal_id not in state["processed_signal_ids"]:
        state["processed_signal_ids"].append(signal.signal_id)
    payload = {
        "event": "SKIP",
        "signal_id": signal.signal_id,
        "portfolio": signal.portfolio,
        "strategy": signal.strategy,
        "symbol": signal.symbol,
        "side": signal.side,
        "reason": reason,
        "matched_strategies": list(signal.matched_strategies),
        **(details or {}),
    }
    log_event(payload)
    notify(
        cfg,
        f"⛔ Demo 진입 거부\n{signal.strategy} {signal.symbol}\nreason={reason}\nTP={signal.tp} SL={signal.sl} hold={signal.max_hold_minutes}m",
    )


def execute_signal(client: BitgetDemoClassic, cfg: dict, state: dict, signal: Signal, ts_ms: int) -> bool:
    price, bid, ask = ticker(client, signal.symbol)
    candles = candles_since_signal(client, signal, ts_ms)
    guard_cfg = GuardConfig(
        active_portfolio=str(cfg["active_portfolio"]),
        enabled_strategies=tuple(cfg["enabled_strategies"]),
        signal_ttl_seconds=int(cfg.get("signal_ttl_seconds", 300)),
        max_spread_pct=float(cfg.get("max_spread_pct", 0.20)),
        sl_recovery_policy=str(cfg.get("sl_recovery_policy", "reject")),
    )
    decision = validate_signal(
        signal,
        float(price),
        float(bid),
        float(ask),
        candles,
        guard_cfg,
        set(state.get("processed_signal_ids", [])),
        ts_ms,
    )
    if not decision.allowed:
        reject(cfg, state, signal, decision.reason, {"current_price": float(price)})
        return False

    positions = all_positions(client)
    active = active_positions(positions)

    if not bool(cfg.get("allow_hedge_same_symbol", False)) and symbol_has_position(active, signal.symbol):
        reject(cfg, state, signal, "SYMBOL_POSITION_EXISTS")
        return False

    if len(active) >= int(cfg["max_open_positions"]):
        reject(cfg, state, signal, "MAX_OPEN_POSITIONS", {"open_positions": len(active)})
        return False

    equity = account_equity(client)
    if equity < Decimal(str(cfg.get("min_equity_usdt", 0))):
        reject(cfg, state, signal, "EQUITY_BELOW_MINIMUM", {"equity": float(equity)})
        return False

    planned_notional = equity * Decimal(str(cfg["position_size_pct"])) / Decimal("100")
    current_exposure = position_exposure_usdt(active)
    max_exposure = equity * Decimal(str(cfg["max_total_exposure_pct"])) / Decimal("100")
    if current_exposure + planned_notional > max_exposure + Decimal("0.00000001"):
        reject(
            cfg,
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
            cfg,
            state,
            signal,
            "DEMO_AUTO_EXECUTE_DISABLED",
            {"planned_notional": float(planned_notional)},
        )
        return False

    contract = contract_config(client, signal.symbol)
    qty = order_size(contract, price, planned_notional)
    tp = normalize_price(contract, signal.tp)
    sl = normalize_price(contract, signal.sl)
    requested_ms = now_ms()

    payload = {
        "symbol": signal.symbol,
        "productType": PRODUCT_TYPE,
        "marginMode": "crossed",
        "marginCoin": MARGIN_COIN,
        "size": qty,
        "side": "buy",
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
    filled_ms = now_ms()
    if signal.signal_id not in state["processed_signal_ids"]:
        state["processed_signal_ids"].append(signal.signal_id)

    protected = bool(
        detail.get("presetStopSurplusPrice") and detail.get("presetStopLossPrice")
    )
    pos = wait_for_position(client, signal.symbol, signal.side)
    if not pos or (bool(cfg.get("require_exchange_tp_sl", True)) and not protected):
        emergency = {
            "signal_id": signal.signal_id,
            "strategy": signal.strategy,
            "symbol": signal.symbol,
            "side": signal.side,
            "qty": qty,
            "entry_avg_price": detail.get("priceAvg"),
        }
        close_tracked_trade(client, emergency, "ENTRY_PROTECTION_VERIFY_FAILED")
        log_event(
            {
                "event": "ENTRY_ABORTED",
                "signal_id": signal.signal_id,
                "strategy": signal.strategy,
                "symbol": signal.symbol,
                "side": signal.side,
                "reason": "ENTRY_PROTECTION_VERIFY_FAILED",
            }
        )
        notify(
            cfg,
            f"🚨 Demo 진입 즉시 복구청산\n{signal.strategy} {signal.symbol}\nTP/SL 또는 포지션 확인 실패",
        )
        return False

    after_positions = all_positions(client)
    after_active = active_positions(after_positions)
    exposure_after = position_exposure_usdt(after_active)
    avg_fill = decimal_or_zero(detail.get("priceAvg"))
    slippage = (
        float(
            (avg_fill / Decimal(str(signal.detected_price)) - Decimal("1"))
            * Decimal("100")
        )
        if avg_fill > 0 and signal.detected_price > 0
        else None
    )

    trade = {
        "signal_id": signal.signal_id,
        "portfolio": signal.portfolio,
        "strategy": signal.strategy,
        "symbol": signal.symbol,
        "side": signal.side,
        "qty": qty,
        "entry_order_id": order_id,
        "entry_avg_price": detail.get("priceAvg"),
        "detected_price": signal.detected_price,
        "tp": tp,
        "sl": sl,
        "opened_at_ms": filled_ms,
        "deadline_ms": filled_ms + int(signal.max_hold_minutes) * 60_000,
        "market_snapshot": signal.market_snapshot,
    }
    state["open_trades"].append(trade)

    log_event(
        {
            "event": "ENTRY",
            **trade,
            "signal_time_ms": signal.signal_time_ms,
            "scan_started_at": signal.scan_started_at,
            "signal_created_at": signal.signal_created_at,
            "order_requested_at_ms": requested_ms,
            "filled_at_ms": filled_ms,
            "total_delay_ms": filled_ms - signal.signal_time_ms,
            "pre_order_price": float(price),
            "avg_fill_price": float(avg_fill),
            "entry_slippage_pct": slippage,
            "equity": float(equity),
            "planned_notional": float(planned_notional),
            "open_positions_after": len(after_active),
            "gross_exposure_after_usdt": float(exposure_after),
            "gross_exposure_after_pct": (
                float(exposure_after / equity * Decimal("100")) if equity > 0 else None
            ),
            "matched_strategies": list(signal.matched_strategies),
        }
    )
    notify(
        cfg,
        f"✅ Demo 진입\n{signal.strategy} {signal.symbol} LONG\nfill={avg_fill} TP={tp} SL={sl} hold={signal.max_hold_minutes}m\nsize≈{float(planned_notional):.2f} USDT\nopen={len(after_active)} exposure={float(exposure_after/equity*Decimal('100')):.1f}%\ndelay={(filled_ms-signal.signal_time_ms)/1000:.1f}s slip={slippage}",
    )
    return True


def main() -> int:
    cfg = load_config()
    state = load_trading_state(STATE_PATH)
    client = BitgetDemoClassic()
    ts_ms = now_ms()

    # Position management always runs, including when new entries are disabled.
    manage_open_trades(client, cfg, state, ts_ms)

    signals = load_signal_jsonl(SIGNALS_PATH)
    processed = set(state.get("processed_signal_ids", []))
    fresh = [s for s in signals if s.signal_id not in processed]

    entries = 0
    for signal in fresh:
        try:
            if execute_signal(client, cfg, state, signal, now_ms()):
                entries += 1
        except Exception as exc:
            # Fail closed: API/infrastructure failures never become orders.
            # The signal is not marked processed so a later run can retry only if it is still within TTL.
            log_event(
                {
                    "event": "ERROR",
                    "signal_id": signal.signal_id,
                    "strategy": signal.strategy,
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "error": str(exc),
                }
            )
            notify(cfg, f"🚨 Demo 실행 오류\n{signal.strategy} {signal.symbol}\n{exc}")

    state["last_run_ms"] = now_ms()
    save_trading_state(STATE_PATH, state)

    print(
        f"[DONE] LONG3 Demo active={cfg.get('active_portfolio')} "
        f"auto={cfg.get('demo_auto_execute')} signals_new={len(fresh)} "
        f"entries={entries} tracked_open={len(state.get('open_trades', []))}"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[FATAL] {exc}")
        sys.exit(1)
