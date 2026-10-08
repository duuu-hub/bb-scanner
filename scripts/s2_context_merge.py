#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json
from pathlib import Path
import numpy as np, pandas as pd

COST=0.20

def pf(v):
    a=np.asarray(v,float);gp=a[a>0].sum();gl=-a[a<0].sum()
    return float(gp/gl) if gl>0 else (float("inf") if gp>0 else None)

def stats(g):
    if len(g)==0:return {"n":0}
    v=g["gross_pct"].astype(float).to_numpy()-COST
    return {"n":int(len(v)),"pf":pf(v),"avg":float(v.mean()),"wr":float((v>0).mean()*100),"sum":float(v.sum())}

def classify(row):
    bull=bear=0
    for h in ("1h","4h","24h"):
        p=row.get(f"alt_positive_{h}")
        m=row.get(f"alt_median_{h}")
        if pd.notna(p):
            if p>=55:bull+=1
            elif p<=45:bear+=1
        if pd.notna(m):
            if m>0:bull+=1
            elif m<0:bear+=1
    score=bull-bear
    return "BULLISH" if score>=3 else ("BEARISH" if score<=-3 else "NEUTRAL")

def bucket_num(x,bins,labels):
    return pd.cut(x,bins=bins,labels=labels,include_lowest=True,right=False)

def group_table(z,col):
    out=[]
    for k,g in z.groupby(col,dropna=False):
        out.append({"bucket":str(k),"share_pct":float(len(g)/len(z)*100) if len(z) else None,**stats(g)})
    return out

def fwd_ret(r):
    e=float(r.get("shadow_entry_price") or 0);q=float(r.get("shadow_exit_price") or 0)
    return ((e-q)/e*100.0) if e>0 and q>0 else np.nan

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--partials",required=True);ap.add_argument("--trades",required=True);ap.add_argument("--forward-state",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(a.partials+"/**/context_partial.csv.gz",recursive=True))
    if len(fs)!=8:raise RuntimeError(f"expected 8 context partials got {len(fs)}")
    parts=[pd.read_csv(f,compression="gzip") for f in fs]
    allp=pd.concat(parts,ignore_index=True)

    sums=allp.groupby("signal_ts",as_index=False).agg({
      **{f"alt_n_{h}":"sum" for h in ("1h","4h","24h")},
      **{f"alt_pos_{h}":"sum" for h in ("1h","4h","24h")},
      **{f"alt_sum_{h}":"sum" for h in ("1h","4h","24h")},
      **{f"btc_{h}":"max" for h in ("1h","4h","24h")},
      **{f"eth_{h}":"max" for h in ("1h","4h","24h")},
    })
    for h in ("1h","4h","24h"):
        sums[f"alt_positive_{h}"]=np.where(sums[f"alt_n_{h}"]>0,sums[f"alt_pos_{h}"]/sums[f"alt_n_{h}"]*100,np.nan)
        sums[f"alt_median_{h}"]=np.nan  # partial sums cannot recover median exactly
        sums[f"alt_mean_{h}"]=np.where(sums[f"alt_n_{h}"]>0,sums[f"alt_sum_{h}"]/sums[f"alt_n_{h}"],np.nan)

    # Regime approximation uses breadth plus mean sign instead of exact median; label explicitly.
    def regime_approx(r):
        bull=bear=0
        for h in ("1h","4h","24h"):
            p=r[f"alt_positive_{h}"];m=r[f"alt_mean_{h}"]
            if np.isfinite(p):
                if p>=55:bull+=1
                elif p<=45:bear+=1
            if np.isfinite(m):
                if m>0:bull+=1
                elif m<0:bear+=1
        sc=bull-bear
        return "BULLISH" if sc>=3 else ("BEARISH" if sc<=-3 else "NEUTRAL")
    sums["regime_approx"]=sums.apply(regime_approx,axis=1)

    t=pd.read_csv(a.trades,compression="gzip")
    t=t[t.delay_min.eq(0)].copy()
    z=t.merge(sums,on="signal_ts",how="left",validate="many_to_one")
    z["btc24_bin"]=bucket_num(z.btc_24h,[-np.inf,-2,0,2,np.inf],["<=-2","-2..0","0..2",">=2"])
    z["btc4_sign"]=np.where(z.btc_4h>=0,"UP","DOWN")
    z["alt4_breadth_bin"]=bucket_num(z.alt_positive_4h,[0,25,45,55,75,101],["<25","25-45","45-55","55-75",">=75"])
    z["alt4_mean_bin"]=bucket_num(z.alt_mean_4h,[-np.inf,-1,0,1,np.inf],["<-1","-1..0","0..1",">=1"])
    z.to_csv(out/"historical_s2_with_context.csv.gz",index=False,compression="gzip")

    state=json.loads(Path(a.forward_state).read_text())
    rows=[]
    for r in state.get("signal_shadow_closed",[]):
        if not ((r.get("portfolio")=="SHORT3" or str(r.get("signal_id","")).startswith("SHORT3:")) and r.get("strategy")=="S2"):
            continue
        m=r.get("market_snapshot") or {}
        ret=fwd_ret(r)
        rows.append({
          "signal_ts":int(r.get("signal_time_ms") or 0),"gross_pct":ret,
          "btc_1h":m.get("btc_1h_pct"),"btc_4h":m.get("btc_4h_pct"),"btc_24h":m.get("btc_24h_pct"),
          "eth_1h":m.get("eth_1h_pct"),"eth_4h":m.get("eth_4h_pct"),"eth_24h":m.get("eth_24h_pct"),
          "alt_positive_4h":m.get("alt_positive_4h_pct"),"alt_mean_4h":m.get("alt_median_4h_pct"),
          "regime_approx":m.get("market_regime"),
        })
    f=pd.DataFrame(rows)
    if len(f):
        f["btc24_bin"]=bucket_num(pd.to_numeric(f.btc_24h),[-np.inf,-2,0,2,np.inf],["<=-2","-2..0","0..2",">=2"])
        f["btc4_sign"]=np.where(pd.to_numeric(f.btc_4h)>=0,"UP","DOWN")
        f["alt4_breadth_bin"]=bucket_num(pd.to_numeric(f.alt_positive_4h),[0,25,45,55,75,101],["<25","25-45","45-55","55-75",">=75"])
        f["alt4_mean_bin"]=bucket_num(pd.to_numeric(f.alt_mean_4h),[-np.inf,-1,0,1,np.inf],["<-1","-1..0","0..1",">=1"])
    f.to_csv(out/"forward_s2_context.csv",index=False)

    dims=["regime_approx","btc24_bin","btc4_sign","alt4_breadth_bin","alt4_mean_bin"]
    summary={"historical_overall":stats(z),"forward_overall":stats(f),"dimensions":{}}
    for d in dims:
        summary["dimensions"][d]={"historical":group_table(z,d),"forward":group_table(f,d)}
    (out/"summary.json").write_text(json.dumps(summary,indent=2,default=str))
    print("S2_CONTEXT_SUMMARY",json.dumps(summary,default=str),flush=True)
if __name__=="__main__":main()
