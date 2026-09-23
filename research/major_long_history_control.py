from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from precision_backtest import build_signals, FEE_PCT

DAY_MS = 86_400_000
BAR15_MS = 15 * 60_000


def pf(values):
    x = pd.to_numeric(values, errors="coerce").dropna()
    pos = x[x > 0].sum()
    neg = -x[x < 0].sum()
    if neg <= 0:
        return float("inf") if pos > 0 else float("nan")
    return float(pos / neg)


def load_local_15m(symbol):
    root = Path("market_data_store/bitget/15m") / symbol
    files = sorted(root.glob("*.csv"))
    if not files:
        raise FileNotFoundError(symbol)
    cols = ["timestamp_ms", "open", "high", "low", "close"]
    df = pd.concat([pd.read_csv(p, usecols=cols) for p in files], ignore_index=True)
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().drop_duplicates("timestamp_ms", keep="last").sort_values("timestamp_ms")
    df["timestamp_ms"] = df["timestamp_ms"].astype("int64")
    return df.reset_index(drop=True)


def add_btc_vol_state(btc):
    x = btc.copy()
    lr = np.log(x["close"].astype(float)).diff()
    x["rv7d"] = lr.rolling(7 * 24 * 4, min_periods=7 * 24 * 4).std()
    times = x["timestamp_ms"].to_numpy(dtype=np.int64)
    vals = x["rv7d"].to_numpy(dtype=float)
    pct = np.full(len(x), np.nan)
    for i in range(len(x)):
        if not math.isfinite(vals[i]):
            continue
        lo = np.searchsorted(times, times[i] - 365 * DAY_MS, side="left")
        hist = vals[lo:i + 1]
        hist = hist[np.isfinite(hist)]
        if len(hist) >= 30 * 24 * 4:
            pct[i] = (hist <= vals[i]).mean() * 100.0
    x["btc_rv7d_pctile365"] = pct
    x["btc_vol_state"] = np.where(
        pct < 33.333333,
        "LOW",
        np.where(pct < 66.666667, "MID", "HIGH"),
    )
    x.loc[~np.isfinite(pct), "btc_vol_state"] = "UNKNOWN"
    return x


def asof_btc_state(btc, ts):
    times = btc["timestamp_ms"].to_numpy(dtype=np.int64)
    i = int(np.searchsorted(times, int(ts), side="right") - 1)
    if i < 0:
        return ("UNKNOWN", float("nan"))
    return (
        str(btc.iloc[i]["btc_vol_state"]),
        float(btc.iloc[i]["btc_rv7d_pctile365"]),
    )


def simulate(signal, bars, direction):
    ts = int(signal.ts)
    i = int(np.searchsorted(bars["timestamp_ms"].to_numpy(dtype=np.int64), ts, side="left"))
    if i >= len(bars) or int(bars.iloc[i]["timestamp_ms"]) != ts:
        return None

    entry = float(signal.price)
    if not math.isfinite(entry) or entry <= 0:
        return None

    horizon = int(signal.horizon_min)
    end_ts = ts + horizon * 60_000
    path = bars[(bars["timestamp_ms"] >= ts) & (bars["timestamp_ms"] < end_ts)]
    if path.empty:
        return None

    tp = float(signal.tp_pct)
    sl = float(signal.sl_pct)
    if direction == "LONG":
        tp_price = entry * (1 + tp / 100)
        sl_price = entry * (1 - sl / 100)
    else:
        tp_price = entry * (1 - tp / 100)
        sl_price = entry * (1 + sl / 100)

    outcome = "TIME"
    exit_price = float(path.iloc[-1]["close"])
    exit_ts = int(path.iloc[-1]["timestamp_ms"]) + BAR15_MS

    for b in path.itertuples(index=False):
        if direction == "LONG":
            hit_tp = float(b.high) >= tp_price
            hit_sl = float(b.low) <= sl_price
        else:
            hit_tp = float(b.low) <= tp_price
            hit_sl = float(b.high) >= sl_price

        if hit_tp and hit_sl:
            outcome = "SL"
            exit_price = sl_price
            exit_ts = int(b.timestamp_ms) + BAR15_MS
            break
        if hit_sl:
            outcome = "SL"
            exit_price = sl_price
            exit_ts = int(b.timestamp_ms) + BAR15_MS
            break
        if hit_tp:
            outcome = "TP"
            exit_price = tp_price
            exit_ts = int(b.timestamp_ms) + BAR15_MS
            break

    if direction == "LONG":
        gross = (exit_price / entry - 1) * 100
    else:
        gross = (1 - exit_price / entry) * 100
    return {
        "symbol": signal.symbol,
        "signal_ts": ts,
        "split": signal.split,
        "base_strategy": signal.strategy,
        "direction": direction,
        "entry_price": entry,
        "exit_price": exit_price,
        "exit_ts": exit_ts,
        "outcome": outcome,
        "gross_pct": gross,
        "net_pct": gross - FEE_PCT,
    }


