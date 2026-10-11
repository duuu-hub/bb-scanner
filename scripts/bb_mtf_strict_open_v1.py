#!/usr/bin/env python3
"""Causal multi-timeframe BB opening extremes, fade vs continuation.
Exploratory OPEN-to-OPEN forward-return diagnostic ONLY; no hard stops,
no intrabar TP/SL, no portfolio, no claim of executable alpha.
"""
import argparse,ast,glob,hashlib,json,os,re
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone
import numpy as np,pandas as pd

RAW_RUN=36095439671
BAR=900000
CUTOFF=1735689600000
START=1609459200000
BURN_TF=19
SIGMA=2.
STABLE={"USDCUSDT","FDUSDUSDT","TUSDUSDT","USDPUSDT","DAIUSDT","BUSDUSDT","USDEUSDT","PYUSDUSDT","EURCUSDT","USD1USDT","USDDUSDT","USDXUSDT"}
TF={"1D":96,"12H":48,"4H":16,"2H":8,"1H":4,"30M":2,"15M":1}
COMBOS={
 "ALL7":("1D","12H","4H","2H","1H","30M","15M"),
 "DAY4":("1D","4H","1H","15M"),
 "NO_DAY6":("12H","4H","2H","1H","30M","15M"),
 "FAST3":("4H","1H","15M"),
 "HOUR2":("1H","15M")
}
MODES=("STRICT_TF_OPEN","LIVE_15M_OPEN")
SIDES=("UPPER","LOWER")
DIRECTIONS=("FADE","FOLLOW")
HOLDS=(4,16,32)
COOLDOWN=32

def canonical():
    src=Path("scripts/psar_open_canonical_compare.py").read_text()
    names={"_symbol","load","contiguous_segments","resample"}
    nodes=[x for x in ast.parse(src).body if isinstance(x,ast.FunctionDef) and x.name in names]
    assert len(nodes)==len(names)
    ns={"np":np,"pd":pd,"re":re,"os":os}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"<canonical>", "exec"),ns)
    return ns,hashlib.sha256(src.encode()).hexdigest()

def bb_open(rop,rclose):
    """Band visible at TF bar's exact OPEN: 19 prior closed TF candles + OPEN.
    This matches chart BB20 at OPEN, not previous-20-closed band."""
    x=pd.Series(rclose)
    sm=x.shift(1).rolling(19,min_periods=19).sum().to_numpy()
    sq=(x*x).shift(1).rolling(19,min_periods=19).sum().to_numpy()
    basis=(sm+rop)/20.
    stdev=np.sqrt(np.maximum(0.,(sq+rop*rop)/20.-basis*basis))
    upper=basis+SIGMA*stdev;lower=basis-SIGMA*stdev
    valid=np.isfinite(upper)&np.isfinite(lower)&(upper>0)&(lower>0)
    return valid,upper,lower

