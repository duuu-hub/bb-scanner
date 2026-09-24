from __future__ import annotations
import argparse, json, math
from pathlib import Path
import numpy as np, pandas as pd

from precision_backtest import candidate_rank, first_cross, fetch_all_minutes, one_trade

REGIME_RULES=("55_45","60_40","65_35")
TF_NAMES=["1W","1D","12H","4H","1H","30M","15M"]

def parse_args():
    p=argparse.ArgumentParser()
    p.add_argument("--source-old",required=True)
    p.add_argument("--source-new",required=True)
    p.add_argument("--regime-dir",required=True)
    p.add_argument("--outdir",default="l3_lite_frequency_results")
    p.add_argument("--workers",type=int,default=8)
    return p.parse_args()

def pf(s,slip=0.0):
    x=pd.to_numeric(s,errors="coerce").dropna()-slip
    pos=float(x[x>0].sum()); neg=float(-x[x<0].sum())
    if neg<=0:return float("inf") if pos>0 else float("nan")
    return pos/neg

def metrics(g,slip=0.0):
    x=pd.to_numeric(g["net_pct"],errors="coerce").dropna()-slip
    return {
        "n":int(len(x)),
        "symbols":int(g.loc[x.index,"symbol"].nunique()) if len(x) else 0,
        "avg":float(x.mean()) if len(x) else np.nan,
        "sum":float(x.sum()) if len(x) else np.nan,
        "pf":pf(g["net_pct"],slip) if len(x) else np.nan,
        "win_pct":float((x>0).mean()*100) if len(x) else np.nan,
    }

def build_variant_signals(source:pd.DataFrame, variant:str)->pd.DataFrame:
    cols=[
        "symbol","ts","time_utc","price","exact_count","within_3pct_count",
        "1W_above","1D_above","12H_above","4H_above","1H_above","30M_above","15M_above","15M_dist"
    ]
    df=source[cols].copy().sort_values(["symbol","ts"]).reset_index(drop=True)
    df["rank"]=candidate_rank(df)
    g=df.groupby("symbol",sort=False)
    df["ret_1h"]=g["price"].pct_change(4)*100.0
    df["ret_4h"]=g["price"].pct_change(16)*100.0
    if variant=="STRICT":
        mask=(df["exact_count"].to_numpy()==6)&(df["4H_above"].to_numpy()==0)
    elif variant=="LITE_A":
        mask=(df["rank"].to_numpy()>=5)&(df["4H_above"].to_numpy()==0)
    elif variant=="ADJACENT_RANK5_ONLY":
        mask=(df["rank"].to_numpy()==5)&(df["4H_above"].to_numpy()==0)
    else:
        raise ValueError(variant)
    trig=first_cross(pd.Series(mask,index=df.index),df["symbol"])
    x=df.loc[trig.to_numpy(),["symbol","ts","time_utc","price","rank","exact_count","within_3pct_count","ret_1h","ret_4h"]].copy()
    x["strategy"]=f"L3_{variant}"
    x["variant"]=variant
    x["direction"]="LONG"
    x["tp_pct"]=10.0
    x["sl_pct"]=4.0
    x["horizon_min"]=720
    x["split"]="all"
    return x.sort_values(["ts","symbol"]).reset_index(drop=True)

def replay(sig,workers):
    minute,fail=fetch_all_minutes(sig,workers)
    rows=[]
    for direction in ("LONG","SHORT"):
        z=sig.copy(); z["direction"]=direction
        for s in z.itertuples(index=False):
            md=minute.get(s.symbol)
            for d in (1,2,3):
                r=one_trade(s,md,d)
                if r is not None:
                    r["variant"]=s.variant
                    r["rank"]=int(s.rank)
                    r["exact_count"]=int(s.exact_count)
                    r["within_3pct_count"]=int(s.within_3pct_count)
                    rows.append(r)
    return pd.DataFrame(rows),fail

def attach_regime(t,b):
    cols=["ts"]+[f"regime_{r}" for r in REGIME_RULES]
    return t.merge(b[cols].drop_duplicates("ts"),left_on="signal_ts",right_on="ts",how="left").drop(columns="ts")

