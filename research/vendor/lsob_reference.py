# Vendored reference implementation from maxs231/lsob-backtest
# Source: https://github.com/maxs231/lsob-backtest
# License: MIT; see research/vendor/LSOB_LICENSE
# Kept intentionally close to upstream for reproduction before modifications.

"""
LSOB (Liquidity Sweep Order Block) backtest for BTC 1h candles, long and short.

Detects a liquidity sweep, waits for displacement candles to confirm the
reversal, then places a limit order at the resulting order block. Stop and
target follow from the zone; see README.md for the full rule set.

Runs on Binance spot data as a proxy for Hyperliquid BTC-PERP, but charges
Hyperliquid's perp fees. The engine is deliberately pessimistic: adverse fills
win ties, stops pay taker fees plus slippage.

    python backtest_lsob.py
    python backtest_lsob.py --rrr 3 --start 2025-08-24
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import pandas as pd

# Default file locations follow the script, not the shell's working directory,
# so the module runs identically from an IDE play button and from a terminal.
SCRIPT_DIR = Path(__file__).resolve().parent

Side = Literal["long", "short"]

# Hyperliquid perp base tier. Cheaper tiers start at $5M of 14-day volume;
# this strategy turns over roughly $2.5M across two years on a $1k account, so
# the base tier is where it stays.
TAKER_FEE = 0.00045
MAKER_FEE = 0.00015


@dataclass
class Config:
    csv_path: str = str(SCRIPT_DIR / "btc_binance_1h.csv")

    # A pivot needs `swing_right` candles to its right before it is confirmed,
    # which is what keeps the engine free of look-ahead bias.
    swing_left: int = 3
    swing_right: int = 3

    # Rule A: the wick must clear the swing level by this much. Without it an
    # equal high would count as a sweep, and no stops sit above an equal high.
    sweep_buffer_pct: float = 0.0005
    # Placed just beyond the stop level so that merely touching the edge does
    # not stop us out. Set to 0 for exactly on the line.
    sl_buffer_pct: float = 0.0005
    # "zone" puts the stop at the far edge of the order block, "sweep" beyond
    # the sweep extreme. Zone is much tighter and tested better.
    stop_mode: Literal["zone", "sweep"] = "zone"
    # The single most decisive parameter: 2 candles loses money, 3 does not.
    displacement_bars: int = 3
    # Rule C: cancel an untouched limit order after N candles; 0 disables it.
    # The expiry matters far more with the tight zone stop than with the old
    # wide one — a zone that takes a day to get retested is stale, and a tight
    # stop behind it gets taken out. Disabling this cost 0.07 R per trade and
    # a third of the out-of-sample retention.
    expiry_bars: int = 24
    max_swing_age: int = 100

    rrr: float = 2.0
    risk_pct: float = 2.0
    initial_equity: float = 1_000.0
    slippage_pct: float = 0.0005

    leverage: float = 10.0
    maint_margin_frac: float = 0.0125

    maker_fee: float = MAKER_FEE
    taker_fee: float = TAKER_FEE
    # True models chasing price into the zone with a market order instead of
    # letting a limit rest there. Costs about a fifth of expectancy.
    entry_is_taker: bool = False


@dataclass
class Setup:
    """A detected pattern still waiting for price to return to the zone."""
    side: Side
    sweep_idx: int
    sweep_extreme: float
    ob_high: float
    ob_low: float
    entry: float
    stop: float
    target: float
    created_idx: int


@dataclass
class Trade:
    side: Side
    entry_idx: int
    entry_time: pd.Timestamp
    entry: float
    stop: float
    target: float
    exit_idx: int = -1
    exit_time: Optional[pd.Timestamp] = None
    exit_price: float = 0.0
    outcome: str = ""
    qty: float = 0.0
    pnl: float = 0.0
    r_multiple: float = 0.0
    fees: float = 0.0
    notional: float = 0.0
    margin: float = 0.0
    size_capped: bool = False
    liq_price: float = 0.0
    # Kept for charting and inspection: which candle swept, and where the zone
    # that produced this trade actually sat.
    sweep_time: Optional[pd.Timestamp] = None
    ob_high: float = 0.0
    ob_low: float = 0.0


def load_data(path: str, start: Optional[str] = None,
              end: Optional[str] = None) -> pd.DataFrame:
    """Load candles, optionally restricted to a date range for OOS testing."""
    df = pd.read_csv(path, parse_dates=["Timestamp"])
    df = df.sort_values("Timestamp").reset_index(drop=True)

    # Build one mask and slice via .loc: chained df[cond] assignment confuses
    # type checkers into thinking the result might be a Series.
    mask = pd.Series(True, index=df.index)
    if start:
        mask &= df["Timestamp"] >= pd.Timestamp(start, tz="UTC")
    if end:
        mask &= df["Timestamp"] <= pd.Timestamp(end, tz="UTC")

    # The engine addresses candles by position, so the index has to be a gapless
    # range again after slicing.
    return df.loc[mask].reset_index(drop=True)


def find_swings(df: pd.DataFrame, left: int, right: int) -> tuple[np.ndarray, np.ndarray]:
    """Mark fractal swing highs and lows.

    A pivot at index i is not knowable until index i + right, since the candles
    to its right must exist first. Callers must honour that delay; the main loop
    does. Vectorising this away silently reintroduces look-ahead bias.
    """
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    n = len(df)
    is_sh = np.zeros(n, dtype=bool)
    is_sl = np.zeros(n, dtype=bool)

    for i in range(left, n - right):
        window_h = high[i - left: i + right + 1]
        window_l = low[i - left: i + right + 1]
        # The extreme must be unique — two candles sharing the high is a double
        # top, not a clean pivot.
        if high[i] == window_h.max() and (window_h == high[i]).sum() == 1:
            is_sh[i] = True
        if low[i] == window_l.min() and (window_l == low[i]).sum() == 1:
            is_sl[i] = True

    return is_sh, is_sl


def build_short_setup(df: pd.DataFrame, i: int, swing_high: float,
                      cfg: Config) -> Optional[Setup]:
    """Build a short setup with candle `i` as the sweep, or None if invalid."""
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    op = df["Open"].to_numpy()
    cl = df["Close"].to_numpy()
    n = len(df)

    # Rule A: a genuine sweep, not an equal high.
    if not high[i] > swing_high * (1 + cfg.sweep_buffer_pct):
        return None
    # Rejection: price went above but closed back below. Otherwise this was a
    # real breakout, not a sweep.
    if not cl[i] < swing_high:
        return None

    # Displacement: consecutive bearish candles immediately after the sweep.
    d_start = i + 1
    d_end = d_start
    while d_end < n and cl[d_end] < op[d_end]:
        d_end += 1
    if d_end - d_start < cfg.displacement_bars:
        return None
    last_disp = d_end - 1

    # The order block is the last bullish candle before the drop.
    ob_idx = None
    for j in range(i, max(i - 10, -1), -1):
        if cl[j] > op[j]:
            ob_idx = j
            break
    if ob_idx is None:
        return None

    ob_high, ob_low = high[ob_idx], low[ob_idx]

    # The impulse has to leave the zone behind, or there is no retest to wait for.
    if not cl[last_disp] < ob_low:
        return None

    sweep_high = high[i]
    entry = ob_low
    stop_level = ob_high if cfg.stop_mode == "zone" else sweep_high
    stop = stop_level * (1 + cfg.sl_buffer_pct)

    if stop <= entry:
        return None
    target = entry - cfg.rrr * (stop - entry)
    if target <= 0:
        return None

    return Setup(side="short", sweep_idx=i, sweep_extreme=sweep_high,
                 ob_high=ob_high, ob_low=ob_low, entry=entry, stop=stop,
                 target=target, created_idx=last_disp)


def build_long_setup(df: pd.DataFrame, i: int, swing_low: float,
                     cfg: Config) -> Optional[Setup]:
    """Mirror of build_short_setup: sweep below a swing low, bullish impulse."""
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    op = df["Open"].to_numpy()
    cl = df["Close"].to_numpy()
    n = len(df)

    if not low[i] < swing_low * (1 - cfg.sweep_buffer_pct):
        return None
    if not cl[i] > swing_low:
        return None

    d_start = i + 1
    d_end = d_start
    while d_end < n and cl[d_end] > op[d_end]:
        d_end += 1
    if d_end - d_start < cfg.displacement_bars:
        return None
    last_disp = d_end - 1

    ob_idx = None
    for j in range(i, max(i - 10, -1), -1):
        if cl[j] < op[j]:
            ob_idx = j
            break
    if ob_idx is None:
        return None

    ob_high, ob_low = high[ob_idx], low[ob_idx]

    if not cl[last_disp] > ob_high:
        return None

    sweep_low = low[i]
    entry = ob_high
    stop_level = ob_low if cfg.stop_mode == "zone" else sweep_low
    stop = stop_level * (1 - cfg.sl_buffer_pct)

    if stop >= entry:
        return None
    target = entry + cfg.rrr * (entry - stop)

    return Setup(side="long", sweep_idx=i, sweep_extreme=sweep_low,
                 ob_high=ob_high, ob_low=ob_low, entry=entry, stop=stop,
                 target=target, created_idx=last_disp)


def run_backtest(df: pd.DataFrame, cfg: Config) -> tuple[list[Trade], pd.Series, dict]:
    """Walk the candles in order, managing trades and setups as they arrive."""
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

    invalidation_counts = {"B_zone_not_respected": 0, "C_expired": 0,
                           "D_new_sweep": 0, "filled": 0}
    size_capped_count = [0]
    liquidation_count = [0]

    for i in range(n):
        if open_trade is not None:
            t = open_trade

            # Stop and liquidation both sit on the losing side; price reaches
            # whichever is nearer to the entry first.
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
            # Initialised so that adding a third exit branch that forgets to set
            # the rate fails as a zero fee rather than an UnboundLocalError.
            exit_fee_rate = cfg.taker_fee

            # OHLC cannot resolve what happened first inside the candle, so when
            # stop and target both trade we assume the loss. Pessimistic on
            # purpose — the opposite assumption inflates every result.
            if hit_adverse:
                if is_liq:
                    exit_price = t.liq_price
                    outcome = "liquidation"
                    liquidation_count[0] += 1
                else:
                    slip = 1 - cfg.slippage_pct if t.side == "long" else 1 + cfg.slippage_pct
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
                # Both sides are charged. Deducting only the exit fee overstates
                # returns by roughly a third at this trade count.
                t.pnl = gross - t.fees

                risk_per_unit = abs(t.entry - t.stop)
                t.r_multiple = t.pnl / (risk_per_unit * t.qty) if risk_per_unit else 0.0
                t.exit_idx = i
                t.exit_time = ts.iloc[i]
                t.exit_price = exit_price
                t.outcome = outcome
                equity += t.pnl
                trades.append(t)
                open_trade = None

        # Rebuild rather than mutate: dropping from a list while iterating it
        # skips entries.
        still_pending: list[Setup] = []
        for s in pending:
            if i <= s.created_idx:
                still_pending.append(s)
                continue

            # Rule D: running past the original extreme makes this a new pattern,
            # so the old setup is void.
            if s.side == "short" and high[i] > s.sweep_extreme:
                invalidation_counts["D_new_sweep"] += 1
                continue
            if s.side == "long" and low[i] < s.sweep_extreme:
                invalidation_counts["D_new_sweep"] += 1
                continue

            touched = high[i] >= s.entry if s.side == "short" else low[i] <= s.entry

            # Rule B: wicks into the zone are the point, but a body closing
            # through it means the zone failed.
            body_breaks = (cl[i] > s.ob_high) if s.side == "short" else (cl[i] < s.ob_low)
            if body_breaks:
                invalidation_counts["B_zone_not_respected"] += 1
                continue

            if touched and open_trade is None:
                # Size from the stop distance so every trade risks risk_pct of
                # equity, whatever the zone height happens to be.
                risk_per_unit = abs(s.entry - s.stop)
                qty = (equity * cfg.risk_pct / 100.0) / risk_per_unit

                # Leverage caps exposure, it does not amplify returns: the risk
                # calculation above already fixed the position size.
                max_notional = equity * cfg.leverage
                capped = False
                if s.entry * qty > max_notional:
                    qty = max_notional / s.entry
                    capped = True
                    size_capped_count[0] += 1

                notional = s.entry * qty
                # Isolated margin: the position dies once losses eat the posted
                # margin down to the maintenance requirement.
                liq_move = (1.0 / cfg.leverage) - cfg.maint_margin_frac
                liq_price = (s.entry * (1 - liq_move) if s.side == "long"
                             else s.entry * (1 + liq_move))

                entry_fee_rate = cfg.taker_fee if cfg.entry_is_taker else cfg.maker_fee
                open_trade = Trade(
                    side=s.side, entry_idx=i, entry_time=ts.iloc[i],
                    entry=s.entry, stop=s.stop, target=s.target, qty=qty,
                    fees=s.entry * qty * entry_fee_rate, notional=notional,
                    margin=notional / cfg.leverage, size_capped=capped,
                    liq_price=liq_price, sweep_time=ts.iloc[s.sweep_idx],
                    ob_high=s.ob_high, ob_low=s.ob_low)
                invalidation_counts["filled"] += 1
                continue

            if cfg.expiry_bars > 0 and i - s.created_idx >= cfg.expiry_bars:
                invalidation_counts["C_expired"] += 1
                continue

            still_pending.append(s)

        pending = still_pending

        # A sweep needs its displacement candles to have completed before the
        # setup exists, so the candidate sweep sits that many bars back.
        sweep_i = i - cfg.displacement_bars

        if sweep_i > cfg.swing_left + cfg.swing_right:
            for p in range(sweep_i - 1, max(sweep_i - cfg.max_swing_age, -1), -1):
                # Skip pivots that were not yet confirmed when the sweep happened.
                if p + cfg.swing_right > sweep_i:
                    continue

                if is_sh[p]:
                    s = build_short_setup(df, sweep_i, high[p], cfg)
                    if s is not None and s.created_idx == i:
                        pending.append(s)
                        break
                if is_sl[p]:
                    s = build_long_setup(df, sweep_i, low[p], cfg)
                    if s is not None and s.created_idx == i:
                        pending.append(s)
                        break

        # Mark open positions to market, otherwise the curve flatlines through
        # every trade and understates the drawdown.
        if open_trade is not None:
            direction = 1 if open_trade.side == "long" else -1
            unrealized = direction * (cl[i] - open_trade.entry) * open_trade.qty
            equity_curve[i] = equity + unrealized
        else:
            equity_curve[i] = equity

    invalidation_counts["size_capped_by_leverage"] = size_capped_count[0]
    invalidation_counts["liquidations"] = liquidation_count[0]
    return trades, pd.Series(equity_curve, index=ts), invalidation_counts


def compute_metrics(trades: list[Trade], equity: pd.Series, cfg: Config) -> dict:
    if not trades:
        return {"trades": 0}

    pnls = np.array([t.pnl for t in trades])
    wins = pnls > 0
    losses = pnls <= 0

    avg_win = pnls[wins].mean() if wins.any() else 0.0
    avg_loss = abs(pnls[losses].mean()) if losses.any() else 0.0

    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_dd = drawdown.min()

    hourly_ret = equity.pct_change().dropna()
    ann_factor = np.sqrt(24 * 365)
    sharpe = (hourly_ret.mean() / hourly_ret.std() * ann_factor) if hourly_ret.std() > 0 else 0.0

    gross_profit = pnls[wins].sum() if wins.any() else 0.0
    gross_loss = abs(pnls[losses].sum()) if losses.any() else 0.0
    total_fees = sum(t.fees for t in trades)
    gross_pnl = sum(t.pnl for t in trades) + total_fees

    longs = [t for t in trades if t.side == "long"]
    shorts = [t for t in trades if t.side == "short"]

    def wr(subset: list[Trade]) -> float:
        return 100.0 * sum(1 for t in subset if t.pnl > 0) / len(subset) if subset else 0.0

    return {
        "trades": len(trades),
        "longs": len(longs),
        "shorts": len(shorts),
        "win_rate": 100.0 * wins.sum() / len(trades),
        "win_rate_long": wr(longs),
        "win_rate_short": wr(shorts),
        "total_return_pct": 100.0 * (equity.iloc[-1] / cfg.initial_equity - 1),
        "final_equity": equity.iloc[-1],
        "max_drawdown_pct": 100.0 * max_dd,
        "sharpe": sharpe,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        # Sits below the configured RRR because of fees and slippage.
        "rrr_realized": (avg_win / avg_loss) if avg_loss > 0 else float("inf"),
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
        "expectancy_r": np.mean([t.r_multiple for t in trades]),
        "total_fees": total_fees,
        "gross_pnl": gross_pnl,
        "fee_share_of_gross": (100.0 * total_fees / gross_pnl
                               if gross_pnl > 0 else float("nan")),
        # Fees expressed in R, directly comparable against expectancy.
        "fee_r_per_trade": np.mean([t.fees / (abs(t.entry - t.stop) * t.qty)
                                    for t in trades if t.qty > 0]),
        "avg_notional": np.mean([t.notional for t in trades]),
        "max_notional": max(t.notional for t in trades),
        "avg_effective_leverage": np.mean([t.notional for t in trades]) / cfg.initial_equity,
    }


def print_report(m: dict, inval: dict, cfg: Config, df: pd.DataFrame) -> None:
    print("=" * 62)
    print("LSOB BACKTEST - BTC 1h (Binance spot proxy for BTC-PERP)")
    print("=" * 62)
    print(f"Period          : {df['Timestamp'].iloc[0]:%Y-%m-%d} -> {df['Timestamp'].iloc[-1]:%Y-%m-%d}"
          f"  ({len(df)} candles)")
    expiry_txt = f"{cfg.expiry_bars} bars" if cfg.expiry_bars > 0 else "OFF (rule C disabled)"
    print(f"Params          : RRR={cfg.rrr}  risk={cfg.risk_pct}%  expiry={expiry_txt}  "
          f"displacement>={cfg.displacement_bars}  leverage={cfg.leverage:g}x")
    print(f"Stop            : {cfg.stop_mode} "
          f"({'far edge of order block' if cfg.stop_mode == 'zone' else 'beyond sweep extreme'})"
          f"  buffer {cfg.sl_buffer_pct*100:.3f}%")
    print(f"Fees            : maker {cfg.maker_fee*100:.3f}%  taker {cfg.taker_fee*100:.3f}%  "
          f"entry as {'TAKER' if cfg.entry_is_taker else 'maker'}  "
          f"slippage {cfg.slippage_pct*100:.3f}% on stops")
    print("-" * 62)

    if m.get("trades", 0) == 0:
        print("No trades generated.")
        return

    print(f"Trades          : {m['trades']}  (long {m['longs']} / short {m['shorts']})")
    print(f"Win Rate        : {m['win_rate']:.1f}%   (long {m['win_rate_long']:.1f}% / "
          f"short {m['win_rate_short']:.1f}%)")
    print(f"Total Return    : {m['total_return_pct']:+.2f}%   "
          f"(${cfg.initial_equity:,.0f} -> ${m['final_equity']:,.0f})")
    print(f"Max Drawdown    : {m['max_drawdown_pct']:.2f}%")
    print(f"Sharpe (ann.)   : {m['sharpe']:.2f}")
    print(f"Realized RRR    : {m['rrr_realized']:.2f}  (avg win ${m['avg_win']:,.0f} / "
          f"avg loss ${m['avg_loss']:,.0f})")
    print(f"Profit Factor   : {m['profit_factor']:.2f}")
    print(f"Expectancy      : {m['expectancy_r']:+.3f} R per trade")
    print("-" * 62)
    print("Fees:")
    print(f"  Gross P&L (before fees)            : ${m['gross_pnl']:,.0f}")
    print(f"  Total fees paid                    : ${m['total_fees']:,.0f}")
    print(f"  Net P&L                            : ${m['gross_pnl'] - m['total_fees']:,.0f}")
    print(f"  Fees as share of gross profit      : {m['fee_share_of_gross']:.1f}%")
    print(f"  Fee cost per trade                 : {m['fee_r_per_trade']:.4f} R"
          f"  (vs {m['expectancy_r']:+.3f} R net expectancy)")
    print(f"  Break-even fee level               : "
          f"{m['fee_r_per_trade'] + m['expectancy_r']:.4f} R per trade")
    print("-" * 62)
    print("Leverage / margin:")
    print(f"  Configured leverage                : {cfg.leverage:g}x")
    print(f"  Avg position notional              : ${m['avg_notional']:,.0f} "
          f"(= {m['avg_effective_leverage']:.1f}x starting equity)")
    print(f"  Max position notional              : ${m['max_notional']:,.0f}")
    print(f"  Sizes capped by leverage limit     : {inval['size_capped_by_leverage']}")
    print(f"  Liquidations                       : {inval['liquidations']}")
    print("-" * 62)
    # Worth printing: a filter that never fires offers no protection, however
    # good it sounds in theory.
    print("Setup outcomes (invalidation rules):")
    total_setups = sum(inval.values())
    print(f"  Filled (traded)                    : {inval['filled']}")
    print(f"  B: zone not respected (close thru) : {inval['B_zone_not_respected']}")
    print(f"  C: expired, zone never touched     : {inval['C_expired']}")
    print(f"  D: new liquidity sweep             : {inval['D_new_sweep']}")
    print(f"  Total setups detected              : {total_setups}")
    if total_setups:
        print(f"  Fill rate                          : {100*inval['filled']/total_setups:.1f}%")
    print("=" * 62)


def plot_equity(equity: pd.Series, df: pd.DataFrame, m: dict, cfg: Config, out_path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")   # file output only; must be set before pyplot
    import matplotlib.pyplot as plt

    running_max = equity.cummax()
    drawdown = 100.0 * (equity - running_max) / running_max

    fig, axes = plt.subplots(3, 1, figsize=(14, 11), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1.4, 1.6]})

    ax = axes[0]
    ax.plot(equity.index, equity.values, color="#1f77b4", linewidth=1.4, label="Strategy equity")
    ax.axhline(cfg.initial_equity, color="grey", linestyle="--", linewidth=1, label="Start capital")
    ax.set_ylabel("Equity (USD)")
    ax.set_title(f"LSOB Strategy — BTC 1h  |  {cfg.leverage:g}x leverage, {cfg.risk_pct:g}% risk/trade, "
                 f"RRR {cfg.rrr:g}, stop at {cfg.stop_mode}\n"
                 f"Return {m['total_return_pct']:+.1f}%  |  Max DD {m['max_drawdown_pct']:.1f}%  |  "
                 f"Win rate {m['win_rate']:.1f}%  |  {m['trades']} trades",
                 fontsize=12)
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)

    ax = axes[1]
    ax.fill_between(drawdown.index, drawdown.values, 0, color="#d62728", alpha=0.5)
    ax.set_ylabel("Drawdown (%)")
    ax.grid(alpha=0.3)

    # Buy and hold is the honest benchmark: does the strategy beat doing nothing?
    ax = axes[2]
    bh = cfg.initial_equity * df["Close"] / df["Close"].iloc[0]
    ax.plot(equity.index, bh.values, color="#ff7f0e", linewidth=1.2, label="Buy & hold BTC")
    ax.plot(equity.index, equity.values, color="#1f77b4", linewidth=1.2, label="Strategy")
    ax.set_ylabel("USD")
    ax.set_xlabel("Date")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description="LSOB (Liquidity Sweep Order Block) backtest on BTC 1h candles.")
    p.add_argument("--csv", default=str(SCRIPT_DIR / "btc_binance_1h.csv"))
    p.add_argument("--rrr", type=float, default=2.0)
    p.add_argument("--risk-pct", type=float, default=2.0)
    p.add_argument("--expiry-bars", type=int, default=24,
                   help="Cancel an untouched limit order after N candles; 0 disables rule C")
    p.add_argument("--displacement-bars", type=int, default=3)
    p.add_argument("--swing-left", type=int, default=3)
    p.add_argument("--swing-right", type=int, default=3)
    p.add_argument("--sweep-buffer-pct", type=float, default=0.0005)
    p.add_argument("--sl-buffer-pct", type=float, default=0.0005,
                   help="Buffer beyond the stop level; use 0 for exactly on the edge")
    p.add_argument("--stop-mode", choices=["zone", "sweep"], default="zone",
                   help="zone: stop at the far edge of the order block (default). "
                        "sweep: stop beyond the sweep extreme")
    p.add_argument("--slippage-pct", type=float, default=0.0005)
    p.add_argument("--equity", type=float, default=1_000.0)
    p.add_argument("--leverage", type=float, default=10.0)
    p.add_argument("--maker-fee", type=float, default=MAKER_FEE,
                   help="Maker fee as a fraction, e.g. 0.00015 for 0.015%%")
    p.add_argument("--taker-fee", type=float, default=TAKER_FEE,
                   help="Taker fee as a fraction, e.g. 0.00045 for 0.045%%")
    p.add_argument("--entry-is-taker", action="store_true",
                   help="Assume entries are chased with market orders instead of resting limits")
    p.add_argument("--start", default=None, help="Only use candles from this date (YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="Only use candles up to this date (YYYY-MM-DD)")
    p.add_argument("--trades-csv", default=str(SCRIPT_DIR / "trades.csv"))
    p.add_argument("--equity-csv", default=None, help="Optional path to dump the equity curve")
    p.add_argument("--plot", default=str(SCRIPT_DIR / "equity_curve.png"))
    p.add_argument("--no-plot", action="store_true", help="Skip chart and trade-list output")
    args = p.parse_args()

    cfg = Config(csv_path=args.csv, rrr=args.rrr, risk_pct=args.risk_pct,
                 expiry_bars=args.expiry_bars, displacement_bars=args.displacement_bars,
                 swing_left=args.swing_left, swing_right=args.swing_right,
                 sweep_buffer_pct=args.sweep_buffer_pct, sl_buffer_pct=args.sl_buffer_pct,
                 slippage_pct=args.slippage_pct, initial_equity=args.equity,
                 leverage=args.leverage, maker_fee=args.maker_fee,
                 taker_fee=args.taker_fee, entry_is_taker=args.entry_is_taker,
                 stop_mode=args.stop_mode)

    if not Path(cfg.csv_path).exists():
        raise SystemExit(f"Data file not found: {cfg.csv_path}\n"
                         f"Run: python fetch_data_binance.py --interval 1h --days 730")

    df = load_data(cfg.csv_path, args.start, args.end)
    if len(df) < 100:
        raise SystemExit(f"Only {len(df)} candles in the selected date range — too few to test.")

    trades, equity, inval = run_backtest(df, cfg)
    metrics = compute_metrics(trades, equity, cfg)
    print_report(metrics, inval, cfg, df)

    if args.no_plot:
        return

    if trades:
        tdf = pd.DataFrame([{
            "side": t.side, "entry_time": t.entry_time, "entry": t.entry,
            "stop": t.stop, "target": t.target, "exit_time": t.exit_time,
            "exit_price": t.exit_price, "outcome": t.outcome, "qty": t.qty,
            "pnl": t.pnl, "r_multiple": t.r_multiple, "fees": t.fees,
            "sweep_time": t.sweep_time, "ob_high": t.ob_high, "ob_low": t.ob_low,
        } for t in trades])
        tdf.to_csv(args.trades_csv, index=False)
        print(f"\nTrade list written to {Path(args.trades_csv).resolve()}")

    if args.equity_csv:
        equity.rename("Equity").to_csv(args.equity_csv)
        print(f"Equity curve written to {Path(args.equity_csv).resolve()}")

    plot_equity(equity, df, metrics, cfg, args.plot)
    print(f"Equity chart written to {Path(args.plot).resolve()}")


if __name__ == "__main__":
    main()
