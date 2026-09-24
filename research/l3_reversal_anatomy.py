from __future__ import annotations
import argparse, json, math
from pathlib import Path
import numpy as np, pandas as pd
from precision_backtest import build_signals

FEATURES = [
    "ret_15m","ret_1h","ret_4h","ret_12h","ret_24h","rv_4h","rv_24h",
    "1W_dist","1D_dist","12H_dist","4H_dist","1H_dist","30M_dist","15M_dist",
    "within_3pct_count",
    "weekly_mid_pct_24h_med","daily_mid_pct_24h_med",
    "weekly_upper_pct_24h_med","daily_upper_pct_24h_med",
    "bear_episode_age_h",
]

def args():
    p=argparse.ArgumentParser()
    p.add_argument("--cross-dir",required=True)
    p.add_argument("--target-source",required=True)
    p.add_argument("--regime-dir",required=True)
    p.add_argument("--outdir",default="l3_reversal_anatomy_results")
    return p.parse_args()

def pf(x):
    x=pd.to_numeric(x,errors="coerce").dropna()
    pos=float(x[x>0].sum()); neg=float(-x[x<0].sum())
    if neg<=0:return float("inf") if pos>0 else float("nan")
    return pos/neg

def cluster_ids(ts,hours):
    ts=np.asarray(ts,dtype=np.int64); order=np.argsort(ts); ids=np.empty(len(ts),dtype=int)
    cid=-1; prev=None
    for i in order:
        t=int(ts[i])
        if prev is None or t-prev>hours*3600_000: cid+=1
        ids[i]=cid; prev=t
    return ids

def unique_signals(trades, universe):
    t=trades[(trades["regime_60_40"]=="BEAR")].copy()
    # paired outcomes on exact symbol/timestamp; one row per signal
    idx=["symbol","signal_ts"]
    p=t.pivot_table(index=idx,columns=["direction","delay_min"],values="net_pct",aggfunc="first")
    p.columns=[f"{d.lower()}_net_d{int(k)}" for d,k in p.columns]
    p=p.reset_index()
    p["universe"]=universe
    return p

def add_price_features(source):
    x=source.sort_values(["symbol","ts"]).copy()
    g=x.groupby("symbol",sort=False)
    for bars,name in [(1,"ret_15m"),(4,"ret_1h"),(16,"ret_4h"),(48,"ret_12h"),(96,"ret_24h")]:
        x[name]=g["price"].pct_change(bars)*100.0
    lr=g["price"].transform(lambda s: np.log(s).diff())
    x["_lr"]=lr
    x["rv_4h"]=x.groupby("symbol")["_lr"].transform(lambda s:s.rolling(16,min_periods=16).std())*100.0
    x["rv_24h"]=x.groupby("symbol")["_lr"].transform(lambda s:s.rolling(96,min_periods=96).std())*100.0
    return x

def add_bear_age(b):
    b=b.sort_values("ts").copy()
    isbear=b["regime_60_40"].eq("BEAR")
    start=(isbear & ~isbear.shift(1,fill_value=False))
    episode=start.cumsum()
    b["_bear_episode"]=np.where(isbear,episode,np.nan)
    age=np.full(len(b),np.nan)
    for eid,g in b[isbear].groupby("_bear_episode"):
        t0=int(g["ts"].iloc[0])
        age[g.index.to_numpy()]=(g["ts"].to_numpy(dtype=np.int64)-t0)/3600_000
    b["bear_episode_age_h"]=age
    return b

def describe(signals):
    rows=[]
    for feat in FEATURES:
        if feat not in signals: continue
        for u,g in signals.groupby("universe"):
            z=pd.to_numeric(g[feat],errors="coerce").dropna()
            rows.append({"feature":feat,"universe":u,"n":len(z),
                         "mean":float(z.mean()) if len(z) else np.nan,
                         "median":float(z.median()) if len(z) else np.nan,
                         "q25":float(z.quantile(.25)) if len(z) else np.nan,
                         "q75":float(z.quantile(.75)) if len(z) else np.nan,
                         "std":float(z.std(ddof=1)) if len(z)>1 else np.nan})
    return pd.DataFrame(rows)

