import json
import os
import sys
import time
from decimal import Decimal

from bitget_demo_lifecycle_test import (
    BitgetDemoClassic,
    PRODUCT_TYPE,
    MARGIN_COIN,
    q_nearest,
    q_up,
    wait_for_fill,
)
from trade_guard import GuardConfig, Signal, validate_signal


CONFIG_PATH = "config/trading_config.json"


def load_repo_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    if os.getenv("DEMO_CONFIRM", "").strip() != "GUARDED_DEMO_ORDER_TEST":
        raise RuntimeError("Demo confirmation phrase mismatch. No order sent.")

    repo_cfg = load_repo_config()

    # Hard lock: this integration test may only run while repository defaults are DEMO + live disabled.
    if str(repo_cfg.get("trading_mode", "")).upper() != "DEMO":
        raise RuntimeError("Repository trading_mode is not DEMO. Aborting.")
    if bool(repo_cfg.get("live_trading_enabled")):
        raise RuntimeError("live_trading_enabled=true. Aborting.")

    symbol = os.getenv("DEMO_SYMBOL", "BTCUSDT").strip().upper()
    target_notional = Decimal(os.getenv("DEMO_NOTIONAL_USDT", "20"))
    client = BitgetDemoClassic()

    contracts = client.public_get(
        "/api/v2/mix/market/contracts",
        {"productType": "usdt-futures", "symbol": symbol},
    ) or []
    if not contracts:
        raise RuntimeError(f"No contract config returned for {symbol}")
    contract = contracts[0]

    tickers = client.public_get(
        "/api/v2/mix/market/ticker",
        {"productType": "usdt-futures", "symbol": symbol},
    ) or []
    if not tickers:
        raise RuntimeError(f"No ticker returned for {symbol}")

    current = Decimal(str(tickers[0]["lastPr"]))
    now_ms = int(time.time() * 1000)

    price_place = int(contract.get("pricePlace") or 8)
    price_end_step = Decimal(str(contract.get("priceEndStep") or "1"))
    price_step = Decimal(1).scaleb(-price_place) * price_end_step

    entry_min = q_nearest(current * Decimal("0.995"), price_step)
    entry_max = q_nearest(current * Decimal("1.005"), price_step)
    tp = q_nearest(current * Decimal("1.05"), price_step)
    sl = q_nearest(current * Decimal("0.95"), price_step)

    signal = Signal(
        signal_id=f"STEP7:BULL:{symbol}:{now_ms}",
        strategy="BULL",
        symbol=symbol,
        side="LONG",
        signal_time_ms=now_ms,
        entry_min=float(entry_min),
        entry_max=float(entry_max),
        tp=float(tp),
        sl=float(sl),
        max_hold_minutes=30,
    )

    # Repository default remains OFF. For this one-time Demo integration test only,
    # activate BULL in memory to prove signal -> guard -> order wiring.
    guard_cfg = GuardConfig(
        active_strategy="BULL",
        signal_ttl_seconds=int(repo_cfg.get("signal_ttl_seconds", 300)),
        sl_recovery_policy=str(repo_cfg.get("sl_recovery_policy", "skip")),
    )

    decision = validate_signal(
        signal=signal,
        current_price=float(current),
        candles_since_signal=[],
        config=guard_cfg,
        seen_signal_ids=set(),
        now_ms=now_ms,
    )
    print(
        f"[INFO] Fake signal: id={signal.signal_id} strategy={signal.strategy} "
        f"side={signal.side} price={current} range=[{entry_min},{entry_max}] TP={tp} SL={sl}"
    )
    print(f"[INFO] Guard decision: allowed={decision.allowed} reason={decision.reason}")

    if not decision.allowed:
        raise RuntimeError(f"Guard rejected valid test signal: {decision.reason}")

    # Position sizing from current contract limits.
    min_qty = Decimal(str(contract.get("minTradeNum") or "0"))
    size_step = Decimal(str(contract.get("sizeMultiplier") or "0"))
    min_usdt = Decimal(str(contract.get("minTradeUSDT") or "0"))
    volume_place = int(contract.get("volumePlace") or 8)

    notional = max(target_notional, min_usdt)
    qty = max(min_qty, notional / current)
    if size_step > 0:
        qty = q_up(qty, size_step)
    qty_s = f"{qty:.{volume_place}f}"

    entry_payload = {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "marginMode": "crossed",
        "marginCoin": MARGIN_COIN,
        "size": qty_s,
        "side": "buy",
        "tradeSide": "open",
        "orderType": "market",
        "clientOid": f"guard7_{int(time.time())}"[:32],
        "presetStopSurplusPrice": f"{tp:.{price_place}f}",
        "presetStopLossPrice": f"{sl:.{price_place}f}",
    }

    opened = False
    try:
        print(f"[INFO] Guard passed -> sending DEMO order only. qty={qty_s}")
        placed = client.private_post("/api/v2/mix/order/place-order", entry_payload)
        order_id = str(placed.get("orderId") or "")
        if not order_id:
            raise RuntimeError(f"Entry accepted but no orderId returned: {placed}")
        opened = True
        print(f"[OK] Guarded Demo entry accepted. orderId={order_id}")

        detail = wait_for_fill(client, symbol, order_id)
        print(
            f"[OK] Guarded entry filled. avgPrice={detail.get('priceAvg')} "
            f"TP={detail.get('presetStopSurplusPrice')} SL={detail.get('presetStopLossPrice')}"
        )

        seen = {signal.signal_id}
        duplicate = validate_signal(
            signal=signal,
            current_price=float(current),
            candles_since_signal=[],
            config=guard_cfg,
            seen_signal_ids=seen,
            now_ms=now_ms + 1,
        )
        print(f"[INFO] Duplicate replay check: allowed={duplicate.allowed} reason={duplicate.reason}")
        if duplicate.allowed or duplicate.reason != "DUPLICATE_SIGNAL":
            raise RuntimeError("Duplicate-signal protection failed.")

        close_payload = {
            "symbol": symbol,
            "productType": PRODUCT_TYPE,
            "marginMode": "crossed",
            "marginCoin": MARGIN_COIN,
            "size": qty_s,
            "side": "buy",
            "tradeSide": "close",
            "orderType": "market",
            "clientOid": f"guard7close_{int(time.time())}"[:32],
        }
        closed = client.private_post("/api/v2/mix/order/place-order", close_payload)
        close_id = str(closed.get("orderId") or "")
        if not close_id:
            raise RuntimeError(f"Close accepted but no orderId returned: {closed}")
        print(f"[OK] Exact-size Demo close accepted. orderId={close_id}")

        close_detail = wait_for_fill(client, symbol, close_id)
        opened = False
        print(f"[OK] Close filled. avgPrice={close_detail.get('priceAvg')}")
        print("[OK] STEP 7 SUCCESS: FAKE SIGNAL -> GUARD -> DEMO ENTRY -> TP/SL -> DUPLICATE BLOCK -> CLOSE")

    finally:
        if opened:
            print("[WARN] Cleanup guard: attempting exact-size Demo close.")
            try:
                cleanup = client.private_post(
                    "/api/v2/mix/order/place-order",
                    {
                        "symbol": symbol,
                        "productType": PRODUCT_TYPE,
                        "marginMode": "crossed",
                        "marginCoin": MARGIN_COIN,
                        "size": qty_s,
                        "side": "buy",
                        "tradeSide": "close",
                        "orderType": "market",
                        "clientOid": f"guard7cleanup_{int(time.time())}"[:32],
                    },
                )
                print(f"[OK] Cleanup close sent. orderId={cleanup.get('orderId')}")
            except Exception as cleanup_exc:
                print(f"[FAIL] Cleanup close failed: {cleanup_exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        sys.exit(1)
