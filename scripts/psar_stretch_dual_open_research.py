#!/usr/bin/env python3
"""Frozen PSAR stretched-trend follow vs extreme fade: OPEN-to-OPEN diagnostic.
No intrabar exits, no stop/TP. This is an EXPLORATORY diagnostic, NOT deployable.
"""
import argparse,ast,glob,hashlib,json,os,re
from collections import defaultdict
from pathlib import Path
import numpy as np,pandas as pd

DATA_RUN=36095439671
BURN=100
AGES=(4,8,12)
DIST_THRESH=(3.,4.,5.)
HOLDS=(4,8)
MODES=("FOLLOW","FADE","FADE_LOCAL")
STABLE={"USDCUSDT","FDUSDUSDT","TUSDUSDT","USDPUSDT","DAIUSDT","BUSDUSDT",
"USDEUSDT","PYUSDUSDT","EURCUSDT","USD1USDT","USDDUSDT","USDXUSDT"}
CUTOFF=1735689600000
FIELDS=("n","wins20","wins40","gross_sum","pos20","neg20","pos40","neg40",
        "mae_sum","mae_over5","worst40","best40")
def canonical():
    source=Path("scripts/psar_open_canonical_compare.py").read_text()
    names={"_symbol","load","contiguous_segments","resample","psar_open_projection"}
    nodes=[node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name in names]
    assert {n.name for n in nodes}==names
    ns={"np":np,"pd":pd,"os":os,"re":re}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"<canonical-source>","exec"),ns)
    return ns,hashlib.sha256(source.encode()).hexdigest()
def checks(fn):
    x=np.arange(310,dtype=float);h=100+np.sin(x/7)*8+x*.01+2;l=h-3
    s,b=fn["psar_open_projection"](h,l)
    assert np.sum(b[1:]!=b[:-1])>3
    for i in (3,30,100,200,309):
        h2=h.copy();l2=l.copy();h2[i]+=120;l2[i]-=100
        s2,b2=fn["psar_open_projection"](h2,l2)
        assert np.allclose(s[:i+1],s2[:i+1],equal_nan=True)
        assert np.array_equal(b[:i+1],b2[:i+1])
    assert abs((110/100-1)*100-10)<1e-9
    assert abs(((110/100-1)*100)-.40-9.60)<1e-9
    assert abs(((100/110-1)*100)*(-1)-9.09090909090909)<1e-9
    assert exit_time(10,4)==14 and exit_time(10,8)==18
    assert period(CUTOFF-3600000,CUTOFF)=="EXCLUDED_CROSS"
    assert period(CUTOFF,CUTOFF+3600000)=="SEEN_VALIDATION"
    assert period(CUTOFF-7200000,CUTOFF-3600000)=="TRAIN"
    print("AUDIT_PASS open-time PSAR, current-wick invariance, return/cost, exit timestamp, split",flush=True)
def exit_time(i,hold):
    return i+hold
def period(entry,exit):
    if exit < CUTOFF:return "TRAIN"
    if entry >= CUTOFF:return "SEEN_VALIDATION"
    return "EXCLUDED_CROSS"
def empty():
    return {"n":0,"wins20":0,"wins40":0,"gross_sum":0.,"pos20":0.,"neg20":0.,
        "pos40":0.,"neg40":0.,"mae_sum":0.,"mae_over5":0,"worst40":0.,"best40":0.}
def record(d, key, vals, adverse):
    v=d[key];g=vals;net20=g-.2;net40=g-.4
    v["n"]+=len(g);v["wins20"]+=int(np.count_nonzero(net20>0));v["wins40"]+=int(np.count_nonzero(net40>0))
    v["gross_sum"]+=float(np.sum(g));v["pos20"]+=float(np.maximum(net20,0).sum())
    v["neg20"]+=float(np.maximum(-net20,0).sum());v["pos40"]+=float(np.maximum(net40,0).sum())
    v["neg40"]+=float(np.maximum(-net40,0).sum());v["mae_sum"]+=float(adverse.sum())
    v["mae_over5"]+=int(np.count_nonzero(adverse>=5))
    v["worst40"]=min(v["worst40"],float(net40.min()))
    v["best40"]=max(v["best40"],float(net40.max()))
