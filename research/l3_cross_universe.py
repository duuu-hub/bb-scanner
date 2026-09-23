from __future__ import annotations
import argparse, json, math
from pathlib import Path
import numpy as np, pandas as pd
from precision_backtest import build_signals, fetch_all_minutes, one_trade
from research.bb_auto100_holdout import ORIGINAL_AUTO50

REGIME_RULES=("55_45","60_40","65_35")

def args():
    p=argparse.ArgumentParser()
    p.add_argument("--source",required=True)
    p.add_argument("--regime-dir",required=True)
    p.add_argument("--selection",default="market_data_store/bitget/research_auto100_15m/selection.json")
    p.add_argument("--outdir",default="l3_cross_universe_results")
    p.add_argument("--workers",type=int,default=8)
    return p.parse_args()

def pf(s,slip=0.0):
    x=pd.to_numeric(s,errors="coerce").dropna()-slip
    pos=float(x[x>0].sum()); neg=float(-x[x<0].sum())
    if neg<=0:return float("inf") if pos>0 else float("nan")
    return pos/neg

def metrics(g,slip=0.0):
    x=pd.to_numeric(g["net_pct"],errors="coerce").dropna()-slip
    return {"n":int(len(x)),"symbols":int(g.loc[x.index,"symbol"].nunique()) if len(x) else 0,
            "avg_net_pct":float(x.mean()) if len(x) else np.nan,
            "sum_net_pct":float(x.sum()) if len(x) else np.nan,
            "pf":pf(g["net_pct"],slip),
            "win_pct":float((x>0).mean()*100) if len(x) else np.nan}

def simulate_both(signals, minute_map):
    rows=[]
    for direction in ("LONG","SHORT"):
        z=signals.copy(); z["direction"]=direction
        for s in z.itertuples(index=False):
            md=minute_map.get(s.symbol)
            for d in (1,2,3):
                r=one_trade(s,md,d)
                if r is not None: rows.append(r)
    return pd.DataFrame(rows)

def add_regime(trades,breadth):
    cols=["ts"]+[f"regime_{r}" for r in REGIME_RULES]
    b=breadth[cols].drop_duplicates("ts")
    return trades.merge(b,left_on="signal_ts",right_on="ts",how="left").drop(columns="ts")

def summary(universe,trades):
    rows=[]
    for rule in REGIME_RULES:
        col=f"regime_{rule}"
        for regime in ("BEAR","RANGE","BULL"):
            for direction in ("LONG","SHORT"):
                for d in (1,2,3):
                    g=trades[(trades[col]==regime)&(trades.direction==direction)&(trades.delay_min==d)]
                    m=metrics(g)
                    rows.append({"universe":universe,"regime_rule":rule,"regime":regime,
                                 "direction":direction,"delay_min":d,**m,
                                 "pf_cost025":pf(g.net_pct,.25) if len(g) else np.nan,
                                 "pf_cost050":pf(g.net_pct,.50) if len(g) else np.nan})
    return pd.DataFrame(rows)

def event_ids(ts,hours=24):
    ts=np.asarray(ts,dtype=np.int64); order=np.argsort(ts); ids=np.empty(len(ts),dtype=int)
    cid=-1; prev=None
    for i in order:
        t=int(ts[i])
        if prev is None or t-prev>hours*3600_000: cid+=1
        ids[i]=cid; prev=t
    return ids

def event_summary(universe,trades):
    rows=[]
    for rule in REGIME_RULES:
        col=f"regime_{rule}"
        for d in (1,2,3):
            g=trades[(trades[col]=="BEAR")&(trades.direction=="SHORT")&(trades.delay_min==d)].copy()
            if g.empty:
                rows.append({"universe":universe,"regime_rule":rule,"delay_min":d,"trades":0,"symbols":0,"events24h":0})
                continue
            g=g.sort_values("signal_ts").reset_index(drop=True)
            g["event_id"]=event_ids(g.signal_ts,24)
            e=g.groupby("event_id")["net_pct"].sum()
            rows.append({"universe":universe,"regime_rule":rule,"delay_min":d,
                         "trades":len(g),"symbols":g.symbol.nunique(),"events24h":len(e),
                         "profitable_events":int((e>0).sum()),
                         "profitable_event_pct":float((e>0).mean()*100),
                         "event_avg_pct":float(e.mean()),"event_median_pct":float(e.median()),
                         "event_sum_pct":float(e.sum())})
    return pd.DataFrame(rows)

