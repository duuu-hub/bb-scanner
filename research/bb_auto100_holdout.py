from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from precision_backtest import build_signals, fetch_all_minutes, one_trade

BAR15_MS = 15 * 60_000
DAY_MS = 24 * 60 * 60_000

# Exact AUTO50 universe used by the original 730-day BB breadth study.
# It included several RWA-style contracts before the crypto-only universe fix.
ORIGINAL_AUTO50 = {
    "BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","BNBUSDT","SUIUSDT",
    "PEPEUSDT","WIFUSDT","龙虾USDT","NILUSDT","INITUSDT","METISUSDT","KATUSDT",
    "ANKRUSDT","CRMUSDT","EWJUSDT","GDXUSDT","OPGUSDT","HOMEUSDT","TSLAUSDT",
    "SMRUSDT","TRUMPUSDT","LITEUSDT","BEUSDT","哈基米USDT","EGLDUSDT","EWYUSDT",
    "1MCHEEMSUSDT","BROCCOLIUSDT","SQDUSDT","ARQQUSDT","YGGUSDT","SPELLUSDT",
    "GMEUSDT","BUSDT","GLWUSDT","LINUSDT","GUSDT","PLTRUSDT","ADAUSDT","CCUSDT",
    "JSTUSDT","STRCUSDT","ABNBUSDT","EWZUSDT","ZILUSDT","ROBOUSDT","GUNUSDT","KAVAUSDT",
}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--selection", default="market_data_store/bitget/research_auto100_15m/selection.json")
    p.add_argument("--market-root", default="market_data_store/bitget/15m")
    p.add_argument("--outdir", default="bb_auto100_holdout_results")
    p.add_argument("--workers", type=int, default=8)
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


