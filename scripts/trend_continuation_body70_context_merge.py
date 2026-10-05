#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np
import pandas as pd

DAY=24*60*60*1000
COST=0.20

def perf(g):
    y=g.gross_pct.astype(float).to_numpy()-COST
    if not len(y): return {"n":0}
    gp=y[y>0].sum(); gl=-y[y<0].sum()
    return {"n":int(len(y)),"wr_pct":float((y>0).mean()*100),"ev_pct":float(y.mean()),
            "pf":float(gp/gl) if gl>0 else None,"sum_net_pct":float(y.sum())}

def table(z,col):
    out=[]
    for k,g in z.groupby(col,dropna=False):
        out.append({"bucket":str(k),**perf(g),"share_pct":float(len(g)/len(z)*100) if len(z) else None})
    return out

def stable_compare(prior,recent,col):
    ps={x["bucket"]:x for x in table(prior,col)}
    rs={x["bucket"]:x for x in table(recent,col)}
    keys=sorted(set(ps)|set(rs))
    out=[]
    for k in keys:
        p=ps.get(k,{}); r=rs.get(k,{})
        out.append({"bucket":k,
                    "prior_n":p.get("n"),"prior_pf":p.get("pf"),"prior_ev":p.get("ev_pct"),
                    "recent_n":r.get("n"),"recent_pf":r.get("pf"),"recent_ev":r.get("ev_pct")})
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--partials",required=True); ap.add_argument("--out",required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/context_partial.csv.gz",recursive=True))
    if len(fs)!=8: raise RuntimeError(f"expected 8 partials, got {len(fs)}")
    z=pd.concat([pd.read_csv(f,compression="gzip") for f in fs],ignore_index=True)
    z=z.sort_values(["signal_ts","symbol"]).drop_duplicates(["symbol","signal_ts"]).reset_index(drop=True)

    # Causal crowding: count same-timestamp executed BODY70 signals and trailing 1h including current timestamp.
    cnt=z.groupby("signal_ts").size().sort_index()
    times=cnt.index.to_numpy(np.int64); vals=cnt.to_numpy(np.int64)
    same_map=dict(zip(times,vals))
    roll={}
    left=0; s=0
    for i,t in enumerate(times):
        s+=vals[i]
        while times[left] < t-60*60*1000:
            s-=vals[left]; left+=1
        roll[int(t)]=int(s)
    z["crowd_same"]=z.signal_ts.map(same_map)
    z["crowd_1h"]=z.signal_ts.map(roll)

    dt=pd.to_datetime(z.signal_ts,unit="ms",utc=True)
    z["hour_utc"]=dt.dt.hour
    z["hour6"]=pd.cut(z.hour_utc,[-1,5,11,17,23],labels=["00-05","06-11","12-17","18-23"])
    z["strength_bin"]=pd.cut(z.strength8h_pct,[-np.inf,25,35,50,np.inf],labels=["20-25","25-35","35-50",">=50"])
    z["pct_bin"]=pd.cut(z.xsec_pct,[0.90,0.95,0.98,0.99,1.000001],include_lowest=True,labels=["90-95","95-98","98-99","99-100"])
    z["body_bin"]=pd.cut(z.body_ratio,[0.70,0.80,0.90,1.000001],include_lowest=True,labels=["70-80","80-90","90-100"])
    z["range_bin"]=pd.cut(z.signal_range_pct,[-np.inf,2,4,8,np.inf],labels=["<2","2-4","4-8",">=8"])
    z["crowd_same_bin"]=pd.cut(z.crowd_same,[0,1,3,6,np.inf],labels=["1","2-3","4-6",">=7"])
    z["crowd1h_bin"]=pd.cut(z.crowd_1h,[0,3,8,15,np.inf],labels=["<=3","4-8","9-15",">=16"])

    max_ts=int(z.signal_ts.max()); cut=max_ts-120*DAY
    prior=z[z.signal_ts<cut]; recent=z[z.signal_ts>=cut]
    dims=["strength_bin","pct_bin","body_bin","range_bin","crowd_same_bin","crowd1h_bin","hour6"]

    summary={
        "definition":{"cohort":"executed frozen BODY70 immediate trades","cost_pct":COST,
                      "recent120_anchor_max_signal_ts":max_ts,"recent120_cut_ts":cut,
                      "crowd_same":"number of executed BODY70 signals at same 15m timestamp",
                      "crowd_1h":"causal count over trailing 60m including current timestamp"},
        "overall":perf(z),"prior":perf(prior),"recent120":perf(recent),
        "dimensions":{d:stable_compare(prior,recent,d) for d in dims},
    }
    z.to_csv(out/"context_all.csv.gz",index=False,compression="gzip")
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("BODY70_CONTEXT_JSON")
    print(json.dumps(summary,default=str),flush=True)

if __name__=="__main__": main()
