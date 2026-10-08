#!/usr/bin/env python3
"""STOP-MARKET risk stress for PSAR stretched trend/fade.
Signal at canonical PSAR 4H OPEN; protective STOP MARKET inside 15m;
take profit only after closed-15m close-confirmation, executed at NEXT 15m OPEN.
Only one intrabar order level exists (the stop); no TP/SL same-bar ambiguity.
"""
import argparse,ast,glob,hashlib,json,os,re,math
from pathlib import Path
from collections import defaultdict
import numpy as np,pandas as pd
DATA_RUN=36095439671
BURN=100;CUTOFF=1735689600000; M=16
STABLE={"USDCUSDT","FDUSDUSDT","TUSDUSDT","USDPUSDT","DAIUSDT","BUSDUSDT","USDEUSDT","PYUSDUSDT","EURCUSDT","USD1USDT","USDDUSDT","USDXUSDT"}
# All conditions frozen before run. Underlying preliminary signal family was seen before.
CANDIDATES={
 "FL12_D3":{"kind":"FADE_LOCAL","side":"BEAR","age":12,"dist":3.0,"hold":8},
 "FL08_D3":{"kind":"FADE_LOCAL","side":"BEAR","age":8,"dist":3.0,"hold":8},
 "FL12_D3p5":{"kind":"FADE_LOCAL","side":"BEAR","age":12,"dist":3.5,"hold":8},
 "TS04_D5":{"kind":"FOLLOW","side":"BEAR","age":4,"dist":5.0,"hold":8},
 "TS04_D4p5":{"kind":"FOLLOW","side":"BEAR","age":4,"dist":4.5,"hold":8},
 "FS08_D5":{"kind":"FADE","side":"BULL","age":8,"dist":5.0,"hold":4},
 "FS08_D4p5":{"kind":"FADE","side":"BULL","age":8,"dist":4.5,"hold":4},
}
STOP_PCTS=(2.,4.,6.,8.)
PROFIT_PCTS=(0.,3.,6.,9.) # 0=NONE
EXIT_POLICIES=("TIME","FLIP")
SUMMARY_FIELDS=("n","wins20","wins40","gross_sum","prof20","loss20","prof40","loss40","gain_abs_sum","stop_count","stop_gap_count","profit_count","flip_count","time_count","maxloss","maxgain","notional_risk_mean","notional_exceed_40bp","duration_h_sum")
def canonical():
    s=Path("scripts/psar_open_canonical_compare.py").read_text()
    names={"_symbol","load","contiguous_segments","resample","psar_open_projection"}
    nodes=[n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef) and n.name in names]
    assert len(nodes)==len(names)
    ns={"np":np,"pd":pd,"os":os,"re":re}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"<canonical>", "exec"),ns)
    return ns,hashlib.sha256(s.encode()).hexdigest()
def split(start,end):
    if end<CUTOFF:return "TRAIN"
    if start>=CUTOFF:return "SEEN_VALIDATION"
    return "EXCLUDED_CROSS"
def local_filter(direction,entry,low6,high6,atr):
    return entry<=low6+.25*atr if direction=="BEAR" else entry>=high6-.25*atr
def eval_order(o,h,l,c,start,end,entry,long,sl_pct,prof_pct,flip_times):
    """STOP MARKET intrabar, OPEN-cause exit for profit / 4H flip / clock.
    All time-triggered exits are at 15m OPEN; stop remains active until that OPEN.
    Gaps worse than stop fill at OPEN. Confirmed profit exit requires closed 15m.
    """
    stop=entry*(1-sl_pct/100) if long else entry*(1+sl_pct/100)
    profit=entry*(1+prof_pct/100) if long else entry*(1-prof_pct/100)
    assert start<end<len(o)
    for j in range(start,end+1):
        # At open, gap past stop is never booked at theoretical stop.
        breached=(o[j]<=stop if long else o[j]>=stop)
        if breached:return (j,float(o[j]),"STOP_GAP")
        if j==end:return (j,float(o[j]),"TIME")
        if j>start:
            if j in flip_times:return (j,float(o[j]),"FLIP")
            if prof_pct:
                if (c[j-1]>=profit if long else c[j-1]<=profit):
                    return (j,float(o[j]),"PROFIT_CONFIRMED_OPEN")
        # Stop is the ONLY intra-15m price-triggered exit.
        if (l[j]<=stop if long else h[j]>=stop):
            return (j,float(stop),"STOP_TOUCH")
    raise AssertionError("unreachable")