def event_ids(ts,hours):
    ts=np.asarray(ts,dtype=np.int64); order=np.argsort(ts); ids=np.empty(len(ts),dtype=int)
    cid=-1; prev=None
    for i in order:
        t=int(ts[i])
        if prev is None or t-prev>hours*3600_000: cid+=1
        ids[i]=cid; prev=t
    return ids

def summarize(universe,trades):
    rows=[]
    for variant in ("STRICT","LITE_A","ADJACENT_RANK5_ONLY"):
        tv=trades[trades.variant==variant]
        for rule in REGIME_RULES:
            rc=f"regime_{rule}"
            for direction in ("LONG","SHORT"):
                for d in (1,2,3):
                    g=tv[(tv[rc]=="BEAR")&(tv.direction==direction)&(tv.delay_min==d)]
                    m=metrics(g)
                    rows.append({
                        "universe":universe,"variant":variant,"regime_rule":rule,
                        "direction":direction,"delay_min":d,**m,
                        "pf_cost025":metrics(g,.25)["pf"] if len(g) else np.nan,
                        "pf_cost050":metrics(g,.50)["pf"] if len(g) else np.nan,
                    })
    return pd.DataFrame(rows)

def event_summary(universe,trades):
    rows=[]
    for variant in ("STRICT","LITE_A","ADJACENT_RANK5_ONLY"):
        tv=trades[trades.variant==variant]
        for rule in REGIME_RULES:
            rc=f"regime_{rule}"
            for d in (1,2,3):
                g=tv[(tv[rc]=="BEAR")&(tv.direction=="SHORT")&(tv.delay_min==d)].copy()
                for h in (24,48):
                    if g.empty:
                        rows.append({"universe":universe,"variant":variant,"regime_rule":rule,"delay_min":d,"cluster_h":h,"trades":0,"symbols":0,"events":0})
                        continue
                    g=g.sort_values("signal_ts").reset_index(drop=True)
                    g["event_id"]=event_ids(g.signal_ts,h)
                    e=g.groupby("event_id")["net_pct"].sum()
                    rows.append({
                        "universe":universe,"variant":variant,"regime_rule":rule,"delay_min":d,"cluster_h":h,
                        "trades":len(g),"symbols":g.symbol.nunique(),"events":len(e),
                        "profitable_events":int((e>0).sum()),
                        "profitable_event_pct":float((e>0).mean()*100),
                        "event_avg":float(e.mean()),"event_median":float(e.median()),"event_sum":float(e.sum())
                    })
    return pd.DataFrame(rows)

def concentration(universe,trades):
    rows=[]
    for variant in ("STRICT","LITE_A","ADJACENT_RANK5_ONLY"):
        tv=trades[trades.variant==variant]
        for d in (1,2,3):
            g=tv[(tv.regime_60_40=="BEAR")&(tv.direction=="SHORT")&(tv.delay_min==d)].copy()
            if g.empty: continue
            by=g.groupby("symbol")["net_pct"].agg(["count","sum"]).reset_index()
            total=by.symbol.nunique(); total_abs=float(by["sum"].abs().sum())
            top=by.sort_values("sum",ascending=False)
            for k in (1,3,5):
                kk=min(k,total)
                chosen=top.head(kk)
                rows.append({
                    "universe":universe,"variant":variant,"delay_min":d,
                    "top_n":kk,"total_symbols":total,"top_pct_symbols":100*kk/total,
                    "abs_pnl_share_pct":float(chosen["sum"].abs().sum()/total_abs*100) if total_abs else np.nan,
                    "symbols":",".join(chosen.symbol.astype(str))
                })
    return pd.DataFrame(rows)

def frequency(universe,sig,source):
    days=max(1,(int(source.ts.max())-int(source.ts.min()))/86400000)
    rows=[]
    for variant,g in sig.groupby("variant"):
        rows.append({
            "universe":universe,"variant":variant,"calendar_days":days,
            "raw_signals":len(g),"signal_symbols":g.symbol.nunique(),
            "signals_per_30d":len(g)/days*30.0,
            "avg_days_per_signal":days/len(g) if len(g) else np.nan
        })
    return pd.DataFrame(rows)