def summarize(trades):
    rows = []
    for keys, g in trades.groupby(
        ["base_strategy", "btc_vol_state", "split", "direction"],
        dropna=False,
    ):
        strategy, state, split, direction = keys
        rows.append({
            "base_strategy": strategy,
            "btc_vol_state": state,
            "split": split,
            "direction": direction,
            "n": int(len(g)),
            "avg_net_pct": float(g["net_pct"].mean()),
            "profit_factor": pf(g["net_pct"]),
            "pf_slip025": pf(g["net_pct"] - 0.25),
            "pf_slip050": pf(g["net_pct"] - 0.50),
            "sum_net_pct": float(g["net_pct"].sum()),
            "win_rate_pct": float((g["net_pct"] > 0).mean() * 100),
        })
    return pd.DataFrame(rows)


def regime_persistence(btc):
    x = btc[btc["btc_vol_state"] != "UNKNOWN"].copy()
    x["change"] = x["btc_vol_state"].ne(x["btc_vol_state"].shift())
    x["run_id"] = x["change"].cumsum()
    runs = x.groupby(["run_id", "btc_vol_state"]).agg(
        start_ts=("timestamp_ms", "min"),
        end_ts=("timestamp_ms", "max"),
        bars=("timestamp_ms", "size"),
    ).reset_index()
    runs["days"] = runs["bars"] * 15 / 60 / 24

    shares = x["btc_vol_state"].value_counts(normalize=True).mul(100)
    rows = []
    for state in ("LOW", "MID", "HIGH"):
        r = runs[runs["btc_vol_state"] == state]
        rows.append({
            "btc_vol_state": state,
            "share_of_15m_bars_pct": float(shares.get(state, 0.0)),
            "runs": int(len(r)),
            "median_run_days": float(r["days"].median()) if len(r) else float("nan"),
            "mean_run_days": float(r["days"].mean()) if len(r) else float("nan"),
            "p90_run_days": float(r["days"].quantile(0.9)) if len(r) else float("nan"),
        })
    return pd.DataFrame(rows), runs


def main():
    out = Path("major_history_results")
    out.mkdir(parents=True, exist_ok=True)

    source = pd.read_csv("major_7y_source/snapshots.csv.gz")
    signals = build_signals(source)
    signals = signals[signals["strategy"].isin(
        ["L1_MOMENTUM_1H10", "L2_EXPLOSIVE_4H30", "L3_4H_LAG"]
    )].copy()

    btc = add_btc_vol_state(load_local_15m("BTCUSDT"))
    bars = {
        "BTCUSDT": load_local_15m("BTCUSDT"),
        "ETHUSDT": load_local_15m("ETHUSDT"),
    }

    rows = []
    for s in signals.itertuples(index=False):
        if s.symbol not in bars:
            continue
        for direction in ("LONG", "SHORT"):
            r = simulate(s, bars[s.symbol], direction)
            if r is None:
                continue
            state, pct = asof_btc_state(btc, int(s.ts))
            r["btc_vol_state"] = state
            r["btc_rv7d_pctile365"] = pct
            rows.append(r)

    trades = pd.DataFrame(rows)
    trades.to_csv(out / "major_long_short_trades.csv.gz", index=False, compression="gzip")
    summary = summarize(trades)
    summary.to_csv(out / "major_by_btc_vol.csv", index=False)

    persist, runs = regime_persistence(btc)
    persist.to_csv(out / "btc_vol_regime_persistence.csv", index=False)
    runs.to_csv(out / "btc_vol_regime_runs.csv", index=False)

    print("\n=== 7Y BTC/ETH MAJOR CONTROL ===")
    print("Entry is signal-boundary 15m open; conservative 15m same-bar TP+SL -> SL.")
    print(summary.to_string(index=False))
    print("\n=== BTC VOL REGIME PERSISTENCE ===")
    print(persist.to_string(index=False))
    print(f"\n[DONE] signals={len(signals)} trade_rows={len(trades)}")


if __name__ == "__main__":
    main()
