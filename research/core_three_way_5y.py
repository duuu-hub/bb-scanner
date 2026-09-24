#!/usr/bin/env python3
"""Frozen-rule portfolio transfer test. Research only; no trading integration.

LONG3 L2 transactions come from completed run 36019652969. Continuation SHORT
is transferred from Bitget AUTO100 to the existing Binance USD-M 5Y archive;
this is deliberately labelled a venue/universe transfer, not a Bitget replay.
"""
import io
import math
import os
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OUT = Path("artifacts")
OUT.mkdir(exist_ok=True)
START = pd.Timestamp("2021-09-01", tz="UTC")
END = pd.Timestamp("2026-09-01", tz="UTC")  # exclusive: five complete years
CORE_FEE = .25  # round trip, percent of core notional
L2_FEE = .12
SHORT_FEE = .20


def spot_daily(symbol):
    root = Path("spot_daily")
    root.mkdir(exist_ok=True)
    cache = root / f"{symbol}.csv.gz"
    if cache.exists():
        d = pd.read_csv(cache, parse_dates=["dt"])
        d["dt"] = pd.to_datetime(d.dt, utc=True)
        return d
    session = requests.Session()
    rows = []
    # Need warmup for the frozen 30-day regime and entry decision.
    for month in pd.date_range("2021-07-01", "2026-08-01", freq="MS", tz="UTC"):
        ym = month.strftime("%Y-%m")
        url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{ym}.zip"
        r = session.get(url, timeout=60)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            member = next(n for n in z.namelist() if n.endswith(".csv"))
            frame = pd.read_csv(z.open(member), header=None)
        rows.append(frame.iloc[:, [0, 1, 4]].set_axis(["timestamp", "open", "close"], axis=1))
    d = pd.concat(rows, ignore_index=True)
    ms = np.where(pd.to_numeric(d.timestamp) > 1e14, d.timestamp / 1000, d.timestamp)
    d["dt"] = pd.to_datetime(ms, unit="ms", utc=True)
    d = d[["dt", "open", "close"]].drop_duplicates("dt").sort_values("dt")
    d.to_csv(cache, index=False, compression="gzip")
    return d


def core_panel():
    a = spot_daily("BTCUSDT").rename(columns={"open": "b_open", "close": "b_close"})
    b = spot_daily("ETHUSDT").rename(columns={"open": "e_open", "close": "e_close"})
    d = a.merge(b, on="dt")
    for pre in ("b", "e"):
        c = d[f"{pre}_close"]
        d[f"{pre}_ret30"] = c / c.shift(30) - 1
        d[f"{pre}_er"] = (c - c.shift(30)).abs() / c.diff().abs().rolling(30, min_periods=15).sum()
    d["active"] = ((d.b_ret30 > 0) & (d.e_ret30 > 0) & ((d.b_er + d.e_er) / 2 >= .193654)).astype(int)
    d["base"] = d.active.shift(1).fillna(0).astype(int)
    d["gross"] = ((d.b_close / d.b_open - 1) + (d.e_close / d.e_open - 1)) * 50
    base = d.base.to_numpy()
    alt = np.zeros(len(d), dtype=int)
    ent = None
    forced = False
    for i in range(len(d)):
        if base[i] and (i == 0 or not base[i - 1]):
            ent, forced = i, False
        if not base[i]:
            ent, forced = None, False
        if base[i] and not forced:
            alt[i] = 1
            if ent is not None and i - ent == 2:
                old = d.iloc[ent]
                now = d.iloc[i]
                two_day = ((now.b_close / old.b_close - 1) + (now.e_close / old.e_close - 1)) * 50
                if np.isfinite(two_day) and two_day <= 0:
                    forced = True
    d["d2"] = alt
    for name in ("base", "d2"):
        p = d[name]
        d[name + "_net"] = p * d.gross - p.diff().abs().fillna(p.abs()) * (CORE_FEE / 2)
    return d[(d.dt >= START) & (d.dt < END)].copy()


def load_l2():
    p = Path("input_l2/long3_5y_trades.csv")
    d = pd.read_csv(p, parse_dates=["signal_time", "exit_time"])
    d = d[d.strategy.eq("L2")].copy()
    d["entry_time"] = pd.to_datetime(d.signal_time, utc=True)
    d["exit_time"] = pd.to_datetime(d.exit_time, utc=True) + pd.Timedelta(minutes=15)
    d["ret"] = d.net_pct
    d["module"] = "L2"
    return d[["module", "symbol", "entry_time", "exit_time", "ret"]]


