from __future__ import annotations
import argparse, math
from pathlib import Path
import numpy as np, pandas as pd

FEATURES=["ret_15m","ret_1h","ret_4h","ret_12h","ret_24h","rv_4h","rv_24h",
"1W_dist","1D_dist","12H_dist","4H_dist","1H_dist","30M_dist","15M_dist",
"weekly_mid_pct_24h_med","daily_mid_pct_24h_med","bear_episode_age_h"]

def parse_args():
    p=argparse.ArgumentParser()
    p.add_argument("--signals",required=True)
    p.add_argument("--outdir",default="l3_reversal_stress_results")
    return p.parse_args()

def pf(x):
    x=pd.to_numeric(x,errors="coerce").dropna()
    pos=float(x[x>0].sum()); neg=float(-x[x<0].sum())
    if neg<=0:return float("inf") if pos>0 else float("nan")
    return pos/neg

def metrics(g,d=1):
    s=pd.to_numeric(g[f"short_net_d{d}"],errors="coerce").dropna()
    l=pd.to_numeric(g[f"long_net_d{d}"],errors="coerce").dropna()
    return {"n":len(g),"symbols":g.symbol.nunique(),
            "short_avg":float(s.mean()),"short_sum":float(s.sum()),"short_pf":pf(s),
            "long_avg":float(l.mean()),"long_sum":float(l.sum()),"long_pf":pf(l),
            "edge_avg_short_minus_long":float((s-l).mean())}

def add_event_id(x,hours):
    z=x.sort_values("signal_ts").copy()
    ids=[]; cid=-1; prev=None
    for t in z.signal_ts.astype("int64"):
        if prev is None or int(t)-prev>hours*3600_000: cid+=1
        ids.append(cid); prev=int(t)
    z[f"event_{hours}h"]=ids
    return z

