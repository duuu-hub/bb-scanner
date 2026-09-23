import json
import time
from pathlib import Path

from auto_trader import main as run_engine
from bitget_demo_lifecycle_test import BitgetDemoClassic
from trade_state import load_trading_state

CONFIG = Path("config/trading_config.json")
SIGNALS = Path("signals/pending.jsonl")
STATE = Path("state/trading_state.json")
LOG = Path("logs/executions.jsonl")


def main():
    client = BitgetDemoClassic()
    ticker = client.public_get(
        "/api/v2/mix/market/ticker",
        {"productType": "usdt-futures", "symbol": "BTCUSDT"},
    ) or []
    if not ticker:
        raise RuntimeError("No BTCUSDT ticker")
    price = float(ticker[0]["lastPr"])
    ts = int(time.time() * 1000)

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg.update({
        "active_strategy": "BULL",
        "demo_auto_execute": True,
        "position_size_pct": 1.0,
        "max_order_notional_usdt": 20.0,
        "max_total_exposure_pct": 50.0,
        "max_open_positions": 1,
        "telegram_trade_notifications": False,
    })
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    STATE.write_text(
        json.dumps({
            "processed_signal_ids": [],
            "open_trades": [],
            "closed_trades": [],
            "last_run_ms": None,
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    LOG.write_text("", encoding="utf-8")

    signal = {
        "signal_id": f"FINAL_E2E:BULL:BTCUSDT:{ts}",
        "strategy": "BULL",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "signal_time_ms": ts,
        "entry_min": price * 0.995,
        "entry_max": price * 1.005,
        "tp": price * 1.05,
        "sl": price * 0.95,
        "max_hold_minutes": 0,
    }
    SIGNALS.write_text(json.dumps(signal) + "\n", encoding="utf-8")

    print("[TEST] Pass 1: signal -> guard -> risk -> Demo entry -> TP/SL verify")
    rc1 = run_engine()
    if rc1 != 0:
        raise RuntimeError(f"first engine run failed rc={rc1}")

    state1 = load_trading_state(STATE)
    if len(state1["open_trades"]) != 1:
        raise RuntimeError(f"expected one tracked open trade, got {state1['open_trades']}")

    time.sleep(1.0)
    print("[TEST] Pass 2: position manager -> max-hold close -> no duplicate re-entry")
    rc2 = run_engine()
    if rc2 != 0:
        raise RuntimeError(f"second engine run failed rc={rc2}")

    state2 = load_trading_state(STATE)
    if state2["open_trades"]:
        raise RuntimeError(f"expected zero tracked open trades, got {state2['open_trades']}")
    if not state2["closed_trades"]:
        raise RuntimeError("expected one closed trade")

    events = LOG.read_text(encoding="utf-8")
    if '"event": "ENTRY"' not in events or '"event": "CLOSE"' not in events:
        raise RuntimeError("audit log missing ENTRY or CLOSE")

    print("[OK] FINAL DEMO SYSTEM TEST PASSED")
    print("[OK] SIGNAL -> GUARD -> STALE CHECK -> POSITION/EXPOSURE CHECK -> ENTRY -> TP/SL VERIFY -> STATE -> MAX-HOLD CLOSE -> AUDIT")


if __name__ == "__main__":
    main()
