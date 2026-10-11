#!/usr/bin/env python3
"""PSAR-independent strict multi-TF BB OPEN pump-fade: extra observed opening extension.
Every entry is MARKET at a 15M OPEN visibly >= setup OPEN +X%;
no resting limit touches assumed. One intrabar stop, optional SOFT band exit.
Exploratory, 2025+ already-seen, not demo authority.
"""
from __future__ import annotations
import os,json,glob,argparse
from datetime import datetime,timezone
from pathlib import Path
from collections import defaultdict,Counter
import pandas as pd,numpy as np
import bb_mtf_strict_open_v1 as source
import bb_mtf_extreme_entrystop_v2 as engine
BAR=source.BAR;CUTOFF=source.CUTOFF;START=source.START
GROUPS=("ALL7","DAY4","NO_DAY6")
EXTENSIONS=(0.,1.,2.,3.,5.)
WINDOWS=(4,8) # max 1H or 2H wait to observe an OPEN extension
STOPS=(5.,8.,12.)
HOLDS=(8,16,32)
EXITS=("TIME","BB15_CLOSE_REVERSION")
COOLDOWN=32
def audit():
    engine.audit()
    op=np.array([100.,100.5,102.,103.,105.,101.,100.,106.,101.])
    assert extension_entry(op,0,0.,4)==0
    assert extension_entry(op,0,1.,4)==2
    assert extension_entry(op,0,3.,4)==3
    assert extension_entry(op,0,5.,4)==4
    assert extension_entry(op,0,5.,8)==4
    assert extension_entry(op,0,6.,8)==7
    assert extension_entry(op,0,7.,8) is None
    print("PUMP_EXTENSION_AUDIT_PASS observed future candle OPEN only, no intrabar high/low entry, market-taker if gap past threshold",flush=True)
def extension_entry(op,setup,extension_pct,window):
    if extension_pct==0:return int(setup)
    target=float(op[setup])*(1+extension_pct/100.)
    last=min(len(op),setup+1+window)
    for j in range(setup+1,last):
        if float(op[j])>=target:return int(j)
    return None