def concentration(universe,trades):
    rows=[]
    for rule in REGIME_RULES:
        col=f"regime_{rule}"
        for d in (1,2,3):
            g=trades[(trades[col]=="BEAR")&(trades.direction=="SHORT")&(trades.delay_min==d)].copy()
            if g.empty: continue
            by=g.groupby("symbol")["net_pct"].sum().sort_values(ascending=False)
            total_abs=float(by.abs().sum())
            total=len(by)
            for k in (1,3,5):
                kk=min(k,total); chosen=by.head(kk)
                rows.append({"universe":universe,"regime_rule":rule,"delay_min":d,
                             "top_n":kk,"total_symbols":total,"top_pct_symbols":100*kk/total,
                             "abs_pnl_share_pct":float(chosen.abs().sum()/total_abs*100) if total_abs else np.nan,
                             "symbols":",".join(chosen.index.astype(str))})
    return pd.DataFrame(rows)

def main():
    a=args(); out=Path(a.outdir); out.mkdir(parents=True,exist_ok=True)
    source=pd.read_csv(a.source)
    sel=json.loads(Path(a.selection).read_text(encoding="utf-8"))
    holdout=set(s for s in sel["symbols"] if s not in ORIGINAL_AUTO50)
    got=set(source.symbol.dropna().astype(str).unique())
    if not got.issubset(holdout):
        raise RuntimeError(f"source has non-holdout symbols: {sorted(got-holdout)[:10]}")
    start=int(source.ts.min()); end=int(source.ts.max())
    print(f"[WINDOW] {pd.to_datetime(start,unit='ms',utc=True)} .. {pd.to_datetime(end,unit='ms',utc=True)}")
    print(f"[NEW66] source_symbols={len(got)} expected_holdout={len(holdout)}")

    breadth=pd.read_csv(Path(a.regime_dir)/"regime_breadth_results"/"breadth_timeseries.csv.gz")
    old=pd.read_csv(Path(a.regime_dir)/"regime_breadth_results"/"long_vs_short_trades.csv.gz")
    old=old[(old.base_strategy=="L3_4H_LAG") & old.signal_ts.between(start,end)].copy()

    signals=build_signals(source)
    l3=signals[signals.strategy=="L3_4H_LAG"].copy()
    print(f"[NEW66 L3] signals={len(l3)} symbols={l3.symbol.nunique()}")
    if l3.empty: raise RuntimeError("0 L3 signals in NEW66 source")

    minute,fail=fetch_all_minutes(l3,a.workers)
    if fail: pd.DataFrame(fail,columns=["symbol","error"]).to_csv(out/"minute_failures.csv",index=False)
    new=simulate_both(l3,minute)
    new=add_regime(new,breadth)
    if new[[f"regime_{r}" for r in REGIME_RULES]].isna().all(axis=None):
        raise RuntimeError("regime merge failed completely")

    # Original AUTO50 trade artifact already carries the exact frozen regime labels.
    # Keep only the exact same calendar window as NEW66.
    old["universe"]="AUTO50_ORIGINAL"
    new["universe"]="NEW66_HOLDOUT"

    sm=pd.concat([summary("AUTO50_ORIGINAL",old),summary("NEW66_HOLDOUT",new)],ignore_index=True)
    ev=pd.concat([event_summary("AUTO50_ORIGINAL",old),event_summary("NEW66_HOLDOUT",new)],ignore_index=True)
    co=pd.concat([concentration("AUTO50_ORIGINAL",old),concentration("NEW66_HOLDOUT",new)],ignore_index=True)

    sm.to_csv(out/"l3_cross_universe_summary.csv",index=False)
    ev.to_csv(out/"l3_cross_universe_events.csv",index=False)
    co.to_csv(out/"l3_cross_universe_concentration.csv",index=False)
    new.to_csv(out/"new66_l3_long_short_trades.csv.gz",index=False,compression="gzip")
    old.to_csv(out/"auto50_l3_same_window_trades.csv.gz",index=False,compression="gzip")

    focus=sm[(sm.regime=="BEAR")].copy()
    print("\n=== L3 BEAR CROSS-UNIVERSE ===")
    print(focus.to_string(index=False))
    print("\n=== 24H INDEPENDENT EVENTS ===")
    print(ev.to_string(index=False))
    print("\n=== CONCENTRATION WITH DENOMINATOR ===")
    print(co.to_string(index=False))
    print("\nIMPORTANT: cross-sectional holdout only; calendar dates overlap prior discovery research. No threshold, TP/SL, horizon, or delay was tuned.")
    print(f"[DONE] old_rows={len(old)} new_rows={len(new)} new_l3_signals={len(l3)}")

if __name__=="__main__": main()
