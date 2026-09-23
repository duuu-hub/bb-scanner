from __future__ import annotations

import json
from pathlib import Path


DEFAULT_STATE = {
    "processed_signal_ids": [],
    "open_trades": [],
    "closed_trades": [],
    "spread_shadow_open": [],
    "spread_shadow_closed": [],
    "signal_shadow_open": [],
    "signal_shadow_closed": [],
    "last_run_ms": None,
}


def load_trading_state(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return json.loads(json.dumps(DEFAULT_STATE))
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    state = json.loads(json.dumps(DEFAULT_STATE))
    state.update(raw if isinstance(raw, dict) else {})
    for key in (
        "processed_signal_ids",
        "open_trades",
        "closed_trades",
        "spread_shadow_open",
        "spread_shadow_closed",
        "signal_shadow_open",
        "signal_shadow_closed",
    ):
        if not isinstance(state.get(key), list):
            state[key] = []
    return state


def save_trading_state(path: str | Path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Bound histories so state does not grow forever.
    state["processed_signal_ids"] = list(dict.fromkeys(state.get("processed_signal_ids", [])))[-5000:]
    state["closed_trades"] = state.get("closed_trades", [])[-1000:]
    state["spread_shadow_closed"] = state.get("spread_shadow_closed", [])[-2000:]
    state["signal_shadow_closed"] = state.get("signal_shadow_closed", [])[-5000:]
    p.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
