from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np, pandas as pd

def pf(vals, extra=0.0):
    x=pd.to_numeric(pd.Series(vals),errors="coerce").dropna()-extra
    pos=float(x[x>0].sum()); neg=float(-x[x<0].sum())
    if neg<=0:return float("inf") if pos>0 else float("nan")
    return pos/neg

def metrics(g, extra=0.0):
    x=pd.to_numeric(g.net_pct,errors="coerce").dropna()-extra
    return dict(n=len(x), symbols=g.loc[x.index,"symbol"].nunique() if len(x) else 0,
                avg=x.mean() if len(x) else np.nan, sum=x.sum() if len(x) else np.nan,
                pf=pf(g.loc[x.index,"net_pct"],extra) if len(x) else np.nan,
                win_pct=(x>0).mean()*100 if len(x) else np.nan)

def event_map(signals,hours=24):
    z=signals.drop_duplicates(["symbol","signal_ts"]).sort_values(["signal_ts","symbol"]).copy()
    ids=[]; ords=[]; cid=-1; prev=None; within=0
    for t in z.signal_ts.astype("int64"):
        if prev is None or int(t)-prev>hours*3600_000:
            cid+=1; within=1
        else:
            within+=1
        ids.append(cid); ords.append(within); prev=int(t)
    z[f"event_{hours}h"]=ids
    z[f"event_{hours}h_ordinal"]=ords
    return z[["symbol","signal_ts",f"event_{hours}h",f"event_{hours}h_ordinal"]]

def add_ordinals(t):
    keys=t[["universe","symbol","signal_ts"]].drop_duplicates().sort_values(["universe","signal_ts","symbol"]).copy()
    keys["symbol_ordinal"]=keys.groupby(["universe","symbol"]).cumcount()+1
    pieces=[]
    for u,g in keys.groupby("universe"):
        e=event_map(g,24)
        g=g.merge(e,on=["symbol","signal_ts"],how="left")
        pieces.append(g)
    keys=pd.concat(pieces,ignore_index=True)
    return t.merge(keys,on=["universe","symbol","signal_ts"],how="left")

def pair_dirs(g):
    p=g.pivot_table(index=["universe","symbol","signal_ts","delay_min","event_24h","event_24h_ordinal","symbol_ordinal"],
                    columns="direction",values="net_pct",aggfunc="first").reset_index()
    return p

def summarize_groups(p):
    rows=[]
    scopes=[
        ("EVENT_FIRST",p[p.event_24h_ordinal==1]),
        ("EVENT_REPEAT",p[p.event_24h_ordinal>=2]),
        ("SYMBOL_FIRST",p[p.symbol_ordinal==1]),
        ("SYMBOL_SECOND",p[p.symbol_ordinal==2]),
        ("SYMBOL_THIRD_PLUS",p[p.symbol_ordinal>=3]),
        ("SYMBOL_REPEAT_ANY",p[p.symbol_ordinal>=2]),
    ]
    for name,z in scopes:
        for direction in ["SHORT","LONG"]:
            if direction not in z.columns: continue
            q=z[["universe","symbol","signal_ts",direction]].rename(columns={direction:"net_pct"})
            for u,gu in q.groupby("universe"):
                for extra in [0.0,.25,.50]:
                    m=metrics(gu,extra)
                    rows.append(dict(scope=name,universe=u,direction=direction,extra_cost=extra,**m))
    return pd.DataFrame(rows)

