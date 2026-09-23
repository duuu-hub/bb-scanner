from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

BAR2H_MS = 2 * 60 * 60_000


def load_total(path, prefix):
    df = pd.read_csv(path).sort_values("timestamp_ms").reset_index(drop=True)
    df["timestamp_ms"] = pd.to_numeric(df["timestamp_ms"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["timestamp_ms", "close"]).copy()
    df["timestamp_ms"] = df["timestamp_ms"].astype("int64")
    # A 2h TradingView candle is usable only after it has fully closed.
    df["available_ts"] = df["timestamp_ms"] + BAR2H_MS
    df[f"{prefix}_ret_24h"] = (df["close"] / df["close"].shift(12) - 1.0) * 100.0
    return df


def asof_frame(df, signal_times, prefix):
    times = df["available_ts"].to_numpy(dtype=np.int64)
    vals = df[f"{prefix}_ret_24h"].to_numpy(dtype=float)
    rows = []
    for ts in signal_times:
        i = int(np.searchsorted(times, int(ts), side="right") - 1)
        rows.append({
            "signal_ts": int(ts),
            f"{prefix}_ret_24h": vals[i] if i >= 0 else float("nan"),
        })
    return pd.DataFrame(rows)

RNG = np.random.default_rng(20260923)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--outdir", default="bb_auto100_switch_holdout_results")
    return p.parse_args()


def calc_pf(values):
    x = pd.to_numeric(values, errors="coerce").dropna()
    pos = x[x > 0].sum()
    neg = -x[x < 0].sum()
    if neg <= 0:
        return float("inf") if pos > 0 else float("nan")
    return float(pos / neg)


def metrics(g, extra_slip=0.0):
    x = pd.to_numeric(g["net_pct"], errors="coerce").dropna() - extra_slip
    return {
        "n": int(len(x)),
        "symbols": int(g.loc[x.index, "symbol"].nunique()) if len(x) else 0,
        "avg_net_pct": float(x.mean()) if len(x) else float("nan"),
        "sum_net_pct": float(x.sum()) if len(x) else float("nan"),
        "profit_factor": calc_pf(x),
        "win_rate_pct": float((x > 0).mean() * 100.0) if len(x) else float("nan"),
    }


def weekly_cluster_bootstrap(g, reps=5000):
    if g.empty:
        return {
            "boot_mean_lo": float("nan"),
            "boot_mean_med": float("nan"),
            "boot_mean_hi": float("nan"),
        }
    x = g.copy()
    x["week"] = pd.to_datetime(x["signal_ts"], unit="ms", utc=True).dt.strftime("%G-W%V")
    weeks = x["week"].dropna().unique()
    if len(weeks) < 2:
        return {
            "boot_mean_lo": float("nan"),
            "boot_mean_med": float(x["net_pct"].mean()),
            "boot_mean_hi": float("nan"),
        }
    vals = []
    for _ in range(reps):
        sample = RNG.choice(weeks, size=len(weeks), replace=True)
        ret = []
        for w in sample:
            ret.extend(x.loc[x["week"] == w, "net_pct"].tolist())
        vals.append(float(np.mean(ret)))
    lo, med, hi = np.percentile(vals, [2.5, 50, 97.5])
    return {
        "boot_mean_lo": float(lo),
        "boot_mean_med": float(med),
        "boot_mean_hi": float(hi),
    }


def select_policy(trades):
    x = trades.copy()
    x["t3_24h_dir"] = np.where(
        x["t3_ret_24h"] > 0,
        "UP",
        np.where(x["t3_ret_24h"] < 0, "DOWN", "FLAT"),
    )
    x["policy_direction"] = "OFF"
    x["policy_arm"] = "OFF"

    mid = x["btc_vol_state"].eq("MID")
    x.loc[mid, "policy_direction"] = "SHORT"
    x.loc[mid, "policy_arm"] = "MID_SHORT"

    high_up = x["btc_vol_state"].eq("HIGH") & x["t3_24h_dir"].eq("UP")
    x.loc[high_up, "policy_direction"] = "LONG"
    x.loc[high_up, "policy_arm"] = "HIGH_T3UP_LONG"

    selected = x[
        x["policy_direction"].ne("OFF")
        & x["direction"].eq(x["policy_direction"])
    ].copy()
    return x, selected


def matched_direction_compare(all_rows, selected):
    key = ["symbol", "signal_ts", "delay_min"]
    chosen = selected[key + ["policy_arm", "direction", "net_pct"]].rename(
        columns={"direction": "chosen_direction", "net_pct": "chosen_net_pct"}
    )
    other = all_rows[key + ["direction", "net_pct"]].copy()
    out = chosen.merge(other, on=key, how="left")
    out = out[out["direction"] != out["chosen_direction"]].rename(
        columns={"net_pct": "opposite_net_pct", "direction": "opposite_direction"}
    )
    out["chosen_minus_opposite"] = out["chosen_net_pct"] - out["opposite_net_pct"]
    return out


def concentration(selected):
    rows = []
    for delay in (1, 2, 3):
        g = selected[selected["delay_min"] == delay].copy()
        contrib = g.groupby("symbol")["net_pct"].sum().sort_values(ascending=False)
        for k in (0, 1, 3, 5):
            drop = set(contrib.head(k).index) if k else set()
            z = g[~g["symbol"].isin(drop)]
            rows.append({
                "delay_min": delay,
                "drop_top": k,
                "dropped_symbols": ",".join(contrib.head(k).index) if k else "",
                **metrics(z),
                "pf_slip025": metrics(z, 0.25)["profit_factor"],
                "pf_slip050": metrics(z, 0.50)["profit_factor"],
            })
    return pd.DataFrame(rows)


def temporal(selected):
    x = selected.copy()
    dt = pd.to_datetime(x["signal_ts"], unit="ms", utc=True)
    midpoint = int((x["signal_ts"].min() + x["signal_ts"].max()) / 2)
    x["half"] = np.where(x["signal_ts"] < midpoint, "FIRST_HALF", "SECOND_HALF")
    x["month"] = dt.dt.strftime("%Y-%m")

    rows = []
    for col in ("half", "month"):
        for (label, delay), g in x.groupby([col, "delay_min"]):
            rows.append({
                "slice_type": col,
                "slice": label,
                "delay_min": int(delay),
                **metrics(g),
                "pf_slip025": metrics(g, 0.25)["profit_factor"],
                "pf_slip050": metrics(g, 0.50)["profit_factor"],
            })
    return pd.DataFrame(rows)


def summary(selected):
    rows = []
    for delay in (1, 2, 3):
        g = selected[selected["delay_min"] == delay]
        rows.append({
            "scope": "SWITCH_TOTAL",
            "delay_min": delay,
            **metrics(g),
            "pf_slip025": metrics(g, 0.25)["profit_factor"],
            "pf_slip050": metrics(g, 0.50)["profit_factor"],
            **weekly_cluster_bootstrap(g),
        })
        for arm, a in g.groupby("policy_arm"):
            rows.append({
                "scope": arm,
                "delay_min": delay,
                **metrics(a),
                "pf_slip025": metrics(a, 0.25)["profit_factor"],
                "pf_slip050": metrics(a, 0.50)["profit_factor"],
                **weekly_cluster_bootstrap(a),
            })
    return pd.DataFrame(rows)


def main():
    args = parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    trades = pd.read_csv(args.input)
    required = {"symbol","signal_ts","delay_min","direction","net_pct","btc_vol_state"}
    missing = required - set(trades.columns)
    if missing:
        raise RuntimeError(f"missing columns: {sorted(missing)}")

    total3 = load_total("market_data_store/tradingview/TOTAL3.csv", "t3")
    unique_ts = np.array(sorted(trades["signal_ts"].unique()), dtype=np.int64)
    ctx = asof_frame(total3, unique_ts, "t3")
    ctx = ctx[["signal_ts","t3_ret_24h"]]
    en = trades.merge(ctx, on="signal_ts", how="left")

    en, selected = select_policy(en)
    if selected.empty:
        raise RuntimeError("0 selected switch trades")

    sm = summary(selected)
    conc = concentration(selected)
    temp = temporal(selected)
    matched = matched_direction_compare(en, selected)

    matched_summary = matched.groupby(["policy_arm","delay_min"]).agg(
        n=("chosen_minus_opposite","size"),
        avg_chosen=("chosen_net_pct","mean"),
        avg_opposite=("opposite_net_pct","mean"),
        avg_direction_edge=("chosen_minus_opposite","mean"),
    ).reset_index()

    en.to_csv(outdir / "enriched_l1_trades.csv.gz", index=False, compression="gzip")
    selected.to_csv(outdir / "switch_policy_trades.csv", index=False)
    sm.to_csv(outdir / "switch_summary.csv", index=False)
    conc.to_csv(outdir / "switch_concentration.csv", index=False)
    temp.to_csv(outdir / "switch_temporal.csv", index=False)
    matched.to_csv(outdir / "matched_direction.csv", index=False)
    matched_summary.to_csv(outdir / "matched_direction_summary.csv", index=False)

    print("\n=== FROZEN L1 BTC-VOL + TOTAL3 SWITCH HOLDOUT ===")
    print(sm.to_string(index=False))
    print("\n=== MATCHED CHOSEN VS OPPOSITE DIRECTION ===")
    print(matched_summary.to_string(index=False))
    print("\n=== CONCENTRATION ===")
    print(conc.to_string(index=False))
    print("\n=== TEMPORAL ===")
    print(temp.to_string(index=False))
    print("\n=== IMPORTANT ===")
    print("Policy is frozen from prior AUTO50 research: BTC vol MID => SHORT; BTC vol HIGH + TOTAL3 24h UP => LONG; else OFF.")
    print("TOTAL3 uses only completed 2h candles via available_ts, so signal-time context is point-in-time.")
    print("Universe is the 66-symbol cross-sectional holdout from the prior AUTO100 run; dates still overlap discovery.")
    print("No thresholds were searched in this holdout.")
    print(f"[DONE] selected_rows={len(selected)} unique_events={selected[['symbol','signal_ts']].drop_duplicates().shape[0]}")


if __name__ == "__main__":
    main()
