#!/usr/bin/env python3
"""Exploratory age x prior-bar PSAR distance x future favorable-extreme probability.
No orders/execution; target is descriptive, observable predictors at current TF OPEN.
"""
import argparse,ast,glob,hashlib,json,os,re
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
import pandas as pd
RAW_RUN=36095439671
AGES=(2,4,6,8,12,16,24,32)
DBINS=(".0-.5",".5-1","1-2","2-3","3-5","5+")
STABLE={"USDCUSDT","FDUSDUSDT","TUSDUSDT","USDPUSDT","DAIUSDT","BUSDUSDT","USDEUSDT","PYUSDUSDT","EURCUSDT","USD1USDT","USDDUSDT","USDXUSDT"}
BURN=100
CUTOFF=1735689600000
HEADERS=("n","new","new4","new8","flip4","flip8","new_gain_sum","remain_bars_sum")
def canon():
    source=Path("scripts/psar_open_canonical_compare.py").read_text()
    names={"_symbol","load","contiguous_segments","resample","psar_open_projection"}
    tree=ast.parse(source)
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes}==names
    ns={"np":np,"pd":pd,"os":os,"re":re}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"<canonical>","exec"),ns)
    return ns,hashlib.sha256(source.encode()).hexdigest()
def validate(fn):
    xs=np.arange(370,dtype=float)
    hh=100+9*np.sin(xs/12)+1+xs*.03;ll=hh-2
    ps,bb=fn["psar_open_projection"](hh,ll)
    assert (bb[1:]!=bb[:-1]).sum()>5
    for i in [3,30,100,210,369]:
        hh2=hh.copy();ll2=ll.copy()
        hh2[i]+=88;ll2[i]-=70
        ps2,bb2=fn["psar_open_projection"](hh2,ll2)
        assert np.allclose(ps[:i+1],ps2[:i+1],equal_nan=True)
        assert np.array_equal(bb[:i+1],bb2[:i+1])
    assert is_new(np.array([10.,11.,12.]),np.array([8.,9.,10.]),np.array([14.,13.]),np.array([9.,10.]),True)
    assert is_new(np.array([10.,11.,12.]),np.array([8.,9.,10.]),np.array([10.,9.]),np.array([10.,11.]),False) is False
    assert distance_bin(.01)==0 and distance_bin(.5)==1 and distance_bin(5.0)==5
    assert period(1735689599000,1735689599999)=="TRAIN"
    assert period(CUTOFF,CUTOFF+1000)=="SEEN_VALIDATION"
    print("AUDIT_PASS PSAR causal + feature definition + time split + bin edges",flush=True)
def distance_bin(x):
    return int(np.searchsorted([.5,1.,2.,3.,5.],x,side="right"))
def period(a,b):
    return "TRAIN" if b<CUTOFF else "SEEN_VALIDATION" if a>=CUTOFF else "CROSS_SPLIT"
def is_new(past_h,past_l,fut_h,fut_l,bull):
    if bull:return bool(np.max(fut_h)>np.max(past_h))
    return bool(np.min(fut_l)<np.min(past_l))
def update(agg,key,new,next4,next8,flip4,flip8,gain,remaining):
    r=agg[key]
    r["n"]+=1;r["new"]+=int(new);r["new4"]+=int(next4);r["new8"]+=int(next8)
    r["flip4"]+=int(flip4);r["flip8"]+=int(flip8)
    r["new_gain_sum"]+=gain;r["remain_bars_sum"]+=remaining
def mk():
    return defaultdict(lambda:{k:0 for k in HEADERS})
