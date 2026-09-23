from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from trade_guard import Signal


REQUIRED_FIELDS = {
    "signal_id",
    "strategy",
    "symbol",
    "side",
    "signal_time_ms",
    "entry_min",
    "entry_max",
    "tp",
    "sl",
    "max_hold_minutes",
}


def signal_from_dict(row: dict) -> Signal:
    missing = REQUIRED_FIELDS - set(row)
    if missing:
        raise ValueError(f"missing signal fields: {sorted(missing)}")
    return Signal(
        signal_id=str(row["signal_id"]),
        strategy=str(row["strategy"]).upper(),
        symbol=str(row["symbol"]).upper(),
        side=str(row["side"]).upper(),
        signal_time_ms=int(row["signal_time_ms"]),
        entry_min=float(row["entry_min"]),
        entry_max=float(row["entry_max"]),
        tp=float(row["tp"]),
        sl=float(row["sl"]),
        max_hold_minutes=int(row["max_hold_minutes"]),
    )


def load_signal_jsonl(path: str | Path) -> list[Signal]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(signal_from_dict(json.loads(line)))
        except Exception as exc:
            raise ValueError(f"{p}:{lineno}: {exc}") from exc
    return out


def append_signal(path: str | Path, signal: Signal) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(asdict(signal), ensure_ascii=False, sort_keys=True) + "\n")
