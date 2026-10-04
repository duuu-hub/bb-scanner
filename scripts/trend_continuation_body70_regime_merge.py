#!/usr/bin/env python3
"""Merge BODY70 breadth partials and diagnose regime decay."""
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np
import pandas as pd

DAY_MS=24*60*60*1000
COST=0.20

def stats(g):
    if len(g)==0:
        return {"n":0,"wr_pct":None,"ev_pct":None,"pf":None,"sum_net_pct":0.0}
    y=g["gross_pct"].astype(float).to_numpy()-COST
    gp=y[y>0].sum(); gl=-y[y<0].sum()
    return {
        "n":int(len(y)),
        "wr_pct":float((y>0).mean()*100),
        "ev_pct":float(y.mean()),
        "pf":float(gp/gl) if gl>0 else None,
        "sum_net_pct":float(y.sum()),
    }

def bucket_breadth(x):
    return np.where(x<0.40,"LOW_<40",np.where(x<0.60,"MID_40_60","HIGH_>=60"))

def attach_regime(t,b):
    z=t.merge(b,on="signal_ts",how="left",validate="many_to_one")
    z["breadth4"]=np.where(z.n4>0,z.pos4/z.n4,np.nan)
    z["breadth24"]=np.where(z.n24>0,z.pos24/z.n24,np.nan)
    z["mean4"]=np.where(z.n4>0,z.sum4/z.n4,np.nan)
    z["mean24"]=np.where(z.n24>0,z.sum24/z.n24,np.nan)
    z["mean_abs4"]=np.where(z.n4>0,z.abs4/z.n4,np.nan)
    z["mean_abs24"]=np.where(z.n24>0,z.abs24/z.n24,np.nan)
    z["breadth4_bucket"]=bucket_breadth(z.breadth4)
    z["breadth24_bucket"]=bucket_breadth(z.breadth24)
    z["btc4_sign"]=np.where(z.btc4>=0,"UP","DOWN")
    z["btc24_sign"]=np.where(z.btc24>=0,"UP","DOWN")
    z["btc7d_sign"]=np.where(z.btc7d>=0,"UP","DOWN")
    z["regime24"]=np.select(
        [
            (z.breadth24>=0.60)&(z.btc24>=0),
            (z.breadth24<0.40)|(z.btc24<0),
        ],
        ["RISK_ON","RISK_OFF"],
        default="MIXED",
    )
    return z

def grouped(z,col):
    out=[]
    for k,g in z.groupby(col,dropna=False):
        out.append({"bucket":str(k),"share_pct":float(len(g)/len(z)*100) if len(z) else None,**stats(g)})
    return out

def period_groups(z):
    max_ts=int(z.signal_ts.max())
    recent_cut=max_ts-120*DAY_MS
    recent180_cut=max_ts-180*DAY_MS
    train=z[z.signal_ts<pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000]
    hold=z[z.signal_ts>=pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000]
    recent120=z[z.signal_ts>=recent_cut]
    prior120=z[z.signal_ts<recent_cut]
    recent180=z[z.signal_ts>=recent180_cut]
    return {
        "ALL":z,"TRAIN":train,"HOLDOUT":hold,
        "PRIOR_TO_RECENT120":prior120,"RECENT120":recent120,"RECENT180":recent180,
    },max_ts,recent_cut,recent180_cut