def process(a):
    fn,engine_sha=source.canonical()
    fs=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert fs
    paths=[f for i,f in enumerate(fs) if i%a.shards==a.shard]
    rows=[];counters=defaultdict(int)
    for ki,file in enumerate(paths,1):
        sym=fn["_symbol"](file)
        if sym in source.STABLE:counters["stable_excluded"]+=1;continue
        t,o,h,l,c=fn["load"](file)
        for start,end in fn["contiguous_segments"](t):
            if end-start<96*25:
                counters["short_segment"]+=1;continue
            ts,op,hi,lo,cl=[x[start:end] for x in (t,o,h,l,c)]
            st=source.tf_states(ts,op,hi,lo,cl,fn)["STRICT_TF_OPEN"]
            v,u,d=source.bb_open(op,cl)
            for group in GROUPS:
                flag=engine.sideflag(st,source.COMBOS[group],"UPPER")
                fresh=np.flatnonzero(flag & ~np.r_[False,flag[:-1]])
                next_allowed=0
                for setup in fresh:
                    if int(ts[setup])<START or setup<96*20 or setup<next_allowed:continue
                    next_allowed=setup+COOLDOWN
                    counters[f"signal_{group}"]+=1
                    if setup+max(WINDOWS)+max(HOLDS)>=len(ts):
                        counters["eod_excluded"]+=1;continue
                    bb15=float(u[setup])
                    if not np.isfinite(bb15) or bb15<=0:
                        counters["bb_invalid"]+=1;continue
                    setup_distance=abs(float(op[setup])-bb15)/float(op[setup])*100
                    for ext in EXTENSIONS:
                        for window in WINDOWS:
                            j=extension_entry(op,int(setup),ext,window)
                            if j is None:
                                counters[f"no_entry_E{ext:g}_W{window}"]+=1;continue
                            if int(ts[j])-int(ts[setup])!=(j-setup)*BAR:
                                counters["entry_gap"]+=1;continue
                            for hold in HOLDS:
                                final=j+hold
                                if final>=len(ts):counters["unresolved"]+=1;continue
                                signaltime=int(ts[setup]);en=int(ts[j]);last=int(ts[final])
                                if signaltime<CUTOFF and last>=CUTOFF:
                                    counters["cross_cutoff"]+=1;continue
                                split="TRAIN" if signaltime<CUTOFF else "SEEN"
                                for sl in STOPS:
                                    for exitmode in EXITS:
                                        out=engine.simulate(ts,op,hi,lo,cl,j,"UPPER",sl,hold,exitmode,bb15)
                                        if out is None:counters["data_gap"]+=1;continue
                                        gross=float(out["gross_pct"])
                                        rows.append((sym,group,ext,window,sl,hold,exitmode,
                                          signaltime,en,int(out["exit_ts"]),float(op[j]),float(out["exit_price"]),setup_distance,
                                          gross,gross-.2,gross-.4,out["mae_pct"],out["mfe_pct"],
                                          out["reason"],split,datetime.fromtimestamp(signaltime/1000,tz=timezone.utc).year))
            counters["segments"]+=1
        print("PUMP_EXTENSION_PROGRESS",a.shard,ki,len(paths),sym,"trades",len(rows),flush=True)
    cols=["symbol","group","extension_pct","wait_bars","stop_pct","hold_bars","exit_mode","setup_ts","entry_ts","exit_ts",
          "entry","exit","setup_dist_pct","gross_pct","net20_pct","net40_pct","mae_pct","mfe_pct","exit_reason","split","year"]
    pd.DataFrame(rows,columns=cols).to_csv(a.out,index=False,compression="gzip")
    doc={"experiment":"BB_MTF_OPEN_EXTRA_PUMP_V3","class":"EXPLORATORY","source_data_run":source.RAW_RUN,
     "commit":os.getenv("GITHUB_SHA","local"),"source_engine_sha256":engine_sha,
     "shard":a.shard,"shards":a.shards,"files":len(paths),"all_files":len(fs),
     "rows":len(rows),"grid":{"groups":GROUPS,"extensions":EXTENSIONS,"windows":WINDOWS,"stops":STOPS,"holds":HOLDS,"exits":EXITS},
     "counters":dict(counters)}
    Path(a.meta).write_text(json.dumps(doc,indent=2))
    print("PUMP_EXTENSION_SHARD_PASS",a.shard,len(rows),len(paths),flush=True)
