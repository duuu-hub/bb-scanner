from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
BAR2H_MS = 2 * HOUR_MS
RNG = np.random.default_rng(42)


def pf(values):
    x = pd.to_numeric(values, errors="coerce").dropna()
    pos = x[x > 0].sum()
    neg = -x[x < 0].sum()
    if neg <= 0:
        return float("inf") if pos > 0 else float("nan")
    return float(pos / neg)


def state3(value):
    if pd.isna(value):
        return "UNKNOWN"
    if value < 33.333333:
        return "LOW"
    if value < 66.666667:
        return "MID"
    return "HIGH"


def sign_state(value):
    if pd.isna(value):
        return "UNKNOWN"
    if value > 0:
        return "UP"
    if value < 0:
        return "DOWN"
    return "FLAT"


def align(a, b):
    if pd.isna(a) or pd.isna(b):
        return "UNKNOWN"
    if a > 0 and b > 0:
        return "BOTH_UP"
    if a < 0 and b < 0:
        return "BOTH_DOWN"
    return "MIXED"


def load_total(path, prefix):
    df = pd.read_csv(path).sort_values("timestamp_ms").reset_index(drop=True)
    df["timestamp_ms"] = pd.to_numeric(df["timestamp_ms"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["timestamp_ms", "close"])
    df["timestamp_ms"] = df["timestamp_ms"].astype("int64")
    df["available_ts"] = df["timestamp_ms"] + BAR2H_MS
    close = df["close"].astype(float)

    for label, bars in (("6h", 3), ("24h", 12), ("72h", 36), ("7d", 84)):
        df[f"{prefix}_ret_{label}"] = (close / close.shift(bars) - 1.0) * 100.0

    lr = np.log(close).diff()
    df[f"{prefix}_rv7d"] = lr.rolling(84, min_periods=84).std()
    df[f"{prefix}_sma20d"] = close.rolling(240, min_periods=120).mean()
    df[f"{prefix}_sma50d"] = close.rolling(600, min_periods=240).mean()
    df[f"{prefix}_dist_sma20d"] = (close / df[f"{prefix}_sma20d"] - 1.0) * 100.0
    df[f"{prefix}_dist_sma50d"] = (close / df[f"{prefix}_sma50d"] - 1.0) * 100.0

    times = df["available_ts"].to_numpy(dtype=np.int64)
    vals = df[f"{prefix}_rv7d"].to_numpy(dtype=float)
    pct = np.full(len(df), np.nan)
    for i in range(len(df)):
        if not math.isfinite(vals[i]):
            continue
        lo = np.searchsorted(times, times[i] - 365 * DAY_MS, side="left")
        hist = vals[lo:i + 1]
        hist = hist[np.isfinite(hist)]
        if len(hist) >= 360:
            pct[i] = (hist <= vals[i]).mean() * 100.0
    df[f"{prefix}_rv7d_pctile365"] = pct
    return df


def asof_frame(df, signal_times, prefix):
    cols = [c for c in df.columns if c.startswith(prefix + "_")]
    times = df["available_ts"].to_numpy(dtype=np.int64)
    rows = []
    for ts in signal_times:
        i = int(np.searchsorted(times, int(ts), side="right") - 1)
        row = {"signal_ts": int(ts)}
        if i >= 0:
            for c in cols:
                row[c] = df.iloc[i][c]
        rows.append(row)
    return pd.DataFrame(rows)


def metrics(g, extra_slip=0.0):
    x = pd.to_numeric(g["net_pct"], errors="coerce").dropna() - extra_slip
    return {
        "n": int(len(x)),
        "avg": float(x.mean()) if len(x) else float("nan"),
        "pf": pf(x),
        "sum": float(x.sum()) if len(x) else float("nan"),
        "win": float((x > 0).mean() * 100.0) if len(x) else float("nan"),
    }


def add_signal_density(enriched, signals):
    density = []
    for r in signals.itertuples(index=False):
        ts = int(r.ts)
        row = {
            "symbol": r.symbol,
            "signal_ts": ts,
            "base_strategy": r.base_strategy,
        }
        for hours, label in ((6, "6h"), (24, "24h"), (72, "72h")):
            g = signals[(signals["ts"] > ts - hours * HOUR_MS) & (signals["ts"] <= ts)]
            row[f"sig_count_{label}"] = int(len(g))
            row[f"sig_symbols_{label}"] = int(g["symbol"].nunique())
            row[f"same_strategy_count_{label}"] = int((g["base_strategy"] == r.base_strategy).sum())
        density.append(row)
    density = pd.DataFrame(density)
    return enriched.merge(
        density,
        on=["symbol", "signal_ts", "base_strategy"],
        how="left",
    )


def build_features(trades, signals, total3, total3es):
    unique_ts = np.array(sorted(signals["ts"].unique()), dtype=np.int64)
    ctx = asof_frame(total3, unique_ts, "t3")
    ctx = ctx.merge(asof_frame(total3es, unique_ts, "t3es"), on="signal_ts", how="outer")

    sigcols = [
        "symbol", "ts", "base_strategy", "rank", "exact_count", "streak",
        "ret_1h", "ret_4h", "universe_count", "weekly_upper_pct",
        "weekly_near3_pct", "weekly_mid_pct", "daily_upper_pct",
        "daily_near3_pct", "daily_mid_pct", "h4_upper_pct",
        "h4_near3_pct", "h4_mid_pct",
    ]
    sig = signals[sigcols].rename(columns={"ts": "signal_ts"})
    en = trades.merge(sig, on=["symbol", "signal_ts", "base_strategy"], how="left", suffixes=("", "_sig"))
    en = en.merge(ctx, on="signal_ts", how="left")
    en = add_signal_density(en, signals)

    en["t3_vol_state"] = en["t3_rv7d_pctile365"].map(state3)
    en["t3es_vol_state"] = en["t3es_rv7d_pctile365"].map(state3)
    for p in ("t3", "t3es"):
        for h in ("6h", "24h", "72h", "7d"):
            en[f"{p}_{h}_dir"] = en[f"{p}_ret_{h}"].map(sign_state)
        en[f"{p}_sma20_state"] = np.where(en[f"{p}_dist_sma20d"] > 0, "ABOVE", "BELOW")
        en[f"{p}_sma50_state"] = np.where(en[f"{p}_dist_sma50d"] > 0, "ABOVE", "BELOW")

    en["t3_t3es_24h_alignment"] = [
        align(a, b) for a, b in zip(en["t3_ret_24h"], en["t3es_ret_24h"])
    ]
    en["btc_4h_dir"] = en["btc_ret_4h"].map(sign_state)
    en["btc_24h_dir"] = en["btc_ret_24h"].map(sign_state)
    en["eth_4h_dir"] = en["eth_ret_4h"].map(sign_state)
    en["eth_24h_dir"] = en["eth_ret_24h"].map(sign_state)

    def near_bucket(x):
        if pd.isna(x):
            return "UNKNOWN"
        if x < 5:
            return "LT5"
        if x < 15:
            return "5_15"
        if x < 30:
            return "15_30"
        return "GE30"

    def c24(x):
        if pd.isna(x):
            return "UNKNOWN"
        if x <= 1:
            return "1"
        if x <= 4:
            return "2_4"
        return "5P"

    def c6(x):
        if pd.isna(x):
            return "UNKNOWN"
        if x <= 1:
            return "1"
        if x <= 3:
            return "2_3"
        return "4P"

    def c72(x):
        if pd.isna(x):
            return "UNKNOWN"
        if x <= 2:
            return "1_2"
        if x <= 6:
            return "3_6"
        return "7P"

    def streak_bucket(x):
        if pd.isna(x):
            return "UNKNOWN"
        if x <= 1:
            return "1"
        if x <= 4:
            return "2_4"
        return "5P"

    def ret1_bucket(x):
        if pd.isna(x):
            return "UNKNOWN"
        if x < 0:
            return "NEG"
        if x < 10:
            return "0_10"
        if x < 15:
            return "10_15"
        if x < 25:
            return "15_25"
        return "25P"

    def ret4_bucket(x):
        if pd.isna(x):
            return "UNKNOWN"
        if x < 0:
            return "NEG"
        if x < 10:
            return "0_10"
        if x < 30:
            return "10_30"
        if x < 50:
            return "30_50"
        return "50P"

    en["weekly_near_bucket"] = en["weekly_near3_pct"].map(near_bucket)
    en["sig_count24_bucket"] = en["sig_count_24h"].map(c24)
    en["sig_count6_bucket"] = en["sig_count_6h"].map(c6)
    en["sig_count72_bucket"] = en["sig_count_72h"].map(c72)
    en["streak_bucket"] = en["streak"].map(streak_bucket)
    en["ret1_bucket"] = en["ret_1h"].map(ret1_bucket)
    en["ret4_bucket"] = en["ret_4h"].map(ret4_bucket)

    dt = pd.to_datetime(en["signal_ts"], unit="ms", utc=True)
    hour = dt.dt.hour
    en["utc_session"] = np.select(
        [hour <= 7, hour <= 15],
        ["UTC00_07", "UTC08_15"],
        default="UTC16_23",
    )
    en["weekpart"] = np.where(dt.dt.dayofweek >= 5, "WEEKEND", "WEEKDAY")
    return en


def candidate_screen(en):
    factors = [
        "btc_vol_state", "btc_eth_4h_alignment", "btc_eth_24h_alignment",
        "btc_4h_dir", "btc_24h_dir", "eth_4h_dir", "eth_24h_dir",
        "t3_vol_state", "t3es_vol_state", "t3_6h_dir", "t3_24h_dir",
        "t3_72h_dir", "t3_7d_dir", "t3es_6h_dir", "t3es_24h_dir",
        "t3es_72h_dir", "t3es_7d_dir", "t3_sma20_state",
        "t3_sma50_state", "t3es_sma20_state", "t3es_sma50_state",
        "t3_t3es_24h_alignment", "breadth_level", "breadth_trend_1d",
        "breadth_trend_3d", "breadth_trend_7d", "weekly_near_bucket",
        "sig_count24_bucket", "sig_count6_bucket", "sig_count72_bucket",
        "rank", "exact_count", "streak_bucket", "ret1_bucket",
        "ret4_bucket", "utc_session", "weekpart",
    ]

    pairs = [
        ("btc_vol_state", "breadth_trend_1d"),
        ("btc_vol_state", "breadth_trend_3d"),
        ("btc_vol_state", "weekly_near_bucket"),
        ("btc_vol_state", "sig_count24_bucket"),
        ("btc_vol_state", "btc_eth_4h_alignment"),
        ("btc_vol_state", "btc_eth_24h_alignment"),
        ("btc_vol_state", "t3_vol_state"),
        ("btc_vol_state", "t3_24h_dir"),
        ("btc_vol_state", "t3_6h_dir"),
        ("t3_vol_state", "breadth_trend_1d"),
        ("t3_vol_state", "sig_count24_bucket"),
        ("weekly_near_bucket", "sig_count24_bucket"),
        ("breadth_trend_1d", "sig_count24_bucket"),
    ]
    for a, b in pairs:
        name = f"{a}__X__{b}"
        en[name] = en[a].astype(str) + "__" + en[b].astype(str)
        factors.append(name)

    rows = []
    for factor in factors:
        for strategy in sorted(en["base_strategy"].dropna().unique()):
            vals = [v for v in en[factor].dropna().unique() if "UNKNOWN" not in str(v)]
            for val in vals:
                train = en[
                    (en["base_strategy"] == strategy)
                    & (en[factor] == val)
                    & (en["delay_min"] == 1)
                    & (en["split"] == "train70")
                ]
                bydir = {d: metrics(train[train["direction"] == d]) for d in ("LONG", "SHORT")}
                if min(bydir["LONG"]["n"], bydir["SHORT"]["n"]) < 8:
                    continue

                chosen = max(("LONG", "SHORT"), key=lambda d: bydir[d]["avg"])
                other = "SHORT" if chosen == "LONG" else "LONG"
                row = {
                    "factor": factor,
                    "factor_value": val,
                    "strategy": strategy,
                    "chosen_direction_train": chosen,
                    "train_n": bydir[chosen]["n"],
                    "train_pf_d1": bydir[chosen]["pf"],
                    "train_avg_d1": bydir[chosen]["avg"],
                    "train_direction_separation": bydir[chosen]["avg"] - bydir[other]["avg"],
                }
                for split in ("train70", "test30"):
                    for delay in (1, 2, 3):
                        g = en[
                            (en["base_strategy"] == strategy)
                            & (en[factor] == val)
                            & (en["delay_min"] == delay)
                            & (en["split"] == split)
                            & (en["direction"] == chosen)
                        ]
                        m = metrics(g)
                        row[f"{split}_n_d{delay}"] = m["n"]
                        row[f"{split}_pf_d{delay}"] = m["pf"]
                        row[f"{split}_avg_d{delay}"] = m["avg"]
                        if split == "test30":
                            row[f"test30_pf_slip025_d{delay}"] = metrics(g, 0.25)["pf"]
                            row[f"test30_pf_slip050_d{delay}"] = metrics(g, 0.50)["pf"]
                row["min_n_all"] = min(
                    row[f"{split}_n_d{delay}"]
                    for split in ("train70", "test30")
                    for delay in (1, 2, 3)
                )
                row["test_min_pf"] = min(row[f"test30_pf_d{d}"] for d in (1, 2, 3))
                row["test_min_pf_slip025"] = min(
                    row[f"test30_pf_slip025_d{d}"] for d in (1, 2, 3)
                )
                row["test_min_pf_slip050"] = min(
                    row[f"test30_pf_slip050_d{d}"] for d in (1, 2, 3)
                )
                rows.append(row)

    out = pd.DataFrame(rows)
    if not out.empty:
        out["passes_basic_robustness"] = (
            (out["min_n_all"] >= 8)
            & (out["train_pf_d1"] > 1.15)
            & (out["test_min_pf"] > 1.0)
        )
        out = out.sort_values(
            ["passes_basic_robustness", "test_min_pf_slip025", "train_pf_d1"],
            ascending=[False, False, False],
        )
    return en, out


def choose_l1(row):
    if row["btc_vol_state"] == "MID":
        return "SHORT"
    if row["btc_vol_state"] == "HIGH" and row["t3_24h_dir"] == "UP":
        return "LONG"
    return "OFF"


def choose_l1_simple(row):
    return "SHORT" if row["btc_vol_state"] == "MID" else "OFF"


def choose_l3(row):
    if row["breadth_trend_1d"] == "FLAT":
        return "LONG"
    if row["breadth_trend_1d"] == "CONTRACTING":
        return "SHORT"
    return "OFF"


def policy_rows(en, strategy, chooser):
    g = en[en["base_strategy"] == strategy].copy()
    g["policy_direction"] = g.apply(chooser, axis=1)
    return g[(g["policy_direction"] != "OFF") & (g["direction"] == g["policy_direction"])].copy()


def decluster(g, hours):
    g = g.sort_values(["entry_ts", "symbol"]).copy()
    last = {}
    keep = []
    for idx, r in g.iterrows():
        t = int(r["entry_ts"])
        s = r["symbol"]
        if s not in last or t - last[s] >= hours * HOUR_MS:
            keep.append(idx)
            last[s] = t
    return g.loc[keep]


def weekly_bootstrap(g, reps=2000):
    if g.empty:
        return (float("nan"), float("nan"), float("nan"))
    x = g.copy()
    x["week"] = pd.to_datetime(x["signal_ts"], unit="ms", utc=True).dt.strftime("%G-W%V")
    weeks = x["week"].unique()
    vals = []
    for _ in range(reps):
        sample = RNG.choice(weeks, size=len(weeks), replace=True)
        acc = []
        for w in sample:
            acc.extend(x.loc[x["week"] == w, "net_pct"].tolist())
        vals.append(np.mean(acc))
    return tuple(np.percentile(vals, [2.5, 50, 97.5]))


def policy_analysis(en):
    policies = {
        "L1_BTC_VOL_MID_SHORT": policy_rows(en, "L1_MOMENTUM_1H10", choose_l1_simple),
        "L1_VOL_PLUS_TOTAL3_SWITCH": policy_rows(en, "L1_MOMENTUM_1H10", choose_l1),
        "L3_BREADTH_SWITCH": policy_rows(en, "L3_4H_LAG", choose_l3),
    }

    summary = []
    quarters = []
    symbols = []
    declustered = []

    for name, p in policies.items():
        for split in ("train70", "test30"):
            for delay in (1, 2, 3):
                g = p[(p["split"] == split) & (p["delay_min"] == delay)]
                m = metrics(g)
                row = {
                    "policy": name,
                    "split": split,
                    "delay_min": delay,
                    **m,
                    "pf_slip025": metrics(g, 0.25)["pf"],
                    "pf_slip050": metrics(g, 0.50)["pf"],
                }
                if delay == 1:
                    lo, med, hi = weekly_bootstrap(g)
                    row["weekly_boot_mean_ci_low"] = lo
                    row["weekly_boot_mean_median"] = med
                    row["weekly_boot_mean_ci_high"] = hi
                summary.append(row)

                for hours in (6, 24):
                    d = decluster(g, hours)
                    dm = metrics(d)
                    declustered.append({
                        "policy": name,
                        "split": split,
                        "delay_min": delay,
                        "decluster_hours": hours,
                        **dm,
                    })

            qbase = p[(p["split"] == split) & (p["delay_min"] == 1)].copy()
            qbase["quarter"] = pd.to_datetime(
                qbase["signal_ts"], unit="ms", utc=True
            ).dt.to_period("Q").astype(str)
            for q, g in qbase.groupby("quarter"):
                quarters.append({
                    "policy": name,
                    "split": split,
                    "quarter": q,
                    **metrics(g),
                })
            sg = qbase.groupby("symbol")["net_pct"].agg(["size", "sum", "mean"]).reset_index()
            sg["policy"] = name
            sg["split"] = split
            symbols.append(sg)

    return (
        pd.DataFrame(summary),
        pd.DataFrame(quarters),
        pd.concat(symbols, ignore_index=True) if symbols else pd.DataFrame(),
        pd.DataFrame(declustered),
    )


def main():
    out = Path("exhaustive_context_results")
    out.mkdir(parents=True, exist_ok=True)

    trades = pd.read_csv("prior_context/trades_with_market_context.csv.gz")
    signals = pd.read_csv("prior_regime/regime_breadth_results/signals_with_regime.csv")
    total3 = load_total("market_data_store/tradingview/TOTAL3.csv", "t3")
    total3es = load_total("market_data_store/tradingview/TOTAL3ES.csv", "t3es")

    en = build_features(trades, signals, total3, total3es)
    en, screen = candidate_screen(en)
    psummary, pquarters, psymbols, pdecluster = policy_analysis(en)

    en.to_csv(out / "enriched_trades.csv.gz", index=False, compression="gzip")
    screen.to_csv(out / "candidate_screen.csv", index=False)
    psummary.to_csv(out / "policy_summary.csv", index=False)
    pquarters.to_csv(out / "policy_quarters.csv", index=False)
    psymbols.to_csv(out / "policy_symbols.csv", index=False)
    pdecluster.to_csv(out / "policy_decluster.csv", index=False)

    print("\n=== ROBUST ONE/PAIR FACTOR CANDIDATES ===")
    cols = [
        "factor", "factor_value", "strategy", "chosen_direction_train",
        "train_n", "min_n_all", "train_pf_d1", "test_min_pf",
        "test_min_pf_slip025", "test_min_pf_slip050",
    ]
    good = screen[screen["passes_basic_robustness"]].head(30)
    print(good[cols].to_string(index=False) if len(good) else "none")

    print("\n=== PREDECLARED SIMPLE POLICY ROBUSTNESS ===")
    print(psummary.to_string(index=False))
    print("\n=== IMPORTANT ===")
    print("Candidate selection is exploratory and multiple factors were screened.")
    print("train70/test30 are chronological stability splits, but test30 is no longer untouched because prior analysis has already inspected it.")
    print("AUTO50 uses the current-active universe historically, so survivorship bias remains.")
    print("Thresholds here are coarse fixed states; no dense outcome-based numeric optimization was performed.")
    print(f"[DONE] enriched_rows={len(en)} screen_rows={len(screen)}")


if __name__ == "__main__":
    main()
