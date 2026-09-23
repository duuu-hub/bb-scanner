from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Literal, Optional

import numpy as np
import pandas as pd

Side = Literal["long", "short"]


@dataclass(frozen=True)
class SMCConfig:
    swing_left: int = 3
    swing_right: int = 3
    sweep_buffer_pct: float = 0.0005
    sl_buffer_pct: float = 0.0005
    displacement_bars: int = 3
    expiry_bars: int = 24
    max_swing_age: int = 100
    rrr: float = 2.0


@dataclass(frozen=True)
class Setup:
    side: Side
    sweep_idx: int
    sweep_extreme: float
    ob_high: float
    ob_low: float
    entry: float
    stop: float
    target: float
    created_idx: int
    sweep_time_ms: int
    created_time_ms: int


def find_swings(df: pd.DataFrame, left: int, right: int) -> tuple[np.ndarray, np.ndarray]:
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    n = len(df)
    is_sh = np.zeros(n, dtype=bool)
    is_sl = np.zeros(n, dtype=bool)

    for i in range(left, n - right):
        wh = high[i - left : i + right + 1]
        wl = low[i - left : i + right + 1]
        if high[i] == wh.max() and int((wh == high[i]).sum()) == 1:
            is_sh[i] = True
        if low[i] == wl.min() and int((wl == low[i]).sum()) == 1:
            is_sl[i] = True
    return is_sh, is_sl


def _ts_ms(df: pd.DataFrame, idx: int) -> int:
    return int(pd.Timestamp(df["Timestamp"].iloc[idx]).timestamp() * 1000)


def build_short_setup_causal(
    df: pd.DataFrame,
    sweep_i: int,
    current_i: int,
    swing_high: float,
    cfg: SMCConfig,
) -> Optional[Setup]:
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    op = df["Open"].to_numpy(dtype=float)
    cl = df["Close"].to_numpy(dtype=float)

    if current_i != sweep_i + cfg.displacement_bars:
        return None
    if sweep_i < 0 or current_i >= len(df):
        return None
    if not high[sweep_i] > swing_high * (1 + cfg.sweep_buffer_pct):
        return None
    if not cl[sweep_i] < swing_high:
        return None

    start = sweep_i + 1
    if start > current_i:
        return None
    if not np.all(cl[start : current_i + 1] < op[start : current_i + 1]):
        return None

    ob_idx = None
    for j in range(sweep_i, max(sweep_i - 10, -1), -1):
        if cl[j] > op[j]:
            ob_idx = j
            break
    if ob_idx is None:
        return None

    ob_high = float(high[ob_idx])
    ob_low = float(low[ob_idx])
    if not cl[current_i] < ob_low:
        return None

    entry = ob_low
    stop = ob_high * (1 + cfg.sl_buffer_pct)
    if stop <= entry:
        return None
    target = entry - cfg.rrr * (stop - entry)
    if target <= 0:
        return None

    return Setup(
        side="short",
        sweep_idx=sweep_i,
        sweep_extreme=float(high[sweep_i]),
        ob_high=ob_high,
        ob_low=ob_low,
        entry=float(entry),
        stop=float(stop),
        target=float(target),
        created_idx=current_i,
        sweep_time_ms=_ts_ms(df, sweep_i),
        created_time_ms=_ts_ms(df, current_i),
    )