def study(a,fn,enghash):
    m={"1h":4,"4h":16}[a.tf]
    allf=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert allf
    # Check symbol+interval overlap in entire fixed data collection before sharding.
    bysymbol={}
    for f in allf:
        sym=fn["_symbol"](f)
        t=fn["load"](f)[0]
        lo,hi=int(t[0]),int(t[-1])
        for plo,phi,old in bysymbol.get(sym,[]):
            if max(plo,lo)<=min(phi,hi):raise RuntimeError(f"overlap {sym}: {f} {old}")
        bysymbol.setdefault(sym,[]).append((lo,hi,f))
    selected=[f for i,f in enumerate(allf) if i%a.shards==a.shard]
    agg=mk(); events=0; excludedatr=0; excluded_seg=0; sample_count=0
    for file in selected:
        symbol=fn["_symbol"](file)
        t,o,h,l,c=fn["load"](file)
        for first,last in fn["contiguous_segments"](t):
            if last-first<m*(BURN+33):excluded_seg+=1;continue
            rt,ro,rh,rl,rc=fn["resample"](t[first:last],o[first:last],h[first:last],l[first:last],c[first:last],m)
            if len(rt)<=BURN+33:excluded_seg+=1;continue
            sar,bull=fn["psar_open_projection"](rh,rl)
            preclose=np.r_[np.nan,rc[:-1]]
            tr=np.maximum(rh-rl,np.maximum(abs(rh-preclose),abs(rl-preclose)))
            atrclosed=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()
            atr=np.r_[np.nan,atrclosed[:-1]]
            starts=np.flatnonzero(bull[1:]!=bull[:-1])+1
            starts=[int(j) for j in starts if j>=BURN]
            for s,e in zip(starts[:-1],starts[1:]):
                events+=1
                side="BULL" if bull[s] else "BEAR"
                split=period(int(rt[s]),int(rt[e-1]))
                if e-s<2:continue
                for age in AGES:
                    if e-s<age:continue
                    i=s+age-1
                    if not (np.isfinite(atr[i]) and atr[i]>0 and np.isfinite(sar[i]) and ro[i]>0):
                        excludedatr+=1;continue
                    d=abs(float(ro[i])-float(sar[i]))/atr[i]
                    if not np.isfinite(d):excludedatr+=1;continue
                    oldhi=np.max(rh[s:i]);oldlo=np.min(rl[s:i])
                    new=is_new(rh[s:i],rl[s:i],rh[i:e],rl[i:e],bool(bull[s]))
                    end4=min(i+4,e);end8=min(i+8,e)
                    n4=is_new(rh[s:i],rl[s:i],rh[i:end4],rl[i:end4],bool(bull[s]))
                    n8=is_new(rh[s:i],rl[s:i],rh[i:end8],rl[i:end8],bool(bull[s]))
                    favorable_future=float(np.max(rh[i:e]) if bull[s] else np.min(rl[i:e]))
                    gain=max(0., (favorable_future/oldhi-1)*100 if bull[s] else (1-favorable_future/oldlo)*100)
                    assert not n4 or new
                    assert not n8 or new
                    remaining=e-i
                    flip4=remaining<=4;flip8=remaining<=8
                    db=DBINS[distance_bin(d)]
                    cohorts=("ALL","NON_STABLE") if symbol not in STABLE else ("ALL",)
                    for cohort in cohorts:
                        for dgrp in ("ALL",db):
                            key="|".join([a.tf,cohort,split,side,str(age),dgrp])
                            update(agg,key,new,n4,n8,flip4,flip8,gain,remaining)
                    sample_count+=1
        print("PROGRESS",a.tf,a.shard,symbol,"completed_events",events,"sample_count",sample_count,flush=True)
    out=dict(experiment="PSAR_AGE_DISTANCE_BREAKOUT_20261008",classification="EXPLORATORY",
        branch="research-rank5-binance-15m-5y",git_sha=os.getenv("GITHUB_SHA","local"),
        data_run=RAW_RUN,tf=a.tf,shard=a.shard,shards=a.shards,all_files=len(allf),
        files=len(selected),events=events,samples=sample_count,excluded_atr=excludedatr,
        excluded_segments=excluded_seg,canon_sha256=enghash,
        record_definition="at TF bar OPEN age A and |OPEN-PSAR_OPEN| / ATR14_previous_closed",
        target="ANY subsequent trend-direction high/low wick exceeding the maximum favorable wick seen prior to current bar, before next PSAR reversal",
        horizon="from current TF bar inclusive until next opposite PSAR OPEN, earliest reversal terminates",
        position="NONE; all probabilities are descriptive not execution or PnL",
        costs="N/A, no fills",uncertainty="non-PTI surviving-symbol universe; correlated market events; 2025-2026 seen",
        aggregates=dict(agg))
    Path(a.out).write_text(json.dumps(out,ensure_ascii=False))
    print("SHARD_DONE",a.tf,a.shard,"events",events,"samples",sample_count,"groups",len(agg),flush=True)