def continuation():
    """Apply the previously frozen Bitget SHORT thresholds on Binance futures."""
    trades = []
    files = sorted(Path("canonical_um").glob("*.parquet"))
    if not files:
        raise RuntimeError("missing existing Binance USD-M canonical archive")
    for ix, path in enumerate(files, 1):
        d = pd.read_parquet(path, columns=["open_time", "open", "high", "low", "close"])
        d = d.dropna().sort_values("open_time").drop_duplicates("open_time")
        t = d.open_time.to_numpy(dtype="int64")
        if len(t) < 100:
            continue
        c = d.close.to_numpy(dtype=float)
        o = d.open.to_numpy(dtype=float)
        h = d.high.to_numpy(dtype=float)
        l = d.low.to_numpy(dtype=float)
        series = pd.Series(np.log(c)).diff()
        rv4 = series.rolling(16).std(ddof=0).to_numpy() * 100
        rv24 = series.rolling(96).std(ddof=0).to_numpy() * 100
        ret1 = pd.Series(c).pct_change(4).to_numpy() * 100
        ret4 = pd.Series(c).pct_change(16).to_numpy() * 100
        ret24 = pd.Series(c).pct_change(96).to_numpy() * 100
        good = (ret1 <= -1) & (ret4 < 0) & (ret24 <= -9.858) & (rv4 >= 1.214) & (rv24 >= 1.315)
        # A 15m gap in any of the required rolling paths invalidates features.
        gap = pd.Series(np.diff(t, prepend=t[0]) != 900_000).rolling(97).max().fillna(1).to_numpy() == 0
        indices = np.flatnonzero(good & gap & (t >= int(START.timestamp() * 1000)) & (t < int(END.timestamp() * 1000)))
        busy_until = -1
        for i in indices:
            ei = i + 1  # completed signal bar -> next 15m open
            if ei >= len(t) or t[ei] != t[i] + 900_000 or t[ei] <= busy_until:
                continue
            ep = o[ei]
            deadline = t[ei] + 6 * 3_600_000
            last = min(len(t) - 1, np.searchsorted(t, deadline, side="left") - 1)
            if last < ei:
                continue
            reason = "TIME"
            xp = c[last]
            exi = last
            for j in range(ei, last + 1):
                # Missing bars across the holding window end at the last actual bar.
                if j > ei and t[j] != t[j - 1] + 900_000:
                    exi, xp, reason = j - 1, c[j - 1], "GAP"
                    break
                if h[j] >= ep * 1.02:  # SL wins same-candle ambiguity
                    exi, xp, reason = j, ep * 1.02, "SL"
                    break
                if l[j] <= ep * .96:
                    exi, xp, reason = j, ep * .96, "TP"
                    break
            gross = (1 - xp / ep) * 100
            trades.append(("CONT", path.stem, pd.to_datetime(t[ei], unit="ms", utc=True),
                           pd.to_datetime(t[exi] + 900_000, unit="ms", utc=True), gross - SHORT_FEE, reason))
            busy_until = t[exi]
        if ix % 50 == 0:
            print("continuation symbols", ix, "/", len(files), "trades", len(trades), flush=True)
    return pd.DataFrame(trades, columns=["module", "symbol", "entry_time", "exit_time", "ret", "reason"])


def independent_events(d):
    if d.empty:
        return 0
    return d.entry_time.dt.floor("24h").nunique()


def metrics(daily, accepted, maximum):
    v = daily.to_numpy(float) / 100
    eq = np.cumprod(1 + v)
    curve = np.r_[1., eq]
    return {"return_pct": (eq[-1] - 1) * 100,
            "mdd_daily_close_pct": (curve / np.maximum.accumulate(curve) - 1).min() * 100,
            "sharpe_daily": np.mean(v) / np.std(v) * math.sqrt(365) if np.std(v) else 0,
            "l2_trades": int((accepted.module == "L2").sum()),
            "short_trades": int((accepted.module == "CONT").sum()),
            "max_gross_exposure_pct": maximum * 100}


