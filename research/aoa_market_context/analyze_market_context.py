#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "research" / "aoa_market_context"
POLICY = BASE / "aoa_policy_2019h2_2021_compact.csv"
EPISODES = BASE / "aoa_episodes_2019h2_2021_compact.csv"
CANDLE_DIR = ROOT / "market_data_store" / "bitget" / "15m" / "BTCUSDT"
OUT = BASE / "output"
OUT.mkdir(parents=True, exist_ok=True)

DIR_SIGN = {"L": 1.0, "S": -1.0}
MARKET_FEATURES = [
    "ret15m","ret1h","ret4h","ret24h","ret3d","ret7d",
    "rv4h","rv24h","atr14_pct","atr96_pct","bb_z20","bb_width20",
    "rsi14","vol_z96","range_pos24h","dd7d","er24h","ema20_80",
    "trend_z24h","high_vol"
]
INTERNAL_FEATURES = ["fav","log_tb","advn","fav_n","decn"]


def load_candles() -> pd.DataFrame:
    files = sorted(CANDLE_DIR.glob("*.csv"))
    frames = []
    for p in files:
        ym = p.stem
        if ym < "2019-06" or ym > "2022-01":
            continue
        x = pd.read_csv(p)
        frames.append(x)
    if not frames:
        raise RuntimeError("No BTCUSDT candle files found")
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates("timestamp_ms").sort_values("timestamp_ms").reset_index(drop=True)
    for c in ["open","high","low","close","base_volume","quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["bar_start"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    df["bar_end_s"] = (df["timestamp_ms"] // 1000 + 900).astype("int64")

    close = df["close"]
    logret = np.log(close).diff()
    for n, name in [(1,"ret15m"),(4,"ret1h"),(16,"ret4h"),(96,"ret24h"),(288,"ret3d"),(672,"ret7d")]:
        df[name] = (close / close.shift(n) - 1.0) * 10000.0

    df["rv4h"] = logret.rolling(16, min_periods=12).std() * np.sqrt(16) * 10000.0
    df["rv24h"] = logret.rolling(96, min_periods=72).std() * np.sqrt(96) * 10000.0

    prev_close = close.shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr14_pct"] = tr.rolling(14, min_periods=10).mean() / close * 10000.0
    df["atr96_pct"] = tr.rolling(96, min_periods=72).mean() / close * 10000.0

    ma20 = close.rolling(20, min_periods=15).mean()
    sd20 = close.rolling(20, min_periods=15).std()
    df["bb_z20"] = (close - ma20) / sd20.replace(0, np.nan)
    df["bb_width20"] = 4.0 * sd20 / ma20 * 10000.0

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100.0 - 100.0 / (1.0 + rs)

    lv = np.log1p(df["quote_volume"])
    vmu = lv.rolling(96, min_periods=72).mean()
    vsd = lv.rolling(96, min_periods=72).std()
    df["vol_z96"] = (lv - vmu) / vsd.replace(0, np.nan)

    hi96 = df["high"].rolling(96, min_periods=72).max()
    lo96 = df["low"].rolling(96, min_periods=72).min()
    df["range_pos24h"] = (close - lo96) / (hi96 - lo96).replace(0, np.nan)

    hi7 = df["high"].rolling(672, min_periods=384).max()
    df["dd7d"] = (close / hi7 - 1.0) * 10000.0

    net = (close - close.shift(96)).abs()
    path = close.diff().abs().rolling(96, min_periods=72).sum()
    df["er24h"] = net / path.replace(0, np.nan)

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema80 = close.ewm(span=80, adjust=False).mean()
    df["ema20_80"] = (ema20 / ema80 - 1.0) * 10000.0

    denom = df["rv24h"].replace(0, np.nan)
    df["trend_z24h"] = df["ret24h"] / denom
    vol_med = df["rv24h"].rolling(2880, min_periods=672).median()
    df["high_vol"] = (df["rv24h"] > vol_med).astype(float)

    # Forward outcomes are only for event-study reporting; never used as predictors.
    for n, name in [(1,"fwd15m"),(4,"fwd1h"),(16,"fwd4h"),(96,"fwd24h")]:
        df[name] = (close.shift(-n) / close - 1.0) * 10000.0
    return df


def attach(events: pd.DataFrame, candles: pd.DataFrame, ts_col: str) -> pd.DataFrame:
    ev = events.copy()
    ev["_t"] = pd.to_numeric(ev[ts_col], errors="coerce").astype("Int64")
    bar_ends = candles["bar_end_s"].to_numpy()
    idx = np.searchsorted(bar_ends, ev["_t"].to_numpy(dtype="int64"), side="right") - 1
    ok = (idx >= 0) & (idx < len(candles))
    ev = ev.loc[ok].copy()
    idx = idx[ok]
    feats = MARKET_FEATURES + ["fwd15m","fwd1h","fwd4h","fwd24h","close","bar_start"]
    for c in feats:
        ev[c] = candles.iloc[idx][c].to_numpy()
    return ev


def safe_median(x):
    x = pd.to_numeric(pd.Series(x), errors="coerce").dropna()
    return None if x.empty else float(x.median())


def safe_mean(x):
    x = pd.to_numeric(pd.Series(x), errors="coerce").dropna()
    return None if x.empty else float(x.mean())


def auc_model(train, test, features, target):
    tr = train.dropna(subset=[target]).copy()
    te = test.dropna(subset=[target]).copy()
    if len(tr) < 30 or len(te) < 20 or tr[target].nunique() < 2 or te[target].nunique() < 2:
        return {"auc": None, "accuracy": None, "n_train": len(tr), "n_test": len(te), "coef": {}}
    pipe = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("lr", LogisticRegression(max_iter=3000, class_weight="balanced", C=0.5)),
    ])
    pipe.fit(tr[features], tr[target].astype(int))
    p = pipe.predict_proba(te[features])[:,1]
    pred = (p >= 0.5).astype(int)
    lr = pipe.named_steps["lr"]
    coef = {f: float(v) for f, v in zip(features, lr.coef_[0])}
    coef = dict(sorted(coef.items(), key=lambda kv: abs(kv[1]), reverse=True))
    return {
        "auc": float(roc_auc_score(te[target].astype(int), p)),
        "accuracy": float(accuracy_score(te[target].astype(int), pred)),
        "n_train": int(len(tr)), "n_test": int(len(te)), "coef": coef
    }