def live_tf(ts,p15,times,close,step):
    """At current 15M OPEN, 19 completed TF closes plus currently known 15M OPEN.
    TF closed candle is eligible only if its close timestamp <= current OPEN.
    """
    period=step*BAR
    closes=times+period
    idx=np.searchsorted(closes,ts,side="right")
    ok=(idx>=19)
    upper=np.full(len(ts),np.nan)
    lower=np.full(len(ts),np.nan)
    if not ok.any():return np.zeros(len(ts),bool),upper,lower
    j=idx[ok]
    sample=np.flatnonzero(ok)
    expected_last=(ts[sample]//period)*period
    ok_cont=(closes[j-1]==expected_last)&((closes[j-1]-closes[j-19])==18*period)
    sample=sample[ok_cont];j=j[ok_cont]
    if not len(sample):return np.zeros(len(ts),bool),upper,lower
    p=np.r_[0.,np.cumsum(close,dtype=float)]
    q=np.r_[0.,np.cumsum(close*close,dtype=float)]
    mean=(p[j]-p[j-19]+p15[sample])/20.
    var=np.maximum(0.,(q[j]-q[j-19]+p15[sample]**2)/20.-mean*mean)
    upper[sample]=mean+SIGMA*np.sqrt(var)
    lower[sample]=mean-SIGMA*np.sqrt(var)
    valid=np.isfinite(upper)&np.isfinite(lower)&(upper>0)&(lower>0)
    return valid,upper,lower

def tf_states(ts,op,hi,lo,cl,fn):
    n=len(ts)
    state={key:{} for key in MODES}
    for name,m in TF.items():
        if m==1:rt,ro,rc=ts,op,cl
        else:
            rt,ro,rh,rl,rc=fn["resample"](ts,op,hi,lo,cl,m)
        if not len(rt):
            for mode in MODES:state[mode][name]=(np.zeros(n,bool),np.zeros(n,bool),np.zeros(n,bool))
            continue
        v,u,d=bb_open(ro,rc)
        if m==1:pos=np.arange(n,dtype=np.int64)
        else:pos=np.searchsorted(rt,ts,side="right")-1
        pv=np.maximum(0,pos)
        covered=(pos>=0)&(ts<rt[pv]+m*BAR)&(ts>=rt[pv])
        current_valid=covered&v[pv]
        up=current_valid&(ro[pv]>u[pv])
        dn=current_valid&(ro[pv]<d[pv])
        state["STRICT_TF_OPEN"][name]=(current_valid,up,dn)
        valid_l,ul,dl=live_tf(ts,op,rt,rc,m)
        state["LIVE_15M_OPEN"][name]=(valid_l,valid_l&(op>ul),valid_l&(op<dl))
    return state

def smoke(fn):
    close=np.ones(60)*100
    op=np.ones(60)*100
    v,u,l=bb_open(op,close)
    assert not v[18] and v[19]
    assert not np.any(op[v]>u[v]) and not np.any(op[v]<l[v])
    op2=op.copy();op2[20]=200.
    v2,u2,l2=bb_open(op2,close)
    assert v2[20] and u2[20]<200
    # Crucial: changing current candle's future CLOSE does not change its opening band.
    c2=close.copy();c2[20]=500.
    v3,u3,l3=bb_open(op2,c2)
    assert u[19]==u3[19] and u2[20]==u3[20] and l2[20]==l3[20]
    # Separate observation: TF OPEN != current 15m OPEN.
    times=np.arange(100,dtype=np.int64)*4*BAR
    trend=np.arange(100,dtype=float)+100
    eval_ts=np.arange(4*80*BAR,4*81*BAR,BAR,dtype=np.int64)
    valid,lup,llo=live_tf(eval_ts,np.array([1000.,1000.,1000.,1000.]),times,trend,4)
    assert len(valid)==4 and valid.all() and (lup<1000).all()
    assert START<CUTOFF
    assert all(h<=COOLDOWN for h in HOLDS)
    # TF open-time projection does NOT reference future H/L/C.
    print("AUDIT_PASS 19 prior TF closes + TF observed OPEN, causal BB20x2, 15M live separately, 1H/4H/1D align, 32bar de-dupe",flush=True)

def empty():
    return {"n":0,"net20_sum":0.,"net40_sum":0.,"gross_sum":0.,"wins40":0,
            "profit20":0.,"loss20":0.,"profit40":0.,"loss40":0.,
            "worst40":0.,"best40":0.,"mae_sum":0.,"mae_over5":0}
def record(v,gross,mae):
    n20=gross-.20;n40=gross-.40
    v["n"]+=1;v["gross_sum"]+=gross;v["net20_sum"]+=n20;v["net40_sum"]+=n40
    v["wins40"]+=int(n40>0)
    v["profit20"]+=max(n20,0);v["loss20"]+=max(-n20,0)
    v["profit40"]+=max(n40,0);v["loss40"]+=max(-n40,0)
    v["worst40"]=min(v["worst40"],n40);v["best40"]=max(v["best40"],n40)
    v["mae_sum"]+=mae;v["mae_over5"]+=int(mae>=5.)

def analyze(args,fn,engine):
    files=sorted(glob.glob(args.data+"/**/*.csv.gz",recursive=True))
    assert len(files)>0
    selected=[f for i,f in enumerate(files) if i%args.shards==args.shard]
    agg=defaultdict(empty);first=defaultdict(int);year_agg=defaultdict(empty)
    counts={"files":len(selected),"segments":0,"excluded_stable":0,"excluded_short":0,
            "censored_at_eod":0,"cross_split":0,"events_first_total":0,"events_cooldown_total":0}
    for fi,file in enumerate(selected,1):
        sym=fn["_symbol"](file)
        if sym in STABLE:counts["excluded_stable"]+=1;continue
        t,o,h,l,c=fn["load"](file)
        for st,en in fn["contiguous_segments"](t):
            if en-st<96*25:
                counts["excluded_short"]+=1
                continue
            ts,op,hi,lo,cl=(x[st:en] for x in (t,o,h,l,c))
            counts["segments"]+=1
            state=tf_states(ts,op,hi,lo,cl,fn)
            for mode in MODES:
                for combo,frames in COMBOS.items():
                    v=np.ones(len(ts),bool);u=np.ones(len(ts),bool);d=np.ones(len(ts),bool)
                    for tf in frames:
                        vv,uu,dd=state[mode][tf]
                        v&=vv;u&=uu;d&=dd
                    for side,flag in [("UPPER",v&u),("LOWER",v&d)]:
                        # signal at 15M OPEN; require 15M candle opened OUTSIDE by construction
                        prev=np.r_[False,flag[:-1]]
                        fresh=np.flatnonzero(flag&~prev)
                        counts["events_first_total"]+=len(fresh)
                        cooldown_next=-1
                        for i in fresh:
                            current=int(ts[i])
                            if current<START or i<96*20:continue
                            if i<cooldown_next:continue
                            cooldown_next=int(i)+COOLDOWN
                            counts["events_cooldown_total"]+=1
                            first[f"{mode}|{combo}|{side}"]+=1
                            if i+max(HOLDS)>=len(ts):
                                counts["censored_at_eod"]+=1;continue
                            for hold in HOLDS:
                                end=int(i)+hold
                                if int(ts[end])-current!=hold*BAR:
                                    raise RuntimeError("noncontiguous parent bars across signal horizon")
                                split=("TRAIN" if int(ts[end])<CUTOFF else "SEEN" if current>=CUTOFF else "CROSS")
                                if split=="CROSS":
                                    counts["cross_split"]+=1
                                    continue
                                ret=(float(op[end])/float(op[i])-1)*100
                                fa=(-ret if side=="UPPER" else ret)
                                maefade=((float(np.max(hi[i:end]))/op[i]-1)*100 if side=="UPPER" else
                                          (1-float(np.min(lo[i:end]))/op[i])*100)
                                maefade=max(0,maefade)
                                maefollow=((1-float(np.min(lo[i:end]))/op[i])*100 if side=="UPPER" else
                                            (float(np.max(hi[i:end]))/op[i]-1)*100)
                                maefollow=max(0,maefollow)
                                yr=datetime.fromtimestamp(current/1000,tz=timezone.utc).year
                                for direction,g,mae in (("FADE",fa,maefade),("FOLLOW",-fa,maefollow)):
                                    key=f"{mode}|{combo}|{side}|{direction}|H{hold}|{split}"
                                    record(agg[key],g,mae)
                                    record(year_agg[f"{mode}|{combo}|{side}|{direction}|H{hold}|Y{yr}"],g,mae)
        print("BB_MTF_PROGRESS",args.shard,fi,len(selected),sym,"valid_events",counts["events_cooldown_total"],flush=True)
    doc={"experiment":"BB_MTF_STRICT_OPEN_CONFLUENCE_V1","classification":"EXPLORATORY",
         "repo":"duuu-hub/bb-scanner","branch":"research-rank5-binance-15m-5y",
         "commit":os.getenv("GITHUB_SHA","local"),"engine_sha256":engine,"data_run":RAW_RUN,
         "shard":args.shard,"shards":args.shards,"all_files":len(files),"counts":counts,
         "grid":{"bands":"BB(20,2) first 19 completed prior TF closes + current observed OPEN",
                  "modes":MODES,"combos":COMBOS,"sides":SIDES,"directions":DIRECTIONS,"exit_after_15m_bars":HOLDS,
                  "freshness":"newly-entered qualifying condition at current 15m OPEN","cooldown_bars":COOLDOWN},
         "observability":"STRICT uses each active TF candle's OWN actual opening; LIVE uses current 15M opening price versus 19 completed bars + live opening for EACH TF. No TF candle future H/L/C in features.",
         "execution":"hypothetical market at signal 15M OPEN, exit market at next 15M OPEN after H bars, no intrabar TP or stop",
         "fees":"all-in 20bp and 40bp roundtrip from notional; funding excluded",
         "split":"TRAIN 2021-2024, seen 2025-2026, excluded crossing full holding horizon",
         "caveat":"gross MAE is within-hold stress metric only, NO stop/TP, NO account portfolio, current-listed universe survivorship possible",
         "first_events":dict(first),"aggregates":dict(agg),"year_aggregates":dict(year_agg)}
    Path(args.out).write_text(json.dumps(doc))
    print("BB_MTF_SHARD_PASS",args.shard,"files",len(selected),"signals",counts["events_cooldown_total"],flush=True)

def stats(x):
    n=x["n"]
    if n==0:return {}
    return {"n":n,"net20":round(x["net20_sum"]/n,5),"net40":round(x["net40_sum"]/n,5),
            "PF40":round(x["profit40"]/x["loss40"],4) if x["loss40"] else None,
            "WR40":round(100*x["wins40"]/n,3),
            "MAE_mean":round(x["mae_sum"]/n,4),
            "MAE_over5_pct":round(100*x["mae_over5"]/n,3),
            "worst40":round(x["worst40"],4)}
def merge(args,engine):
    fs=sorted(glob.glob(args.data+"/**/bb-mtf-shard-*.json",recursive=True))
    assert len(fs)==args.shards,(args.shards,len(fs))
    ds=[json.load(open(f)) for f in fs]
    assert {d["shard"] for d in ds}==set(range(args.shards))
    assert len({d["commit"] for d in ds})==1
    assert len({d["engine_sha256"] for d in ds})==1 and ds[0]["engine_sha256"]==engine
    assert len({json.dumps(d["grid"],sort_keys=True) for d in ds})==1
    assert len({d["all_files"] for d in ds})==1
    assert sum(d["counts"]["files"] for d in ds)==ds[0]["all_files"]
    merged=defaultdict(empty)
    for d in ds:
        for key,x in d["aggregates"].items():
            a=merged[key]
            for field in a:
                if field in ("worst40","best40"):continue
                a[field]+=x[field]
            if x["n"]:
                a["worst40"]=min(a["worst40"],x["worst40"])
                a["best40"]=max(a["best40"],x["best40"])
    rows=[]
    for mode in MODES:
        for combo in COMBOS:
            for side in SIDES:
                for direction in DIRECTIONS:
                    for h in HOLDS:
                        base=f"{mode}|{combo}|{side}|{direction}|H{h}"
                        tr=merged.get(base+"|TRAIN",empty())
                        seen=merged.get(base+"|SEEN",empty())
                        rows.append({"config":base,"mode":mode,"combo":combo,"side":side,
                            "direction":direction,"hold_15m_bars":h,
                            "train":stats(tr),"seen":stats(seen)})
    ranked=[x for x in rows if x["train"].get("n",0)>=100 and x["seen"].get("n",0)>=50]
    ranked.sort(key=lambda x:(x["train"]["net40"],x["train"]["PF40"] or 0),reverse=True)
    report={"experiment":"BB_MTF_STRICT_OPEN_CONFLUENCE_V1","classification":"EXPLORATORY",
      "run_sha":ds[0]["commit"],"raw_run":RAW_RUN,"engine_sha256":engine,"shards":len(ds),
      "files":sum(d["counts"]["files"] for d in ds),
      "count_summary":{k:sum(d["counts"][k] for d in ds) for k in ds[0]["counts"]},
      "all_results":rows,"train_ranked_adequate":ranked[:40],
      "disclaimer":"OPEN-to-OPEN signal screen, NOT protective TP/SL backtest or executable portfolio. 2025-26 previously seen. Costs 20/40bp roundtrip. Funding omitted. Historic universe selection biases possible.",
      "grid":ds[0]["grid"]}
    Path(args.out).write_text(json.dumps(report,indent=2))
    for row in rows:
        if row["mode"]=="STRICT_TF_OPEN" and row["combo"] in ("ALL7","DAY4","FAST3") and row["hold_15m_bars"]==16:
            print("COMPARE",row["config"],"TRAIN",row["train"],"SEEN",row["seen"],flush=True)
    print("TOP_N",len(ranked),"min_count_train100_seen50",flush=True)
    for row in ranked[:16]:
        print("TOP",row["config"],"TRAIN",row["train"],"SEEN",row["seen"],flush=True)
    print("BB_MTF_MERGE_PASS",len(ds),sum(d["counts"]["files"] for d in ds),"files",flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--audit",action="store_true")
    ap.add_argument("--merge",action="store_true");ap.add_argument("--data",default="data")
    ap.add_argument("--out");ap.add_argument("--shard",type=int,default=0)
    ap.add_argument("--shards",type=int,default=4);args=ap.parse_args()
    fn,engine=canonical();smoke(fn)
    if args.audit:return
    assert args.out and args.shards>0 and 0<=args.shard<args.shards
    if args.merge:merge(args,engine)
    else:analyze(args,fn,engine)
if __name__=="__main__":main()
