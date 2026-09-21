import argparse
import math
import heapq
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import fetch_range, rows_to_df

MIN = 60_000
FEE_PCT = 0.12
POSITION_FRACTION = 0.30
CAPITAL_CAP = 1.00
TF_NAMES = ["1W", "1D", "12H", "4H", "1H", "30M", "15M"]

STRATEGIES = {
    "L1_MOMENTUM_1H10": {
        "direction": "LONG", "tp": 10.0, "sl": 5.0, "horizon_min": 720,
    },
    "L2_EXPLOSIVE_4H30": {
        "direction": "LONG", "tp": 10.0, "sl": 2.5, "horizon_min": 60,
    },
    "L3_4H_LAG": {
        "direction": "LONG", "tp": 10.0, "sl": 4.0, "horizon_min": 720,
    },
    "S1_EXTREME_7_7": {
        "direction": "SHORT", "tp": 10.0, "sl": 4.0, "horizon_min": 720,
    },
    "S2_15M_LAG_NEAR1": {
        "direction": "SHORT", "tp": 10.0, "sl": 4.0, "horizon_min": 240,
    },
    "S3_PERSIST_8": {
        "direction": "SHORT", "tp": 10.0, "sl": 5.0, "horizon_min": 60,
    },
}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--outdir", default="precision_results")
    p.add_argument("--workers", type=int, default=6)
    return p.parse_args()


def candidate_rank(df):
    ec = df["exact_count"].to_numpy()
    w3 = df["within_3pct_count"].to_numpy()
    rank = np.zeros(len(df), dtype=np.int8)
    rank[ec == 7] = 7
    rank[ec == 6] = 6
    rank[(ec == 5) & (w3 == 7)] = 5
    rank[(ec == 4) & (w3 == 7)] = 4
    return rank


def streak_bool(s):
    x = s.astype(int)
    groups = (x == 0).cumsum()
    return x.groupby(groups).cumsum().to_numpy()


def first_cross(mask, symbols):
    mask = pd.Series(mask, index=symbols.index).fillna(False).astype(bool)
    prev = mask.groupby(symbols).shift(1).fillna(False).astype(bool)
    return mask & ~prev


def build_signals(source):
    usecols = [
        "symbol", "ts", "time_utc", "price", "exact_count",
        "within_3pct_count", "1W_above", "1D_above", "12H_above",
        "4H_above", "1H_above", "30M_above", "15M_above",
        "15M_dist",
    ]
    df = pd.read_csv(source, usecols=usecols)
    df = df.sort_values(["symbol", "ts"]).reset_index(drop=True)
    df["rank"] = candidate_rank(df)
    df["candidate"] = df["rank"] >= 4

    g = df.groupby("symbol", sort=False)
    df["ret_1h"] = g["price"].pct_change(4) * 100.0
    df["ret_4h"] = g["price"].pct_change(16) * 100.0
    df["streak"] = g["candidate"].transform(streak_bool).astype(int)

    # Preserve the original 70/30 time split from the source research window.
    all_ts = np.sort(df["ts"].unique())
    cutoff_idx = max(0, int(len(all_ts) * 0.70) - 1)
    cutoff = all_ts[cutoff_idx]
    df["split"] = np.where(df["ts"] <= cutoff, "train70", "test30")

    above_cols = [f"{tf}_above" for tf in TF_NAMES]
    above = df[above_cols].to_numpy(dtype=int)

    def only_missing(tf):
        idx = TF_NAMES.index(tf)
        return (df["exact_count"].to_numpy() == 6) & (above[:, idx] == 0)

    raw = {
        "L1_MOMENTUM_1H10": (df["rank"] >= 6) & (df["ret_1h"] >= 10),
        "L2_EXPLOSIVE_4H30": (df["rank"] >= 6) & (df["ret_4h"] >= 30),
        "L3_4H_LAG": pd.Series(only_missing("4H")),
        "S1_EXTREME_7_7": (df["rank"] == 7) & (df["ret_4h"] >= 10),
        "S2_15M_LAG_NEAR1": pd.Series(only_missing("15M")) & (df["15M_dist"] >= -1),
        "S3_PERSIST_8": df["candidate"] & (df["streak"] == 8),
    }

    signal_parts = []
    for name, mask in raw.items():
        if "PERSIST" in name:
            trig = pd.Series(mask, index=df.index).fillna(False)
        else:
            trig = first_cross(mask, df["symbol"])

        cols = [
            "symbol", "ts", "time_utc", "price", "split",
            "rank", "exact_count", "streak", "ret_1h", "ret_4h",
        ]
        x = df.loc[trig.to_numpy(), cols].copy()
        cfg = STRATEGIES[name]
        x["strategy"] = name
        x["direction"] = cfg["direction"]
        x["tp_pct"] = cfg["tp"]
        x["sl_pct"] = cfg["sl"]
        x["horizon_min"] = cfg["horizon_min"]
        signal_parts.append(x)

    signals = pd.concat(signal_parts, ignore_index=True)
    return signals.sort_values(["ts", "symbol", "strategy"]).reset_index(drop=True)