def research(a,fn,engine_sha):
    m={"1h":4,"4h":16}[a.tf]; allfiles=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True))
    assert len(allfiles)>0
    selected=[f for j,f in enumerate(allfiles) if j%a.shards==a.shard]
    agg=defaultdict(empty)
    skipped_gap=0;skipped_short=0;skipped_split=0;raw_candidates=0;valid_entries=0;symbols=set()
    for k,file in enumerate(selected,1):
        sym=fn["_symbol"](file);symbols.add(sym)
        if sym in STABLE:continue
        t,o,h,l,c=fn["load"](file)
        for lo,hi in fn["contiguous_segments"](t):
            if hi-lo < m*(BURN+12+8):skipped_short+=1;continue
            rt,ro,rh,rl,rc=fn["resample"](t[lo:hi],o[lo:hi],h[lo:hi],l[lo:hi],c[lo:hi],m)
            n=len(rt)
            if n<BURN+20:skipped_short+=1;continue
            assert np.all(np.diff(rt)==m*900000)
            sar,bull=fn["psar_open_projection"](rh,rl)
            prevclose=np.r_[np.nan,rc[:-1]]
            tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prevclose),np.abs(rl-prevclose)))
            atrclose=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()
            atr=np.r_[np.nan,atrclose[:-1]]
            high6=pd.Series(rh).shift(1).rolling(6,min_periods=1).max().to_numpy()
            low6=pd.Series(rl).shift(1).rolling(6,min_periods=1).min().to_numpy()
            flips=np.flatnonzero(bull[1:]!=bull[:-1])+1
            starts=flips[flips>=BURN]
            ends=np.r_[starts[1:],n]
            if not len(starts):continue
            for age in AGES:
                ix=starts+age-1
                ok=(ix<ends)&(ix+max(HOLDS)<n)
                ix=ix[ok]
                if not len(ix):continue
                ab=atr[ix];sar_i=sar[ix]
                side=bull[ix];oo=ro[ix]
                dist=np.where(side,oo-sar_i,sar_i-oo)/ab
                valid=np.isfinite(ab)&(ab>0)&np.isfinite(sar_i)&np.isfinite(dist)&(dist>0)&(oo>0)
                ix=ix[valid];dist=dist[valid];side=side[valid]
                if not len(ix):continue
                local=np.where(side,ro[ix]>=high6[ix]-.25*atr[ix],
                               ro[ix]<=low6[ix]+.25*atr[ix])
                raw_candidates+=len(ix)
                for dmin in DIST_THRESH:
                    eligible=(dist>=dmin)
                    if not eligible.any():continue
                    for mode in MODES:
                        eligible_mode=eligible if mode!="FADE_LOCAL" else eligible & local
                        js=ix[eligible_mode]
                        if not len(js):continue
                        bb=side[eligible_mode]
                        direction=np.where(bb,1.,-1.)
                        if mode!="FOLLOW":direction=-direction
                        for hold in HOLDS:
                            # Exactly one theoretical trade per PSAR trend at specified age.
                            # This also prevents same-symbol overlap for a given config.
                            jj=js[js+hold<n]
                            if not len(jj):continue
                            dirn=direction[:len(jj)] # valid because max-hold eligible prefilter
                            entry=ro[jj]; ex=ro[jj+hold]
                            gross=100.0*dirn*(ex/entry-1.)
                            future_h=np.array([rh[i:i+hold].max() for i in jj])
                            future_l=np.array([rl[i:i+hold].min() for i in jj])
                            adverse=np.where(dirn>0,100*(1-future_l/entry),100*(future_h/entry-1))
                            adverse=np.maximum(adverse,0)
                            dates=np.array([period(int(rt[i]),int(rt[i+hold])) for i in jj])
                            years=np.array([pd.Timestamp(int(rt[i]),unit="ms",tz="UTC").year for i in jj])
                            for split in ("TRAIN","SEEN_VALIDATION"):
                                mask=dates==split
                                if not np.any(mask):continue
                                for b in (True,False):
                                    which=mask & (bb==b)
                                    if not np.any(which):continue
                                    base=f"{a.tf}|{mode}|{'BULL' if b else 'BEAR'}|A{age}|D{dmin:g}|H{hold}"
                                    record(agg,base+"|"+split,gross[which],adverse[which])
                                    valid_entries+=int(which.sum())
                                    for year in np.unique(years[which]):
                                        yy=which&(years==year)
                                        record(agg,base+f"|YEAR{year}",gross[yy],adverse[yy])
                                    record(agg,base+f"|SYMBOL{sym}",gross[which],adverse[which])
                            skipped_split+=int((dates=="EXCLUDED_CROSS").sum())
        print("PROGRESS",a.tf,a.shard,k,len(selected),sym,"raw",raw_candidates,flush=True)
    report={"experiment":"PSAR_EXTREME_DUAL_DIRECTIONS_V1","classification":"EXPLORATORY",
        "commit":os.getenv("GITHUB_SHA","local"),"repo":"duuu-hub/bb-scanner",
        "branch":"research-rank5-binance-15m-5y",
        "data_run":DATA_RUN,"engine_sha256":engine_sha,
        "tf":a.tf,"shard":a.shard,"shards":a.shards,"files":len(selected),"all_files":len(allfiles),
        "symbols_seen":len(symbols),"raw_candidates":raw_candidates,"entries_aggregated":valid_entries,
        "skipped_short_segments":skipped_short,"excluded_cross_split_trades":skipped_split,
        "signal":"At candle OPEN, projected canonical PSAR + prior-closed ATR14, age exactly A, signed distance >= D",
        "modes":"FOLLOW=same direction as PSAR; FADE=opposite; FADE_LOCAL=opposite AND current OPEN near last 6 completed candle favorable extreme within .25 ATR",
        "entry":"Market/taker at current timeframe OPEN, not a limit",
        "exit":"Market/taker at OPEN of candle entry+H, no TP/SL and no intra-bar fills",
        "cost":"20bp and 40bp all-in round-trip from entry notional, funding excluded",
        "no_overlap":"At most one candidate per completed PSAR trend per configuration; separate configurations are counterfactual and can overlap; no account-level sizing claimed",
        "splits":"TRAIN up to 2024-12-31 UTC, 2025+ SEEN_VALIDATION; boundary crossed excluded",
        "caveat":"No stop protection; risk metrics MAE diagnostic only. Execution/portfolio verification needed before demo.",
        "aggregates":dict(agg)}
    Path(a.out).write_text(json.dumps(report,indent=2))
    print("SHARD_PASS",a.tf,a.shard,len(agg),"keys",raw_candidates,"events",flush=True)