def main():
    a=parse_args(); out=Path(a.outdir); out.mkdir(parents=True,exist_ok=True)
    x=pd.read_csv(a.signals).sort_values("signal_ts").reset_index(drop=True)
    x["bear_episode_start_est"]=(x.signal_ts - x.bear_episode_age_h*3600_000).round(-5)
    x=add_event_id(x,24)
    x=add_event_id(x,48)

    base=[]
    for u,g in x.groupby("universe"):
        for d in (1,2,3): base.append({"scope":u,"delay":d,**metrics(g,d)})
    base=pd.DataFrame(base); base.to_csv(out/"base.csv",index=False)

    n=x[x.universe=="NEW66_HOLDOUT"].copy()
    loo=[]
    for sym in sorted(n.symbol.unique()):
        g=n[n.symbol!=sym]
        for d in (1,2,3): loo.append({"dropped_symbol":sym,"delay":d,**metrics(g,d)})
    loo=pd.DataFrame(loo); loo.to_csv(out/"new66_leave_one_symbol_out.csv",index=False)

    # Repeater concentration diagnostics only, not proposed rules.
    counts=n.symbol.value_counts()
    repeat_syms=counts[counts>1].index.tolist()
    rep=[]
    scopes=[("ALL",n),
            ("DROP_BEAT",n[n.symbol!="BEATUSDT"]),
            ("DROP_VELVET",n[n.symbol!="VELVETUSDT"]),
            ("DROP_BEAT_VELVET",n[~n.symbol.isin(["BEATUSDT","VELVETUSDT"])]),
            ("SINGLE_SIGNAL_SYMBOLS",n[n.symbol.map(counts)==1])]
    for name,g in scopes:
        for d in (1,2,3): rep.append({"scope":name,"delay":d,**metrics(g,d)})
    rep=pd.DataFrame(rep); rep.to_csv(out/"repeater_diagnostic.csv",index=False)

    # First signal per symbol / per 24h event are sensitivity diagnostics only.
    sens=[]
    for name,g in [
        ("ALL_NEW66",n),
        ("FIRST_PER_SYMBOL",n.sort_values("signal_ts").drop_duplicates("symbol",keep="first")),
        ("FIRST_PER_24H_EVENT",n.sort_values("signal_ts").drop_duplicates("event_24h",keep="first")),
        ("FIRST_PER_48H_EVENT",n.sort_values("signal_ts").drop_duplicates("event_48h",keep="first")),
    ]:
        for d in (1,2,3): sens.append({"scope":name,"delay":d,**metrics(g,d)})
    sens=pd.DataFrame(sens); sens.to_csv(out/"first_occurrence_sensitivity.csv",index=False)

    # Event-level aggregate and leave-one-event-out.
    evrows=[]; evloo=[]
    for h in (24,48):
        col=f"event_{h}h"
        for eid,g in n.groupby(col):
            row={"cluster_h":h,"event_id":int(eid),"signals":len(g),"symbols":g.symbol.nunique(),
                 "start_ts":int(g.signal_ts.min()),"end_ts":int(g.signal_ts.max())}
            for d in (1,2,3):
                row[f"short_sum_d{d}"]=g[f"short_net_d{d}"].sum()
                row[f"long_sum_d{d}"]=g[f"long_net_d{d}"].sum()
                row[f"edge_sum_d{d}"]=(g[f"short_net_d{d}"]-g[f"long_net_d{d}"]).sum()
            evrows.append(row)
        for eid in sorted(n[col].unique()):
            g=n[n[col]!=eid]
            for d in (1,2,3): evloo.append({"cluster_h":h,"dropped_event":int(eid),"delay":d,**metrics(g,d)})
    ev=pd.DataFrame(evrows); ev.to_csv(out/"new66_events.csv",index=False)
    evloo=pd.DataFrame(evloo); evloo.to_csv(out/"new66_leave_one_event_out.csv",index=False)

    # Continuous BEAR episode estimate from known episode age (descriptive).
    berows=[]
    for (start,u),g in x.groupby(["bear_episode_start_est","universe"]):
        row={"bear_episode_start_est":start,"universe":u,"signals":len(g),"symbols":g.symbol.nunique(),
             "min_age_h":g.bear_episode_age_h.min(),"max_age_h":g.bear_episode_age_h.max()}
        for d in (1,2,3):
            row[f"short_sum_d{d}"]=g[f"short_net_d{d}"].sum()
            row[f"long_sum_d{d}"]=g[f"long_net_d{d}"].sum()
            row[f"edge_sum_d{d}"]=(g[f"short_net_d{d}"]-g[f"long_net_d{d}"]).sum()
        berows.append(row)
    be=pd.DataFrame(berows)
    shared=be.groupby("bear_episode_start_est").universe.nunique()
    be["shared_bear_episode"]=be.bear_episode_start_est.map(shared).ge(2)
    be.to_csv(out/"bear_episode_comparison.csv",index=False)

    # Within NEW66 post-hoc feature association: hypothesis generation only.
    assoc=[]
    edge=n.short_net_d1-n.long_net_d1
    for f in FEATURES:
        z=pd.to_numeric(n[f],errors="coerce")
        ok=z.notna()&edge.notna()
        rho=z[ok].rank().corr(edge[ok].rank()) if ok.sum()>=4 else np.nan
        assoc.append({"feature":f,"n":int(ok.sum()),"spearman_vs_short_minus_long_d1":rho})
    assoc=pd.DataFrame(assoc).sort_values("spearman_vs_short_minus_long_d1",key=lambda s:s.abs(),ascending=False)
    assoc.to_csv(out/"new66_feature_edge_association_hypothesis_only.csv",index=False)

    # Compare repeat-heavy symbols to the rest on context.
    comp=[]
    heavy=n.symbol.isin(["BEATUSDT","VELVETUSDT"])
    for f in FEATURES:
        a1=pd.to_numeric(n.loc[heavy,f],errors="coerce").dropna()
        b1=pd.to_numeric(n.loc[~heavy,f],errors="coerce").dropna()
        comp.append({"feature":f,"beat_velvet_n":len(a1),"other_n":len(b1),
                     "beat_velvet_mean":a1.mean(),"other_mean":b1.mean(),
                     "beat_velvet_median":a1.median(),"other_median":b1.median()})
    comp=pd.DataFrame(comp); comp.to_csv(out/"beat_velvet_vs_others_features.csv",index=False)

    print("=== BASE ==="); print(base.to_string(index=False))
    print("\n=== REPEATER DIAGNOSTIC ==="); print(rep.to_string(index=False))
    print("\n=== FIRST OCCURRENCE SENSITIVITY ==="); print(sens.to_string(index=False))
    print("\n=== NEW66 LEAVE ONE SYMBOL OUT ==="); print(loo.to_string(index=False))
    print("\n=== NEW66 EVENTS ==="); print(ev.to_string(index=False))
    print("\n=== NEW66 LEAVE ONE EVENT OUT ==="); print(evloo.to_string(index=False))
    print("\n=== BEAR EPISODES ==="); print(be.to_string(index=False))
    print("\n=== FEATURE ASSOCIATION (HYPOTHESIS ONLY) ==="); print(assoc.to_string(index=False))
    print("\n=== BEAT+VELVET VS OTHERS ==="); print(comp.to_string(index=False))
    print("\nIMPORTANT: all exclusions/feature associations are sensitivity diagnostics, not new entry rules.")
    print("[DONE]")

if __name__=="__main__":main()