def policy_rows(p):
    rows=[]
    for u,g in p.groupby("universe"):
        policies={
            "ALL_SHORT": np.where(np.ones(len(g),dtype=bool),g["SHORT"],np.nan),
            "ALL_LONG": np.where(np.ones(len(g),dtype=bool),g["LONG"],np.nan),
            "EVENT_FIRST_SHORT_REPEAT_LONG": np.where(g.event_24h_ordinal==1,g["SHORT"],g["LONG"]),
            "EVENT_FIRST_SHORT_REPEAT_SKIP": np.where(g.event_24h_ordinal==1,g["SHORT"],np.nan),
            "SYMBOL_FIRST2_SHORT_THIRDPLUS_LONG": np.where(g.symbol_ordinal<=2,g["SHORT"],g["LONG"]),
            "SYMBOL_FIRST_SHORT_REPEAT_LONG": np.where(g.symbol_ordinal==1,g["SHORT"],g["LONG"]),
        }
        for pname,vals in policies.items():
            z=g.copy(); z["policy_net"]=vals
            z=z[pd.notna(z.policy_net)]
            for extra in [0.0,.25,.50]:
                x=z.policy_net-extra
                rows.append(dict(universe=u,policy=pname,extra_cost=extra,n=len(x),
                                 symbols=z.symbol.nunique(),avg=x.mean(),sum=x.sum(),pf=pf(z.policy_net,extra),
                                 win_pct=(x>0).mean()*100))
    return pd.DataFrame(rows)

def event_level_policy(p):
    rows=[]
    for u,g in p.groupby("universe"):
        for pname,arr in {
            "ALL_SHORT":g["SHORT"],
            "EVENT_FIRST_SHORT_REPEAT_LONG":pd.Series(np.where(g.event_24h_ordinal==1,g["SHORT"],g["LONG"]),index=g.index),
            "EVENT_FIRST_SHORT_REPEAT_SKIP":pd.Series(np.where(g.event_24h_ordinal==1,g["SHORT"],np.nan),index=g.index),
        }.items():
            z=g.copy();z["policy_net"]=arr;z=z[pd.notna(z.policy_net)]
            e=z.groupby("event_24h").policy_net.sum()
            rows.append(dict(universe=u,policy=pname,events=len(e),profitable_events=int((e>0).sum()),
                             event_win_pct=(e>0).mean()*100,event_avg=e.mean(),event_median=e.median(),event_sum=e.sum(),
                             top1_abs_share=(e.abs().sort_values(ascending=False).head(1).sum()/e.abs().sum()*100) if e.abs().sum() else np.nan))
    return pd.DataFrame(rows)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--trades",required=True);ap.add_argument("--outdir",default="repeat_reversal_results")
    a=ap.parse_args();out=Path(a.outdir);out.mkdir(parents=True,exist_ok=True)
    t=pd.read_csv(a.trades)
    t=t[(t.variant=="ADJACENT_RANK5_ONLY")&(t.regime_60_40=="BEAR")&(t.delay_min.isin([1,2,3]))].copy()
    t=add_ordinals(t)
    p=pair_dirs(t)

    G=[]
    P=[]
    E=[]
    for d in [1,2,3]:
        q=p[p.delay_min==d].copy()
        g=summarize_groups(q);g["delay_min"]=d;G.append(g)
        pol=policy_rows(q);pol["delay_min"]=d;P.append(pol)
        ev=event_level_policy(q);ev["delay_min"]=d;E.append(ev)
    G=pd.concat(G,ignore_index=True);P=pd.concat(P,ignore_index=True);E=pd.concat(E,ignore_index=True)

    G.to_csv(out/"repeat_direction_summary.csv",index=False)
    P.to_csv(out/"policy_comparison.csv",index=False)
    E.to_csv(out/"event_policy_summary.csv",index=False)
    p.to_csv(out/"paired_signal_results.csv.gz",index=False,compression="gzip")

    print("=== REPEAT DIRECTION +1m ===")
    print(G[(G.delay_min==1)&(G.extra_cost==0)].to_string(index=False))
    print("\n=== POLICY +1m ===")
    print(P[(P.delay_min==1)].to_string(index=False))
    print("\n=== EVENT POLICY +1m ===")
    print(E[E.delay_min==1].to_string(index=False))
    print("\nIMPORTANT: reversal policies are POST-HOC hypotheses generated after observing repeat SHORT weakness. They are not eligible to replace the frozen demo rule without prospective validation.")
    print("[DONE]")

if __name__=="__main__": main()