def regime(row):
    z = row["trend_z24h"]
    if pd.isna(z):
        return "UNKNOWN"
    if z > 0.5:
        base = "UP"
    elif z < -0.5:
        base = "DOWN"
    else:
        base = "RANGE"
    return base + ("_HV" if row["high_vol"] >= 0.5 else "_LV")


def main():
    candles = load_candles()
    pol = pd.read_csv(POLICY)
    eps = pd.read_csv(EPISODES)

    polj = attach(pol, candles, "t")
    eps_start = attach(eps, candles, "st")
    eps_end = attach(eps, candles, "et")

    polj["dir_sign"] = polj["d"].map(DIR_SIGN)
    eps_start["dir_sign"] = eps_start["d"].map(DIR_SIGN)
    eps_end["dir_sign"] = eps_end["d"].map(DIR_SIGN)

    for h in ["ret15m","ret1h","ret4h","ret24h","ret3d","ret7d"]:
        polj["signed_"+h] = polj["dir_sign"] * polj[h]
        eps_start["signed_"+h] = eps_start["dir_sign"] * eps_start[h]
        eps_end["signed_"+h] = eps_end["dir_sign"] * eps_end[h]

    for h in ["fwd15m","fwd1h","fwd4h","fwd24h"]:
        polj["signed_"+h] = polj["dir_sign"] * polj[h]
        eps_start["signed_"+h] = eps_start["dir_sign"] * eps_start[h]

    polj["year"] = pd.to_datetime(polj["t"], unit="s", utc=True).dt.year
    polj["log_tb"] = np.log1p(pd.to_numeric(polj["tb"], errors="coerce").clip(lower=0))
    eps_start["year"] = pd.to_datetime(eps_start["st"], unit="s", utc=True).dt.year
    eps_start["win"] = (eps_start["ret_bps"] > 0).astype(int)
    eps_start["regime"] = eps_start.apply(regime, axis=1)

    # 1) New-direction selection: ENTRY + FLIP_ENTRY
    entries = polj[polj["a"].isin(["E","FE"])].copy()
    entries["is_long"] = (entries["d"] == "L").astype(int)
    entries["contrarian_1h"] = (entries["dir_sign"] * entries["ret1h"] < 0).astype(float)
    entries["contrarian_4h"] = (entries["dir_sign"] * entries["ret4h"] < 0).astype(float)
    entries["contrarian_24h"] = (entries["dir_sign"] * entries["ret24h"] < 0).astype(float)

    entry_summary = {
        "n": int(len(entries)),
        "long_pct": float((entries["d"]=="L").mean()*100),
        "signed_past_median_bps": {h: safe_median(entries["signed_"+h]) for h in ["ret15m","ret1h","ret4h","ret24h","ret3d","ret7d"]},
        "contrarian_pct": {h: float(entries["contrarian_"+h].mean()*100) for h in ["1h","4h","24h"]},
        "signed_forward_median_bps": {h: safe_median(entries["signed_"+h]) for h in ["fwd15m","fwd1h","fwd4h","fwd24h"]},
    }

    train_e = entries[entries["year"] <= 2020]
    test_e = entries[entries["year"] == 2021]
    entry_direction_model = auc_model(train_e, test_e, MARKET_FEATURES, "is_long")

    # 2) Position management: ADD vs REDUCE/EXIT/FLIP_EXIT
    manage = polj[polj["a"].isin(["A","R","X","FX"])].copy()
    manage["is_add"] = (manage["a"]=="A").astype(int)
    manage["is_final"] = manage["a"].isin(["X","FX"]).astype(int)
    signed_market = ["signed_ret15m","signed_ret1h","signed_ret4h","signed_ret24h"]
    market_manage_features = signed_market + [
        "rv4h","rv24h","atr14_pct","atr96_pct","bb_z20","bb_width20","rsi14",
        "vol_z96","range_pos24h","dd7d","er24h","ema20_80","high_vol"
    ]
    train_m = manage[manage["year"] <= 2020]
    test_m = manage[manage["year"] == 2021]

    add_internal = auc_model(train_m, test_m, INTERNAL_FEATURES, "is_add")
    add_market = auc_model(train_m, test_m, market_manage_features, "is_add")
    add_combined = auc_model(train_m, test_m, INTERNAL_FEATURES + market_manage_features, "is_add")

    # Among decreases, partial reduction vs final exit/flip
    dec = manage[manage["a"].isin(["R","X","FX"])].copy()
    train_d = dec[dec["year"] <= 2020]
    test_d = dec[dec["year"] == 2021]
    final_internal = auc_model(train_d, test_d, INTERNAL_FEATURES, "is_final")
    final_market = auc_model(train_d, test_d, market_manage_features, "is_final")
    final_combined = auc_model(train_d, test_d, INTERNAL_FEATURES + market_manage_features, "is_final")

    # 3) Watering vs pyramiding context
    adds = manage[manage["a"]=="A"].copy()
    adds["add_type"] = np.where(adds["fav"] < -1e-9, "ADVERSE", np.where(adds["fav"] > 1e-9, "FAVORABLE", "NEUTRAL"))
    add_context = {}
    for typ, g in adds.groupby("add_type"):
        add_context[typ] = {
            "n": int(len(g)),
            "internal_fav_median_bps": safe_median(g["fav"]),
            "signed_market_median_bps": {h: safe_median(g["signed_"+h]) for h in ["ret15m","ret1h","ret4h","ret24h"]},
            "volume_z_median": safe_median(g["vol_z96"]),
            "rsi_median": safe_median(g["rsi14"]),
            "range_pos24h_median": safe_median(g["range_pos24h"]),
            "episode_win_pct": float((g["ret"]>0).mean()*100),
            "episode_ret_median_bps": safe_median(g["ret"])
        }

    # Adverse-add outcome by size of market move against current position
    adverse = adds[adds["add_type"]=="ADVERSE"].copy()
    adverse["against_1h_bps"] = -adverse["signed_ret1h"]
    adverse["shock_bin"] = pd.cut(adverse["against_1h_bps"], [-np.inf,0,25,50,100,200,np.inf],
                                  labels=["not_against","0-25","25-50","50-100","100-200","200+"])
    shock_rows = []
    for b,g in adverse.groupby("shock_bin", observed=True):
        shock_rows.append({
            "bin": str(b), "n": int(len(g)),
            "win_pct": float((g["ret"]>0).mean()*100),
            "median_episode_ret_bps": safe_median(g["ret"]),
            "median_add_fav_bps": safe_median(g["fav"])
        })

    # 4) Episode performance by causal regime at entry and alignment
    eps_start["regime_base"] = eps_start["regime"].str.split("_").str[0]
    eps_start["aligned"] = (
        ((eps_start["regime_base"]=="UP") & (eps_start["d"]=="L")) |
        ((eps_start["regime_base"]=="DOWN") & (eps_start["d"]=="S"))
    )
    regime_rows = []
    for (rg, d), g in eps_start.groupby(["regime","d"]):
        regime_rows.append({
            "regime": rg, "direction": d, "n": int(len(g)),
            "win_pct": float(g["win"].mean()*100),
            "median_ret_bps": safe_median(g["ret_bps"]),
            "sum_net_xbt": float(g["net_xbt"].sum()),
            "median_duration_min": float(g["duration_sec"].median()/60)
        })
    alignment_rows = []
    nonrange = eps_start[eps_start["regime_base"].isin(["UP","DOWN"])].copy()
    for k,g in nonrange.groupby("aligned"):
        alignment_rows.append({
            "aligned": bool(k), "n": int(len(g)),
            "win_pct": float(g["win"].mean()*100),
            "median_ret_bps": safe_median(g["ret_bps"]),
            "sum_net_xbt": float(g["net_xbt"].sum())
        })

    # 5) Winning vs losing entry context
    outcome_entry = {}
    for label, g in eps_start.groupby("win"):
        outcome_entry["WIN" if label==1 else "LOSS"] = {
            "n": int(len(g)),
            "features_median": {f: safe_median(g[f]) for f in MARKET_FEATURES if f!="high_vol"},
            "high_vol_pct": float(g["high_vol"].mean()*100)
        }
    outcome_model = auc_model(
        eps_start[eps_start["year"]<=2020],
        eps_start[eps_start["year"]==2021],
        MARKET_FEATURES,
        "win"
    )

    # 6) Flip-exit context specifically
    fx = polj[polj["a"]=="FX"].copy()
    flip_summary = {
        "n": int(len(fx)),
        "internal_fav_median_bps": safe_median(fx["fav"]),
        "time_in_pos_median_min": safe_median(fx["tb"]) / 60 if len(fx) else None,
        "signed_past_median_bps": {h: safe_median(fx["signed_"+h]) for h in ["ret15m","ret1h","ret4h","ret24h","ret3d"]},
        "rsi_median": safe_median(fx["rsi14"]),
        "range_pos24h_median": safe_median(fx["range_pos24h"]),
        "bb_z_median": safe_median(fx["bb_z20"])
    }

    # Quantile table: probability of ADD by signed 1h market move.
    qsrc = manage.dropna(subset=["signed_ret1h"]).copy()
    qsrc["signed1h_q"] = pd.qcut(qsrc["signed_ret1h"], 5, duplicates="drop")
    add_by_market = []
    for q,g in qsrc.groupby("signed1h_q", observed=True):
        add_by_market.append({
            "signed1h_bin": str(q), "n": int(len(g)),
            "add_pct": float(g["is_add"].mean()*100),
            "final_pct": float(g["is_final"].mean()*100),
            "median_internal_fav_bps": safe_median(g["fav"])
        })

    results = {
        "coverage": {
            "candle_start": str(candles["bar_start"].min()),
            "candle_end": str(candles["bar_start"].max()),
            "policy_rows": int(len(pol)),
            "policy_joined": int(len(polj)),
            "episodes": int(len(eps)),
            "episode_starts_joined": int(len(eps_start))
        },
        "entry_direction": entry_summary,
        "entry_direction_oos_model_2021": entry_direction_model,
        "add_vs_reduce_oos_2021": {
            "internal_only": add_internal,
            "market_only": add_market,
            "combined": add_combined
        },
        "partial_vs_final_oos_2021": {
            "internal_only": final_internal,
            "market_only": final_market,
            "combined": final_combined
        },
        "add_context": add_context,
        "adverse_add_shock_bins": shock_rows,
        "regime_by_direction": regime_rows,
        "regime_alignment": alignment_rows,
        "winner_loser_entry_context": outcome_entry,
        "entry_context_win_oos_model_2021": outcome_model,
        "flip_exit_context": flip_summary,
        "add_probability_by_signed_1h_quintile": add_by_market
    }

    with open(OUT/"results.json","w",encoding="utf-8") as f:
        json.dump(results,f,ensure_ascii=False,indent=2)

    # Save joined tables for later strategy extraction.
    polj.to_csv(OUT/"policy_with_market.csv.gz",index=False,compression="gzip")
    eps_start.to_csv(OUT/"episodes_with_entry_market.csv",index=False)

    lines = []
    lines.append("# AOA × User BTCUSDT 15m Market Context\n")
    lines.append(f"- Joined policy decisions: {len(polj):,}/{len(pol):,}")
    lines.append(f"- Joined episodes: {len(eps_start):,}/{len(eps):,}")
    lines.append("- Feature timing: last fully completed 15m candle before each AOA decision (causal, no lookahead).\n")
    lines.append("## New direction selection")
    lines.append(f"- Decisions: {entry_summary['n']}")
    lines.append(f"- Contrarian share vs prior 1h/4h/24h: {entry_summary['contrarian_pct']}")
    lines.append(f"- OOS 2021 market-only long/short AUC: {entry_direction_model['auc']}\n")
    lines.append("## Management model OOS 2021")
    lines.append(f"- ADD vs decrease — internal AUC {add_internal['auc']}, market AUC {add_market['auc']}, combined AUC {add_combined['auc']}")
    lines.append(f"- Partial vs final — internal AUC {final_internal['auc']}, market AUC {final_market['auc']}, combined AUC {final_combined['auc']}\n")
    lines.append("## Add context")
    for k,v in add_context.items():
        lines.append(f"- {k}: {v}")
    lines.append("\n## Flip context")
    lines.append(str(flip_summary))
    lines.append("\n## Regime alignment")
    for r in alignment_rows:
        lines.append(f"- {r}")
    (OUT/"REPORT.md").write_text("\n".join(lines),encoding="utf-8")

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
