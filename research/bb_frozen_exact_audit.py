from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np, pandas as pd

def parse_args():
    p=argparse.ArgumentParser()
    p.add_argument("--input",required=True)
    p.add_argument("--outdir",default="bb_frozen_exact_audit_results")
    return p.parse_args()

def pf(s,slip=0.0):
    x=pd.to_numeric(s,errors="coerce").dropna()-slip
    pos=float(x[x>0].sum()); neg=float(-x[x<0].sum())
    if neg<=0:return float("inf") if pos>0 else float("nan")
    return pos/neg

def metrics(g,d,slip=0.0):
    c=f"short_net_d{d}"; x=pd.to_numeric(g[c],errors="coerce").dropna()-slip
    return {"n":len(x),"symbols":g.loc[x.index,"symbol"].nunique() if len(x) else 0,
            "avg":float(x.mean()) if len(x) else np.nan,"sum":float(x.sum()) if len(x) else np.nan,
            "pf":pf(g[c],slip),"win_pct":float((x>0).mean()*100) if len(x) else np.nan}

def cluster_ids(ts, hours):
    order=np.argsort(ts); ids=np.empty(len(ts),dtype=int); cid=-1; prev=None
    for idx in order:
        t=int(ts[idx])
        if prev is None or t-prev>hours*3600_000: cid+=1
        ids[idx]=cid; prev=t
    return ids

def main():
    a=parse_args(); out=Path(a.outdir); out.mkdir(parents=True,exist_ok=True)
    x=pd.read_csv(a.input)
    cand=x[x["candidate_short"]==True].copy()
    rows=[]
    for d in (1,2,3):
        for name,g in [("ALL_MID",x),("CANDIDATE",cand),("OFF",x[x["candidate_short"]!=True])]:
            rows.append({"scope":name,"delay":d,**metrics(g,d),
                         "pf_cost025":pf(g[f"short_net_d{d}"],.25),
                         "pf_cost050":pf(g[f"short_net_d{d}"],.50)})
    comp=pd.DataFrame(rows)
    comp.to_csv(out/"candidate_vs_parent.csv",index=False)

    votes=[]
    for v,g in x.groupby("short_votes"):
        for d in (1,2,3):
            votes.append({"votes":int(v),"delay":d,"n":len(g),"symbols":g.symbol.nunique(),
                          "short_avg":g[f"short_net_d{d}"].mean(),"short_pf":pf(g[f"short_net_d{d}"]),
                          "long_avg":g[f"long_net_d{d}"].mean(),"long_pf":pf(g[f"long_net_d{d}"])})
    vt=pd.DataFrame(votes); vt.to_csv(out/"vote_count_diagnostic.csv",index=False)

    loo=[]
    for d in (1,2,3):
        for sym in sorted(cand.symbol.unique()):
            g=cand[cand.symbol!=sym]
            loo.append({"delay":d,"dropped_symbol":sym,**metrics(g,d)})
    loo=pd.DataFrame(loo); loo.to_csv(out/"leave_one_symbol_out.csv",index=False)

    tails=[]
    for d in (1,2,3):
        c=f"short_net_d{d}"; by=cand.groupby("symbol")[c].sum().sort_values()
        for kind,sym in [("BOTTOM1",by.index[0]),("TOP1",by.index[-1])]:
            g=cand[cand.symbol!=sym]
            tails.append({"delay":d,"drop":kind,"symbol":sym,"symbol_pnl":float(by.loc[sym]),
                          "total_symbols":cand.symbol.nunique(),"removed_pct_symbols":100/cand.symbol.nunique(),**metrics(g,d)})
    tails=pd.DataFrame(tails); tails.to_csv(out/"tail_symmetry.csv",index=False)

    evrows=[]
    for h in (6,12,24,48):
        z=cand.sort_values("signal_ts").copy()
        z["event_id"]=cluster_ids(z.signal_ts.to_numpy(),h)
        for d in (1,2,3):
            c=f"short_net_d{d}"; e=z.groupby("event_id")[c].sum()
            pos=e[e>0]
            evrows.append({"cluster_hours":h,"delay":d,"trades":len(z),"symbols":z.symbol.nunique(),
                           "events":len(e),"profitable_events":int((e>0).sum()),
                           "profitable_event_pct":float((e>0).mean()*100),
                           "event_avg":float(e.mean()),"event_median":float(e.median()),
                           "event_sum":float(e.sum()),
                           "top1_positive_share_pct":float(pos.max()/pos.sum()*100) if len(pos) and pos.sum()>0 else np.nan})
    ev=pd.DataFrame(evrows); ev.to_csv(out/"event_clustering.csv",index=False)

    print("=== CANDIDATE VS PARENT ==="); print(comp.to_string(index=False))
    print("\n=== VOTE COUNT ==="); print(vt.to_string(index=False))
    print("\n=== LEAVE ONE SYMBOL OUT ==="); print(loo.to_string(index=False))
    print("\n=== TOP/BOTTOM SYMMETRY ==="); print(tails.to_string(index=False))
    print("\n=== EVENT CLUSTERING ==="); print(ev.to_string(index=False))

if __name__=="__main__": main()
