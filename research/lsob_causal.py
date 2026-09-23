from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from research.vendor.lsob_reference import (
    Config,
    Setup,
    Trade,
    find_swings,
)


def build_short_setup_causal(
    df: pd.DataFrame,
    sweep_i: int,
    current_i: int,
    swing_high: float,
    cfg: Config,
) -> Optional[Setup]:
    """Strictly causal short setup known at current_i close."""
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    op = df["Open"].to_numpy()
    cl = df["Close"].to_numpy()

    if current_i != sweep_i + cfg.displacement_bars:
        return None
    if sweep_i < 0 or current_i >= len(df):
        return None
    if not high[sweep_i] > swing_high * (1 + cfg.sweep_buffer_pct):
        return None
    if not cl[sweep_i] < swing_high:
        return None

    d_start = sweep_i + 1
    if d_start > current_i:
        return None
    # Only candles already closed by current_i are inspected.
    if not np.all(cl[d_start:current_i + 1] < op[d_start:current_i + 1]):
        return None

    ob_idx = None
    for j in range(sweep_i, max(sweep_i - 10, -1), -1):
        if cl[j] > op[j]:
            ob_idx = j
            break
    if ob_idx is None:
        return None

    ob_high, ob_low = high[ob_idx], low[ob_idx]
    if not cl[current_i] < ob_low:
        return None

    sweep_high = high[sweep_i]
    entry = ob_low
    stop_level = ob_high if cfg.stop_mode == "zone" else sweep_high
    stop = stop_level * (1 + cfg.sl_buffer_pct)
    if stop <= entry:
        return None
    target = entry - cfg.rrr * (stop - entry)
    if target <= 0:
        return None

    return Setup(
        side="short",
        sweep_idx=sweep_i,
        sweep_extreme=sweep_high,
        ob_high=ob_high,
        ob_low=ob_low,
        entry=entry,
        stop=stop,
        target=target,
        created_idx=current_i,
    )


def build_long_setup_causal(
    df: pd.DataFrame,
    sweep_i: int,
    current_i: int,
    swing_low: float,
    cfg: Config,
) -> Optional[Setup]:
    """Strictly causal long setup known at current_i close."""
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    op = df["Open"].to_numpy()
    cl = df["Close"].to_numpy()

    if current_i != sweep_i + cfg.displacement_bars:
        return None
    if sweep_i < 0 or current_i >= len(df):
        return None
    if not low[sweep_i] < swing_low * (1 - cfg.sweep_buffer_pct):
        return None
    if not cl[sweep_i] > swing_low:
        return None

    d_start = sweep_i + 1
    if d_start > current_i:
        return None
    if not np.all(cl[d_start:current_i + 1] > op[d_start:current_i + 1]):
        return None

    ob_idx = None
    for j in range(sweep_i, max(sweep_i - 10, -1), -1):
        if cl[j] < op[j]:
            ob_idx = j
            break
    if ob_idx is None:
        return None

    ob_high, ob_low = high[ob_idx], low[ob_idx]
    if not cl[current_i] > ob_high:
        return None

    sweep_low = low[sweep_i]
    entry = ob_high
    stop_level = ob_low if cfg.stop_mode == "zone" else sweep_low
    stop = stop_level * (1 - cfg.sl_buffer_pct)
    if stop >= entry:
        return None
    target = entry + cfg.rrr * (entry - stop)

    return Setup(
        side="long",
        sweep_idx=sweep_i,
        sweep_extreme=sweep_low,
        ob_high=ob_high,
        ob_low=ob_low,
        entry=entry,
        stop=stop,
        target=target,
        created_idx=current_i,
    )


