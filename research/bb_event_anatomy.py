from __future__ import annotations

import argparse
import gzip
import io
import json
import math
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from market_data.contract_filters import RESEARCH_CORE_SYMBOLS

BAR15_MS = 15 * 60_000
BAR1H_MS = 60 * 60_000
BAR2H_MS = 2 * 60 * 60_000
BAR4H_MS = 4 * 60 * 60_000
DAY_MS = 24 * 60 * 60_000
RNG = np.random.default_rng(20260923)

FEATURES = [
    "ret_1h_pct",
    "ret_4h_pct",
    "ret_24h_pct",
    "ret_72h_pct",
    "accel_1h_vs_4h",
    "quote_vol_surge_1h_vs_prior24h",
    "rv24h_pct",
    "bb1h_dist_pct",
    "bb1h_width_pct",
    "bb4h_dist_pct",
    "bb4h_width_pct",
    "signal_count_6h",
    "signal_count_24h",
    "prior_symbol_l1_30d",
    "hours_since_prev_symbol_l1",
    "breadth_pos4h_pct",
    "breadth_pos24h_pct",
    "breadth_ret24_gt10_pct",
    "market_median_4h_pct",
    "market_median_24h_pct",
    "btc_ret_4h_pct",
    "btc_ret_24h_pct",
    "total3_ret_24h_pct",
]