def build_long_setup_causal(
    df: pd.DataFrame,
    sweep_i: int,
    current_i: int,
    swing_low: float,
    cfg: SMCConfig,
) -> Optional[Setup]:
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    op = df["Open"].to_numpy(dtype=float)
    cl = df["Close"].to_numpy(dtype=float)

    if current_i != sweep_i + cfg.displacement_bars:
        return None
    if sweep_i < 0 or current_i >= len(df):
        return None
    if not low[sweep_i] < swing_low * (1 - cfg.sweep_buffer_pct):
        return None
    if not cl[sweep_i] > swing_low:
        return None

    start = sweep_i + 1
    if start > current_i:
        return None
    if not np.all(cl[start : current_i + 1] > op[start : current_i + 1]):
        return None

    ob_idx = None
    for j in range(sweep_i, max(sweep_i - 10, -1), -1):
        if cl[j] < op[j]:
            ob_idx = j
            break
    if ob_idx is None:
        return None

    ob_high = float(high[ob_idx])
    ob_low = float(low[ob_idx])
    if not cl[current_i] > ob_high:
        return None

    entry = ob_high
    stop = ob_low * (1 - cfg.sl_buffer_pct)
    if stop >= entry:
        return None
    target = entry + cfg.rrr * (entry - stop)

    return Setup(
        side="long",
        sweep_idx=sweep_i,
        sweep_extreme=float(low[sweep_i]),
        ob_high=ob_high,
        ob_low=ob_low,
        entry=float(entry),
        stop=float(stop),
        target=float(target),
        created_idx=current_i,
        sweep_time_ms=_ts_ms(df, sweep_i),
        created_time_ms=_ts_ms(df, current_i),
    )


def detect_setup_at(
    df: pd.DataFrame,
    current_i: int,
    cfg: SMCConfig,
    swings: tuple[np.ndarray, np.ndarray] | None = None,
) -> Optional[Setup]:
    sweep_i = current_i - cfg.displacement_bars
    if sweep_i <= cfg.swing_left + cfg.swing_right:
        return None

    is_sh, is_sl = swings if swings is not None else find_swings(
        df, cfg.swing_left, cfg.swing_right
    )
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)

    for p in range(
        sweep_i - 1,
        max(sweep_i - cfg.max_swing_age, -1),
        -1,
    ):
        if p + cfg.swing_right > sweep_i:
            continue
        if is_sh[p]:
            setup = build_short_setup_causal(df, sweep_i, current_i, float(high[p]), cfg)
            if setup is not None:
                return setup
        if is_sl[p]:
            setup = build_long_setup_causal(df, sweep_i, current_i, float(low[p]), cfg)
            if setup is not None:
                return setup
    return None


def replay_active_setups(df: pd.DataFrame, cfg: SMCConfig) -> list[Setup]:
    """Replay setup lifecycle and return theoretical untouched pending setups.

    Ordering intentionally mirrors the research backtest:
    new-sweep invalidation -> body invalidation -> entry touch -> expiry.
    """
    if df.empty:
        return []

    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    cl = df["Close"].to_numpy(dtype=float)
    swings = find_swings(df, cfg.swing_left, cfg.swing_right)
    pending: list[Setup] = []

    for i in range(len(df)):
        still: list[Setup] = []
        for setup in pending:
            if i <= setup.created_idx:
                still.append(setup)
                continue

            if setup.side == "short" and high[i] > setup.sweep_extreme:
                continue
            if setup.side == "long" and low[i] < setup.sweep_extreme:
                continue

            body_breaks = (
                cl[i] > setup.ob_high
                if setup.side == "short"
                else cl[i] < setup.ob_low
            )
            if body_breaks:
                continue

            touched = (
                high[i] >= setup.entry
                if setup.side == "short"
                else low[i] <= setup.entry
            )
            if touched:
                continue

            if cfg.expiry_bars > 0 and i - setup.created_idx >= cfg.expiry_bars:
                continue

            still.append(setup)

        pending = still
        setup = detect_setup_at(df, i, cfg, swings)
        if setup is not None:
            pending.append(setup)

    return pending


def setup_id(strategy: str, setup: Setup) -> str:
    raw = (
        f"{strategy.upper()}|{setup.side}|{setup.created_time_ms}|"
        f"{setup.sweep_time_ms}|{setup.entry:.12g}|{setup.stop:.12g}"
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"SMC:{strategy.upper()}:{setup.side.upper()}:{setup.created_time_ms}:{digest}"


def setup_dict(strategy: str, symbol: str, setup: Setup) -> dict:
    return {
        "setup_id": setup_id(strategy, setup),
        "strategy": strategy.upper(),
        "symbol": symbol.upper(),
        **asdict(setup),
    }