def portfolio(core, alt, variant, mode):
    """Daily-close attribution; admission respects intraday overlap and cap."""
    p = core.set_index("dt")
    active = p[variant].astype(bool)
    candidates = alt.sort_values(["entry_time", "module", "symbol"]).copy()
    available = []
    accepted = []
    maxexp = 0.
    # Separate: 50/25/25 permanently reserved, 30% of each sub pool per position.
    # Shared: 100% core when active; flat periods use 30% per alt, 200% cap.
    # Matched: same sub notional as separate, gated to flat; isolates regime gate.
    core_weight = .5 if mode in ("separate", "matched_gate") else 1.
    weights = {"L2": .25 * .3, "CONT": .25 * .3} if mode in ("separate", "matched_gate") else {"L2": .3, "CONT": .3}
    for r in candidates.itertuples():
        day = r.entry_time.floor("D")
        if day not in p.index:
            continue
        is_core = bool(active.loc[day])
        if mode != "separate" and is_core:
            continue
        available = [x for x in available if x.exit_time > r.entry_time]
        if any(x.symbol == r.symbol for x in available):
            continue
        # Pool internal 200% cap; shared total incl. core 200% cap.
        current = sum(x.weight for x in available)
        if mode == "separate":
            m = sum(x.weight for x in available if x.module == r.module)
            if m + weights[r.module] > .25 * 2 + 1e-9:
                continue
        elif current + weights[r.module] > (1.0 if mode == "shared_priority" else 2.0) + 1e-9:
            # Reserve one full equity unit for tomorrow's core reopening;
            # the signal cannot know tomorrow's regime at the current open.
            continue
        item = pd.Series({"module": r.module, "symbol": r.symbol,
                          "entry_time": r.entry_time, "exit_time": r.exit_time,
                          "ret": r.ret, "weight": weights[r.module]})
        available.append(item)
        accepted.append(item)
        maxexp = max(maxexp, current + weights[r.module] + (core_weight if is_core else 0))
    a = pd.DataFrame(accepted, columns=["module", "symbol", "entry_time", "exit_time", "ret", "weight"])
    net = p[variant + "_net"].copy() * core_weight
    if len(a):
        # Realized on exit; daily-close DD is a lower bound on intraday drawdown.
        exits = a.assign(day=a.exit_time.dt.floor("D"), cash_pct=a.ret * a.weight)
        contributions = exits.groupby("day").cash_pct.sum()
        net = net.add(contributions, fill_value=0).reindex(p.index, fill_value=0)
    # Scan every entry/exit and daily core changes to measure max actual overlap.
    changes = []
    for r in a.itertuples():
        changes.extend([(r.entry_time, 1, r.weight), (r.exit_time, 0, -r.weight)])
    for dt, z in active.items():
        changes.append((dt, -1, core_weight if z else 0.))
    changes.sort(key=lambda x: (x[0], x[1]))
    alt_exposure, core_exposure = 0., 0.
    for _, kind, val in changes:
        if kind == -1:
            core_exposure = val
        else:
            alt_exposure += val
        maxexp = max(maxexp, alt_exposure + core_exposure)
    return net, a, metrics(net, a, maxexp)


def main():
    core = core_panel()
    l2 = load_l2()
    short = continuation()
    l2 = l2[(l2.entry_time >= START) & (l2.entry_time < END)]
    short = short[(short.entry_time >= START) & (short.entry_time < END)]
    alltr = pd.concat([l2, short], ignore_index=True)
    alltr.to_csv(OUT / "frozen_candidates.csv.gz", index=False, compression="gzip")
    core[["dt", "base", "d2", "base_net", "d2_net"]].to_csv(OUT / "core_daily.csv.gz", index=False, compression="gzip")
    overview = []
    for module, x in [("L2", l2), ("CONT", short)]:
        overview.append({"module": module, "trades": len(x), "symbols": x.symbol.nunique(),
                         "signal_days": independent_events(x), "avg_pct": x.ret.mean(),
                         "pf": x.loc[x.ret > 0, "ret"].sum() / abs(x.loc[x.ret < 0, "ret"].sum()),
                         "first": x.entry_time.min(), "last": x.entry_time.max()})
    pd.DataFrame(overview).to_csv(OUT / "module_coverage.csv", index=False)
    results = []
    annual = []
    for core_name in ("base", "d2"):
        raw = core[core_name + "_net"]
        eq = np.cumprod(1 + raw.to_numpy() / 100)
        results.append({"core": core_name, "mode": "core_only_100pct", "start": str(START.date()),
                        "end": str((END-pd.Timedelta(days=1)).date()),
                        "return_pct": (eq[-1]-1)*100,
                        "mdd_daily_close_pct": (np.r_[1., eq]/np.maximum.accumulate(np.r_[1., eq])-1).min()*100,
                        "sharpe_daily": raw.mean()/raw.std(ddof=0)*math.sqrt(365),
                        "l2_trades": 0, "short_trades": 0,
                        "max_gross_exposure_pct": 100.})
    for core_name in ("base", "d2"):
        for mode in ("separate", "matched_gate", "shared_priority"):
            daily, admitted, row = portfolio(core, alltr, core_name, mode)
            row.update(core=core_name, mode=mode, start=str(START.date()), end=str((END-pd.Timedelta(days=1)).date()))
            results.append(row)
            daily.rename("daily_return_pct").to_csv(OUT / f"daily_{core_name}_{mode}.csv")
            admitted.to_csv(OUT / f"accepted_{core_name}_{mode}.csv", index=False)
            for year, ys in daily.groupby(daily.index.year):
                yearly_equity = np.cumprod(1 + ys.to_numpy() / 100)
                annual.append({"core":core_name,"mode":mode,"year":year,
                               "days":len(ys),"return_pct":(yearly_equity[-1]-1)*100,
                               "l2_trades":int(((admitted.module == "L2") &
                                 (admitted.exit_time.dt.year == year)).sum()),
                               "short_trades":int(((admitted.module == "CONT") &
                                 (admitted.exit_time.dt.year == year)).sum())})
    x = pd.DataFrame(results)
    x.to_csv(OUT / "portfolio_comparison.csv", index=False)
    pd.DataFrame(annual).to_csv(OUT / "annual.csv", index=False)
    print("MODULE COVERAGE", pd.DataFrame(overview).to_string(index=False), flush=True)
    print("PORTFOLIO", x.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