def load_btc(root: Path):
    files = sorted((root / "BTCUSDT").glob("*.csv"))
    if not files:
        raise FileNotFoundError("BTCUSDT long-history store missing")
    df = pd.concat(
        [pd.read_csv(p, usecols=["timestamp_ms", "close"]) for p in files],
        ignore_index=True,
    )
    df["timestamp_ms"] = pd.to_numeric(df["timestamp_ms"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna().drop_duplicates("timestamp_ms", keep="last").sort_values("timestamp_ms")
    df = df[df["close"] > 0].reset_index(drop=True)
    df["timestamp_ms"] = df["timestamp_ms"].astype("int64")
    df["available_ts"] = df["timestamp_ms"] + BAR15_MS
    lr = np.log(df["close"]).diff()
    bars7 = 7 * 24 * 4
    df["rv7d"] = lr.rolling(bars7, min_periods=bars7).std()

    times = df["available_ts"].to_numpy(dtype=np.int64)
    vals = df["rv7d"].to_numpy(dtype=float)
    pct = np.full(len(df), np.nan)
    min_hist = 30 * 24 * 4
    for i in range(len(df)):
        if not math.isfinite(vals[i]):
            continue
        lo = np.searchsorted(times, times[i] - 365 * DAY_MS, side="left")
        hist = vals[lo:i + 1]
        hist = hist[np.isfinite(hist)]
        if len(hist) >= min_hist:
            pct[i] = (hist <= vals[i]).mean() * 100.0
    df["btc_rv7d_pctile365"] = pct
    return df


def btc_context_at(btc, ts):
    times = btc["available_ts"].to_numpy(dtype=np.int64)
    i = int(np.searchsorted(times, int(ts), side="right") - 1)
    if i < 0:
        return ("UNKNOWN", float("nan"))
    p = float(btc.iloc[i]["btc_rv7d_pctile365"])
    if not math.isfinite(p):
        return ("UNKNOWN", p)
    if p < 33.333333:
        return ("LOW", p)
    if p < 66.666667:
        return ("MID", p)
    return ("HIGH", p)


def simulate_both(l1, minute_data):
    rows = []
    for direction in ("LONG", "SHORT"):
        x = l1.copy()
        x["direction"] = direction
        for s in x.itertuples(index=False):
            md = minute_data.get(s.symbol)
            for delay in (1, 2, 3):
                r = one_trade(s, md, delay)
                if r is not None:
                    rows.append(r)
    return pd.DataFrame(rows)


def summary_table(trades):
    rows = []
    for (state, direction, delay), g in trades.groupby(
        ["btc_vol_state", "direction", "delay_min"], dropna=False
    ):
        base = metrics(g)
        rows.append({
            "btc_vol_state": state,
            "direction": direction,
            "delay_min": int(delay),
            **base,
            "pf_slip025": metrics(g, 0.25)["profit_factor"],
            "pf_slip050": metrics(g, 0.50)["profit_factor"],
        })
    return pd.DataFrame(rows)


def concentration(primary):
    rows = []
    for delay in (1, 2, 3):
        g = primary[primary["delay_min"] == delay].copy()
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


def temporal(primary, window_start, window_end):
    midpoint = int(window_start + (window_end - window_start) / 2)
    x = primary.copy()
    x["half"] = np.where(x["signal_ts"] < midpoint, "FIRST_HALF", "SECOND_HALF")
    x["month"] = pd.to_datetime(x["signal_ts"], unit="ms", utc=True).dt.strftime("%Y-%m")
    rows = []
    for label_col in ("half", "month"):
        for (label, delay), g in x.groupby([label_col, "delay_min"]):
            rows.append({
                "slice_type": label_col,
                "slice": label,
                "delay_min": int(delay),
                **metrics(g),
                "pf_slip025": metrics(g, 0.25)["profit_factor"],
                "pf_slip050": metrics(g, 0.50)["profit_factor"],
            })
    return pd.DataFrame(rows)


def symbol_table(primary):
    g = primary[primary["delay_min"] == 1].copy()
    if g.empty:
        return pd.DataFrame()
    out = g.groupby("symbol").agg(
        n=("net_pct", "size"),
        sum_net_pct=("net_pct", "sum"),
        avg_net_pct=("net_pct", "mean"),
    ).reset_index()
    pfs = g.groupby("symbol")["net_pct"].apply(calc_pf).rename("profit_factor").reset_index()
    return out.merge(pfs, on="symbol").sort_values("sum_net_pct", ascending=False)


def main():
    args = parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    selection = json.loads(Path(args.selection).read_text(encoding="utf-8"))
    auto100 = list(selection["symbols"])
    overlap = [s for s in auto100 if s in ORIGINAL_AUTO50]
    holdout = [s for s in auto100 if s not in ORIGINAL_AUTO50]

    source = pd.read_csv(args.source)
    source_symbols = sorted(source["symbol"].dropna().unique())
    unexpected = sorted(set(source_symbols) - set(holdout))
    missing = sorted(set(holdout) - set(source_symbols))

    meta = {
        "auto100_count": len(auto100),
        "original_auto50_count": len(ORIGINAL_AUTO50),
        "overlap_count": len(overlap),
        "cross_section_holdout_count": len(holdout),
        "overlap_symbols": overlap,
        "holdout_symbols": holdout,
        "source_symbols": source_symbols,
        "missing_holdout_symbols": missing,
        "unexpected_source_symbols": unexpected,
        "hypothesis": "Frozen H1: L1 signal + BTC 7d realized-vol trailing-365d percentile MID [33.333,66.667) => SHORT; TP10/SL5/max720m.",
        "note": "Cross-sectional holdout only. Dates overlap discovery period, so this is not an untouched time holdout.",
    }
    (outdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    if unexpected:
        raise RuntimeError(f"source contains non-holdout symbols: {unexpected[:10]}")

    signals = build_signals(source)
    l1 = signals[signals["strategy"] == "L1_MOMENTUM_1H10"].copy()
    if l1.empty:
        raise RuntimeError("0 L1 signals in holdout universe")

    print(f"[UNIVERSE] AUTO100={len(auto100)} overlap_old_AUTO50={len(overlap)} NEW_HOLDOUT={len(holdout)}")
    print(f"[SIGNALS] L1={len(l1)} symbols={l1['symbol'].nunique()}")

    minute_data, failures = fetch_all_minutes(l1, args.workers)
    if failures:
        pd.DataFrame(failures, columns=["symbol", "error"]).to_csv(outdir / "minute_failures.csv", index=False)

    trades = simulate_both(l1, minute_data)
    btc = load_btc(Path(args.market_root))
    ctx = {}
    for ts in trades["signal_ts"].unique():
        ctx[int(ts)] = btc_context_at(btc, int(ts))
    trades["btc_vol_state"] = trades["signal_ts"].map(lambda x: ctx[int(x)][0])
    trades["btc_rv7d_pctile365"] = trades["signal_ts"].map(lambda x: ctx[int(x)][1])

    summary = summary_table(trades)
    primary = trades[
        (trades["btc_vol_state"] == "MID")
        & (trades["direction"] == "SHORT")
    ].copy()

    window_start = int(source["ts"].min())
    window_end = int(source["ts"].max())
    conc = concentration(primary)
    temp = temporal(primary, window_start, window_end)
    sym = symbol_table(primary)

    trades.to_csv(outdir / "all_l1_long_short_trades.csv.gz", index=False, compression="gzip")
    summary.to_csv(outdir / "summary_by_btc_vol.csv", index=False)
    primary.to_csv(outdir / "frozen_h1_trades.csv", index=False)
    conc.to_csv(outdir / "frozen_h1_concentration.csv", index=False)
    temp.to_csv(outdir / "frozen_h1_temporal.csv", index=False)
    sym.to_csv(outdir / "frozen_h1_symbols.csv", index=False)

    print("\n=== FROZEN H1 CROSS-SECTION HOLDOUT ===")
    for delay in (1, 2, 3):
        g = primary[primary["delay_min"] == delay]
        m = metrics(g)
        print(
            f"+{delay}m n={m['n']} symbols={m['symbols']} avg={m['avg_net_pct']:.4f}% "
            f"PF={m['profit_factor']:.3f} PF+0.25={metrics(g,0.25)['profit_factor']:.3f} "
            f"PF+0.50={metrics(g,0.50)['profit_factor']:.3f}"
        )

    print("\n=== CONCENTRATION ===")
    print(conc.to_string(index=False))
    print("\n=== TEMPORAL ===")
    print(temp.to_string(index=False))
    print("\n=== IMPORTANT ===")
    print("These symbols were not in the original AUTO50 discovery universe.")
    print("The calendar window overlaps discovery, so this tests cross-sectional generalization, not new-time generalization.")
    print("No threshold search is performed: BTC-vol MID and L1 TP/SL/horizon are frozen.")
    print(f"[DONE] trades={len(trades)} frozen_h1_rows={len(primary)}")


if __name__ == "__main__":
    main()
