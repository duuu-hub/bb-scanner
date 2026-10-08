#!/usr/bin/env python3
"""Exploratory complete-trend PSAR highest-high / lowest-low age study.
Use Binance UM 15m 5-year artifact 36095439671 and canonical OPEN-time PSAR.
No fills/trades are simulated; historic extreme positions are hindsight.
"""
import argparse, ast, glob, hashlib, json, os, re
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

NAMES = {"_symbol", "load", "contiguous_segments", "resample", "psar_open_projection"}
BURN = 100
LEN_EDGES = [0,4,8,16,32,10000000]
LEN_LABELS = ["1-4","5-8","9-16","17-32","33+"]
AGE_EDGES = [0,1,2,4,8,16,32,10000000]
AGE_LABELS = ["1","2","3-4","5-8","9-16","17-32","33+"]

def functions():
    src = Path("scripts/psar_open_canonical_compare.py").read_text()
    tree = ast.parse(src)
    selected = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
    assert {n.name for n in selected} == NAMES, "canonical engine unavailable"
    ns = {"np":np, "pd":pd, "os":os, "re":re}
    exec(compile(ast.Module(body=selected,type_ignores=[]),"<canonical>","exec"),ns)
    return ns,hashlib.sha256(src.encode()).hexdigest()

def audit(fn):
    x=np.arange(400,dtype=float)
    h=100+8*np.sin(x/9)+.04*x+1
    l=100+8*np.sin(x/9)+.04*x-1
    base, sides=fn["psar_open_projection"](h,l)
    assert np.count_nonzero(sides[1:]!=sides[:-1])>6
    for i in (3,60,101,255,399):
        hh=h.copy(); ll=l.copy();hh[i]+=33;ll[i]-=33
        new,bb=fn["psar_open_projection"](hh,ll)
        assert np.allclose(new[:i+1],base[:i+1],equal_nan=True)
        assert np.array_equal(bb[:i+1],sides[:i+1])
    t=np.arange(8,dtype=np.int64)*900000
    o=np.arange(10,18,dtype=float);rh=o+2;rl=o-2
    rt,ro,hi,lo,rc=fn["resample"](t,o,rh,rl,o+1,4)
    assert rt.tolist()==[0,3600000] and hi.tolist()==[15,19] and lo.tolist()==[8,12]
    print("AUDIT_PASS open projection causality, flip reproducibility, 15m resample",flush=True)

def period(a,b):
    cut=1735689600000
    if b<cut:return "TRAIN"
    if a>=cut:return "SEEN_VALIDATION"
    return "CROSS_SPLIT"

def study(a,fn,sha):
    m={"1h":4,"4h":16}[a.tf]
    paths=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True))
    assert paths,"input artifact missing"
    covered={}
    for p in paths:
        s=fn["_symbol"](p);t=fn["load"](p)[0]
        r=(int(t[0]),int(t[-1]))
        for lo,hi,old in covered.get(s,[]):
            assert max(lo,r[0])>min(hi,r[1]),f"duplicate {s} {p} {old}"
        covered.setdefault(s,[]).append((*r,p))
    chosen=[p for i,p in enumerate(paths) if i%a.shards==a.shard]
    records=[]; skipped=0
    for p in chosen:
        s=fn["_symbol"](p);t,o,h,l,c=fn["load"](p)
        for start,end in fn["contiguous_segments"](t):
            if end-start<m*(BURN+3):skipped+=1;continue
            rt,ro,rh,rl,rc=fn["resample"](t[start:end],o[start:end],h[start:end],l[start:end],c[start:end],m)
            if len(rt)<BURN+3:skipped+=1;continue
            ps,bull=fn["psar_open_projection"](rh,rl)
            flips=np.flatnonzero(bull[1:]!=bull[:-1])+1
            starts=[int(v) for v in flips if v>=BURN]
            for i,j in zip(starts[:-1],starts[1:]):
                length=j-i
                hiage=int(np.argmax(rh[i:j]))+1
                loage=int(np.argmin(rl[i:j]))+1
                isbull=bool(bull[i])
                peak=hiage if isbull else loage
                val=float(np.max(rh[i:j]) if isbull else np.min(rl[i:j]))
                favorable=(val/ro[i]-1)*100 if isbull else (1-val/ro[i])*100
                assert 1<=peak<=length and favorable>=-1e-8 and np.isfinite(ps[i])
                records.append(dict(symbol=s,start_ts=int(rt[i]),end_ts=int(rt[j]),
                    split=period(int(rt[i]),int(rt[j-1])),
                    side="BULL" if isbull else "BEAR",length=length,
                    peak_age=peak,high_age=hiage,low_age=loage,
                    peak_relative=peak/length,favorable_pct=float(favorable)))
        print("PROGRESS",a.tf,a.shard,s,len(records),flush=True)
    assert records,"no completed trends"
    pd.DataFrame(records).to_csv(a.out,index=False)
    meta=dict(repo="duuu-hub/bb-scanner",branch="research-rank5-binance-15m-5y",
        commit_sha=os.getenv("GITHUB_SHA","local"),canonical_engine_sha256=sha,
        data_artifact_run=36095439671,tf=a.tf,all_files=len(paths),
        files=len(chosen),shard=a.shard,shards=a.shards,n=len(records),
        skipped_segments=skipped,burnin=100,
        definition="OPEN-time flip from previous confirmed bars; first completed flip after burnin; next flip excluded from prior trend; wick high for BULL / wick low for BEAR; earliest tie",
        costs="N/A, descriptive study only",status="EXPLORATORY")
    Path(a.out+".meta.json").write_text(json.dumps(meta,indent=2))
    print("SHARD_DONE",json.dumps(meta),flush=True)