def run_backtest_causal(
    df: pd.DataFrame,
    cfg: Config,
) -> tuple[list[Trade], pd.Series, dict]:
    """Reference trade management with strictly causal setup formation."""
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    cl = df["Close"].to_numpy()
    ts = df["Timestamp"]
    n = len(df)

    is_sh, is_sl = find_swings(df, cfg.swing_left, cfg.swing_right)

    equity = cfg.initial_equity
    equity_curve = np.full(n, np.nan)
    pending: list[Setup] = []
    open_trade: Optional[Trade] = None
    trades: list[Trade] = []

    invalidation_counts = {
        "B_zone_not_respected": 0,
        "C_expired": 0,
        "D_new_sweep": 0,
        "filled": 0,
    }
    size_capped_count = 0
    liquidation_count = 0

    for i in range(n):
        if open_trade is not None:
            t = open_trade
            if t.side == "long":
                adverse_level = max(t.stop, t.liq_price)
                hit_adverse = low[i] <= adverse_level
                hit_target = high[i] >= t.target
            else:
                adverse_level = min(t.stop, t.liq_price)
                hit_adverse = high[i] >= adverse_level
                hit_target = low[i] <= t.target
            is_liq = adverse_level == t.liq_price

            exit_price = None
            outcome = ""
            exit_fee_rate = cfg.taker_fee
            if hit_adverse:
                if is_liq:
                    exit_price = t.liq_price
                    outcome = "liquidation"
                    liquidation_count += 1
                else:
                    slip = (
                        1 - cfg.slippage_pct
                        if t.side == "long"
                        else 1 + cfg.slippage_pct
                    )
                    exit_price = t.stop * slip
                    outcome = "stop"
                exit_fee_rate = cfg.taker_fee
            elif hit_target:
                exit_price = t.target
                outcome = "target"
                exit_fee_rate = cfg.maker_fee

            if exit_price is not None:
                direction = 1 if t.side == "long" else -1
                gross = direction * (exit_price - t.entry) * t.qty
                t.fees += exit_price * t.qty * exit_fee_rate
                t.pnl = gross - t.fees
                risk_per_unit = abs(t.entry - t.stop)
                t.r_multiple = (
                    t.pnl / (risk_per_unit * t.qty)
                    if risk_per_unit and t.qty
                    else 0.0
                )
                t.exit_idx = i
                t.exit_time = ts.iloc[i]
                t.exit_price = exit_price
                t.outcome = outcome
                equity += t.pnl
                trades.append(t)
                open_trade = None

        still_pending: list[Setup] = []
        for setup in pending:
            if i <= setup.created_idx:
                still_pending.append(setup)
                continue

            if setup.side == "short" and high[i] > setup.sweep_extreme:
                invalidation_counts["D_new_sweep"] += 1
                continue
            if setup.side == "long" and low[i] < setup.sweep_extreme:
                invalidation_counts["D_new_sweep"] += 1
                continue

            touched = (
                high[i] >= setup.entry
                if setup.side == "short"
                else low[i] <= setup.entry
            )
            body_breaks = (
                cl[i] > setup.ob_high
                if setup.side == "short"
                else cl[i] < setup.ob_low
            )
            if body_breaks:
                invalidation_counts["B_zone_not_respected"] += 1
                continue

            if touched and open_trade is None:
                risk_per_unit = abs(setup.entry - setup.stop)
                qty = (equity * cfg.risk_pct / 100.0) / risk_per_unit
                max_notional = equity * cfg.leverage
                capped = False
                if setup.entry * qty > max_notional:
                    qty = max_notional / setup.entry
                    capped = True
                    size_capped_count += 1

                notional = setup.entry * qty
                liq_move = (1.0 / cfg.leverage) - cfg.maint_margin_frac
                liq_price = (
                    setup.entry * (1 - liq_move)
                    if setup.side == "long"
                    else setup.entry * (1 + liq_move)
                )
                entry_fee_rate = (
                    cfg.taker_fee if cfg.entry_is_taker else cfg.maker_fee
                )
                open_trade = Trade(
                    side=setup.side,
                    entry_idx=i,
                    entry_time=ts.iloc[i],
                    entry=setup.entry,
                    stop=setup.stop,
                    target=setup.target,
                    qty=qty,
                    fees=setup.entry * qty * entry_fee_rate,
                    notional=notional,
                    margin=notional / cfg.leverage,
                    size_capped=capped,
                    liq_price=liq_price,
                    sweep_time=ts.iloc[setup.sweep_idx],
                    ob_high=setup.ob_high,
                    ob_low=setup.ob_low,
                )
                invalidation_counts["filled"] += 1
                continue

            if (
                cfg.expiry_bars > 0
                and i - setup.created_idx >= cfg.expiry_bars
            ):
                invalidation_counts["C_expired"] += 1
                continue

            still_pending.append(setup)

        pending = still_pending

        sweep_i = i - cfg.displacement_bars
        if sweep_i > cfg.swing_left + cfg.swing_right:
            for p in range(
                sweep_i - 1,
                max(sweep_i - cfg.max_swing_age, -1),
                -1,
            ):
                if p + cfg.swing_right > sweep_i:
                    continue

                if is_sh[p]:
                    setup = build_short_setup_causal(
                        df, sweep_i, i, high[p], cfg
                    )
                    if setup is not None:
                        pending.append(setup)
                        break
                if is_sl[p]:
                    setup = build_long_setup_causal(
                        df, sweep_i, i, low[p], cfg
                    )
                    if setup is not None:
                        pending.append(setup)
                        break

        if open_trade is not None:
            direction = 1 if open_trade.side == "long" else -1
            unrealized = (
                direction
                * (cl[i] - open_trade.entry)
                * open_trade.qty
            )
            equity_curve[i] = equity + unrealized
        else:
            equity_curve[i] = equity

    invalidation_counts["size_capped_by_leverage"] = size_capped_count
    invalidation_counts["liquidations"] = liquidation_count
    return trades, pd.Series(equity_curve, index=ts), invalidation_counts