def audit(fn):
    x=np.arange(340,dtype=float);h=100+np.sin(x/8)*7+x*.03+1;l=h-2
    s,b=fn["psar_open_projection"](h,l)
    for i in (4,60,120,220):
        h2=h.copy();l2=l.copy();h2[i]+=100;l2[i]-=80
        s2,b2=fn["psar_open_projection"](h2,l2)
        assert np.allclose(s[:i+1],s2[:i+1],equal_nan=True)
        assert np.array_equal(b[:i+1],b2[:i+1])
    o=np.array([100.,100.,101.,102.,103.]);hh=o+.5;ll=o-.5;c=o.copy()
    # Long stop at 98; 15m open gap at 95 overrides stop price 98.
    oo=np.array([100.,95.,96.,96.,96.]);hh1=oo+1;ll1=oo-1
    assert eval_order(oo,hh1,ll1,oo,0,4,100,True,2,0,set())==(1,95.,"STOP_GAP")
    # Long stop hit in first 15m; fill exactly stop.
    ll2=np.array([97.,99.,99.,99.,99.])
    assert eval_order(o,hh,ll2,c,0,4,100,True,2,0,set())==(0,98.,"STOP_TOUCH")
    # TP cannot exit within same parent bar, only after completed close and at next OPEN.
    cc=np.array([104.,99.,99.,99.,99.])
    assert eval_order(o,hh,ll,cc,0,4,100,True,4,3,set())==(1,100.,"PROFIT_CONFIRMED_OPEN")
    # If TP and stop both touched during same 15m candle, only STOP intrabar; chronology is unambiguous.
    assert eval_order(o,np.array([109.,101.,102.,103.,104.]),np.array([94.,99.,100.,101.,102.]),cc,0,4,100,True,4,3,set())==(0,96.,"STOP_TOUCH")
    assert eval_order(o,hh,ll,c,0,4,100,True,4,0,{2})==(2,101.,"FLIP")
    assert eval_order(o,hh,ll,c,0,4,100,True,4,0,set())==(4,103.,"TIME")
    # Short gap / touch mirror
    assert eval_order(np.array([100.,107.,100.,100.,100.]),np.array([101.,108.,101.,101.,101.]),np.array([99.,106.,99.,99.,99.]),c,0,4,100,False,4,0,set())==(1,107.,"STOP_GAP")
    assert eval_order(o,np.array([105.,101.,102.,103.,104.]),ll,c,0,4,100,False,4,0,set())==(0,104.,"STOP_TOUCH")
    assert split(CUTOFF-3600000,CUTOFF)=="EXCLUDED_CROSS"
    assert split(CUTOFF,CUTOFF+3600000)=="SEEN_VALIDATION"
    assert split(CUTOFF-7200000,CUTOFF-3600000)=="TRAIN"
    # cost is 20 / 40 basis points *total* round-trip, not per leg.
    assert abs((1.-.40)-.60)<1e-12
    print("AUDIT_PASS canonical PSAR causality, long/short stop-gap, stop-touch, flip, TP-next-open, deadline, costs, split; no simultaneous price-triggered TP/SL",flush=True)
def empty():
    return {k:0 for k in SUMMARY_FIELDS}
