from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Literal, Optional

Side = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class Signal:
    signal_id: str
    portfolio: str
    strategy: str
    symbol: str
    side: Side
    signal_time_ms: int
    detected_price: float
    entry_min: float
    entry_max: float
    tp: float
    sl: float
    max_hold_minutes: int
    scan_started_at: str = ""
    signal_created_at: str = ""
    matched_strategies: tuple[str, ...] = field(default_factory=tuple)
    selected_strategy: str = ""
    market_snapshot: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Candle:
    open_time_ms: int
    high: float
    low: float


@dataclass(frozen=True)
class GuardConfig:
    active_portfolio: str = "LONG3"
    enabled_strategies: tuple[str, ...] = ("L1", "L2", "L3")
    signal_ttl_seconds: int = 300
    max_spread_pct: float = 0.20
    sl_recovery_policy: Literal["reject", "allow"] = "reject"
    entry_mode: Literal["MARKET", "MAKER_LIMIT"] = "MARKET"


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def validate_signal(
    signal: Signal,
    current_price: float,
    bid: float,
    ask: float,
    candles_since_signal: Iterable[Candle],
    config: GuardConfig,
    seen_signal_ids: Optional[set[str]] = None,
    now_ms: Optional[int] = None,
) -> GuardDecision:
    seen_signal_ids = seen_signal_ids or set()
    now_ms = _now_ms() if now_ms is None else now_ms

    if signal.portfolio.upper() != config.active_portfolio.upper():
        return GuardDecision(False, "INACTIVE_PORTFOLIO")
    if signal.strategy.upper() not in {x.upper() for x in config.enabled_strategies}:
        return GuardDecision(False, "DISABLED_STRATEGY")
    if signal.side.upper() != "LONG":
        return GuardDecision(False, "LONG3_LONG_ONLY")
    if signal.signal_id in seen_signal_ids:
        return GuardDecision(False, "DUPLICATE_SIGNAL")

    age_ms = now_ms - signal.signal_time_ms
    if age_ms < 0:
        return GuardDecision(False, "SIGNAL_FROM_FUTURE")
    if age_ms > config.signal_ttl_seconds * 1000:
        return GuardDecision(False, "STALE_SIGNAL")

    if signal.entry_min > signal.entry_max:
        return GuardDecision(False, "INVALID_ENTRY_RANGE")

    if config.entry_mode == "MAKER_LIMIT":
        reference_price = signal.detected_price
        if not (signal.entry_min <= reference_price <= signal.entry_max):
            return GuardDecision(False, "INVALID_MAKER_REFERENCE_PRICE")
        if not (signal.sl < reference_price < signal.tp):
            return GuardDecision(False, "INVALID_TP_SL_FOR_LONG")
    else:
        if not (signal.entry_min <= current_price <= signal.entry_max):
            return GuardDecision(False, "PRICE_OUTSIDE_ENTRY_RANGE")
        if not (signal.sl < current_price < signal.tp):
            return GuardDecision(False, "INVALID_TP_SL_FOR_LONG")

        if bid <= 0 or ask <= 0 or ask < bid:
            return GuardDecision(False, "INVALID_BID_ASK")
        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / mid * 100.0 if mid > 0 else 999.0
        if spread_pct > config.max_spread_pct:
            return GuardDecision(False, "SPREAD_TOO_WIDE")

    for candle in candles_since_signal:
        tp_hit = candle.high >= signal.tp
        sl_hit = candle.low <= signal.sl
        if tp_hit and sl_hit:
            return GuardDecision(False, "AMBIGUOUS_TP_SL_SAME_CANDLE")
        if tp_hit:
            return GuardDecision(False, "TP_ALREADY_TOUCHED")
        if sl_hit and config.sl_recovery_policy == "reject":
            return GuardDecision(False, "SL_ALREADY_TOUCHED")

    return GuardDecision(True, "OK")