def merge_windows(signals):
    windows = {}
    for symbol, g in signals.groupby("symbol"):
        arr = []
        for r in g.itertuples(index=False):
            start = int(r.ts)
            # Need up to +3m entry plus the full holding horizon.
            end = start + (int(r.horizon_min) + 5) * MIN
            arr.append((start, end))
        arr.sort()
        merged = []
        cs, ce = arr[0]
        for s, e in arr[1:]:
            if s <= ce + 5 * MIN:
                ce = max(ce, e)
            else:
                merged.append((cs, ce))
                cs, ce = s, e
        merged.append((cs, ce))
        windows[symbol] = merged
    return windows


def fetch_symbol_minutes(symbol, windows):
    frames = []
    for start, end in windows:
        rows = fetch_range(symbol, "1m", 1, start, end)
        frames.append(rows_to_df(rows, 1)[["ts", "open", "high", "low", "close"]])
    if not frames:
        return symbol, pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return symbol, df


def fetch_all_minutes(signals, workers):
    windows = merge_windows(signals)
    out = {}
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_symbol_minutes, symbol, w): symbol
            for symbol, w in windows.items()
        }
        total = len(futures)
        done = 0
        for future in as_completed(futures):
            symbol = futures[future]
            done += 1
            try:
                sym, minute_df = future.result()
                out[sym] = minute_df
                print(f"[1M] {done}/{total} {sym}: {len(minute_df)} rows")
            except Exception as exc:
                failures.append((symbol, str(exc)))
                print(f"[ERROR] {symbol}: {exc}")
    return out, failures


def one_trade(signal, minute_df, delay_min):
    if minute_df is None or minute_df.empty:
        return None

    signal_ts = int(signal.ts)
    # +1m = close of the first one-minute candle after the 15m boundary.
    entry_candle_ts = signal_ts + (delay_min - 1) * MIN
    entry_rows = minute_df[minute_df["ts"] == entry_candle_ts]
    if entry_rows.empty:
        return None

    entry_price = float(entry_rows.iloc[-1]["close"])
    if not math.isfinite(entry_price) or entry_price <= 0:
        return None

    entry_time = signal_ts + delay_min * MIN
    end_time = entry_time + int(signal.horizon_min) * MIN
    path = minute_df[
        (minute_df["ts"] >= entry_time)
        & (minute_df["ts"] < end_time)
    ]
    if path.empty:
        return None

    direction = signal.direction
    tp_pct = float(signal.tp_pct)
    sl_pct = float(signal.sl_pct)

    if direction == "LONG":
        tp_price = entry_price * (1.0 + tp_pct / 100.0)
        sl_price = entry_price * (1.0 - sl_pct / 100.0)
        # No-stop risk metrics across the full holding horizon.
        worst_adverse_pct = (1.0 - float(path["low"].min()) / entry_price) * 100.0
        best_favorable_pct = (float(path["high"].max()) / entry_price - 1.0) * 100.0
        hold_close_pct = (float(path.iloc[-1]["close"]) / entry_price - 1.0) * 100.0
    else:
        tp_price = entry_price * (1.0 - tp_pct / 100.0)
        sl_price = entry_price * (1.0 + sl_pct / 100.0)
        # For shorts, adverse move is price rising above entry.
        worst_adverse_pct = (float(path["high"].max()) / entry_price - 1.0) * 100.0
        best_favorable_pct = (1.0 - float(path["low"].min()) / entry_price) * 100.0
        hold_close_pct = (1.0 - float(path.iloc[-1]["close"]) / entry_price) * 100.0

    outcome = "TIME"
    exit_price = float(path.iloc[-1]["close"])
    exit_ts = int(path.iloc[-1]["ts"]) + MIN

    for bar in path.itertuples(index=False):
        if direction == "LONG":
            hit_tp = float(bar.high) >= tp_price
            hit_sl = float(bar.low) <= sl_price
        else:
            hit_tp = float(bar.low) <= tp_price
            hit_sl = float(bar.high) >= sl_price

        # If both happen inside the same 1m candle, take the conservative SL.
        if hit_tp and hit_sl:
            outcome = "SL"
            exit_price = sl_price
            exit_ts = int(bar.ts) + MIN
            break
        if hit_sl:
            outcome = "SL"
            exit_price = sl_price
            exit_ts = int(bar.ts) + MIN
            break
        if hit_tp:
            outcome = "TP"
            exit_price = tp_price
            exit_ts = int(bar.ts) + MIN
            break

    if direction == "LONG":
        gross_pct = (exit_price / entry_price - 1.0) * 100.0
    else:
        gross_pct = (1.0 - exit_price / entry_price) * 100.0

    net_pct = gross_pct - FEE_PCT
    signal_to_entry_pct = (entry_price / float(signal.price) - 1.0) * 100.0

    return {
        "delay_min": delay_min,
        "symbol": signal.symbol,
        "strategy": signal.strategy,
        "direction": direction,
        "split": signal.split,
        "signal_ts": signal_ts,
        "entry_ts": entry_time,
        "exit_ts": exit_ts,
        "signal_price": float(signal.price),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "signal_to_entry_pct": signal_to_entry_pct,
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
        "horizon_min": int(signal.horizon_min),
        "outcome": outcome,
        "gross_pct": gross_pct,
        "net_pct": net_pct,
        "nostop_worst_adverse_pct": worst_adverse_pct,
        "nostop_best_favorable_pct": best_favorable_pct,
        "nostop_hold_close_pct": hold_close_pct,
    }