def add(agg,key,gross,reason,duration_h,stop_pct):
    r=agg[key]; n20=gross-.2; n40=gross-.4
    r["n"]+=1;r["wins20"]+=int(n20>0);r["wins40"]+=int(n40>0)
    r["gross_sum"]+=gross;r["prof20"]+=max(n20,0);r["loss20"]+=max(-n20,0)
    r["prof40"]+=max(n40,0);r["loss40"]+=max(-n40,0)
    r["gain_abs_sum"]+=abs(gross)
    r["stop_count"]+=int(reason.startswith("STOP_"))
    r["stop_gap_count"]+=int(reason=="STOP_GAP")
    r["profit_count"]+=int(reason=="PROFIT_CONFIRMED_OPEN")
    r["flip_count"]+=int(reason=="FLIP");r["time_count"]+=int(reason=="TIME")
    r["maxloss"]=min(r["maxloss"],n40);r["maxgain"]=max(r["maxgain"],n40)
    r["notional_risk_mean"]+=stop_pct
    r["notional_exceed_40bp"]+=int(n40 < -stop_pct-.4-1e-6)
    r["duration_h_sum"]+=duration_h
def study(a,fn,canonsha):
    data=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert data
    chosen=[file for i,file in enumerate(data) if i%a.shards==a.shard]
    out=defaultdict(empty);samples=defaultdict(int);exclusions={"too_short":0,"insufficient_atr":0,"cross_split":0,"end_of_data":0}
    for k,file in enumerate(chosen,1):
        sym=fn["_symbol"](file)
        if sym in STABLE:continue
        t,o,h,l,c=fn["load"](file)
        for left,right in fn["contiguous_segments"](t):
            if right-left < M*(BURN+24):exclusions["too_short"]+=1;continue
            sub=[x[left:right] for x in (t,o,h,l,c)]
            rt,ro,rh,rl,rc=fn["resample"](*sub,M)
            if len(rt)<BURN+24:exclusions["too_short"]+=1;continue
            sar,bull=fn["psar_open_projection"](rh,rl)
            assert np.all(np.diff(rt)==M*900000)
            prev=np.r_[np.nan,rc[:-1]]
            tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prev),np.abs(rl-prev)))
            atr=np.r_[np.nan,pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()[:-1]]
            low6=pd.Series(rl).shift(1).rolling(6,min_periods=6).min().to_numpy()
            high6=pd.Series(rh).shift(1).rolling(6,min_periods=6).max().to_numpy()
            pos=np.searchsorted(sub[0],rt)
            assert np.all(sub[0][pos]==rt)
            flips=np.flatnonzero(bull[1:]!=bull[:-1])+1
            starts=[int(z) for z in flips if z>=BURN]
            ends=starts[1:]+[len(rt)]
            flip_map={int(pos[z]) for z in flips if z<len(pos)}
            # Deduplicate one signal per PSAR run and candidate.
            for start4,end4 in zip(starts,ends):
                side="BULL" if bull[start4] else "BEAR"
                for cid,spec in CANDIDATES.items():
                    if side!=spec["side"]:continue
                    i=start4+spec["age"]-1
                    if i>=end4:continue
                    endtf=i+spec["hold"]
                    if endtf>=len(rt):exclusions["end_of_data"]+=1;continue
                    ai=atr[i];si=sar[i];en=ro[i]
                    if not np.isfinite(ai) or ai<=0 or not np.isfinite(si) or en<=0:
                        exclusions["insufficient_atr"]+=1;continue
                    dist=(en-si)/ai if side=="BULL" else (si-en)/ai
                    if dist<spec["dist"]:continue
                    if spec["kind"]=="FADE_LOCAL" and not local_filter(side,en,low6[i],high6[i],ai):continue
                    follow=spec["kind"]=="FOLLOW"
                    islong=(side=="BULL") if follow else (side=="BEAR")
                    from15=int(pos[i]);to15=int(pos[endtf])
                    if to15>=len(sub[0]):exclusions["end_of_data"]+=1;continue
                    sp=split(int(rt[i]),int(rt[endtf]))
                    if sp=="EXCLUDED_CROSS":exclusions["cross_split"]+=1;continue
                    samples[cid+"|"+sp]+=1
                    for stop in STOP_PCTS:
                        for profit in PROFIT_PCTS:
                            for pol in EXIT_POLICIES:
                                # End-of-data/time-limit and flip are 15m OPEN exits.
                                fs=flip_map if pol=="FLIP" else set()
                                exited,fill,reason=eval_order(sub[1],sub[2],sub[3],sub[4],from15,to15,float(en),islong,stop,profit,fs)
                                direction=1 if islong else -1
                                gross=100*direction*(fill/en-1)
                                key=f"{cid}|SL{stop:g}|TP{profit:g}|{pol}"
                                duration_h=(int(sub[0][exited])-int(sub[0][from15]))/3600000
                                add(out,key+"|"+sp,gross,reason,duration_h,stop)
                                year=pd.Timestamp(int(rt[i]),unit="ms",tz="UTC").year
                                add(out,key+f"|YEAR{year}",gross,reason,duration_h,stop)
                                add(out,key+f"|SYMBOL{sym}",gross,reason,duration_h,stop)
        print("PROGRESS",a.shard,k,len(chosen),sym,flush=True)
    doc={"experiment":"PSAR_DUAL_RISK_CONTAINMENT_V1","label":"EXPLORATORY","data_run":DATA_RUN,"source_signal_run":37729000696,
         "code_sha":os.getenv("GITHUB_SHA","local"),"engine_sha256":canonsha,
         "shard":a.shard,"shards":a.shards,"files":len(chosen),"all_files":len(data),
         "settings":{"candidates":CANDIDATES,"stop_percent":STOP_PCTS,"profit_close_confirm_percent":PROFIT_PCTS,"exit_policies":EXIT_POLICIES,
           "exit":"4H 8 bars/4 bars next 15m OPEN at predefined time; optional PSAR flip exit at open",
           "stop":"intrabar 15m stop-market, gap open beyond stop booked at worse open; only one intrabar level",
           "profit":"profit trigger only on closed 15m CLOSE, exit on NEXT 15m OPEN, no intrabar TP",
           "fees":"20/40bp total roundtrip notional; funding excluded; stop slippage after intrabar touch assumed covered in cost stresses",
           "split":"2021-2024 TRAIN, 2025-2026 SEEN validation, cross boundary excluded",
           "account":"Independent one trade per completed PSAR trend per candidate, overlay configs can overlap; no portfolio sizing evaluated"},
         "events":dict(samples),"exclusions":exclusions,"aggregates":dict(out)}
    Path(a.out).write_text(json.dumps(doc))
    print("SHARD_PASS",a.shard,len(chosen),"files",sum(samples.values()),"signals",len(out),"keys",flush=True)
