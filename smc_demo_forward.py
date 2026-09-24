from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import requests

from bitget_demo_lifecycle_test import (
    BitgetDemoClassic,
    MARGIN_COIN,
    PRODUCT_TYPE,
    q_down,
    q_nearest,
    q_up,
    wait_for_fill,
)
from smc_forward_engine import SMCConfig, replay_active_setups, setup_dict

CONFIG_PATH = Path("config/smc_demo_config.json")
STATE_PATH = Path("state/smc_demo_state.json")
LOG_PATH = Path("logs/smc_demo_executions.jsonl")
HOUR_MS = 60 * 60 * 1000

DEFAULT_STATE = {
    "processed_setup_ids": [],
    "pending_orders": [],
    "open_trades": [],
    "closed_trades": [],
    "skipped_events": [],
    "last_run_ms": None,
}


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ms() -> int:
    return int(time.time() * 1000)


def decimal_or_zero(value) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(",", ""))
    except Exception:
        return Decimal("0")


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if str(cfg.get("trading_mode", "")).upper() != "DEMO":
        raise RuntimeError("SMC forward is DEMO-only.")
    if bool(cfg.get("live_trading_enabled")):
        raise RuntimeError("live_trading_enabled must remain false.")
    names = [str(x.get("name", "")).upper() for x in cfg.get("enabled_strategies", [])]
    symbols = [str(x.get("symbol", "")).upper() for x in cfg.get("enabled_strategies", [])]
    if names != ["ETH_D3", "XRP_D3"] or symbols != ["ETHUSDT", "XRPUSDT"]:
        raise RuntimeError("SMC forward candidates are frozen to ETH_D3 and XRP_D3.")
    if any(int(x.get("displacement_bars", 0)) != 3 for x in cfg["enabled_strategies"]):
        raise RuntimeError("Both SMC strategies must remain d3.")
    if float(cfg.get("rrr", 0)) != 2.0:
        raise RuntimeError("rrr must remain 2.0 for frozen forward validation.")
    return cfg


