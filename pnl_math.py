from __future__ import annotations

from decimal import Decimal, InvalidOperation


def linear_return_pct(entry, exit_price, side: str) -> float | None:
    """Return price PnL percent for a USDT-margined linear position.

    Long:  (exit - entry) / entry
    Short: (entry - exit) / entry

    Returns None for missing/non-positive prices or an unknown side.
    """
    try:
        entry_d = Decimal(str(entry))
        exit_d = Decimal(str(exit_price))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if entry_d <= 0 or exit_d <= 0:
        return None

    side_u = str(side or "").upper()
    if side_u == "LONG":
        ret = (exit_d - entry_d) / entry_d
    elif side_u == "SHORT":
        ret = (entry_d - exit_d) / entry_d
    else:
        return None
    return float(ret * Decimal("100"))
