from __future__ import annotations
import argparse, gzip, io, json, math, zipfile
from pathlib import Path

import numpy as np
import pandas as pd

RNG=np.random.default_rng(20260923)

# Frozen from stage-1 exploratory anatomy on 86 MID-vol L1 events.
# These are NOT independently validated thresholds.
RET1H_HIGH=15.002010235933481
BBW1H_HIGH=31.56361428067038
BREADTH4H_LOW=49.10394265232974
BTC4H_NOT_HIGH=0.3309109471063701


def args():
    p=argparse.ArgumentParser()
    p.add_argument("--artifact",required=True)
    p.add_argument("--outdir",default="bb_event_candidate_robustness_results")
    return p.parse_args()


def read_event(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        raw=z.read("mid_event_features.csv.gz")
    return pd.read_csv(gzip.GzipFile(fileobj=io.BytesIO(raw)))


def pf(s,slip=0.0):
    x=pd.to_numeric(s,errors="coerce").dropna()-slip
    pos=float(x[x>0].sum()); neg=float(-x[x<0].sum())
    if neg<=0:return float("inf") if pos>0 else float("nan")
    return pos/neg


def metrics(g,d,slip=0.0):
    c=f"short_net_d{d}"
    x=pd.to_numeric(g[c],errors="coerce").dropna()-slip
    return {
        "n":int(len(x)),"symbols":int(g.loc[x.index,"symbol"].nunique()) if len(x) else 0,
        "avg_net_pct":float(x.mean()) if len(x) else float("nan"),
        "sum_net_pct":float(x.sum()) if len(x) else float("nan"),
        "pf":pf(g[c],slip),
        "win_pct":float((x>0).mean()*100.0) if len(x) else float("nan"),
    }


def add_votes(x):
    x=x.copy()
    x["vote_ret1h_high"]=(x["ret_1h_pct"]>=RET1H_HIGH).astype(int)
    x["vote_bbw1h_high"]=(x["bb1h_width_pct"]>=BBW1H_HIGH).astype(int)
    x["vote_breadth4h_low"]=(x["breadth_pos4h_pct"]<=BREADTH4H_LOW).astype(int)
    x["vote_btc4h_not_high"]=(x["btc_ret_4h_pct"]<=BTC4H_NOT_HIGH).astype(int)
    x["short_votes"]=x[[c for c in x.columns if c.startswith("vote_")]].sum(axis=1)
    mid=(int(x["signal_ts"].min())+int(x["signal_ts"].max()))//2
    x["half"]=np.where(x["signal_ts"]<mid,"FIRST_HALF","SECOND_HALF")
    x["month"]=pd.to_datetime(x["signal_ts"],unit="ms",utc=True).dt.strftime("%Y-%m")
    return x


def weekly_boot(g,d,reps=5000):
    if g.empty:return (np.nan,np.nan,np.nan)
    z=g.copy()
    z["week"]=pd.to_datetime(z.signal_ts,unit="ms",utc=True).dt.strftime("%G-W%V")
    weeks=z.week.unique()
    if len(weeks)<2:return (np.nan,float(z[f"short_net_d{d}"].mean()),np.nan)
    vals=[]
    for _ in range(reps):
        ws=RNG.choice(weeks,len(weeks),replace=True)
        a=np.concatenate([z.loc[z.week==w,f"short_net_d{d}"].to_numpy(dtype=float) for w in ws])
        vals.append(float(a.mean()))
    return tuple(np.percentile(vals,[2.5,50,97.5]).tolist())


def summary(candidate):
    rows=[]
    scopes=[("ALL",candidate),("OLD34",candidate[candidate.universe_group=="OLD34"]),
            ("NEW66",candidate[candidate.universe_group=="NEW66"]),
            ("FIRST_HALF",candidate[candidate.half=="FIRST_HALF"]),
            ("SECOND_HALF",candidate[candidate.half=="SECOND_HALF"])]
    for name,g in scopes:
        for d in (1,2,3):
            lo,med,hi=weekly_boot(g,d)
            rows.append({"scope":name,"delay_min":d,**metrics(g,d),
                         "pf_slip025":pf(g[f"short_net_d{d}"],.25),
                         "pf_slip050":pf(g[f"short_net_d{d}"],.50),
                         "week_boot_mean_lo":lo,"week_boot_mean_med":med,"week_boot_mean_hi":hi})
    for month,g in candidate.groupby("month"):
        for d in (1,2,3):
            rows.append({"scope":f"MONTH_{month}","delay_min":d,**metrics(g,d),
                         "pf_slip025":pf(g[f"short_net_d{d}"],.25),
                         "pf_slip050":pf(g[f"short_net_d{d}"],.50),
                         "week_boot_mean_lo":np.nan,"week_boot_mean_med":np.nan,"week_boot_mean_hi":np.nan})
    return pd.DataFrame(rows)


def concentration(candidate):
    rows=[]
    for d in (1,2,3):
        c=f"short_net_d{d}"
        contrib=candidate.groupby("symbol")[c].sum().sort_values(ascending=False)
        total_pos=float(contrib[contrib>0].sum())
        for k in (0,1,3,5):
            drop=set(contrib.head(k).index) if k else set()
            g=candidate[~candidate.symbol.isin(drop)]
            rows.append({"delay_min":d,"drop_top":k,
                         "dropped_symbols":",".join(contrib.head(k).index) if k else "",
                         **metrics(g,d),
                         "pf_slip025":pf(g[c],.25),"pf_slip050":pf(g[c],.50)})
        shares=(contrib.clip(lower=0)/total_pos) if total_pos>0 else contrib*0
        rows.append({"delay_min":d,"drop_top":-1,"dropped_symbols":"CONCENTRATION_META",
                     "n":len(candidate),"symbols":candidate.symbol.nunique(),
                     "avg_net_pct":float(candidate[c].mean()),"sum_net_pct":float(candidate[c].sum()),
                     "pf":pf(candidate[c]),"win_pct":float((candidate[c]>0).mean()*100),
                     "pf_slip025":float((shares.pow(2)).sum()), # HHI stored here for compactness
                     "pf_slip050":float(shares.head(3).sum())}) # top3 positive-PnL share
    return pd.DataFrame(rows)


def exact_vote_table(x):
    rows=[]
    for v,g in x.groupby("short_votes"):
        for d in (1,2,3):
            rows.append({
                "short_votes":int(v),"delay_min":d,"n":len(g),"symbols":g.symbol.nunique(),
                "short_avg":g[f"short_net_d{d}"].mean(),"short_pf":pf(g[f"short_net_d{d}"]),
                "long_avg":g[f"long_net_d{d}"].mean(),"long_pf":pf(g[f"long_net_d{d}"]),
                "avg_short_minus_long":g[f"edge_d{d}"].mean(),
            })
    return pd.DataFrame(rows)


def complement(candidate,x):
    off=x[x.short_votes<2].copy()
    rows=[]
    for scope,g in [("ALL_OFF",off),("OLD34_OFF",off[off.universe_group=="OLD34"]),
                    ("NEW66_OFF",off[off.universe_group=="NEW66"])]:
        for d in (1,2,3):
            rows.append({"scope":scope,"delay_min":d,"n":len(g),"symbols":g.symbol.nunique(),
                         "short_pf":pf(g[f"short_net_d{d}"]),"short_avg":g[f"short_net_d{d}"].mean(),
                         "long_pf":pf(g[f"long_net_d{d}"]),"long_avg":g[f"long_net_d{d}"].mean(),
                         "edge":g[f"edge_d{d}"].mean()})
    return pd.DataFrame(rows)


def main():
    a=args(); out=Path(a.outdir);out.mkdir(parents=True,exist_ok=True)
    x=add_votes(read_event(a.artifact))
    cand=x[x.short_votes>=2].copy()

    sm=summary(cand); conc=concentration(cand); vt=exact_vote_table(x); comp=complement(cand,x)
    x.to_csv(out/"events_with_frozen_votes.csv.gz",index=False,compression="gzip")
    cand.to_csv(out/"candidate_short_votes_ge2.csv",index=False)
    sm.to_csv(out/"candidate_summary.csv",index=False)
    conc.to_csv(out/"candidate_concentration.csv",index=False)
    vt.to_csv(out/"exact_vote_diagnostic.csv",index=False)
    comp.to_csv(out/"off_group_direction_diagnostic.csv",index=False)

    meta={
      "candidate":"L1 + BTC-vol MID; SHORT only when >=2 of 4 frozen stage-1 votes fire; otherwise OFF.",
      "votes":{
       "ret1h_high":f"ret_1h_pct >= {RET1H_HIGH}",
       "bb1h_width_high":f"bb1h_width_pct >= {BBW1H_HIGH}",
       "breadth4h_low":f"breadth_pos4h_pct <= {BREADTH4H_LOW}",
       "btc4h_not_high":f"btc_ret_4h_pct <= {BTC4H_NOT_HIGH}",
      },
      "warning":"All four features and cut points were discovered on the same 86-event sample. Robustness here is internal only, not independent validation.",
      "candidate_events":len(cand),"candidate_symbols":int(cand.symbol.nunique()),
      "old34_events":int((cand.universe_group=="OLD34").sum()),"new66_events":int((cand.universe_group=="NEW66").sum()),
    }
    (out/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    print("=== META ===");print(json.dumps(meta,ensure_ascii=False,indent=2))
    print("\n=== FROZEN DISCOVERY CANDIDATE ROBUSTNESS ===");print(sm.to_string(index=False))
    print("\n=== LEAVE TOP CONTRIBUTORS OUT ===");print(conc[conc.drop_top>=0].to_string(index=False))
    print("\n=== EXACT VOTE DIAGNOSTIC ===");print(vt.to_string(index=False))
    print("\n=== OFF GROUP DIRECTION DIAGNOSTIC ===");print(comp.to_string(index=False))
    print(f"\n[DONE] candidate_events={len(cand)}")


if __name__=="__main__":main()