def calc_pf(ret):
    pos = ret[ret > 0].sum()
    neg = -ret[ret < 0].sum()
    if neg <= 0:
        return float("inf") if pos > 0 else np.nan
    return pos / neg


def max_drawdown(equity_values):
    if not equity_values:
        return 0.0
    arr = np.asarray(equity_values, dtype=float)
    peak = np.maximum.accumulate(arr)
    dd = arr / peak - 1.0
    return float(dd.min() * 100.0)


def summarize_trades(trades):
    rows = []
    for keys, g in trades.groupby(["delay_min", "strategy", "split"], dropna=False):
        delay, strategy, split = keys
        ret = g["net_pct"]
        rows.append({
            "delay_min": int(delay),
            "strategy": strategy,
            "split": split,
            "n": len(g),
            "symbols": g["symbol"].nunique(),
            "win_rate_pct": (ret > 0).mean() * 100.0,
            "avg_net_pct": ret.mean(),
            "median_net_pct": ret.median(),
            "profit_factor": calc_pf(ret),
            "tp_rate_pct": (g["outcome"] == "TP").mean() * 100.0,
            "sl_rate_pct": (g["outcome"] == "SL").mean() * 100.0,
            "time_rate_pct": (g["outcome"] == "TIME").mean() * 100.0,
            "avg_signal_to_entry_pct": g["signal_to_entry_pct"].mean(),
        })
    return pd.DataFrame(rows).sort_values(["delay_min", "strategy", "split"])


def simulate_portfolio(trades, delay_min, capped=True):
    t = trades[trades["delay_min"] == delay_min].copy()
    t = t.sort_values(["entry_ts", "strategy", "symbol"]).reset_index(drop=True)

    # Strategy priority comes only from TRAIN data for this delay.
    train = t[t["split"] == "train70"]
    train_pf = {
        name: calc_pf(g["net_pct"])
        for name, g in train.groupby("strategy")
    }

    equity = 1.0
    reserved = 0.0
    open_heap = []
    accepted = []
    equity_curve = [equity]
    peak_open = 0
    peak_exposure = 0.0
    skipped_cap = 0
    seq = 0

    def close_until(ts):
        nonlocal equity, reserved
        while open_heap and open_heap[0][0] <= ts:
            _, _, pos = heapq.heappop(open_heap)
            pnl = pos["size"] * (pos["net_pct"] / 100.0)
            equity += pnl
            reserved -= pos["size"]
            equity_curve.append(equity)

    for entry_ts, batch in t.groupby("entry_ts", sort=True):
        close_until(int(entry_ts))
        batch = batch.copy()
        batch["_priority"] = batch["strategy"].map(
            lambda x: train_pf.get(x, 0.0)
        )
        batch = batch.sort_values(
            ["_priority", "strategy"], ascending=[False, True]
        )

        for r in batch.itertuples(index=False):
            size = equity * POSITION_FRACTION
            if capped and reserved + size > equity * CAPITAL_CAP + 1e-12:
                skipped_cap += 1
                continue

            pos = {
                "delay_min": delay_min,
                "symbol": r.symbol,
                "strategy": r.strategy,
                "direction": r.direction,
                "split": r.split,
                "entry_ts": int(r.entry_ts),
                "exit_ts": int(r.exit_ts),
                "size": size,
                "net_pct": float(r.net_pct),
                "outcome": r.outcome,
            }
            accepted.append(pos)
            reserved += size
            seq += 1
            heapq.heappush(open_heap, (int(r.exit_ts), seq, pos))
            peak_open = max(peak_open, len(open_heap))
            if equity > 0:
                peak_exposure = max(peak_exposure, reserved / equity)

    close_until(10**20)

    accepted_df = pd.DataFrame(accepted)
    return {
        "delay_min": delay_min,
        "mode": "CAP_100" if capped else "UNCAPPED",
        "position_fraction_pct": POSITION_FRACTION * 100.0,
        "signals_available": len(t),
        "trades_taken": len(accepted),
        "skipped_cap": skipped_cap,
        "final_equity_multiple": equity,
        "return_pct": (equity - 1.0) * 100.0,
        "max_drawdown_pct": max_drawdown(equity_curve),
        "peak_open_positions": peak_open,
        "peak_exposure_pct": peak_exposure * 100.0,
        "win_rate_pct": (
            (accepted_df["net_pct"] > 0).mean() * 100.0
            if not accepted_df.empty else np.nan
        ),
        "profit_factor_trade_returns": (
            calc_pf(accepted_df["net_pct"])
            if not accepted_df.empty else np.nan
        ),
    }, accepted_df


