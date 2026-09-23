import json
import time
from pathlib import Path

import auto_trader
from auto_trader import close_tracked_trade
from bitget_demo_lifecycle_test import BitgetDemoClassic
from trade_state import load_trading_state, save_trading_state

CONFIG = Path("config/trading_config.json")
SIGNALS = Path("signals/pending.jsonl")
STATE = Path("state/trading_state.json")
LOG = Path("logs/executions.jsonl")


def main():
    originals = {}
    for path in (CONFIG, SIGNALS, STATE, LOG):
        originals[path] = path.read_bytes() if path.exists() else None

    client = BitgetDemoClassic()
    opened_trade = None
    try:
        ticker = client.public_get(
            "/api/v2/mix/market/ticker",
            {"productType": "usdt-futures", "symbol": "BTCUSDT"},
        ) or []
        if not ticker:
            raise RuntimeError("No BTCUSDT ticker")
        price = float(ticker[0]["lastPr"])
        ts = int(time.time() * 1000)
        boundary = (ts // 900_000) * 900_000

        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        if cfg.get("trading_mode") != "DEMO" or cfg.get("live_trading_enabled") is not False:
            raise RuntimeError("Refusing E2E: repository config is not DEMO/live-disabled")
        if cfg.get("active_portfolio") != "LONG3":
            raise RuntimeError("Refusing E2E: active_portfolio is not LONG3")
        cfg["demo_auto_execute"] = True
        cfg["telegram_trade_notifications"] = False
        CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(
            json.dumps(
                {
                    "processed_signal_ids": [],
                    "open_trades": [],
                    "closed_trades": [],
                    "last_run_ms": None,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text("", encoding="utf-8")
        SIGNALS.parent.mkdir(parents=True, exist_ok=True)

        signal = {
            "signal_id": f"LONG3:L1:BTCUSDT:{boundary}:E2E",
            "portfolio": "LONG3",
            "strategy": "L1",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "signal_time_ms": ts,
            "detected_price": price,
            "entry_min": price * (1 - 0.0082),
            "entry_max": price * (1 + 0.0095),
            "tp": price * 1.10,
            "sl": price * 0.95,
            "max_hold_minutes": 720,
            "scan_started_at": auto_trader.utc_iso(),
            "signal_created_at": auto_trader.utc_iso(),
            "matched_strategies": ["L1"],
            "selected_strategy": "L1",
            "market_snapshot": {"e2e": True},
        }
        SIGNALS.write_text(json.dumps(signal) + "\n", encoding="utf-8")

        print("[TEST] LONG3 engine: signal -> guard -> real Demo position/exposure -> entry -> TP/SL verify")
        rc = auto_trader.main()
        if rc != 0:
            raise RuntimeError(f"engine failed rc={rc}")

        state = load_trading_state(STATE)
        if len(state["open_trades"]) != 1:
            raise RuntimeError(f"expected one tracked open trade, got {state['open_trades']}")
        opened_trade = state["open_trades"][0]

        print("[TEST] Cleanup: exact tracked Demo size market-close")
        result = close_tracked_trade(client, opened_trade, "E2E_CLEANUP")
        opened_trade["closed_at_ms"] = int(time.time() * 1000)
        opened_trade["close_reason"] = "E2E_CLEANUP"
        opened_trade["return_pct"] = result.get("return_pct")
        state["open_trades"] = []
        state["closed_trades"].append(opened_trade)
        save_trading_state(STATE, state)
        opened_trade = None

        events = LOG.read_text(encoding="utf-8")
        if '"event": "ENTRY"' not in events or '"event": "CLOSE"' not in events:
            raise RuntimeError("audit log missing ENTRY/CLOSE")
        print("[OK] LONG3 FINAL DEMO E2E PASSED")
        print("[OK] L1 contract + 30% sizing + actual exposure + fill + TP/SL verify + exact cleanup")
    finally:
        if opened_trade is not None:
            try:
                close_tracked_trade(client, opened_trade, "E2E_EMERGENCY_CLEANUP")
                print("[OK] Emergency Demo cleanup completed")
            except Exception as exc:
                print(f"[FAIL] Emergency cleanup failed: {exc}")

        for path, data in originals.items():
            if data is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)


if __name__ == "__main__":
    main()
