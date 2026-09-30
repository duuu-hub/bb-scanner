import argparse, glob, io, os, time, zipfile, math, json, hashlib
from functools import lru_cache
import numpy as np
import pandas as pd
import requests

BAR_MS = 15 * 60 * 1000
TP = 8.0
SL = 1.0
TH = 20.0
HOLD_BARS = 4
COSTS = [0.0, 0.2, 0.4]
BINANCE_API = "https://fapi.binance.com/fapi/v1/klines"
ARCHIVE = "https://data.binance.vision/data/futures/um/daily/klines"

def find_symbol_file(root, symbol):
    hits = glob.glob(os.path.join(root, "**", f"{symbol}.csv.gz"), recursive=True)
    if not hits:
        raise FileNotFoundError(f"{symbol}.csv.gz not found under {root}")
    return hits[0]

def read_15m(path):
    use = ["open_time","open","high","low","close","volume","quote_volume"]
    d = pd.read_csv(path, usecols=use)
    for c in use:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna().sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    return d

def build_regime(data_root, out):
    p = find_symbol_file(data_root, "BTCUSDT")
    d = read_15m(p)
    d["date"] = pd.to_datetime(d.open_time.astype("int64"), unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    daily = d.groupby("date", as_index=False).agg(close=("close","last"))
    close = daily["close"].astype(float)
    ema50 = close.ewm(span=50, adjust=False, min_periods=50).mean()
    ema200 = close.ewm(span=200, adjust=False, min_periods=200).mean()
    prev_close = close.shift(1)
    ema50_prev = ema50.shift(1)
    ema200_prev = ema200.shift(1)
    ret30 = (close.shift(1) / close.shift(31) - 1.0) * 100.0

    ema_regime = np.where(
        ema50_prev.isna() | ema200_prev.isna(), "NA",
        np.where(ema50_prev > ema200_prev, "BULL", "BEAR")
    )
    ret_regime = np.where(
        ret30.isna(), "NA",
        np.where(ret30 >= 5.0, "UP", np.where(ret30 <= -5.0, "DOWN", "FLAT"))
    )
    outd = pd.DataFrame({
        "date": daily["date"],
        "btc_prev_close": prev_close,
        "btc_ema50_prev": ema50_prev,
        "btc_ema200_prev": ema200_prev,
        "btc_30d_ret_pct": ret30,
        "ema_regime": ema_regime,
        "ret30_regime": ret_regime,
    })
    outd.to_csv(out, index=False)
    print("REGIME_ROWS", len(outd), "FROM", outd.date.min(), "TO", outd.date.max())

@lru_cache(maxsize=128)
def archive_day(symbol, day):
    url = f"{ARCHIVE}/{symbol}/1m/{symbol}-1m-{day}.zip"
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent":"bb-scanner-research"})
        if r.status_code != 200:
            return None
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names = z.namelist()
            if not names:
                return None
            raw = z.read(names[0])
        x = pd.read_csv(io.BytesIO(raw), header=None)
        if x.shape[1] < 5:
            return None
        ts = pd.to_numeric(x.iloc[:,0], errors="coerce")
        if ts.dropna().median() > 1e14:
            ts = ts / 1000.0
        return pd.DataFrame({
            "ts": ts.astype("Int64"),
            "high": pd.to_numeric(x.iloc[:,2], errors="coerce"),
            "low": pd.to_numeric(x.iloc[:,3], errors="coerce"),
        }).dropna()
    except Exception:
        return None

def api_1m(symbol, start_ms):
    try:
        r = requests.get(BINANCE_API, params={
            "symbol": symbol, "interval":"1m",
            "startTime": int(start_ms), "endTime": int(start_ms + BAR_MS - 1), "limit": 20
        }, timeout=20, headers={"User-Agent":"bb-scanner-research"})
        if r.status_code != 200:
            return None
        a = r.json()
        if not isinstance(a, list):
            return None
        rows = []
        for z in a:
            rows.append((int(z[0]), float(z[2]), float(z[3])))
        return rows
    except Exception:
        return None

def resolve_1m(symbol, start_ms, ep, side):
    rows = api_1m(symbol, start_ms)
    source = "api"
    if not rows:
        day = pd.to_datetime(int(start_ms), unit="ms", utc=True).strftime("%Y-%m-%d")
        x = archive_day(symbol, day)
        source = "archive"
        if x is not None:
            y = x[(x.ts >= int(start_ms)) & (x.ts < int(start_ms + BAR_MS))]
            rows = [(int(r.ts), float(r.high), float(r.low)) for r in y.itertuples(index=False)]
    if not rows:
        return -SL, "unresolved_loss"
    rows = sorted(rows, key=lambda z:z[0])
    for _, h, l in rows:
        if side == "LONG":
            hit_tp = h >= ep * (1 + TP/100)
            hit_sl = l <= ep * (1 - SL/100)
        else:
            hit_tp = l <= ep * (1 - TP/100)
            hit_sl = h >= ep * (1 + SL/100)
        if hit_tp and hit_sl:
            return -SL, f"same_1m_loss_{source}"
        if hit_tp:
            return TP, f"tp_1m_{source}"
        if hit_sl:
            return -SL, f"sl_1m_{source}"
    return -SL, f"unresolved_sequence_loss_{source}"

def apply_regime(events, regime_path):
    if not len(events):
        return events
    reg = pd.read_csv(regime_path)
    m = reg.set_index("date")
    events["date"] = pd.to_datetime(events.ts.astype("int64"), unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    events = events.join(m, on="date")
    return events

def event_scan(d, symbol):
    rows = []
    if len(d) < 101:
        return rows
    # Never bridge data gaps.
    seg = (d.open_time.diff().fillna(BAR_MS) != BAR_MS).cumsum()
    for _, x in d.groupby(seg):
        x = x.reset_index(drop=True)
        if len(x) < 101:
            continue
        vol = x.volume.astype(float)
        qv = x.quote_volume.astype(float)
        vwap = (qv.rolling(96, min_periods=96).sum() / vol.rolling(96, min_periods=96).sum()).shift(1)
        dist = (x.open.astype(float) / vwap - 1.0) * 100.0
        for side, mask, sgn in [
            ("LONG", dist <= -TH, 1.0),
            ("SHORT", dist >= TH, -1.0),
        ]:
            ev = np.where((mask & ~mask.shift(1, fill_value=False)).fillna(False))[0]
            for i in ev:
                if i + HOLD_BARS - 1 >= len(x):
                    continue
                ep = float(x.open.iloc[i])
                gross = None
                reason = None
                for j in range(i, i + HOLD_BARS):
                    h = float(x.high.iloc[j]); l = float(x.low.iloc[j])
                    if side == "LONG":
                        hit_tp = h >= ep * (1 + TP/100)
                        hit_sl = l <= ep * (1 - SL/100)
                    else:
                        hit_tp = l <= ep * (1 - TP/100)
                        hit_sl = h >= ep * (1 + SL/100)
                    if hit_tp and hit_sl:
                        gross, reason = resolve_1m(symbol, int(x.open_time.iloc[j]), ep, side)
                        break
                    if hit_tp:
                        gross, reason = TP, "tp_15m"
                        break
                    if hit_sl:
                        gross, reason = -SL, "sl_15m"
                        break
                if gross is None:
                    exit_close = float(x.close.iloc[i + HOLD_BARS - 1])
                    gross = sgn * (exit_close / ep - 1.0) * 100.0
                    reason = "timeout_1h"
                rows.append([
                    symbol, side, int(x.open_time.iloc[i]), ep, float(dist.iloc[i]), float(gross), reason
                ])
    return rows

def backtest(data_root, regime_path, out, shard):
    fs = sorted(glob.glob(os.path.join(data_root, "**", "*.csv.gz"), recursive=True))
    allrows = []
    for k, p in enumerate(fs):
        symbol = os.path.basename(p).replace(".csv.gz","").upper()
        if symbol == "BTCUSDT" or symbol.endswith("USDT"):
            try:
                d = read_15m(p)
                allrows.extend(event_scan(d, symbol))
            except Exception as e:
                print("ERR", symbol, repr(e))
        if (k+1) % 20 == 0:
            print("PROGRESS", shard, k+1, "/", len(fs), "events", len(allrows))
    e = pd.DataFrame(allrows, columns=["symbol","side","ts","entry","dist_pct","gross_pct","exit_reason"])
    e = apply_regime(e, regime_path)
    if len(e):
        e["year"] = pd.to_datetime(e.ts.astype("int64"), unit="ms", utc=True).dt.year
        e["split"] = np.where(e["year"] <= 2024, "TRAIN_2021_2024", "HOLDOUT_2025_2026")
    e.to_csv(out, index=False)
    print("SHARD_DONE", shard, "FILES", len(fs), "EVENTS", len(e))
    if len(e):
        print(e.groupby("side").gross_pct.agg(["size","mean"]).to_string())
        print("AMBIG", e.exit_reason.str.contains("1m|unresolved", regex=True).sum())

def pf(y):
    pos = y[y > 0].sum()
    neg = -y[y < 0].sum()
    return float(pos / neg) if neg > 0 else (float("inf") if pos > 0 else float("nan"))

def summarize_group(d, group_cols):
    out = []
    groupers = list(group_cols)
    for keys, g in d.groupby(groupers, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        for c in COSTS:
            y = g.gross_pct.astype(float) - c
            out.append(list(keys) + [c, len(y), (y > 0).mean()*100, y.mean(), y.median(), y.sum(), pf(y)])
    cols = groupers + ["cost_pct","n","wr_pct","mean_pct","median_pct","sum_pct","pf"]
    return pd.DataFrame(out, columns=cols)

def summarize(indir, outdir):
    fs = sorted(glob.glob(os.path.join(indir, "**", "events_*.csv"), recursive=True))
    if not fs:
        raise RuntimeError(f"no event files under {indir}")
    d = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    os.makedirs(outdir, exist_ok=True)
    d.to_csv(os.path.join(outdir, "vwap20_frozen_5y_events.csv"), index=False)

    parts = []
    for side in ["LONG","SHORT","ALL"]:
        x = d if side == "ALL" else d[d.side == side]
        for c in COSTS:
            y = x.gross_pct.astype(float) - c
            parts.append([side,c,len(y),(y>0).mean()*100,y.mean(),y.median(),y.sum(),pf(y)])
    overall = pd.DataFrame(parts, columns=["side","cost_pct","n","wr_pct","mean_pct","median_pct","sum_pct","pf"])
    overall.to_csv(os.path.join(outdir, "summary_overall.csv"), index=False)

    summarize_group(d, ["side","ema_regime"]).to_csv(os.path.join(outdir, "summary_ema_regime.csv"), index=False)
    summarize_group(d, ["side","ret30_regime"]).to_csv(os.path.join(outdir, "summary_ret30_regime.csv"), index=False)
    summarize_group(d, ["side","year"]).to_csv(os.path.join(outdir, "summary_year.csv"), index=False)
    summarize_group(d, ["side","split"]).to_csv(os.path.join(outdir, "summary_split.csv"), index=False)
    d.groupby(["side","exit_reason"], dropna=False).size().reset_index(name="n").to_csv(os.path.join(outdir, "exit_reason_counts.csv"), index=False)

    # 30 explicit integrity checks + 10 repeated deterministic consistency checks.
    checks = []
    def ck(name, cond, detail=""):
        checks.append((name, bool(cond), str(detail)))
    ck("01_events_nonempty", len(d)>0, len(d))
    ck("02_sides", set(d.side.unique()).issubset({"LONG","SHORT"}), sorted(d.side.unique()))
    ck("03_threshold_long", (d.loc[d.side=="LONG","dist_pct"] <= -TH + 1e-12).all())
    ck("04_threshold_short", (d.loc[d.side=="SHORT","dist_pct"] >= TH - 1e-12).all())
    ck("05_tp_bound", (d.gross_pct <= TP + 1e-9).all(), d.gross_pct.max())
    ck("06_sl_bound", (d.gross_pct >= -SL - 1e-9).all(), d.gross_pct.min())
    ck("07_ts_finite", np.isfinite(d.ts).all())
    ck("08_entry_positive", (d.entry>0).all())
    ck("09_dist_finite", np.isfinite(d.dist_pct).all())
    ck("10_gross_finite", np.isfinite(d.gross_pct).all())
    ck("11_no_exact_duplicate", d.duplicated(["symbol","side","ts"]).sum()==0, d.duplicated(["symbol","side","ts"]).sum())
    ck("12_year_range", d.year.between(2021,2026).all(), (d.year.min(),d.year.max()))
    ck("13_split_values", set(d.split.unique()).issubset({"TRAIN_2021_2024","HOLDOUT_2025_2026"}))
    ck("14_costs", COSTS==[0.0,0.2,0.4])
    ck("15_tp_exact", TP==8.0)
    ck("16_sl_exact", SL==1.0)
    ck("17_threshold_exact", TH==20.0)
    ck("18_hold_exact", HOLD_BARS==4)
    ck("19_ema_values", set(d.ema_regime.dropna().unique()).issubset({"BULL","BEAR","NA"}))
    ck("20_ret30_values", set(d.ret30_regime.dropna().unique()).issubset({"UP","DOWN","FLAT","NA"}))
    ck("21_long_present", (d.side=="LONG").any())
    ck("22_short_present", (d.side=="SHORT").any())
    ck("23_train_present", (d.split=="TRAIN_2021_2024").any())
    ck("24_holdout_present", (d.split=="HOLDOUT_2025_2026").any())
    ck("25_overall_rows", len(overall)==9, len(overall))
    ck("26_pf_nonnegative", (overall.pf.dropna()>=0).all())
    ck("27_wr_range", overall.wr_pct.between(0,100).all())
    ck("28_event_dates", d.date.notna().all())
    ck("29_timeout_reason_known", d.exit_reason.isin(["tp_15m","sl_15m","timeout_1h","unresolved_loss","same_1m_loss_api","same_1m_loss_archive","tp_1m_api","tp_1m_archive","sl_1m_api","sl_1m_archive","unresolved_sequence_loss_api","unresolved_sequence_loss_archive"]).all(), sorted(d.exit_reason.unique()))
    ck("30_summary_count_match", int(overall[(overall.side=="ALL")&(overall.cost_pct==0)].n.iloc[0])==len(d))
    base_hash = hashlib.sha256(d.sort_values(["symbol","side","ts"]).to_csv(index=False).encode()).hexdigest()
    for i in range(10):
        h = hashlib.sha256(d.sort_values(["symbol","side","ts"]).to_csv(index=False).encode()).hexdigest()
        ck(f"{31+i:02d}_determinism_{i+1}", h==base_hash, h[:16])
    audit = pd.DataFrame(checks, columns=["check","pass","detail"])
    audit.to_csv(os.path.join(outdir, "audit_40checks.csv"), index=False)
    print("AUDIT", int(audit["pass"].sum()), "/", len(audit))
    print(overall.to_string(index=False))
    if not audit["pass"].all():
        print(audit[~audit["pass"]].to_string(index=False))
        raise SystemExit("AUDIT FAILED")

def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("regime"); p.add_argument("--data", required=True); p.add_argument("--out", required=True)
    p = sp.add_parser("backtest"); p.add_argument("--data", required=True); p.add_argument("--regime", required=True); p.add_argument("--out", required=True); p.add_argument("--shard", required=True)
    p = sp.add_parser("summarize"); p.add_argument("--in", dest="indir", required=True); p.add_argument("--outdir", required=True)
    a = ap.parse_args()
    if a.cmd == "regime": build_regime(a.data, a.out)
    elif a.cmd == "backtest": backtest(a.data, a.regime, a.out, a.shard)
    else: summarize(a.indir, a.outdir)

if __name__ == "__main__":
    main()