def merge(a,enghash):
    files=sorted(glob.glob(a.data+"/**/psar-age-dist-"+a.tf+"-*.json",recursive=True))
    assert len(files)==a.shards,files
    docs=[json.load(open(x)) for x in files]
    assert {d["shard"] for d in docs}==set(range(a.shards))
    assert len({d["git_sha"] for d in docs})==1
    assert len({d["canon_sha256"] for d in docs})==1
    assert docs[0]["canon_sha256"]==enghash
    assert len({d["all_files"] for d in docs})==1
    assert sum(d["files"] for d in docs)==docs[0]["all_files"]
    acc=mk()
    for d in docs:
        for key,vals in d["aggregates"].items():
            row=acc[key]
            for col in HEADERS:row[col]+=vals[col]
    def derived(key,v):
        tf,cohort,split,side,age,db=key.split("|")
        n=v["n"]
        assert 0<=v["new4"]<=v["new8"]<=v["new"]<=n
        return dict(tf=tf,cohort=cohort,split=split,side=side,age=int(age),distance_bin=db,
            n=n,p_any_new=round(100*v["new"]/n,3),p_new_next4=round(100*v["new4"]/n,3),
            p_new_next8=round(100*v["new8"]/n,3),p_flip_next4=round(100*v["flip4"]/n,3),
            p_flip_next8=round(100*v["flip8"]/n,3),
            avg_further_move_pct=round(v["new_gain_sum"]/n,5),
            avg_candles_until_flip=round(v["remain_bars_sum"]/n,3))
    # Pool across previous splits for descriptive ALL; keep split-specific rows for stability.
    for key,v in list(acc.items()):
        parts=key.split("|");parts[2]="ALL_PERIODS";pooled="|".join(parts)
        dst=acc[pooled]
        for col in HEADERS:dst[col]+=v[col]
    records=sorted([derived(k,v) for k,v in acc.items()],key=lambda r:(r["cohort"],r["side"],r["age"],r["distance_bin"],r["split"]))
    assert records
    rep=dict(experiment="PSAR_AGE_DISTANCE_BREAKOUT_20261008",
        classification="EXPLORATORY",tf=a.tf,shards=a.shards,
        repo="duuu-hub/bb-scanner",data_artifact_run=RAW_RUN,engine_sha256=enghash,
        run_commit_sha=docs[0]["git_sha"],rows=records,
        samples=sum(d["samples"] for d in docs),events=sum(d["events"] for d in docs),
        excluded_atr=sum(d["excluded_atr"] for d in docs),
        limitations=["conditional breakout is within a completed PSAR run, uses future solely as LABEL not a contemporaneous predictor",
            "age and distance at decision time use only prior-closed data plus current OPEN",
            "distance bins chosen before viewing result; future optimization needs new forward",
            "no TP/SL or execution simulated; results cannot establish profitable trading edge",
            "stable pair exclusions are exploratory sensitivity, not a preexisting universe screen",
            "point-in-time listing survivorship bias possible; shared symbols correlated"])
    Path(a.out).write_text(json.dumps(rep,ensure_ascii=False,indent=2))
    for cohort in ("ALL","NON_STABLE"):
        for side in ("BULL","BEAR"):
            for age in (4,8,12,16):
                subset=[z for z in records if z["cohort"]==cohort and z["split"]=="ALL_PERIODS" and z["side"]==side and z["age"]==age]
                base=next((z for z in subset if z["distance_bin"]=="ALL"),None)
                bydist=[z for z in subset if z["distance_bin"]!="ALL"]
                if base:
                    print("SUMMARY",cohort,a.tf,side,"age",age,"n",base["n"],
                         "future_breakout%",base["p_any_new"],"next4%",base["p_new_next4"],
                         "distance_by_bin",[(z["distance_bin"],z["n"],z["p_any_new"]) for z in bydist],flush=True)
    print("MERGE_PASS",a.tf,len(files),"events",rep["events"],"samples",rep["samples"])
def main():
    p=argparse.ArgumentParser();p.add_argument("--tf",choices=("1h","4h"))
    p.add_argument("--data",default="data");p.add_argument("--out")
    p.add_argument("--shard",type=int,default=0);p.add_argument("--shards",type=int,default=4)
    p.add_argument("--audit-only",action="store_true");p.add_argument("--merge",action="store_true")
    a=p.parse_args()
    fn,sha=canon();validate(fn)
    if a.audit_only:return
    assert a.tf and a.out and a.shards>0 and 0<=a.shard<a.shards
    if a.merge:merge(a,sha)
    else:study(a,fn,sha)
if __name__=="__main__":main()