def effect_table(desc):
    rows=[]
    for feat,g in desc.groupby("feature"):
        if set(g.universe)!={"AUTO50_ORIGINAL","NEW66_HOLDOUT"}: continue
        a=g[g.universe=="AUTO50_ORIGINAL"].iloc[0]; n=g[g.universe=="NEW66_HOLDOUT"].iloc[0]
        pooled=np.nan
        if a["n"]>1 and n["n"]>1 and np.isfinite(a["std"]) and np.isfinite(n["std"]):
            denom=max(1,a["n"]+n["n"]-2)
            pooled=math.sqrt(((a["n"]-1)*a["std"]**2+(n["n"]-1)*n["std"]**2)/denom)
        rows.append({"feature":feat,"auto50_n":a["n"],"new66_n":n["n"],
                     "auto50_mean":a["mean"],"new66_mean":n["mean"],
                     "mean_diff_auto_minus_new":a["mean"]-n["mean"],
                     "auto50_median":a["median"],"new66_median":n["median"],
                     "median_diff_auto_minus_new":a["median"]-n["median"],
                     "standardized_mean_diff":(a["mean"]-n["mean"])/pooled if pooled and pooled>0 else np.nan})
    return pd.DataFrame(rows)

def event_overlap(signals,hours):
    z=signals.sort_values("signal_ts").copy()
    z["event_id"]=cluster_ids(z.signal_ts.to_numpy(),hours)
    rows=[]
    for eid,g in z.groupby("event_id"):
        us=set(g.universe)
        row={"cluster_hours":hours,"event_id":int(eid),"start_ts":int(g.signal_ts.min()),"end_ts":int(g.signal_ts.max()),
             "universes":"+".join(sorted(us)),"shared_universe_event":len(us)>1,
             "auto50_signals":int((g.universe=="AUTO50_ORIGINAL").sum()),
             "new66_signals":int((g.universe=="NEW66_HOLDOUT").sum())}
        for d in (1,2,3):
            for u,key in [("AUTO50_ORIGINAL","auto"),("NEW66_HOLDOUT","new")]:
                q=g[g.universe==u]
                row[f"{key}_short_avg_d{d}"]=q[f"short_net_d{d}"].mean() if len(q) else np.nan
                row[f"{key}_long_avg_d{d}"]=q[f"long_net_d{d}"].mean() if len(q) else np.nan
                row[f"{key}_edge_short_minus_long_d{d}"]=(q[f"short_net_d{d}"]-q[f"long_net_d{d}"]).mean() if len(q) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)

def symbol_repeat(signals):
    rows=[]
    for (u,s),g in signals.groupby(["universe","symbol"]):
        rows.append({"universe":u,"symbol":s,"signals":len(g),
                     "short_sum_d1":g.short_net_d1.sum(),"short_pf_d1":pf(g.short_net_d1),
                     "long_sum_d1":g.long_net_d1.sum(),"long_pf_d1":pf(g.long_net_d1),
                     "edge_sum_d1":(g.short_net_d1-g.long_net_d1).sum()})
    return pd.DataFrame(rows).sort_values(["universe","signals","edge_sum_d1"],ascending=[True,False,False])