def merge(a):
    fs=sorted(glob.glob(a.data+"/**/bb-pump-shard-*.csv.gz",recursive=True))
    ms=sorted(glob.glob(a.data+"/**/bb-pump-meta-*.json",recursive=True))
    assert len(fs)==len(ms)==a.shards,(len(fs),len(ms))
    mets=[json.load(open(p)) for p in ms]
    assert {m["shard"] for m in mets}==set(range(a.shards))
    assert len({m["commit"] for m in mets})==1 and len({m["source_engine_sha256"] for m in mets})==1
    assert len({json.dumps(m["grid"],sort_keys=True) for m in mets})==1
    assert sum(m["files"] for m in mets)==mets[0]["all_files"]
    data=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
    assert len(data)==sum(m["rows"] for m in mets)
    assert not data.duplicated(["symbol","group","extension_pct","wait_bars","stop_pct","hold_bars","exit_mode","setup_ts"]).any()
    rows=[];rank=[]
    for group in GROUPS:
      for ext in EXTENSIONS:
       for window in WINDOWS:
        for sl in STOPS:
         for hold in HOLDS:
          for em in EXITS:
            xx=data[(data.group==group)&(data.extension_pct==ext)&(data.wait_bars==window)&
              (data.stop_pct==sl)&(data.hold_bars==hold)&(data.exit_mode==em)]
            tr=xx[xx.split=="TRAIN"];se=xx[xx.split=="SEEN"]
            pt=engine.perf(tr);ps=engine.perf(se)
            if not pt and not ps:continue
            key=f"{group}|E{ext:g}|W{window}|SL{sl:g}|H{hold}|{em}"
            n_days=int(tr.setup_ts.floordiv(86400000).nunique())
            r={"key":key,"group":group,"extension":ext,"window":window,"stop":sl,"hold":hold,"exit_mode":em,
               "train":pt,"seen":ps,"train_days":n_days,"seen_days":int(se.setup_ts.floordiv(86400000).nunique()),
               "train_yearly":{str(y):engine.perf(q) for y,q in tr.groupby("year")},
               "seen_yearly":{str(y):engine.perf(q) for y,q in se.groupby("year")}}
            if len(tr)>=35 and n_days>=18 and tr.year.nunique()>=3:
                r["train_account"]=engine.account(tr,.4)
                r["seen_account"]=engine.account(se,.4)
                if r["train_account"]["accepted"]>=28:
                    rank.append(r)
            rows.append(r)
    rank.sort(key=lambda x:x["train_account"]["return_pct"]-.5*x["train_account"]["realized_only_mdd_pct"],reverse=True)
    base=[]
    for group in GROUPS:
     for ext in EXTENSIONS:
      key=f"{group}|E{ext:g}|W8|SL8|H32|TIME"
      row=next((x for x in rows if x["key"]==key),None)
      if row:base.append(row)
    doc={"experiment":"BB_MTF_OPEN_EXTRA_PUMP_V3","classification":"EXPLORATORY post-hoc",
     "run_sha":mets[0]["commit"],"source_engine_sha256":mets[0]["source_engine_sha256"],
     "source_data_run":source.RAW_RUN,"source_risk_v2_run":38178524284,
     "shards":len(mets),"files":sum(m["files"] for m in mets),"rows":len(data),
     "counters":dict(sum((Counter(m["counters"]) for m in mets),Counter())),
     "grid":mets[0]["grid"],"frozen_baselines":base,"top_train_account":rank[:30],"all_grid":rows,
     "limitations":["Post-hoc exploratory using previously seen 2025+; not new OOS",
      "Extra extension only at MARKET 15m OPEN in first 1-2h, no 1m limit fill and no future price",
      "One intrabar STOP order; BB soft TP at next 15m OPEN only",
      "20/40bp roundtrip fixed notional, funding excluded; gaps worse OPEN",
      "Current-listed symbols and 15m OHLC, MTM MDD excluded, no 30 audits or 10 clean rounds"]}
    Path(a.out).write_text(json.dumps(doc,indent=2))
    for r in base:
      print("BASE",r["key"],"TRAIN",r["train"],"SEEN",r["seen"],"ACCOUNT_TRAIN",r.get("train_account"),"ACCOUNT_SEEN",r.get("seen_account"),flush=True)
    print("TOP_N",len(rank),flush=True)
    for r in rank[:14]:
      print("TOP",r["key"],"TRAIN",r["train"],"TRAIN_ACCOUNT",r["train_account"],
         "SEEN",r["seen"],"SEEN_ACCOUNT",r["seen_account"],flush=True)
    print("PUMP_EXTENSION_MERGE_PASS",len(data),len(mets),"shards",len(rows),"configs",flush=True)
def main():
    p=argparse.ArgumentParser();p.add_argument("--audit",action="store_true")
    p.add_argument("--merge",action="store_true");p.add_argument("--data",default="data")
    p.add_argument("--out");p.add_argument("--meta");p.add_argument("--shards",type=int,default=4)
    p.add_argument("--shard",type=int,default=0)
    a=p.parse_args();audit()
    if a.audit:return
    assert a.out and a.shards>0
    if a.merge:merge(a)
    else:
        assert a.meta and a.shard>=0 and a.shard<a.shards
        process(a)
if __name__=="__main__":main()
