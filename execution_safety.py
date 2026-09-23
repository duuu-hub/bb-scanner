from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CONFIG_PATH = Path("config/trading_mode.json")
VALID_REGIMES = {"OFF", "BULL", "RANGE", "BEAR"}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str
    reason: str
    signal_id: str
    planned_notional_usdt: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    regime = str(cfg.get("active_regime", "OFF")).upper()
    if regime not in VALID_REGIMES:
        raise ValueError(f"Invalid active_regime: {regime}")
    cfg["active_regime"] = regime
    return cfg


def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def make_signal_id(signal: dict[str, Any]) -> str:
    explicit = str(signal.get("signal_id") or "").strip()
    if explicit:
        return explicit

    raw = "|".join(
        [
            str(signal.get("strategy_name") or ""),
            str(signal.get("regime") or ""),
            str(signal.get("symbol") or ""),
            str(signal.get("side") or ""),
            str(signal.get("signal_time") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def reject(signal_id: str, code: str, reason: str) -> Decision:
    return Decision(False, code, reason, signal_id, 0.0)


def _position_exists(positions: Iterable[dict[str, Any]], symbol: str) -> bool:
    target = symbol.upper()
    for row in positions:
        if str(row.get("symbol", "")).upper() != target:
            continue
        try:
            size = float(row.get("size", row.get("total", 0)) or 0)
        except (TypeError, ValueError):
            size = 0.0
        if abs(size) > 0:
            return True
    return False


def _path_touches(
    side: str,
    tp: float,
    sl: float,
    candles_1m: Iterable[dict[str, Any]],
) -> tuple[bool, bool, bool]:
    tp_hit = False
    sl_hit = False
    ambiguous = False

    for bar in candles_1m:
        high = float(bar["high"])
        low = float(bar["low"])

        if side == "LONG":
            hit_tp_this = high >= tp
            hit_sl_this = low <= sl
        else:
            hit_tp_this = low <= tp
            hit_sl_this = high >= sl

        if hit_tp_this and hit_sl_this:
            ambiguous = True
        tp_hit = tp_hit or hit_tp_this
        sl_hit = sl_hit or hit_sl_this

    return tp_hit, sl_hit, ambiguous


def evaluate_signal(
    signal: dict[str, Any],
    market: dict[str, Any],
    account: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> Decision:
    """Pure pre-trade gate. It never sends an order.

    Required signal fields:
      symbol, side, signal_time, entry_low, entry_high, tp, sl,
      max_hold_minutes, strategy_name, regime

    Required market fields:
      current_price, bid, ask, candles_1m_since_signal

    Required account fields:
      equity_usdt, gross_exposure_usdt, positions
      (positions must be an actual verified snapshot when required)

    State:
      seen_signal_ids: list[str]
    """

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sid = make_signal_id(signal)

    active = str(config.get("active_regime", "OFF")).upper()
    if active == "OFF":
        return reject(sid, "TRADING_OFF", "New entries are disabled.")

    regime = str(signal.get("regime") or "").upper()
    if regime not in {"BULL", "RANGE", "BEAR"}:
        return reject(sid, "BAD_REGIME", f"Signal regime is invalid: {regime!r}")
    if regime != active:
        return reject(
            sid,
            "REGIME_MISMATCH",
            f"Signal regime {regime} != active regime {active}.",
        )

    symbol = str(signal.get("symbol") or "").upper()
    side = str(signal.get("side") or "").upper()
    if not symbol:
        return reject(sid, "BAD_SIGNAL", "Missing symbol.")
    if side not in {"LONG", "SHORT"}:
        return reject(sid, "BAD_SIGNAL", f"Invalid side: {side!r}")

    seen = set(state.get("seen_signal_ids") or [])
    if sid in seen:
        return reject(sid, "DUPLICATE_SIGNAL", "This signal was already handled.")

    try:
        signal_time = parse_time(str(signal["signal_time"]))
        age_min = (now - signal_time).total_seconds() / 60.0
    except Exception as exc:
        return reject(sid, "BAD_SIGNAL_TIME", f"Invalid signal_time: {exc}")

    if age_min < -0.25:
        return reject(sid, "FUTURE_SIGNAL", f"Signal is {abs(age_min):.2f} minutes in the future.")

    ttl = float(config.get("signal_ttl_minutes", 3))
    if age_min > ttl:
        return reject(
            sid,
            "STALE_SIGNAL",
            f"Signal age {age_min:.2f}m exceeds TTL {ttl:.2f}m.",
        )

    try:
        entry_low = float(signal["entry_low"])
        entry_high = float(signal["entry_high"])
        tp = float(signal["tp"])
        sl = float(signal["sl"])
        current = float(market["current_price"])
        bid = float(market["bid"])
        ask = float(market["ask"])
    except Exception as exc:
        return reject(sid, "BAD_PRICE_DATA", f"Missing/invalid price field: {exc}")

    if not (0 < entry_low <= entry_high and current > 0 and bid > 0 and ask > 0):
        return reject(sid, "BAD_PRICE_DATA", "Prices must be positive and entry_low <= entry_high.")

    if not (entry_low <= current <= entry_high):
        return reject(
            sid,
            "OUTSIDE_ENTRY_RANGE",
            f"Current price {current} is outside [{entry_low}, {entry_high}].",
        )

    if side == "LONG" and not (sl < current < tp):
        return reject(sid, "BAD_TP_SL", "LONG requires SL < current price < TP.")
    if side == "SHORT" and not (tp < current < sl):
        return reject(sid, "BAD_TP_SL", "SHORT requires TP < current price < SL.")

    spread_pct = (ask - bid) / ((ask + bid) / 2.0) * 100.0
    max_spread = float(config.get("max_spread_pct", 0.20))
    if spread_pct < 0 or spread_pct > max_spread:
        return reject(
            sid,
            "SPREAD_TOO_WIDE",
            f"Spread {spread_pct:.4f}% exceeds {max_spread:.4f}%.",
        )

    candles = market.get("candles_1m_since_signal") or []
    tp_hit, sl_hit, ambiguous = _path_touches(side, tp, sl, candles)

    if ambiguous and str(config.get("ambiguous_bar_policy", "reject")).lower() == "reject":
        return reject(
            sid,
            "AMBIGUOUS_PATH",
            "At least one 1m candle touched both TP and SL; path order is unknown.",
        )

    if tp_hit and str(config.get("tp_touch_policy", "reject")).lower() == "reject":
        return reject(
            sid,
            "TP_ALREADY_TOUCHED",
            "TP was already touched after the signal; the setup is considered consumed.",
        )

    if sl_hit and str(config.get("sl_recovery_policy", "reject")).lower() == "reject":
        return reject(
            sid,
            "SL_ALREADY_TOUCHED",
            "SL was already touched after the signal; recovery entries are disabled.",
        )

    if bool(config.get("require_position_snapshot", True)):
        if account.get("positions_verified") is not True:
            return reject(
                sid,
                "POSITION_SNAPSHOT_UNVERIFIED",
                "Current exchange positions are not verified; fail-closed.",
            )

    positions = account.get("positions") or []
    if _position_exists(positions, symbol):
        return reject(
            sid,
            "POSITION_EXISTS",
            f"An open {symbol} position already exists.",
        )

    try:
        equity = float(account["equity_usdt"])
        gross = float(account.get("gross_exposure_usdt", 0.0))
    except Exception as exc:
        return reject(sid, "BAD_ACCOUNT_DATA", f"Invalid account values: {exc}")

    if equity <= 0 or gross < 0:
        return reject(sid, "BAD_ACCOUNT_DATA", "Equity must be > 0 and gross exposure >= 0.")

    fraction = float(config.get("position_fraction", 0.30))
    max_fraction = float(config.get("max_total_exposure_fraction", 2.00))
    if fraction <= 0 or max_fraction <= 0:
        return reject(sid, "BAD_CONFIG", "Exposure fractions must be positive.")

    planned = equity * fraction
    max_gross = equity * max_fraction
    if gross + planned > max_gross + 1e-9:
        return reject(
            sid,
            "EXPOSURE_LIMIT",
            f"Gross exposure would become {gross + planned:.2f} USDT > {max_gross:.2f} USDT.",
        )

    return Decision(
        True,
        "ALLOW",
        "All pre-trade safety checks passed.",
        sid,
        round(planned, 8),
    )