def metrics(v):
    n=v["n"];return {"n":n,"gross_mean":v["gross_sum"]/n if n else None,
        "net20_mean":v["gross_sum"]/n-.2 if n else None,
        "net40_mean":v["gross_sum"]/n-.4 if n else None,
        "net20_pf":v["pos20"]/v["neg20"] if v["neg20"]>0 else None,
        "net40_pf":v["pos40"]/v["neg40"] if v["neg40"]>0 else None,
        "net40_winrate_pct":100*v["wins40"]/n if n else None,
        "mean_mae_pct":v["mae_sum"]/n if n else None,
        "mae5_pct":100*v["mae_over5"]/n if n else None,
        "worst40_pct":v["worst40"] if n else None}
def merge(a,engine_sha):
    docs=[json.load(open(f)) for f in sorted(glob.glob(a.data+"/**/dual-"+a.tf+"-*.json",recursive=True))]
    assert len(docs)==a.shards,(a.tf,len(docs))
    assert {d["shard"] for d in docs}==set(range(a.shards))
    assert len({d["commit"] for d in docs})==1
    assert len({d["engine_sha256"] for d in docs})==1 and docs[0]["engine_sha256"]==engine_sha
    assert sum(d["files"] for d in docs)==docs[0]["all_files"]
    agg=defaultdict(empty)
    for doc in docs:
        for key,v in doc["aggregates"].items():
            z=agg[key]
            prev=z["n"]
            for k in ("n","wins20","wins40","gross_sum","pos20","neg20","pos40","neg40","mae_sum","mae_over5"):
                z[k]+=v[k]
            z["worst40"]=min(z["worst40"],v["worst40"])
            z["best40"]=max(z["best40"],v["best40"])
    groups={}
    for k,v in agg.items():
        if "|TRAIN" in k or "|SEEN_VALIDATION" in k:
            base,split=k.rsplit("|",1)
            if split in ("TRAIN","SEEN_VALIDATION"):
                groups.setdefault(base,{})[split]=metrics(v)
    report={"experiment":"PSAR_EXTREME_DUAL_DIRECTIONS_V1",
        "classification":"EXPLORATORY","data_run":DATA_RUN,"run_commit":docs[0]["commit"],
        "engine_sha256":engine_sha,"tf":a.tf,"shards":a.shards,
        "grid":{"ages":AGES,"dist_min_atr":DIST_THRESH,"holds_tf_bars":HOLDS,
            "modes":MODES,"side":("BULL","BEAR")},
        "caveat":"No intrabar stop/TP; market OPEN→OPEN diagnostic, 20/40bp all-in, funding excluded, overlapping counterfactual grid, not executable portfolio result",
        "all_configs":groups}
    ranked={}
    for mode in MODES:
        for side in ("BULL","BEAR"):
            key=mode+"|"+side
            opts=[]
            for conf,g in groups.items():
                if f"|{mode}|{side}|" not in conf:continue
                tr=g.get("TRAIN");va=g.get("SEEN_VALIDATION")
                if not tr or not va or tr["n"]<200 or va["n"]<100:continue
                opts.append({"config":conf,"train":tr,"seen_validation":va})
            opts.sort(key=lambda z:z["train"]["net40_mean"],reverse=True)
            ranked[key]={"grid_count_adequate":len(opts),"top_train_only":opts[:8],
                         "profitable_train40":sum(z["train"]["net40_mean"]>0 for z in opts),
                         "profitable_seen40":sum(z["seen_validation"]["net40_mean"]>0 for z in opts),
                         "both_profitable40":sum(z["train"]["net40_mean"]>0 and z["seen_validation"]["net40_mean"]>0 for z in opts)}
            for c in opts[:4]:
                print("TOP",a.tf,key,c["config"],
                    "TRAIN",round(c["train"]["net40_mean"],4),"N",c["train"]["n"],
                    "SEEN",round(c["seen_validation"]["net40_mean"],4),"N",c["seen_validation"]["n"],
                    flush=True)
    report["ranked"]=ranked
    Path(a.out).write_text(json.dumps(report,indent=2))
    print("MERGE_PASS",a.tf,len(docs),"groups",len(groups),
          "allfiles",sum(d["files"] for d in docs),flush=True)
def main():
    p=argparse.ArgumentParser();p.add_argument("--audit",action="store_true");p.add_argument("--merge",action="store_true")
    p.add_argument("--tf",choices=("1h","4h"));p.add_argument("--data",default="data")
    p.add_argument("--shard",type=int,default=0);p.add_argument("--shards",type=int,default=4);p.add_argument("--out")
    a=p.parse_args();fn,hash=canonical();checks(fn)
    if a.audit:return
    assert a.tf and a.out and a.shards>0 and 0<=a.shard<a.shards
    if a.merge:merge(a,hash)
    else:research(a,fn,hash)
if __name__=="__main__":main()
