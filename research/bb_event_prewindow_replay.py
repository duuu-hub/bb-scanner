from __future__ import annotations

import argparse
import gzip
import io
import json
import math
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import fetch_range, rows_to_df

BAR15_MS = 15 * 60_000
HOUR_MS = 60 * 60_000

# Frozen from the 2026-03-27..2026-09-22 anatomy sample. Do not retune here.
RET1H_HIGH = 15.002010235933481
BBW1H_HIGH = 31.56361428067038
BREADTH4H_LOW = 49.10394265232974
BTC4H_NOT_HIGH = 0.3309109471063701

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
    p=argparse.ArgumentParser()
    p.add_argument("--context-artifact",required=True)
    p.add_argument("--breadth-artifact",required=True)
    p.add_argument("--selection",default="market_data_store/bitget/research_auto100_15m/selection.json")
    p.add_argument("--cutoff",default="2026-03-27T00:00:00Z")
    p.add_argument("--workers",type=int,default=8)
    p.add_argument("--outdir",default="bb_event_prewindow_replay_results")
    return p.parse_args()


def read_zip_csv(path, member):
    with zipfile.ZipFile(path) as z:
        raw=z.read(member)
    if member.endswith(".gz"):
        return pd.read_csv(gzip.GzipFile(fileobj=io.BytesIO(raw)))
    return pd.read_csv(io.BytesIO(raw))


def pf(s, slip=0.0):
    x=pd.to_numeric(s,errors="coerce").dropna()-slip
    pos=float(x[x>0].sum()); neg=float(-x[x<0].sum())
    if neg<=0:return float("inf") if pos>0 else float("nan")
    return pos/neg


def load_events(a, overlap):
    tr=read_zip_csv(a.context_artifact,"trades_with_market_context.csv.gz")
    sig=read_zip_csv(a.breadth_artifact,"regime_breadth_results/signals_with_regime.csv")
    cutoff=int(pd.Timestamp(a.cutoff).timestamp()*1000)

    tr=tr[
        (tr["base_strategy"]=="L1_MOMENTUM_1H10")
        & (tr["btc_vol_state"]=="MID")
        & tr["symbol"].isin(overlap)
        & (tr["signal_ts"]<cutoff)
    ].copy()

    meta=tr.sort_values(["symbol","signal_ts"]).drop_duplicates(["symbol","signal_ts"])[
        ["symbol","signal_ts","signal_price","btc_ret_4h","btc_ret_24h"]
    ].copy()

    l1=sig[(sig["base_strategy"]=="L1_MOMENTUM_1H10") & sig["symbol"].isin(overlap)][
        ["symbol","ts","ret_1h","ret_4h"]
    ].drop_duplicates(["symbol","ts"])
    meta=meta.merge(l1,left_on=["symbol","signal_ts"],right_on=["symbol","ts"],how="left").drop(columns="ts")

    p=tr.pivot_table(index=["symbol","signal_ts"],columns=["direction","delay_min"],values="net_pct",aggfunc="first")
    p.columns=[f"{d.lower()}_net_d{int(k)}" for d,k in p.columns]
    p=p.reset_index()
    return meta.merge(p,on=["symbol","signal_ts"],how="left").sort_values("signal_ts").reset_index(drop=True)


def fetch_rolling_4h(symbol, ts):
    # 17 completed 15m closes = exact trailing 4h return at signal boundary.
    start=int(ts)-17*BAR15_MS
    # history-candles end boundary excluded the last completed 15m candle in the old replay
    # request through signal_ts, then filter to close_ts <= signal_ts
    try:
        rows=fetch_range(symbol,"15m",15,start,end)
        df=rows_to_df(rows,15)
        df=df[(df["close_ts"]<=int(ts)) & (df["ts"]>=start)].drop_duplicates("ts").sort_values("ts")
        if len(df)<17:
            return None
        df=df.tail(17)
        expected=np.diff(df["ts"].to_numpy(dtype=np.int64))
        if len(expected) and not np.all(expected==BAR15_MS):
            return None
        first=float(df.iloc[0]["close"]); last=float(df.iloc[-1]["close"])
        if first<=0:return None
        return (last/first-1.0)*100.0
    except Exception:
        return None


