from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal, Optional

Side = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class Signal:
    signal_id: str
    strategy: str
    symbol: str
    side: Side
    signal_time_ms: int
    entry_min: float
    entry_max: float
    tp: float
    sl: float
    max_hold_minutes: int


@dataclass(frozen=True)
class Candle:
    open_time_ms: int
    high: float
    low: float


@dataclass(frozen=True)
class GuardConfig:
    active_strategy: str = "OFF"
    signal_ttl_seconds: int = 300
    sl_recovery_policy: Literal["skip", "allow"] = "skip"


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def validate_signal(
    signal: Signal,
    current_price: float,
    candles_since_signal: Iterable[Candle],
    config: GuardConfig,
    seen_signal_ids: Optional[set[str]] = None,
    now_ms: Optional[int] = None,
) -> GuardDecision:
    seen_signal_ids = seen_signal_ids or set()
    now_ms = _now_ms() if now_ms is None else now_ms

    if config.active_strategy.upper() == "OFF":
        return GuardDecision(False, "TRADING_OFF")

    if signal.strategy.upper() != config.active_strategy.upper():
        return GuardDecision(False, "INACTIVE_STRATEGY")

    if signal.signal_id in seen_signal_ids:
        return GuardDecision(False, "DUPLICATE_SIGNAL")

    age_ms = now_ms - signal.signal_time_ms
    if age_ms < 0:
        return GuardDecision(False, "SIGNAL_FROM_FUTURE")
    if age_ms > config.signal_ttl_seconds * 1000:
        return GuardDecision(False, "SIGNAL_EXPIRED")

    if signal.entry_min > signal.entry_max:
        return GuardDecision(False, "INVALID_ENTRY_RANGE")

    if not (signal.entry_min <= current_price <= signal.entry_max):
        return GuardDecision(False, "PRICE_OUTSIDE_ENTRY_RANGE")

    side = signal.side.upper()
    if side == "LONG":
        if not (signal.sl < current_price < signal.tp):
            return GuardDecision(False, "INVALID_TP_SL_FOR_LONG")
    elif side == "SHORT":
        if not (signal.tp < current_price < signal.sl):
            return GuardDecision(False, "INVALID_TP_SL_FOR_SHORT")
    else:
        return GuardDecision(False, "INVALID_SIDE")

    for c in candles_since_signal:
        if side == "LONG":
            tp_hit = c.high >= signal.tp
            sl_hit = c.low <= signal.sl
        else:
            tp_hit = c.low <= signal.tp
            sl_hit = c.high >= signal.sl

        if tp_hit and sl_hit:
            return GuardDecision(False, "AMBIGUOUS_TP_SL_SAME_CANDLE")
        if tp_hit:
            return GuardDecision(False, "TP_ALREADY_TOUCHED")
        if sl_hit and config.sl_recovery_policy == "skip":
            return GuardDecision(False, "SL_ALREADY_TOUCHED")

    return GuardDecision(True, "OK")