def main():
    args = parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print("[STEP] build strategy signals")
    signals = build_signals(args.source)
    print(signals["strategy"].value_counts().sort_index().to_string())
    signals.to_csv(outdir / "signals.csv", index=False)

    print("[STEP] fetch targeted 1m windows")
    minute_map, failures = fetch_all_minutes(signals, args.workers)
    if failures:
        pd.DataFrame(failures, columns=["symbol", "error"]).to_csv(
            outdir / "fetch_failures.csv", index=False
        )

    print("[STEP] replay entries at +1/+2/+3 minutes")
    trade_rows = []
    for signal in signals.itertuples(index=False):
        minute_df = minute_map.get(signal.symbol)
        for delay in (1, 2, 3):
            row = one_trade(signal, minute_df, delay)
            if row:
                trade_rows.append(row)

    trades = pd.DataFrame(trade_rows)
    trades.to_csv(outdir / "precision_trades.csv", index=False)

    summary = summarize_trades(trades)
    summary.to_csv(outdir / "strategy_summary.csv", index=False)

    # No-stop account damage table. Account loss ~= position fraction * adverse move
    # for 1x exposure; with leverage multiply the effective exposure accordingly.
    risk_rows = []
    for (delay, strategy, split), g in trades.groupby(
        ["delay_min", "strategy", "split"], dropna=False
    ):
        mae = g["nostop_worst_adverse_pct"].dropna()
        if mae.empty:
            continue
        worst_idx = mae.idxmax()
        worst = trades.loc[worst_idx]
        row = {
            "delay_min": int(delay),
            "strategy": strategy,
            "split": split,
            "n": len(g),
            "worst_adverse_pct": float(mae.max()),
            "p95_adverse_pct": float(mae.quantile(0.95)),
            "p99_adverse_pct": float(mae.quantile(0.99)),
            "worst_symbol": worst["symbol"],
            "worst_signal_ts": int(worst["signal_ts"]),
            "worst_entry_price": float(worst["entry_price"]),
            "worst_hold_close_pct": float(worst["nostop_hold_close_pct"]),
        }
        for frac in (0.10, 0.30, 0.50, 1.00):
            row[f"acct_loss_at_{int(frac*100)}pct_pos"] = float(mae.max()) * frac
        # Position fraction required for a given account drawdown at 1x.
        for target in (10, 20, 30, 50, 100):
            row[f"pos_frac_for_{target}pct_acct_loss"] = (
                target / float(mae.max()) * 100.0
                if float(mae.max()) > 0 else np.inf
            )
        risk_rows.append(row)

    risk = pd.DataFrame(risk_rows)
    risk.to_csv(outdir / "nostop_risk_summary.csv", index=False)

    # Worst individual trades across all six strategies.
    worst_trades = trades.sort_values(
        "nostop_worst_adverse_pct", ascending=False
    ).head(100)
    worst_trades.to_csv(outdir / "nostop_worst_trades.csv", index=False)

    portfolio_rows = []
    accepted_parts = []
    for delay in (1, 2, 3):
        for capped in (True, False):
            row, acc = simulate_portfolio(trades, delay, capped=capped)
            portfolio_rows.append(row)
            if not acc.empty:
                acc["mode"] = row["mode"]
                accepted_parts.append(acc)

    portfolio = pd.DataFrame(portfolio_rows)
    portfolio.to_csv(outdir / "portfolio_summary.csv", index=False)
    if accepted_parts:
        pd.concat(accepted_parts, ignore_index=True).to_csv(
            outdir / "portfolio_trades.csv", index=False
        )

    print("\n=== STRATEGY SUMMARY ===")
    print(summary.to_string(index=False))
    print("\n=== NO-STOP RISK SUMMARY ===")
    print(risk.to_string(index=False))
    print("\n=== PORTFOLIO SUMMARY ===")
    print(portfolio.to_string(index=False))
    print(f"\n[DONE] signals={len(signals)} precision_trades={len(trades)} failures={len(failures)}")


if __name__ == "__main__":
    main()