OLD_AUTO50 = {
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
    p.add_argument("--old-artifact", required=True)
    p.add_argument("--new-artifact", required=True)
    p.add_argument("--root", default="market_data_store/bitget/research_auto100_15m")
    p.add_argument("--selection", default="market_data_store/bitget/research_auto100_15m/selection.json")
    p.add_argument("--total3", default="market_data_store/tradingview/TOTAL3.csv")
    p.add_argument("--outdir", default="bb_event_anatomy_results")
    return p.parse_args()


def read_zipped_csv(zip_path: str, member: str) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as z:
        raw = z.read(member)
    if member.endswith(".gz"):
        return pd.read_csv(gzip.GzipFile(fileobj=io.BytesIO(raw)))
    return pd.read_csv(io.BytesIO(raw))


def load_trades(old_zip: str, new_zip: str, overlap: set[str], holdout: set[str]):
    old = read_zipped_csv(old_zip, "trades_with_market_context.csv.gz")
    new = read_zipped_csv(new_zip, "bb_auto100_holdout_results/all_l1_long_short_trades.csv.gz")

    new_start = int(new["signal_ts"].min())
    new_end = int(new["signal_ts"].max())

    old = old[
        (old["base_strategy"] == "L1_MOMENTUM_1H10")
        & old["symbol"].isin(overlap)
        & old["signal_ts"].between(new_start, new_end)
    ].copy()
    old["universe_group"] = "OLD34"

    new = new[new["symbol"].isin(holdout)].copy()
    new["universe_group"] = "NEW66"

    # Normalize the older mirror name; BTC percentile is not needed for this analysis.
    old["strategy"] = "L1_MOMENTUM_1H10"
    base_cols = [
        "symbol","signal_ts","delay_min","direction","net_pct","outcome",
        "signal_price","btc_vol_state","universe_group",
    ]
    old = old[base_cols].copy()
    new = new[base_cols].copy()
    return pd.concat([old, new], ignore_index=True), new_start, new_end


def load_market(root: Path, symbols: set[str]) -> pd.DataFrame:
    files = sorted(root.glob("20??/??/*.csv.gz"))
    if not files:
        raise FileNotFoundError(f"no market files under {root}")
    frames = []
    use = ["symbol","timestamp_ms","close","quote_volume"]
    for p in files:
        x = pd.read_csv(p, compression="gzip", usecols=use)
        x = x[x["symbol"].isin(symbols)]
        if not x.empty:
            frames.append(x)
    df = pd.concat(frames, ignore_index=True)
    for c in ["timestamp_ms","close","quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["symbol","timestamp_ms","close"]).copy()
    df = df[df["close"] > 0]
    df["timestamp_ms"] = df["timestamp_ms"].astype("int64")
    df["quote_volume"] = df["quote_volume"].fillna(0.0).astype(float)
    df = df.drop_duplicates(["symbol","timestamp_ms"], keep="last")
    return df.sort_values(["symbol","timestamp_ms"]).reset_index(drop=True)


def series_map(df: pd.DataFrame):
    out = {}
    for symbol, g in df.groupby("symbol", sort=False):
        ts = g["timestamp_ms"].to_numpy(dtype=np.int64) + BAR15_MS
        close = g["close"].to_numpy(dtype=float)
        qv = g["quote_volume"].to_numpy(dtype=float)
        out[symbol] = (ts, close, qv)
    return out


def ret_at(arr, ts, bars):
    times, close, _qv = arr
    i = int(np.searchsorted(times, int(ts), side="right") - 1)
    j = i - bars
    if i < 0 or j < 0 or close[j] <= 0:
        return float("nan")
    return float((close[i] / close[j] - 1.0) * 100.0)


def event_asset_features(arr, ts):
    times, close, qv = arr
    i = int(np.searchsorted(times, int(ts), side="right") - 1)
    if i < 0:
        return {}
    r1 = ret_at(arr, ts, 4)
    r4 = ret_at(arr, ts, 16)
    r24 = ret_at(arr, ts, 96)
    r72 = ret_at(arr, ts, 288)

    vol_surge = float("nan")
    if i >= 99:
        last1h = float(qv[i-3:i+1].sum())
        prior24h = float(qv[i-99:i-3].sum())
        hourly_avg = prior24h / 24.0
        if hourly_avg > 0:
            vol_surge = last1h / hourly_avg

    rv24 = float("nan")
    if i >= 96:
        w = close[i-96:i+1]
        lr = np.diff(np.log(w))
        if len(lr):
            rv24 = float(np.std(lr, ddof=0) * 100.0)

    return {
        "ret_1h_pct": r1,
        "ret_4h_pct": r4,
        "ret_24h_pct": r24,
        "ret_72h_pct": r72,
        "accel_1h_vs_4h": r1 - r4 / 4.0 if math.isfinite(r1) and math.isfinite(r4) else float("nan"),
        "quote_vol_surge_1h_vs_prior24h": vol_surge,
        "rv24h_pct": rv24,
    }


def resampled_map(df: pd.DataFrame, rule: str, duration_ms: int):
    out = {}
    for symbol, g in df.groupby("symbol", sort=False):
        x = g[["timestamp_ms","close"]].copy()
        x["dt"] = pd.to_datetime(x["timestamp_ms"], unit="ms", utc=True)
        z = x.set_index("dt")["close"].resample(rule, label="left", closed="left").last().dropna()
        starts = (z.index.view("int64") // 1_000_000).astype(np.int64)
        out[symbol] = (starts + duration_ms, z.to_numpy(dtype=float))
    return out


def bb_live_feature(arr, signal_ts, live_price):
    times, closes = arr
    i = int(np.searchsorted(times, int(signal_ts), side="right"))
    if i < 19 or not math.isfinite(live_price) or live_price <= 0:
        return (float("nan"), float("nan"))
    prev = closes[i-19:i]
    x = np.concatenate([prev, np.array([float(live_price)])])
    basis = float(np.mean(x))
    std = float(np.std(x, ddof=0))
    upper = basis + 2.0 * std
    lower = basis - 2.0 * std
    if basis <= 0 or upper <= 0:
        return (float("nan"), float("nan"))
    dist = (live_price / upper - 1.0) * 100.0
    width = (upper - lower) / basis * 100.0
    return (float(dist), float(width))


def total3_context(path: str):
    x = pd.read_csv(path, usecols=["timestamp_ms","close"]).sort_values("timestamp_ms")
    x["timestamp_ms"] = pd.to_numeric(x["timestamp_ms"], errors="coerce")
    x["close"] = pd.to_numeric(x["close"], errors="coerce")
    x = x.dropna()
    x["available_ts"] = x["timestamp_ms"].astype("int64") + BAR2H_MS
    x["ret24"] = (x["close"] / x["close"].shift(12) - 1.0) * 100.0
    return x["available_ts"].to_numpy(dtype=np.int64), x["ret24"].to_numpy(dtype=float)


def total3_at(ctx, ts):
    times, vals = ctx
    i = int(np.searchsorted(times, int(ts), side="right") - 1)
    return float(vals[i]) if i >= 0 else float("nan")


def pivot_events(trades: pd.DataFrame):
    # One row per L1 event, with long/short outcomes for each entry delay.
    event_meta = trades.sort_values(["symbol","signal_ts","delay_min"]).drop_duplicates(
        ["symbol","signal_ts"]
    )[["symbol","signal_ts","signal_price","btc_vol_state","universe_group"]]

    p = trades.pivot_table(
        index=["symbol","signal_ts"],
        columns=["direction","delay_min"],
        values="net_pct",
        aggfunc="first",
    )
    p.columns = [f"{d.lower()}_net_d{int(k)}" for d,k in p.columns]
    p = p.reset_index()
    x = event_meta.merge(p, on=["symbol","signal_ts"], how="left")
    for d in (1,2,3):
        x[f"edge_d{d}"] = x[f"short_net_d{d}"] - x[f"long_net_d{d}"]
    x["direction_edge_avg"] = x[[f"edge_d{d}" for d in (1,2,3)]].mean(axis=1)
    x["event_type"] = np.where(x["direction_edge_avg"] > 0, "REVERSAL_SHORT", "CONTINUATION_LONG")
    x["is_core"] = x["symbol"].isin(set(RESEARCH_CORE_SYMBOLS))
    return x


def signal_density(events_all: pd.DataFrame):
    all_ts = np.sort(events_all["signal_ts"].to_numpy(dtype=np.int64))
    by_symbol = {
        s: np.sort(g["signal_ts"].to_numpy(dtype=np.int64))
        for s,g in events_all.groupby("symbol")
    }
    rows = []
    for r in events_all.itertuples(index=False):
        ts = int(r.signal_ts)
        c6 = int(np.searchsorted(all_ts, ts, side="right") - np.searchsorted(all_ts, ts-6*60*60_000, side="left"))
        c24 = int(np.searchsorted(all_ts, ts, side="right") - np.searchsorted(all_ts, ts-DAY_MS, side="left"))
        st = by_symbol[r.symbol]
        pos = int(np.searchsorted(st, ts, side="left"))
        prior30 = int(pos - np.searchsorted(st, ts-30*DAY_MS, side="left"))
        hprev = float("nan")
        if pos > 0:
            hprev = (ts - int(st[pos-1])) / 3_600_000.0
        rows.append((r.symbol,ts,c6,c24,prior30,hprev))
    return pd.DataFrame(rows, columns=[
        "symbol","signal_ts","signal_count_6h","signal_count_24h",
        "prior_symbol_l1_30d","hours_since_prev_symbol_l1",
    ])


def market_breadth_at(market_map, ts):
    r4=[]; r24=[]
    for arr in market_map.values():
        a=ret_at(arr,ts,16)
        b=ret_at(arr,ts,96)
        if math.isfinite(a): r4.append(a)
        if math.isfinite(b): r24.append(b)
    if not r24:
        return {}
    ar4=np.array(r4,dtype=float); ar24=np.array(r24,dtype=float)
    return {
        "breadth_pos4h_pct": float(np.mean(ar4 > 0)*100.0) if len(ar4) else float("nan"),
        "breadth_pos24h_pct": float(np.mean(ar24 > 0)*100.0),
        "breadth_ret24_gt10_pct": float(np.mean(ar24 > 10.0)*100.0),
        "market_median_4h_pct": float(np.median(ar4)) if len(ar4) else float("nan"),
        "market_median_24h_pct": float(np.median(ar24)),
    }


def calc_pf(s):
    x=pd.to_numeric(s,errors="coerce").dropna()
    pos=float(x[x>0].sum()); neg=float(-x[x<0].sum())
    if neg<=0: return float("inf") if pos>0 else float("nan")
    return pos/neg


def pooled_smd(a,b):
    a=np.array([x for x in a if math.isfinite(x)],dtype=float)
    b=np.array([x for x in b if math.isfinite(x)],dtype=float)
    if len(a)<2 or len(b)<2: return float("nan")
    den=math.sqrt(((len(a)-1)*a.var(ddof=1)+(len(b)-1)*b.var(ddof=1))/(len(a)+len(b)-2))
    return (b.mean()-a.mean())/den if den>0 else float("nan")


def cluster_boot_diff(x, feature, reps=2000):
    groups={}
    for grp in ["OLD34","NEW66"]:
        z=x[x["universe_group"]==grp].dropna(subset=[feature])
        groups[grp]={s:g[feature].to_numpy(dtype=float) for s,g in z.groupby("symbol")}
    if not groups["OLD34"] or not groups["NEW66"]:
        return (float("nan"),)*3
    vals=[]
    old_syms=list(groups["OLD34"]); new_syms=list(groups["NEW66"])
    for _ in range(reps):
        os=RNG.choice(old_syms,size=len(old_syms),replace=True)
        ns=RNG.choice(new_syms,size=len(new_syms),replace=True)
        oa=np.concatenate([groups["OLD34"][s] for s in os])
        na=np.concatenate([groups["NEW66"][s] for s in ns])
        vals.append(float(np.mean(na)-np.mean(oa)))
    return tuple(np.percentile(vals,[2.5,50,97.5]).tolist())


def group_differences(x):
    rows=[]
    for f in FEATURES:
        a=x.loc[x.universe_group=="OLD34",f].dropna()
        b=x.loc[x.universe_group=="NEW66",f].dropna()
        lo,med,hi=cluster_boot_diff(x,f)
        rows.append({
            "feature":f,
            "old_n":len(a),"new_n":len(b),
            "old_mean":a.mean(),"new_mean":b.mean(),
            "old_median":a.median(),"new_median":b.median(),
            "new_minus_old_mean":b.mean()-a.mean(),
            "smd_new_minus_old":pooled_smd(a,b),
            "symbol_cluster_boot_lo":lo,
            "symbol_cluster_boot_med":med,
            "symbol_cluster_boot_hi":hi,
        })
    return pd.DataFrame(rows).sort_values("smd_new_minus_old",key=lambda s:s.abs(),ascending=False)


def outcome_differences(x):
    rows=[]
    scopes=[("ALL",x),("OLD34",x[x.universe_group=="OLD34"]),("NEW66",x[x.universe_group=="NEW66"])]
    for scope,z in scopes:
        for f in FEATURES:
            a=z.loc[z.event_type=="CONTINUATION_LONG",f].dropna()
            b=z.loc[z.event_type=="REVERSAL_SHORT",f].dropna()
            rows.append({
                "scope":scope,"feature":f,
                "continuation_n":len(a),"reversal_n":len(b),
                "continuation_mean":a.mean(),"reversal_mean":b.mean(),
                "continuation_median":a.median(),"reversal_median":b.median(),
                "reversal_minus_continuation_smd":pooled_smd(a,b),
            })
    return pd.DataFrame(rows)


def correlation_table(x):
    rows=[]
    for scope,z in [("ALL",x),("OLD34",x[x.universe_group=="OLD34"]),("NEW66",x[x.universe_group=="NEW66"])]:
        for f in FEATURES:
            q=z[[f,"direction_edge_avg"]].dropna()
            rho=q[f].rank(method="average").corr(q["direction_edge_avg"].rank(method="average")) if len(q)>=5 else float("nan")
            rows.append({"scope":scope,"feature":f,"n":len(q),"spearman_vs_short_minus_long":rho})
    return pd.DataFrame(rows)


def tertile_table(x):
    rows=[]
    for f in FEATURES:
        z=x.dropna(subset=[f]).copy()
        if len(z)<12 or z[f].nunique()<3:
            continue
        q1=float(z[f].quantile(1/3)); q2=float(z[f].quantile(2/3))
        if not q2>q1:
            continue
        z["bucket"]=pd.cut(z[f],[-np.inf,q1,q2,np.inf],labels=["LOW","MID","HIGH"],include_lowest=True)
        for scope,g in [("ALL",z),("OLD34",z[z.universe_group=="OLD34"]),("NEW66",z[z.universe_group=="NEW66"])]:
            for bucket,h in g.groupby("bucket",observed=True):
                if h.empty: continue
                rows.append({
                    "feature":f,"q33":q1,"q67":q2,"scope":scope,"bucket":str(bucket),
                    "n":len(h),"symbols":h.symbol.nunique(),
                    "old_n":int((h.universe_group=="OLD34").sum()),
                    "new_n":int((h.universe_group=="NEW66").sum()),
                    "avg_direction_edge":h.direction_edge_avg.mean(),
                    "reversal_pct":float((h.direction_edge_avg>0).mean()*100.0),
                    "short_pf_d1":calc_pf(h["short_net_d1"]),
                    "long_pf_d1":calc_pf(h["long_net_d1"]),
                    "short_avg_d1":h["short_net_d1"].mean(),
                    "long_avg_d1":h["long_net_d1"].mean(),
                })
    return pd.DataFrame(rows)


def symbol_summary(x):
    return x.groupby(["universe_group","symbol"],as_index=False).agg(
        events=("signal_ts","size"),
        reversal_pct=("direction_edge_avg",lambda s:float((s>0).mean()*100.0)),
        avg_direction_edge=("direction_edge_avg","mean"),
        short_avg_d1=("short_net_d1","mean"),
        long_avg_d1=("long_net_d1","mean"),
    ).sort_values("avg_direction_edge",ascending=False)


def main():
    a=parse_args()
    out=Path(a.outdir); out.mkdir(parents=True,exist_ok=True)

    sel=json.loads(Path(a.selection).read_text(encoding="utf-8"))
    auto100=set(sel["symbols"])
    overlap=auto100 & OLD_AUTO50
    holdout=auto100 - OLD_AUTO50
    trades,start,end=load_trades(a.old_artifact,a.new_artifact,overlap,holdout)
    events=pivot_events(trades)

    # Primary question: why the exact frozen MID-vol short behaved differently.
    primary=events[events["btc_vol_state"]=="MID"].copy()

    root=Path(a.root)
    market=load_market(root,auto100)
    mmap=series_map(market)
    h1map=resampled_map(market,"1h",BAR1H_MS)
    h4map=resampled_map(market,"4h",BAR4H_MS)
    t3=total3_context(a.total3)

    # L1 event density must use all L1 events, not just MID.
    all_events=events[["symbol","signal_ts"]].drop_duplicates().sort_values("signal_ts")
    dens=signal_density(all_events)
    primary=primary.merge(dens,on=["symbol","signal_ts"],how="left")

    breadth_cache={}
    feature_rows=[]
    for r in primary.itertuples(index=False):
        row={"symbol":r.symbol,"signal_ts":int(r.signal_ts)}
        arr=mmap.get(r.symbol)
        if arr:
            row.update(event_asset_features(arr,int(r.signal_ts)))
        if r.symbol in h1map:
            d,w=bb_live_feature(h1map[r.symbol],int(r.signal_ts),float(r.signal_price))
            row["bb1h_dist_pct"]=d; row["bb1h_width_pct"]=w
        if r.symbol in h4map:
            d,w=bb_live_feature(h4map[r.symbol],int(r.signal_ts),float(r.signal_price))
            row["bb4h_dist_pct"]=d; row["bb4h_width_pct"]=w
        ts=int(r.signal_ts)
        if ts not in breadth_cache:
            breadth_cache[ts]=market_breadth_at(mmap,ts)
        row.update(breadth_cache[ts])
        btc=mmap.get("BTCUSDT")
        row["btc_ret_4h_pct"]=ret_at(btc,ts,16) if btc else float("nan")
        row["btc_ret_24h_pct"]=ret_at(btc,ts,96) if btc else float("nan")
        row["total3_ret_24h_pct"]=total3_at(t3,ts)
        feature_rows.append(row)

    fdf=pd.DataFrame(feature_rows)
    primary=primary.merge(fdf,on=["symbol","signal_ts"],how="left")

    gd=group_differences(primary)
    od=outcome_differences(primary)
    cor=correlation_table(primary)
    ter=tertile_table(primary)
    sy=symbol_summary(primary)

    primary.to_csv(out/"mid_event_features.csv.gz",index=False,compression="gzip")
    gd.to_csv(out/"old34_vs_new66_feature_differences.csv",index=False)
    od.to_csv(out/"reversal_vs_continuation_features.csv",index=False)
    cor.to_csv(out/"feature_edge_correlations.csv",index=False)
    ter.to_csv(out/"feature_tertiles.csv",index=False)
    sy.to_csv(out/"symbol_event_summary.csv",index=False)

    meta={
        "auto100":len(auto100),"old_overlap":len(overlap),"new_holdout":len(holdout),
        "mid_events":len(primary),
        "old_mid_events":int((primary.universe_group=="OLD34").sum()),
        "new_mid_events":int((primary.universe_group=="NEW66").sum()),
        "mid_symbols":int(primary.symbol.nunique()),
        "analysis_note":"Exploratory anatomy only. Features are point-in-time/pre-signal; pooled tertiles are descriptive and not trading thresholds.",
        "relative_strength_note":"Direct asset-vs-BTC relative-strength analysis is intentionally not duplicated here because research-long3-relative-strength already covers it separately.",
    }
    (out/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")

    print("=== META ===")
    print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== BIGGEST OLD34 vs NEW66 PRE-SIGNAL DIFFERENCES ===")
    print(gd.head(12).to_string(index=False))
    print("\n=== STRONGEST FEATURES VS SHORT-MINUS-LONG EDGE ===")
    print(cor[cor.scope=="ALL"].sort_values("spearman_vs_short_minus_long",key=lambda s:s.abs(),ascending=False).head(12).to_string(index=False))
    print("\n=== REVERSAL VS CONTINUATION BIGGEST DIFFERENCES ===")
    q=od[od.scope=="ALL"].sort_values("reversal_minus_continuation_smd",key=lambda s:s.abs(),ascending=False).head(12)
    print(q.to_string(index=False))
    print("\n=== TERTILE SNAPSHOT FOR TOP CORRELATES ===")
    top=cor[cor.scope=="ALL"].sort_values("spearman_vs_short_minus_long",key=lambda s:s.abs(),ascending=False).head(5)["feature"]
    print(ter[(ter.scope=="ALL") & ter.feature.isin(top)].to_string(index=False))
    print(f"\n[DONE] mid_events={len(primary)}")


if __name__=="__main__":
    main()