def compute_breadth(events, symbols, workers):
    tasks=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ts in events["signal_ts"].unique():
            for symbol in symbols:
                tasks.append((symbol,int(ts),pool.submit(fetch_rolling_4h,symbol,int(ts))))
        by_ts={int(ts):[] for ts in events["signal_ts"].unique()}
        total=len(tasks)
        for i,(symbol,ts,fut) in enumerate(tasks,1):
            try:r=fut.result()
            except Exception:r=None
            if r is not None and math.isfinite(r):
                by_ts[ts].append(float(r))
            if i%250==0 or i==total:
                print(f"[BREADTH] {i}/{total}",flush=True)

    rows=[]
    for ts,vals in by_ts.items():
        a=np.array(vals,dtype=float)
        rows.append({
            "signal_ts":ts,
            "breadth_universe_n":int(len(a)),
            "breadth_pos4h_pct":float(np.mean(a>0)*100.0) if len(a) else float("nan"),
            "market_median_4h_pct":float(np.median(a)) if len(a) else float("nan"),
        })
    return pd.DataFrame(rows)


def fetch_bb1h_width(symbol, ts, signal_price):
    # Match discovery definition: 19 completed 1h closes + live signal price.
    start=int(ts)-24*HOUR_MS
    end=int(ts)
    try:
        rows=fetch_range(symbol,"1H",60,start,end)
        df=rows_to_df(rows,60)
        df=df[df["close_ts"]<=int(ts)].drop_duplicates("ts").sort_values("ts")
        if len(df)<19:return float("nan")
        prev=df.tail(19)["close"].to_numpy(dtype=float)
        x=np.concatenate([prev,np.array([float(signal_price)])])
        basis=float(np.mean(x)); std=float(np.std(x,ddof=0))
        if basis<=0:return float("nan")
        return (4.0*std/basis)*100.0
    except Exception:
        return float("nan")


def enrich(events, symbols, workers):
    breadth=compute_breadth(events,symbols,workers)
    x=events.merge(breadth,on="signal_ts",how="left")

    widths=[]
    for i,r in enumerate(x.itertuples(index=False),1):
        widths.append(fetch_bb1h_width(r.symbol,int(r.signal_ts),float(r.signal_price)))
        print(f"[BBW] {i}/{len(x)} {r.symbol}",flush=True)
    x["bb1h_width_pct"]=widths

    x["vote_ret1h_high"]=(x["ret_1h"]>=RET1H_HIGH).astype(int)
    x["vote_bbw1h_high"]=(x["bb1h_width_pct"]>=BBW1H_HIGH).astype(int)
    x["vote_breadth4h_low"]=(x["breadth_pos4h_pct"]<=BREADTH4H_LOW).astype(int)
    x["vote_btc4h_not_high"]=(x["btc_ret_4h"]<=BTC4H_NOT_HIGH).astype(int)
    x["short_votes"]=x[[c for c in x.columns if c.startswith("vote_")]].sum(axis=1)
    x["candidate_short"]=x["short_votes"]>=2
    x["quarter"]=pd.to_datetime(x["signal_ts"],unit="ms",utc=True).dt.to_period("Q").astype(str)
    return x


def metrics(g,d,slip=0.0):
    c=f"short_net_d{d}"
    xx=pd.to_numeric(g[c],errors="coerce").dropna()-slip
    return {
        "n":int(len(xx)),"symbols":int(g.loc[xx.index,"symbol"].nunique()) if len(xx) else 0,
        "avg_net_pct":float(xx.mean()) if len(xx) else float("nan"),
        "sum_net_pct":float(xx.sum()) if len(xx) else float("nan"),
        "pf":pf(g[c],slip),
        "win_pct":float((xx>0).mean()*100.0) if len(xx) else float("nan"),
    }