def load_state() -> dict:
    if not STATE_PATH.exists():
        return json.loads(json.dumps(DEFAULT_STATE))
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    state = json.loads(json.dumps(DEFAULT_STATE))
    if isinstance(raw, dict):
        state.update(raw)
    for key in (
        "processed_setup_ids",
        "pending_orders",
        "open_trades",
        "closed_trades",
        "skipped_events",
    ):
        if not isinstance(state.get(key), list):
            state[key] = []
    return state


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["processed_setup_ids"] = list(
        dict.fromkeys(str(x) for x in state.get("processed_setup_ids", []))
    )[-5000:]
    state["closed_trades"] = state.get("closed_trades", [])[-1000:]
    state["skipped_events"] = state.get("skipped_events", [])[-2000:]
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def log_event(event: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp_utc": utc_iso(), **event}
    with LOG_PATH.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    print("[SMC] " + json.dumps(payload, ensure_ascii=False, sort_keys=True))


def telegram_send(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    r.raise_for_status()


def notify(cfg: dict, text: str) -> None:
    if not bool(cfg.get("telegram_trade_notifications", True)):
        return
    try:
        telegram_send(text)
    except Exception as exc:
        print(f"[WARN] SMC Telegram failed: {exc}")


def engine_config(cfg: dict, displacement_bars: int) -> SMCConfig:
    return SMCConfig(
        swing_left=int(cfg["swing_left"]),
        swing_right=int(cfg["swing_right"]),
        sweep_buffer_pct=float(cfg["sweep_buffer_pct"]),
        sl_buffer_pct=float(cfg["sl_buffer_pct"]),
        displacement_bars=int(displacement_bars),
        expiry_bars=int(cfg["expiry_bars"]),
        max_swing_age=int(cfg["max_swing_age"]),
        rrr=float(cfg["rrr"]),
    )


def current_hour_boundary(ts_ms: int | None = None) -> int:
    ts_ms = now_ms() if ts_ms is None else int(ts_ms)
    return (ts_ms // HOUR_MS) * HOUR_MS


def setup_age_hours(setup: dict, ts_ms: int) -> float:
    created_ms = int(setup.get("created_time_ms") or 0)
    if created_ms <= 0:
        return float("inf")
    return max(0.0, (int(ts_ms) - created_ms) / HOUR_MS)


def setup_expired_by_clock(setup: dict, cfg: dict, ts_ms: int) -> bool:
    """Hard wall-clock expiry matching the configured bar horizon.

    This is independent of the replay frame so an API returning stale candles
    cannot resurrect an old setup after it was blocked by another position.
    """
    expiry_bars = int(cfg.get("expiry_bars", 0))
    created_ms = int(setup.get("created_time_ms") or 0)
    if expiry_bars <= 0 or created_ms <= 0:
        return False
    return int(ts_ms) - created_ms >= expiry_bars * HOUR_MS


def fetch_closed_1h(client: BitgetDemoClassic, symbol: str, bars: int) -> pd.DataFrame:
    """Fetch fresh closed 1H candles and fail closed if the feed is stale.

    The previous history-candles query could lag the latest completed hour.
    Use the regular recent-candle endpoint, drop the in-progress hour, and
    require the newest retained candle to be exactly the immediately preceding
    hour before allowing any SMC order action.
    """
    boundary = current_hour_boundary()
    expected_last_open = boundary - HOUR_MS
    rows = client.public_get(
        "/api/v2/mix/market/candles",
        {
            "symbol": symbol,
            "productType": "usdt-futures",
            "granularity": "1H",
            "limit": "200",
        },
    ) or []
    parsed = []
    for row in rows:
        try:
            ts = int(row[0])
            if ts + HOUR_MS > boundary:
                continue
            parsed.append(
                {
                    "Timestamp": pd.Timestamp(ts, unit="ms", tz="UTC"),
                    "Open": float(row[1]),
                    "High": float(row[2]),
                    "Low": float(row[3]),
                    "Close": float(row[4]),
                    "Volume": float(row[5]) if len(row) > 5 else 0.0,
                }
            )
        except Exception:
            continue

    df = pd.DataFrame(parsed)
    if df.empty:
        raise RuntimeError(f"No closed 1H history for {symbol}")

    df = (
        df.drop_duplicates("Timestamp")
        .sort_values("Timestamp")
        .tail(int(bars))
        .reset_index(drop=True)
    )
    latest_open_ms = int(pd.Timestamp(df["Timestamp"].iloc[-1]).timestamp() * 1000)
    if latest_open_ms != expected_last_open:
        raise RuntimeError(
            f"STALE_1H_DATA {symbol}: expected_latest_open={expected_last_open} "
            f"actual_latest_open={latest_open_ms} boundary={boundary}"
        )
    return df


def contract_config(client: BitgetDemoClassic, symbol: str) -> dict:
    rows = client.public_get(
        "/api/v2/mix/market/contracts",
        {"productType": "usdt-futures", "symbol": symbol},
    ) or []
    if not rows:
        raise RuntimeError(f"No contract config for {symbol}")
    return rows[0]


def ticker(client: BitgetDemoClassic, symbol: str) -> tuple[Decimal, Decimal, Decimal]:
    rows = client.public_get(
        "/api/v2/mix/market/ticker",
        {"productType": "usdt-futures", "symbol": symbol},
    ) or []
    if not rows:
        raise RuntimeError(f"No ticker for {symbol}")
    row = rows[0]
    last = decimal_or_zero(row.get("lastPr"))
    bid = decimal_or_zero(row.get("bidPr") or row.get("bidPrice") or last)
    ask = decimal_or_zero(row.get("askPr") or row.get("askPrice") or last)
    if last <= 0 or bid <= 0 or ask <= 0 or ask < bid:
        raise RuntimeError(f"Invalid ticker for {symbol}")
    return last, bid, ask


def normalize_price(contract: dict, value: float | Decimal) -> Decimal:
    place = int(contract.get("pricePlace") or 8)
    end_step = decimal_or_zero(contract.get("priceEndStep") or "1")
    step = Decimal(1).scaleb(-place) * end_step
    return q_nearest(Decimal(str(value)), step)


def format_price(contract: dict, value: Decimal) -> str:
    return f"{value:.{int(contract.get('pricePlace') or 8)}f}"


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
    raise RuntimeError("Could not obtain positive Demo USDT equity.")


def all_positions(client: BitgetDemoClassic) -> list[dict]:
    return client.private_get(
        "/api/v2/mix/position/all-position",
        {"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN},
    ) or []


def active_positions(rows: list[dict]) -> list[dict]:
    return [x for x in rows if decimal_or_zero(x.get("total")) > 0]


def matching_position(rows: list[dict], symbol: str, side: str) -> dict | None:
    hold = side.lower()
    for row in active_positions(rows):
        if (
            str(row.get("symbol", "")).upper() == symbol.upper()
            and str(row.get("holdSide", "")).lower() == hold
        ):
            return row
    return None


def position_exposure(rows: list[dict]) -> Decimal:
    total = Decimal("0")
    for row in active_positions(rows):
        qty = decimal_or_zero(row.get("total"))
        mark = decimal_or_zero(row.get("markPrice") or row.get("openPriceAvg"))
        total += abs(qty * mark)
    return total


def pending_exchange_orders(client: BitgetDemoClassic) -> list[dict]:
    data = client.private_get(
        "/api/v2/mix/order/orders-pending",
        {"productType": PRODUCT_TYPE},
    ) or {}
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rows = data.get("entrustedList") or data.get("orderList") or []
        return rows if isinstance(rows, list) else []
    return []


def pending_order_exposure(rows: list[dict]) -> Decimal:
    total = Decimal("0")
    for row in rows:
        size = decimal_or_zero(row.get("size"))
        price = decimal_or_zero(row.get("price"))
        if size > 0 and price > 0:
            total += abs(size * price)
    return total


def symbol_busy(positions: list[dict], orders: list[dict], symbol: str) -> bool:
    symbol = symbol.upper()
    if any(str(x.get("symbol", "")).upper() == symbol for x in active_positions(positions)):
        return True
    return any(
        str(x.get("symbol", "")).upper() == symbol
        and str(x.get("state", "live")).lower() not in {"filled", "cancelled", "canceled", "failed"}
        for x in orders
    )


def current_hour_entry_touched(
    client: BitgetDemoClassic,
    symbol: str,
    side: str,
    entry: Decimal,
) -> bool:
    boundary = (now_ms() // HOUR_MS) * HOUR_MS
    rows = client.public_get(
        "/api/v2/mix/market/candles",
        {
            "symbol": symbol,
            "productType": "usdt-futures",
            "granularity": "1m",
            "startTime": str(boundary),
            "endTime": str(now_ms()),
            "limit": "100",
        },
    ) or []
    highs = []
    lows = []
    for row in rows:
        try:
            ts = int(row[0])
            if ts < boundary:
                continue
            highs.append(Decimal(str(row[2])))
            lows.append(Decimal(str(row[3])))
        except Exception:
            continue
    if not highs or not lows:
        return False
    return min(lows) <= entry if side == "long" else max(highs) >= entry


def order_size(
    contract: dict,
    entry: Decimal,
    stop: Decimal,
    equity: Decimal,
    remaining_notional: Decimal,
    cfg: dict,
) -> tuple[str, Decimal, Decimal]:
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        raise RuntimeError("Invalid zero-risk setup.")

    risk_budget = equity * Decimal(str(cfg["risk_pct_per_trade"])) / Decimal("100")
    qty_by_risk = risk_budget / risk_per_unit
    per_trade_cap = equity * Decimal(str(cfg["max_notional_pct_per_trade"])) / Decimal("100")
    notional_cap = max(Decimal("0"), min(per_trade_cap, remaining_notional))
    if notional_cap <= 0:
        raise RuntimeError("No account exposure capacity.")

    qty_cap = notional_cap / entry
    qty = min(qty_by_risk, qty_cap)

    min_qty = decimal_or_zero(contract.get("minTradeNum"))
    step = decimal_or_zero(contract.get("sizeMultiplier"))
    min_usdt = decimal_or_zero(contract.get("minTradeUSDT"))
    volume_place = int(contract.get("volumePlace") or 8)

    if step > 0:
        qty = q_down(qty, step)

    required_qty = max(min_qty, (min_usdt / entry if min_usdt > 0 else Decimal("0")))
    if step > 0 and required_qty > 0:
        required_qty = q_up(required_qty, step)

    if qty < required_qty:
        if required_qty * entry > notional_cap:
            raise RuntimeError("Minimum order size exceeds SMC exposure cap.")
        qty = required_qty

    if qty <= 0:
        raise RuntimeError("Calculated quantity is zero.")

    notional = qty * entry
    actual_risk = qty * risk_per_unit
    return f"{qty:.{volume_place}f}", notional, actual_risk


def client_oid(setup_id: str) -> str:
    return "smc_" + hashlib.sha256(setup_id.encode("utf-8")).hexdigest()[:24]


def order_detail(client: BitgetDemoClassic, pending: dict) -> dict:
    return client.private_get(
        "/api/v2/mix/order/detail",
        {
            "symbol": pending["symbol"],
            "productType": PRODUCT_TYPE,
            "orderId": pending["order_id"],
        },
    ) or {}


def cancel_pending(client: BitgetDemoClassic, pending: dict) -> None:
    client.private_post(
        "/api/v2/mix/order/cancel-order",
        {
            "symbol": pending["symbol"],
            "productType": PRODUCT_TYPE,
            "marginCoin": MARGIN_COIN,
            "orderId": pending["order_id"],
        },
    )


def resolve_exchange_close(client: BitgetDemoClassic, trade: dict, ts_ms: int) -> dict:
    opened = int(trade.get("opened_at_ms") or 0)
    try:
        data = client.private_get(
            "/api/v2/mix/order/orders-history",
            {
                "productType": PRODUCT_TYPE,
                "symbol": trade["symbol"],
                "startTime": str(max(0, opened - 5_000)),
                "endTime": str(ts_ms),
                "limit": "100",
            },
        ) or {}
        rows = data.get("entrustedList", []) if isinstance(data, dict) else []
        closes = []
        for row in rows:
            if str(row.get("tradeSide") or "").lower() != "close":
                continue
            if str(row.get("status") or "").lower() not in {
                "filled", "full-fill", "full_fill"
            }:
                continue
            event_ms = int(row.get("uTime") or row.get("cTime") or 0)
            if event_ms >= opened:
                closes.append((event_ms, row))

        if not closes:
            return {"reason": "EXCHANGE_POSITION_GONE", "return_pct": None}

        _, row = min(closes, key=lambda x: x[0])
        source = str(row.get("orderSource") or "").lower()
        if "profit" in source:
            reason = "TAKE_PROFIT"
        elif "loss" in source:
            reason = "STOP_LOSS"
        else:
            reason = "EXCHANGE_CLOSE"

        entry = decimal_or_zero(trade.get("entry_avg_price"))
        exit_price = decimal_or_zero(row.get("priceAvg") or row.get("price"))
        side = str(trade.get("side") or "").lower()
        ret = None
        if entry > 0 and exit_price > 0:
            ret = (
                float((exit_price / entry - Decimal("1")) * Decimal("100"))
                if side == "long"
                else float((entry / exit_price - Decimal("1")) * Decimal("100"))
            )
        return {
            "reason": reason,
            "return_pct": ret,
            "exit_avg_price": str(exit_price) if exit_price > 0 else None,
            "exit_order_id": row.get("orderId"),
            "order_source": source or None,
        }
    except Exception as exc:
        return {"reason": "EXCHANGE_POSITION_GONE", "return_pct": None, "error": str(exc)}


def close_exact(client: BitgetDemoClassic, trade: dict, reason: str) -> dict:
    side = str(trade["side"]).lower()
    payload = {
        "symbol": trade["symbol"],
        "productType": PRODUCT_TYPE,
        "marginMode": "crossed",
        "marginCoin": MARGIN_COIN,
        "size": str(trade["qty"]),
        "side": "buy" if side == "long" else "sell",
        "tradeSide": "close",
        "orderType": "market",
        "clientOid": ("smcclose_" + str(int(time.time())))[:32],
    }
    placed = client.private_post("/api/v2/mix/order/place-order", payload)
    oid = str(placed.get("orderId") or "")
    if not oid:
        raise RuntimeError(f"SMC emergency close missing orderId: {placed}")
    detail = wait_for_fill(client, trade["symbol"], oid)
    log_event(
        {
            "event": "EMERGENCY_CLOSE",
            "reason": reason,
            "setup_id": trade.get("setup_id"),
            "strategy": trade.get("strategy"),
            "symbol": trade["symbol"],
            "side": side,
            "order_id": oid,
            "avg_price": detail.get("priceAvg"),
        }
    )
    return detail


def finalize_fill(
    client: BitgetDemoClassic,
    cfg: dict,
    state: dict,
    pending: dict,
    detail: dict,
    ts_ms: int,
) -> None:
    qty = decimal_or_zero(detail.get("baseVolume") or pending.get("qty"))
    avg = decimal_or_zero(detail.get("priceAvg"))
    if qty <= 0 or avg <= 0:
        raise RuntimeError("Filled SMC order has invalid qty/avg price.")

    trade = {
        "setup_id": pending["setup_id"],
        "strategy": pending["strategy"],
        "symbol": pending["symbol"],
        "side": pending["side"],
        "qty": str(qty),
        "entry_order_id": pending["order_id"],
        "entry_avg_price": str(avg),
        "planned_entry": pending["entry"],
        "tp": pending["tp"],
        "sl": pending["sl"],
        "opened_at_ms": int(detail.get("uTime") or detail.get("cTime") or ts_ms),
        "sweep_time_ms": pending["sweep_time_ms"],
        "created_time_ms": pending["created_time_ms"],
        "planned_notional": pending.get("planned_notional"),
        "planned_risk_usdt": pending.get("planned_risk_usdt"),
    }

    positions = all_positions(client)
    pos = matching_position(positions, trade["symbol"], trade["side"])
    if not pos:
        close_info = resolve_exchange_close(client, trade, ts_ms)
        trade.update(
            {
                "closed_at_ms": ts_ms,
                "close_reason": close_info.get("reason"),
                "return_pct": close_info.get("return_pct"),
                "exit_avg_price": close_info.get("exit_avg_price"),
                "exit_order_id": close_info.get("exit_order_id"),
            }
        )
        state["closed_trades"].append(trade)
        log_event({"event": "FILLED_AND_ALREADY_CLOSED", **trade})
        return

    protected = bool(
        detail.get("presetStopSurplusPrice") and detail.get("presetStopLossPrice")
    )
    protected = protected or bool(pos.get("takeProfit") and pos.get("stopLoss"))
    if bool(cfg.get("require_exchange_tp_sl", True)) and not protected:
        close_exact(client, trade, "TP_SL_MISSING_AFTER_FILL")
        trade.update(
            {
                "closed_at_ms": ts_ms,
                "close_reason": "TP_SL_MISSING_AFTER_FILL",
                "return_pct": None,
            }
        )
        state["closed_trades"].append(trade)
        return

    if not any(x.get("setup_id") == trade["setup_id"] for x in state["open_trades"]):
        state["open_trades"].append(trade)

    slip_r = None
    planned = Decimal(str(pending["entry"]))
    risk = abs(Decimal(str(pending["entry"])) - Decimal(str(pending["sl"])))
    if risk > 0:
        signed = avg - planned if trade["side"] == "long" else planned - avg
        slip_r = float(signed / risk)

    log_event(
        {
            "event": "ENTRY_FILL",
            **trade,
            "entry_slippage_r": slip_r,
        }
    )
    notify(
        cfg,
        f"✅ SMC Demo 체결\n{trade['strategy']} {trade['symbol']} {trade['side'].upper()}\n"
        f"plan={pending['entry']} fill={avg}\nTP={trade['tp']} SL={trade['sl']}",
    )


def manage_pending_orders(
    client: BitgetDemoClassic,
    cfg: dict,
    state: dict,
    active_ids: set[str],
    ts_ms: int,
) -> None:
    kept = []
    for pending in state.get("pending_orders", []):
        try:
            detail = order_detail(client, pending)
            order_state = str(detail.get("state") or "").lower()
            filled_qty = decimal_or_zero(detail.get("baseVolume"))

            if order_state == "filled":
                finalize_fill(client, cfg, state, pending, detail, ts_ms)
                continue

            if filled_qty > 0 and order_state not in {"filled"}:
                try:
                    cancel_pending(client, pending)
                except Exception:
                    pass
                detail = order_detail(client, pending)
                finalize_fill(client, cfg, state, pending, detail, ts_ms)
                continue

            if setup_expired_by_clock(pending, cfg, ts_ms):
                cancel_pending(client, pending)
                age_hours = setup_age_hours(pending, ts_ms)
                log_event(
                    {
                        "event": "SETUP_EXPIRED_CANCEL",
                        "setup_id": pending["setup_id"],
                        "strategy": pending["strategy"],
                        "symbol": pending["symbol"],
                        "order_id": pending["order_id"],
                        "setup_age_hours": age_hours,
                    }
                )
                notify(
                    cfg,
                    f"🧹 SMC Demo 오래된 지정가 취소\n"
                    f"{pending['strategy']} {pending['symbol']}\n"
                    f"setup_age={age_hours:.1f}h expiry={int(cfg.get('expiry_bars', 0))}h",
                )
                continue

            if order_state in {"cancelled", "canceled", "failed"}:
                log_event(
                    {
                        "event": "PENDING_ENDED",
                        "setup_id": pending["setup_id"],
                        "strategy": pending["strategy"],
                        "symbol": pending["symbol"],
                        "order_state": order_state,
                    }
                )
                continue

            if pending["setup_id"] not in active_ids:
                cancel_pending(client, pending)
                log_event(
                    {
                        "event": "SETUP_INVALIDATED_CANCEL",
                        "setup_id": pending["setup_id"],
                        "strategy": pending["strategy"],
                        "symbol": pending["symbol"],
                        "order_id": pending["order_id"],
                    }
                )
                notify(
                    cfg,
                    f"🧹 SMC Demo 미체결 취소\n{pending['strategy']} {pending['symbol']}\nsetup invalidated/expired",
                )
                continue

            kept.append(pending)
        except Exception as exc:
            log_event(
                {
                    "event": "PENDING_MANAGE_ERROR",
                    "setup_id": pending.get("setup_id"),
                    "symbol": pending.get("symbol"),
                    "error": str(exc),
                }
            )
            kept.append(pending)
    state["pending_orders"] = kept


def manage_open_trades(
    client: BitgetDemoClassic,
    cfg: dict,
    state: dict,
    ts_ms: int,
) -> None:
    positions = all_positions(client)
    kept = []
    for trade in state.get("open_trades", []):
        pos = matching_position(positions, trade["symbol"], trade["side"])
        if not pos:
            info = resolve_exchange_close(client, trade, ts_ms)
            trade.update(
                {
                    "closed_at_ms": ts_ms,
                    "close_reason": info.get("reason"),
                    "return_pct": info.get("return_pct"),
                    "exit_avg_price": info.get("exit_avg_price"),
                    "exit_order_id": info.get("exit_order_id"),
                    "exit_order_source": info.get("order_source"),
                }
            )
            state["closed_trades"].append(trade)
            log_event({"event": "POSITION_CLOSED", **trade})
            notify(
                cfg,
                f"📊 SMC Demo 종료\n{trade['strategy']} {trade['symbol']} {trade['side'].upper()}\n"
                f"{trade.get('close_reason')} return={trade.get('return_pct')}",
            )
            continue

        if bool(cfg.get("require_exchange_tp_sl", True)):
            protected = bool(pos.get("takeProfit") and pos.get("stopLoss"))
            if not protected:
                try:
                    detail = client.private_get(
                        "/api/v2/mix/order/detail",
                        {
                            "symbol": trade["symbol"],
                            "productType": PRODUCT_TYPE,
                            "orderId": trade["entry_order_id"],
                        },
                    ) or {}
                    protected = bool(
                        detail.get("presetStopSurplusPrice")
                        and detail.get("presetStopLossPrice")
                    )
                except Exception:
                    protected = False
            if not protected:
                close_exact(client, trade, "TP_SL_MISSING")
                trade.update(
                    {
                        "closed_at_ms": ts_ms,
                        "close_reason": "TP_SL_MISSING",
                        "return_pct": None,
                    }
                )
                state["closed_trades"].append(trade)
                continue

        kept.append(trade)
    state["open_trades"] = kept


def skip_event(state: dict, setup: dict, reason: str, retryable: bool, extra: dict | None = None) -> None:
    row = {
        "setup_id": setup["setup_id"],
        "strategy": setup["strategy"],
        "symbol": setup["symbol"],
        "side": setup["side"],
        "reason": reason,
        "retryable": retryable,
        "at_ms": now_ms(),
        **(extra or {}),
    }
    state["skipped_events"].append(row)
    if not retryable and setup["setup_id"] not in state["processed_setup_ids"]:
        state["processed_setup_ids"].append(setup["setup_id"])
    log_event({"event": "SETUP_SKIP", **row})


def place_setup(
    client: BitgetDemoClassic,
    cfg: dict,
    state: dict,
    setup: dict,
) -> bool:
    sid = setup["setup_id"]
    if sid in set(state.get("processed_setup_ids", [])):
        return False
    if any(x.get("setup_id") == sid for x in state.get("pending_orders", [])):
        return False
    if any(x.get("setup_id") == sid for x in state.get("open_trades", [])):
        return False

    symbol = setup["symbol"]
    ts_ms = now_ms()
    if setup_expired_by_clock(setup, cfg, ts_ms):
        skip_event(
            state,
            setup,
            "SETUP_EXPIRED_WALL_CLOCK",
            False,
            {
                "setup_age_hours": setup_age_hours(setup, ts_ms),
                "expiry_hours": int(cfg.get("expiry_bars", 0)),
            },
        )
        return False

    positions = all_positions(client)
    orders = pending_exchange_orders(client)

    if bool(cfg.get("skip_if_symbol_busy", True)) and symbol_busy(positions, orders, symbol):
        skip_event(state, setup, "SYMBOL_BUSY_LONG3_OR_OTHER", True)
        return False

    if len(active_positions(positions)) >= int(cfg["max_account_open_positions"]):
        skip_event(state, setup, "ACCOUNT_POSITION_LIMIT", True)
        return False

    equity = account_equity(client)
    if equity < Decimal(str(cfg["min_equity_usdt"])):
        skip_event(state, setup, "EQUITY_BELOW_MINIMUM", True, {"equity": float(equity)})
        return False

    contract = contract_config(client, symbol)
    entry = normalize_price(contract, setup["entry"])
    stop = normalize_price(contract, setup["stop"])
    target = normalize_price(contract, setup["target"])
    side = str(setup["side"]).lower()

    last, bid, ask = ticker(client, symbol)
    if current_hour_entry_touched(client, symbol, side, entry):
        skip_event(
            state,
            setup,
            "MISSED_INTRAHOUR_TOUCH_BEFORE_ORDER",
            False,
            {"last": float(last), "entry": float(entry), "bid": float(bid), "ask": float(ask)},
        )
        return False
    if side == "long" and (last <= entry or entry >= ask):
        skip_event(
            state,
            setup,
            "MISSED_ENTRY_BEFORE_ORDER",
            False,
            {"last": float(last), "entry": float(entry), "bid": float(bid), "ask": float(ask)},
        )
        return False
    if side == "short" and (last >= entry or entry <= bid):
        skip_event(
            state,
            setup,
            "MISSED_ENTRY_BEFORE_ORDER",
            False,
            {"last": float(last), "entry": float(entry), "bid": float(bid), "ask": float(ask)},
        )
        return False

    open_exp = position_exposure(positions)
    pending_exp = pending_order_exposure(orders)
    max_total = equity * Decimal(str(cfg["max_total_account_exposure_pct"])) / Decimal("100")
    remaining = max_total - open_exp - pending_exp
    if remaining <= 0:
        skip_event(state, setup, "ACCOUNT_EXPOSURE_LIMIT", True)
        return False

    try:
        qty, planned_notional, actual_risk = order_size(
            contract, entry, stop, equity, remaining, cfg
        )
    except Exception as exc:
        skip_event(state, setup, "SIZE_REJECT", False, {"error": str(exc)})
        return False

    if not bool(cfg.get("demo_auto_execute", False)):
        skip_event(
            state,
            setup,
            "DEMO_AUTO_EXECUTE_DISABLED",
            True,
            {"planned_notional": float(planned_notional)},
        )
        return False

    payload = {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "marginMode": "crossed",
        "marginCoin": MARGIN_COIN,
        "size": qty,
        "price": format_price(contract, entry),
        "side": "buy" if side == "long" else "sell",
        "tradeSide": "open",
        "orderType": "limit",
        "force": "post_only",
        "clientOid": client_oid(sid),
        "presetStopSurplusPrice": format_price(contract, target),
        "presetStopLossPrice": format_price(contract, stop),
    }
    placed = client.private_post("/api/v2/mix/order/place-order", payload)
    oid = str(placed.get("orderId") or "")
    if not oid:
        raise RuntimeError(f"SMC order accepted without orderId: {placed}")

    pending = {
        **setup,
        "order_id": oid,
        "qty": qty,
        "entry": format_price(contract, entry),
        "tp": format_price(contract, target),
        "sl": format_price(contract, stop),
        "placed_at_ms": now_ms(),
        "planned_notional": float(planned_notional),
        "planned_risk_usdt": float(actual_risk),
        "account_equity": float(equity),
        "setup_age_hours_at_order": setup_age_hours(setup, now_ms()),
        "market_price_at_order": float(last),
    }
    state["pending_orders"].append(pending)
    state["processed_setup_ids"].append(sid)
    log_event(
        {
            "event": "LIMIT_PLACED",
            **pending,
            "last": float(last),
            "bid": float(bid),
            "ask": float(ask),
        }
    )
    created_text = datetime.fromtimestamp(
        int(setup["created_time_ms"]) / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")
    notify(
        cfg,
        f"🧾 SMC Demo 지정가 (되돌림 대기)\n"
        f"{setup['strategy']} {symbol} {side.upper()}\n"
        f"현재가={float(last):g} / 지정가={pending['entry']}\n"
        f"TP={pending['tp']} SL={pending['sl']}\n"
        f"셋업생성={created_text} / age={pending['setup_age_hours_at_order']:.1f}h\n"
        f"risk≈{actual_risk:.2f} USDT notional≈{planned_notional:.2f}",
    )
    return True


def build_active_setup_map(client: BitgetDemoClassic, cfg: dict) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for spec in cfg["enabled_strategies"]:
        strategy = str(spec["name"]).upper()
        symbol = str(spec["symbol"]).upper()
        frame = fetch_closed_1h(client, symbol, int(cfg.get("history_bars", 180)))
        ecfg = engine_config(cfg, int(spec["displacement_bars"]))
        setups = replay_active_setups(frame, ecfg)
        scan_ts_ms = now_ms()
        rows = [
            setup_dict(strategy, symbol, s)
            for s in setups
            if not setup_expired_by_clock(
                setup_dict(strategy, symbol, s), cfg, scan_ts_ms
            )
        ]
        rows.sort(key=lambda x: (int(x["created_time_ms"]), x["setup_id"]))
        result[strategy] = rows
        print(
            f"[SCAN] {strategy} {symbol}: candles={len(frame)} "
            f"active_pending_setups={len(rows)} last={frame['Timestamp'].iloc[-1]}"
        )
    return result


def run_forward() -> int:
    if os.getenv("SMC_DEMO_CONFIRM", "").strip() != "SMC_DEMO_FORWARD":
        raise RuntimeError("SMC_DEMO_CONFIRM mismatch; no order actions permitted.")

    cfg = load_config()
    state = load_state()
    client = BitgetDemoClassic()
    ts_ms = now_ms()

    active_by_strategy = build_active_setup_map(client, cfg)
    active_ids = {
        row["setup_id"]
        for rows in active_by_strategy.values()
        for row in rows
    }

    manage_pending_orders(client, cfg, state, active_ids, ts_ms)
    manage_open_trades(client, cfg, state, ts_ms)

    processed = set(state.get("processed_setup_ids", []))
    pending_ids = {x.get("setup_id") for x in state.get("pending_orders", [])}
    open_ids = {x.get("setup_id") for x in state.get("open_trades", [])}

    for spec in cfg["enabled_strategies"]:
        strategy = str(spec["name"]).upper()
        for setup in active_by_strategy.get(strategy, []):
            sid = setup["setup_id"]
            if sid in processed or sid in pending_ids or sid in open_ids:
                continue
            if place_setup(client, cfg, state, setup):
                # One live SMC order per symbol; later active setups wait.
                break

    state["last_run_ms"] = ts_ms
    save_state(state)
    print(
        f"[DONE] SMC Demo pending={len(state['pending_orders'])} "
        f"open={len(state['open_trades'])} closed={len(state['closed_trades'])}"
    )
    return 0


def scan_only() -> int:
    cfg = load_config()

    class PublicOnly:
        def __init__(self):
            self.session = requests.Session()

        def public_get(self, path, params):
            r = self.session.get("https://api.bitget.com" + path, params=params, timeout=15)
            p = r.json()
            if not r.ok or str(p.get("code")) != "00000":
                raise RuntimeError(f"Public GET failed: {p}")
            return p.get("data")

    client = PublicOnly()
    active = build_active_setup_map(client, cfg)
    print(json.dumps(active, ensure_ascii=False, indent=2, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-only", action="store_true")
    args = ap.parse_args()
    return scan_only() if args.scan_only else run_forward()


if __name__ == "__main__":
    raise SystemExit(main())