def attribution(prior,recent,col):
    if len(prior)==0 or len(recent)==0:
        return {}
    prior_overall=stats(prior)["ev_pct"]
    buckets=sorted(set(prior[col].astype(str))|set(recent[col].astype(str)))
    p_ev={}; p_share={}; r_share={}; r_ev={}
    for b in buckets:
        pg=prior[prior[col].astype(str)==b]
        rg=recent[recent[col].astype(str)==b]
        p_ev[b]=stats(pg)["ev_pct"]
        p_share[b]=len(pg)/len(prior) if len(prior) else 0
        r_share[b]=len(rg)/len(recent) if len(recent) else 0
        r_ev[b]=stats(rg)["ev_pct"]
    # only buckets with prior estimate; renormalize recent share over covered buckets
    covered=[b for b in buckets if p_ev[b] is not None]
    cov=sum(r_share[b] for b in covered)
    predicted=None
    if cov>0:
        predicted=sum((r_share[b]/cov)*p_ev[b] for b in covered)
    actual=stats(recent)["ev_pct"]
    return {
        "bucket_col":col,
        "prior_overall_ev_pct":prior_overall,
        "recent_actual_ev_pct":actual,
        "recent_predicted_from_prior_bucket_ev_pct":predicted,
        "composition_effect_pct":(predicted-prior_overall) if predicted is not None and prior_overall is not None else None,
        "within_regime_decay_pct":(actual-predicted) if predicted is not None and actual is not None else None,
        "prior_bucket_ev":p_ev,"prior_share":p_share,"recent_share":r_share,"recent_bucket_ev":r_ev,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--partials",required=True)
    ap.add_argument("--trades",required=True)
    ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)

    fs=sorted(glob.glob(a.partials+"/**/breadth_partial.csv.gz",recursive=True))
    if len(fs)!=8:
        raise RuntimeError(f"expected 8 breadth partials, got {len(fs)}")
    ds=[pd.read_csv(f,compression="gzip") for f in fs]
    allp=pd.concat(ds,ignore_index=True)

    # Sum breadth numerators/denominators. BTC features exist in exactly one shard; coalesce non-null.
    sums=allp.groupby("signal_ts",as_index=False)[["n4","pos4","sum4","abs4","n24","pos24","sum24","abs24"]].sum()
    btc=allp.groupby("signal_ts",as_index=False).agg(
        btc4=("btc4","max"),btc24=("btc24","max"),btc7d=("btc7d","max")
    )
    b=sums.merge(btc,on="signal_ts",how="left",validate="one_to_one")
    b.to_csv(out/"breadth_merged.csv.gz",index=False,compression="gzip")

    t=pd.read_csv(a.trades,compression="gzip")
    t=t[t.delay_min.eq(0)].copy()
    z=attach_regime(t,b)
    z.to_csv(out/"body70_trades_with_regime.csv.gz",index=False,compression="gzip")

    periods,max_ts,recent_cut,recent180_cut=period_groups(z)
    tables={}
    for pname,g in periods.items():
        tables[pname]={
            "overall":stats(g),
            "breadth4":grouped(g,"breadth4_bucket"),
            "breadth24":grouped(g,"breadth24_bucket"),
            "btc4":grouped(g,"btc4_sign"),
            "btc24":grouped(g,"btc24_sign"),
            "btc7d":grouped(g,"btc7d_sign"),
            "regime24":grouped(g,"regime24"),
        }

    attr24=attribution(periods["PRIOR_TO_RECENT120"],periods["RECENT120"],"regime24")
    attr_b24=attribution(periods["PRIOR_TO_RECENT120"],periods["RECENT120"],"breadth24_bucket")
    attr_b4=attribution(periods["PRIOR_TO_RECENT120"],periods["RECENT120"],"breadth4_bucket")

    # Year x regime24 for stability.
    z["year"]=pd.to_datetime(z.signal_ts,unit="ms",utc=True).dt.year
    yr=[]
    for (y,r),g in z.groupby(["year","regime24"]):
        yr.append({"year":int(y),"regime24":r,**stats(g)})
    pd.DataFrame(yr).to_csv(out/"year_regime.csv",index=False)

    # Recent monthly diagnostic.
    z["month"]=pd.to_datetime(z.signal_ts,unit="ms",utc=True).dt.strftime("%Y-%m")
    mon=[]
    for m,g in z.groupby("month"):
        mon.append({
            "month":m,**stats(g),
            "risk_on_share_pct":float((g.regime24=="RISK_ON").mean()*100),
            "risk_off_share_pct":float((g.regime24=="RISK_OFF").mean()*100),
            "breadth24_mean":float(g.breadth24.mean()),
            "btc24_mean_pct":float(g.btc24.mean()*100),
        })
    pd.DataFrame(mon).to_csv(out/"monthly.csv",index=False)

    summary={
        "definition":{
            "candidate":"frozen BODY70 immediate-entry trades, TP3 SL5 6h",
            "cost_pct":COST,
            "breadth4":"share of available Binance UM USDT symbols with trailing 4h return > 0 at completed signal timestamp",
            "breadth24":"share with trailing 24h return > 0",
            "regime24":{
                "RISK_ON":"breadth24>=60% AND BTC24>=0",
                "RISK_OFF":"breadth24<40% OR BTC24<0",
                "MIXED":"otherwise",
            },
            "recent120_anchor_max_signal_ts":max_ts,
            "recent120_cut_ts":recent_cut,
            "recent180_cut_ts":recent180_cut,
        },
        "period_tables":tables,
        "recent120_attribution_regime24":attr24,
        "recent120_attribution_breadth24":attr_b24,
        "recent120_attribution_breadth4":attr_b4,
    }
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("BODY70_REGIME_JSON")
    print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__": main()