def summarize(x):
    rows=[]
    scopes=[
        ("ALL_PRE",x),
        ("CANDIDATE",x[x.candidate_short]),
        ("OFF",x[~x.candidate_short]),
    ]
    for q,g in x.groupby("quarter"):
        scopes.append((f"CANDIDATE_{q}",g[g.candidate_short]))
    for name,g in scopes:
        for d in (1,2,3):
            row={"scope":name,"delay_min":d,**metrics(g,d),
                 "pf_slip025":pf(g[f"short_net_d{d}"],.25),
                 "pf_slip050":pf(g[f"short_net_d{d}"],.50)}
            if name=="OFF":
                row["long_pf"]=pf(g[f"long_net_d{d}"])
                row["long_avg"]=g[f"long_net_d{d}"].mean()
            rows.append(row)
    return pd.DataFrame(rows)


def concentration(x):
    cand=x[x.candidate_short].copy()
    rows=[]
    for d in (1,2,3):
        c=f"short_net_d{d}"
        contrib=cand.groupby("symbol")[c].sum().sort_values(ascending=False)
        for k in (0,1,3,5):
            drop=set(contrib.head(k).index) if k else set()
            g=cand[~cand.symbol.isin(drop)]
            rows.append({"delay_min":d,"drop_top":k,"dropped_symbols":",".join(contrib.head(k).index) if k else "",
                         **metrics(g,d),"pf_slip025":pf(g[c],.25),"pf_slip050":pf(g[c],.50)})
    return pd.DataFrame(rows)


def main():
    a=parse_args(); out=Path(a.outdir);out.mkdir(parents=True,exist_ok=True)
    sel=json.loads(Path(a.selection).read_text(encoding="utf-8"))
    auto100=set(sel["symbols"]); overlap=auto100 & OLD_AUTO50
    events=load_events(a,overlap)
    print(f"[EVENTS] pre-window MID L1={len(events)} symbols={events.symbol.nunique()}",flush=True)
    x=enrich(events,sorted(auto100),a.workers)
    sm=summarize(x); conc=concentration(x)

    x.to_csv(out/"prewindow_events_enriched.csv",index=False)
    sm.to_csv(out/"prewindow_summary.csv",index=False)
    conc.to_csv(out/"prewindow_concentration.csv",index=False)

    meta={
        "cutoff":a.cutoff,"events":len(x),"symbols":int(x.symbol.nunique()),
        "candidate_events":int(x.candidate_short.sum()),
        "candidate_symbols":int(x.loc[x.candidate_short,"symbol"].nunique()),
        "auto100_breadth_selection":len(auto100),
        "frozen_rule":"L1 + BTC-vol MID; SHORT if >=2 of: ret1h>=15.0020102359, 1h BB width>=31.5636142807, AUTO100-available breadth positive4h<=49.1039426523, BTC4h<=0.3309109471.",
        "warning":"Chronologically earlier replay, but NOT pristine untouched OOS: this older period was inspected in prior BB/regime research. No thresholds are changed here.",
        "breadth_note":"Breadth is reconstructed point-in-time from the frozen current-survivor AUTO100 selection; symbols without historical candles at an event are excluded from that event denominator.",
        "breadth_bug_fix":"Fixed an off-by-one API end boundary that previously returned only 16 completed 15m candles and forced breadth_universe_n=0. Thresholds/rule unchanged.",
    }
    (out/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")

    print("=== META ===");print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== PRE-WINDOW FROZEN RULE ===");print(sm.to_string(index=False))
    print("\n=== PRE-WINDOW CONCENTRATION ===");print(conc.to_string(index=False))
    print("\n=== EVENTS ===");print(x[["symbol","signal_ts","quarter","ret_1h","bb1h_width_pct","breadth_universe_n","breadth_pos4h_pct","btc_ret_4h","short_votes","candidate_short"]].to_string(index=False))
    print(f"\n[DONE] candidate_events={int(x.candidate_short.sum())}")


if __name__=="__main__":
    main()