def first_per_symbol_sensitivity(universe,trades):
    rows=[]
    for variant in ("STRICT","LITE_A","ADJACENT_RANK5_ONLY"):
        base=trades[(trades.variant==variant)&(trades.regime_60_40=="BEAR")].copy()
        if base.empty: continue
        keys=base[["symbol","signal_ts"]].drop_duplicates().sort_values("signal_ts").drop_duplicates("symbol",keep="first")
        keep=set(map(tuple,keys[["symbol","signal_ts"]].to_records(index=False)))
        g=base[base.apply(lambda r:(r.symbol,r.signal_ts) in keep,axis=1)]
        for direction in ("LONG","SHORT"):
            for d in (1,2,3):
                z=g[(g.direction==direction)&(g.delay_min==d)]
                rows.append({"universe":universe,"variant":variant,"scope":"FIRST_PER_SYMBOL","direction":direction,"delay_min":d,**metrics(z)})
    return pd.DataFrame(rows)

def main():
    a=parse_args(); out=Path(a.outdir); out.mkdir(parents=True,exist_ok=True)
    breadth=pd.read_csv(Path(a.regime_dir)/"regime_breadth_results"/"breadth_timeseries.csv.gz")
    all_sum=[]; all_ev=[]; all_conc=[]; all_freq=[]; all_first=[]; all_trades=[]
    for universe,path in [("AUTO50_OVERLAP",a.source_old),("NEW66_HOLDOUT",a.source_new)]:
        src=pd.read_csv(path)
        sigs=[]
        for v in ("STRICT","LITE_A","ADJACENT_RANK5_ONLY"):
            q=build_variant_signals(src,v); q["universe"]=universe; sigs.append(q)
        sig=pd.concat(sigs,ignore_index=True)
        print(f"[{universe}] signals by variant\n"+sig.groupby("variant").agg(n=("ts","size"),symbols=("symbol","nunique")).to_string())
        tr,fail=replay(sig,a.workers)
        tr["universe"]=universe
        tr=attach_regime(tr,breadth)
        if fail:
            pd.DataFrame(fail,columns=["symbol","error"]).to_csv(out/f"{universe.lower()}_minute_failures.csv",index=False)
        tr.to_csv(out/f"{universe.lower()}_trades.csv.gz",index=False,compression="gzip")
        sig.to_csv(out/f"{universe.lower()}_signals.csv",index=False)
        all_trades.append(tr)
        all_sum.append(summarize(universe,tr))
        all_ev.append(event_summary(universe,tr))
        all_conc.append(concentration(universe,tr))
        all_freq.append(frequency(universe,sig,src))
        all_first.append(first_per_symbol_sensitivity(universe,tr))
    S=pd.concat(all_sum,ignore_index=True); E=pd.concat(all_ev,ignore_index=True)
    C=pd.concat(all_conc,ignore_index=True); F=pd.concat(all_freq,ignore_index=True)
    P=pd.concat(all_first,ignore_index=True); T=pd.concat(all_trades,ignore_index=True)
    S.to_csv(out/"summary.csv",index=False); E.to_csv(out/"events.csv",index=False)
    C.to_csv(out/"concentration.csv",index=False); F.to_csv(out/"frequency.csv",index=False)
    P.to_csv(out/"first_per_symbol_sensitivity.csv",index=False)
    T.to_csv(out/"all_trades.csv.gz",index=False,compression="gzip")

    print("\n=== FREQUENCY ==="); print(F.to_string(index=False))
    print("\n=== 60/40 BEAR SUMMARY ===")
    print(S[S.regime_rule=="60_40"].to_string(index=False))
    print("\n=== 60/40 BEAR EVENTS ===")
    print(E[(E.regime_rule=="60_40")&(E.cluster_h==24)].to_string(index=False))
    print("\n=== FIRST-PER-SYMBOL SENSITIVITY ==="); print(P.to_string(index=False))
    print("\nIMPORTANT: STRICT is frozen control. LITE_A and ADJACENT_RANK5_ONLY are predeclared neighboring-state hypotheses for frequency/robustness testing, not tuned winners.")
    print("[DONE]")

if __name__=="__main__":main()