def main():
    a=args(); out=Path(a.outdir); out.mkdir(parents=True,exist_ok=True)
    cd=Path(a.cross_dir)
    old=pd.read_csv(cd/"auto50_l3_same_window_trades.csv.gz")
    new=pd.read_csv(cd/"new66_l3_long_short_trades.csv.gz")
    sig=pd.concat([unique_signals(old,"AUTO50_ORIGINAL"),unique_signals(new,"NEW66_HOLDOUT")],ignore_index=True)

    src=add_price_features(pd.read_csv(a.target_source))
    # exact L3 source context from the targeted rebuild
    bs=build_signals(src)
    l3=bs[bs.strategy=="L3_4H_LAG"][["symbol","ts","ret_1h","ret_4h","rank","exact_count","streak"]].copy()
    context_cols=["symbol","ts","price","within_3pct_count","1W_dist","1D_dist","12H_dist","4H_dist","1H_dist","30M_dist","15M_dist",
                  "ret_15m","ret_1h","ret_4h","ret_12h","ret_24h","rv_4h","rv_24h"]
    base=src[context_cols].drop_duplicates(["symbol","ts"])
    # prefer build_signals ret values for exact parity where available
    base=base.merge(l3.rename(columns={"ret_1h":"ret_1h_exact","ret_4h":"ret_4h_exact"}),
                    on=["symbol","ts"],how="left")
    base["ret_1h"]=base["ret_1h_exact"].combine_first(base["ret_1h"])
    base["ret_4h"]=base["ret_4h_exact"].combine_first(base["ret_4h"])
    base=base.drop(columns=["ret_1h_exact","ret_4h_exact"],errors="ignore")
    sig=sig.merge(base,left_on=["symbol","signal_ts"],right_on=["symbol","ts"],how="left").drop(columns="ts")

    breadth=add_bear_age(pd.read_csv(Path(a.regime_dir)/"regime_breadth_results"/"breadth_timeseries.csv.gz"))
    bcols=["ts","weekly_mid_pct_24h_med","daily_mid_pct_24h_med","weekly_upper_pct_24h_med","daily_upper_pct_24h_med","bear_episode_age_h"]
    sig=sig.merge(breadth[bcols],left_on="signal_ts",right_on="ts",how="left").drop(columns="ts")

    # exact timestamp overlap before clustering
    exact=sig.groupby("signal_ts").agg(
        auto50=("universe",lambda s:int((s=="AUTO50_ORIGINAL").sum())),
        new66=("universe",lambda s:int((s=="NEW66_HOLDOUT").sum()))
    ).reset_index()
    exact["shared"]= (exact.auto50>0)&(exact.new66>0)

    desc=describe(sig); eff=effect_table(desc)
    ev24=event_overlap(sig,24); ev48=event_overlap(sig,48)
    rep=symbol_repeat(sig)

    sig.to_csv(out/"l3_bear_signal_anatomy.csv",index=False)
    desc.to_csv(out/"feature_descriptives.csv",index=False)
    eff.to_csv(out/"feature_effects_hypothesis_only.csv",index=False)
    ev24.to_csv(out/"event_overlap_24h.csv",index=False)
    ev48.to_csv(out/"event_overlap_48h.csv",index=False)
    rep.to_csv(out/"symbol_repeat.csv",index=False)
    exact.to_csv(out/"exact_timestamp_overlap.csv",index=False)

    print("=== SAMPLE ===")
    print(sig.groupby("universe").agg(signals=("signal_ts","size"),symbols=("symbol","nunique")).to_string())
    print(f"exact_shared_timestamps={int(exact.shared.sum())}")
    print(f"24h_events={len(ev24)} shared_24h_events={int(ev24.shared_universe_event.sum())}")
    print(f"48h_events={len(ev48)} shared_48h_events={int(ev48.shared_universe_event.sum())}")

    print("\n=== 24H EVENT OVERLAP ==="); print(ev24.to_string(index=False))
    print("\n=== 48H EVENT OVERLAP ==="); print(ev48.to_string(index=False))
    print("\n=== FEATURE DESCRIPTIVES ==="); print(desc.to_string(index=False))
    print("\n=== FEATURE EFFECTS (HYPOTHESIS GENERATION ONLY) ===")
    print(eff.sort_values("standardized_mean_diff",key=lambda s:s.abs(),ascending=False).to_string(index=False))
    print("\n=== SYMBOL REPEAT ==="); print(rep.to_string(index=False))
    print("\nIMPORTANT: feature differences are post-hoc explanatory diagnostics only. No threshold or trading rule is selected from them.")
    print("[DONE]")

if __name__=="__main__": main()