def rounding(x):return round(float(x),3)

def summary(df):
    out=[]
    for split in ("ALL","TRAIN","SEEN_VALIDATION","CROSS_SPLIT"):
        v0=df if split=="ALL" else df[df.split==split]
        for side in ("BULL","BEAR"):
            v1=v0[v0.side==side]
            for L in ("ALL",*LEN_LABELS):
                v=v1 if L=="ALL" else v1[v1.length_bin==L]
                if v.empty:continue
                ages=v.peak_age.to_numpy()
                counts=v.age_bin.value_counts()
                row=dict(split=split,side=side,length_bin=L,n=len(v),
                    mean_age=rounding(np.mean(ages)),median_age=rounding(np.median(ages)),
                    p25_age=rounding(np.quantile(ages,.25)),p75_age=rounding(np.quantile(ages,.75)),
                    median_relative_percent=rounding(v.peak_relative.median()*100),
                    mean_trend_length=rounding(v.length.mean()),
                    mean_favorable_pct=rounding(v.favorable_pct.mean()),
                    peak_in_first4_percent=rounding(100*np.mean(ages<=4)),
                    age_hist={k:int(counts.get(k,0)) for k in AGE_LABELS})
                if L=="ALL":
                    row["if_trend_survives_to_bar_N_prob_peak_already_seen"]={}
                    for age in (1,2,3,4,5,6,8,10,12,16,24,32,48):
                        alive=v[v.length>=age]
                        if len(alive)>=30:
                            row["if_trend_survives_to_bar_N_prob_peak_already_seen"][str(age)]=dict(
                                n=len(alive),percent=rounding(100*np.mean(alive.peak_age<=age)))
                out.append(row)
    return out

def merge(a,fn,sha):
    files=sorted(glob.glob(a.data+"/**/psar-peak-age-"+a.tf+"-*.csv",recursive=True))
    assert len(files)==a.shards,(len(files),a.shards)
    meta=[json.loads(Path(p+".meta.json").read_text()) for p in files]
    assert {x["shard"] for x in meta}==set(range(a.shards))
    assert sum(x["files"] for x in meta)==meta[0]["all_files"]
    assert len({x["commit_sha"] for x in meta})==1
    assert len({x["canonical_engine_sha256"] for x in meta})==1
    assert meta[0]["canonical_engine_sha256"]==sha
    df=pd.concat([pd.read_csv(p) for p in files],ignore_index=True)
    assert len(df)==sum(x["n"] for x in meta)
    assert not df.duplicated(["symbol","start_ts"]).any()
    assert ((df.peak_age>=1)&(df.peak_age<=df.length)).all()
    df["age_bin"]=pd.cut(df.peak_age,bins=AGE_EDGES,labels=AGE_LABELS)
    df["length_bin"]=pd.cut(df.length,bins=LEN_EDGES,labels=LEN_LABELS)
    report=dict(experiment="PSAR_TREND_EXTREME_AGE_20261008",
        status="EXPLORATORY",tf=a.tf,n=len(df),meta=meta,groups=summary(df),
        limitations=["retrospective completed runs; final extreme requires future data",
            "survivorship bias possible from stored data universe",
            "market-correlated symbols are not independent events",
            "no simulated fills/fees/funding or portfolio result",
            "2025-2026 data already SEEN, not true OOS"])
    Path(a.out).write_text(json.dumps(report,indent=2,ensure_ascii=False))
    for row in report["groups"]:
        if row["split"]=="ALL" and row["length_bin"]=="ALL":
            print("SUMMARY",a.tf,json.dumps(row,ensure_ascii=False),flush=True)
    print("MERGE_PASS",a.tf,len(files),len(df),flush=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data",default="data")
    p.add_argument("--tf",choices=["1h","4h"])
    p.add_argument("--shard",type=int,default=0)
    p.add_argument("--shards",type=int,default=4)
    p.add_argument("--out")
    p.add_argument("--audit-only",action="store_true")
    p.add_argument("--merge",action="store_true")
    a=p.parse_args()
    fn,sha=functions();audit(fn)
    if a.audit_only:return
    assert a.tf and a.out and 0<=a.shard<a.shards
    if a.merge:merge(a,fn,sha)
    else:study(a,fn,sha)

if __name__=="__main__":main()