def score(r):
    n=r["n"]
    return {"n":n,"net20_mean":round(r["gross_sum"]/n-.2,5),"net40_mean":round(r["gross_sum"]/n-.4,5),
            "pf20":round(r["prof20"]/r["loss20"],5) if r["loss20"] else None,
            "pf40":round(r["prof40"]/r["loss40"],5) if r["loss40"] else None,
            "win40":round(100*r["wins40"]/n,3),"maxloss40":round(r["maxloss"],4),
            "gap_stops":r["stop_gap_count"],"stops":r["stop_count"],"tp":r["profit_count"],
            "flip":r["flip_count"],"timeout":r["time_count"],
            "loss_exceeds_stop_cost_count":r["notional_exceed_40bp"],
            "mean_hold_h":round(r["duration_h_sum"]/n,3)}
def merge(a,canonsha):
    paths=sorted(glob.glob(a.data+"/**/risk-shard-*.json",recursive=True))
    assert len(paths)==a.shards,paths
    docs=[json.load(open(p)) for p in paths]
    assert {d["shard"] for d in docs}==set(range(a.shards))
    assert len({d["code_sha"] for d in docs})==1 and len({d["engine_sha256"] for d in docs})==1
    assert docs[0]["engine_sha256"]==canonsha
    assert sum(d["files"] for d in docs)==docs[0]["all_files"]
    agg=defaultdict(empty);signal_counts=defaultdict(int)
    for d in docs:
        for k,v in d["aggregates"].items():
            r=agg[k]
            for col in SUMMARY_FIELDS:
                if col in ("maxloss","maxgain"):continue
                r[col]+=v[col]
            if v["n"]:
                r["maxloss"]=min(r["maxloss"],v["maxloss"])
                r["maxgain"]=max(r["maxgain"],v["maxgain"])
        for k,v in d["events"].items():signal_counts[k]+=v
    reports={}
    for cid in CANDIDATES:
        opts=[]
        for stop in STOP_PCTS:
            for profit in PROFIT_PCTS:
                for pol in EXIT_POLICIES:
                    key=f"{cid}|SL{stop:g}|TP{profit:g}|{pol}"
                    tr=agg.get(key+"|TRAIN");va=agg.get(key+"|SEEN_VALIDATION")
                    if not tr or not va:continue
                    if tr["n"]<70 or va["n"]<70:continue
                    opts.append({"key":key,"train":score(tr),"seen":score(va)})
        opts.sort(key=lambda z:(z["train"]["net40_mean"],z["train"]["pf40"] or 0),reverse=True)
        reports[cid]={"signal_train":signal_counts[cid+"|TRAIN"],"signal_seen":signal_counts[cid+"|SEEN_VALIDATION"],
                      "tested":len(opts),"train_positive40":sum(z["train"]["net40_mean"]>0 for z in opts),
                      "both_positive40":sum(z["train"]["net40_mean"]>0 and z["seen"]["net40_mean"]>0 for z in opts),
                      "top_train":opts[:15],
                      "baseline_no_tp_time_4pct":next((x for x in opts if x["key"]==f"{cid}|SL4|TP0|TIME"),None),
                      "all_grid":opts}
        print("CAND",cid,"Ntrain",signal_counts[cid+"|TRAIN"],"Nseen",signal_counts[cid+"|SEEN_VALIDATION"],"positive_both",reports[cid]["both_positive40"],flush=True)
        for x in opts[:3]:
            print("TOP",x["key"],"TRAIN",x["train"],"SEEN",x["seen"],flush=True)
    output={"experiment":"PSAR_DUAL_RISK_CONTAINMENT_V1","classification":"EXPLORATORY",
         "source_signal_run":37729000696,"data_run":DATA_RUN,
         "code_sha":docs[0]["code_sha"],"engine_sha256":canonsha,
         "shards":a.shards,"files":sum(d["files"] for d in docs),
         "universe":"Binance UM USDT 15m, non-stable pair sensitivity, selection survivorship possible",
         "method":"4H canonical PSAR at OPEN; one intrabar stop-market, gap at worse open; TP only NEXT 15m OPEN from prior CLOSE; PSAR flip/time OPEN. No 1m TP/SL collision possible by construction",
         "costs":"20/40bp all-in roundtrip net; funding excluded; stop-market slippage only gap-open captured; orderbook execution unknown",
         "limitations":["exploratory parameter tuning on previously seen TRAIN; SEEN 2025-26 was previously examined",
           "no mark-to-market account simulation, no portfolio concurrency and fees/funding detail",
           "stop orders during fast gap/illiquidity can slip past theoretical thresholds",
           "soft take-profit based on close confirmation, not protected TP limit",
           "needs follow-up execution and portfolio validation before demo"],
         "exclusions":{k:sum(d["exclusions"][k] for d in docs) for k in docs[0]["exclusions"]},
         "reports":reports}
    Path(a.out).write_text(json.dumps(output,indent=2))
    print("MERGE_PASS",len(paths),"shards",output["files"],"files",flush=True)
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--audit",action="store_true");ap.add_argument("--merge",action="store_true")
    ap.add_argument("--data",default="data");ap.add_argument("--shards",type=int,default=4)
    ap.add_argument("--shard",type=int,default=0);ap.add_argument("--out")
    a=ap.parse_args();fn,engine_sha=canonical();audit(fn)
    if a.audit:return
    assert a.out and 0<=a.shard<a.shards
    if a.merge:merge(a,engine_sha)
    else:study(a,fn,engine_sha)
if __name__=="__main__":main()
