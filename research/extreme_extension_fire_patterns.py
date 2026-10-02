#!/usr/bin/env python3
"""Extreme Extension SHORT: causal chart-based add-to-winner ("fire") pattern research.

Data:
- Binance USD-M USDT perpetual frozen 5Y 15m dataset from run 36095439671.
- Same causal trough / 50% cycle-reset semantics as extreme_extension_nextopen_sweep.py.
- First 90 calendar days per symbol ignored.

Purpose:
1) Explore chart-visible "this move is dying" confirmations after an extension event.
2) Generalize the same predeclared candidates across lower extension bases.
3) Report TRAIN (<=2024) and HOLDOUT (>=2025) separately to expose overfit.

All fire entries are causal: condition is confirmed on a completed bar, entry is next 15m OPEN.
Funding excluded. Net40 subtracts 40bp round-trip cost.
"""
from __future__ import annotations
import argparse,csv,gzip,json,math,statistics
from datetime import datetime,timezone
from pathlib import Path

DAY_MS=86_400_000
BAR_MS=900_000
H4_MS=4*60*60*1000
WARMUP_MS=90*DAY_MS
BASE_THRESHOLDS=(5.0,7.5,10.0,15.0,20.0,25.0,30.0)
LOOKAHEAD_DAYS=14
DD_LEVELS=(0.10,0.15,0.20,0.25,0.30)
BF_CONFIGS=((0.10,0.05),(0.15,0.05),(0.15,0.10),(0.20,0.05),(0.20,0.10),(0.25,0.10))
D24_DD=(0.10,0.15,0.20,0.25)
MOM4H_DD=(0.10,0.15,0.20)

def year_of_ms(ts):
    return datetime.fromtimestamp(ts/1000,tz=timezone.utc).year

def split_name(ts):
    return "train_2021_2024" if year_of_ms(ts)<=2024 else "holdout_2025_2026"

def pctile(vals,q):
    if not vals:return None
    xs=sorted(vals)
    if len(xs)==1:return xs[0]
    p=(len(xs)-1)*q
    lo=math.floor(p);hi=math.ceil(p)
    if lo==hi:return xs[lo]
    w=p-lo
    return xs[lo]*(1-w)+xs[hi]*w

def pf(vals):
    pos=sum(x for x in vals if x>0)
    neg=-sum(x for x in vals if x<0)
    if neg==0:return None if pos==0 else 999.0
    return pos/neg

def load_symbol(path):
    rows=[]
    with gzip.open(path,"rt",encoding="utf-8",newline="") as f:
        r=csv.DictReader(f)
        for x in r:
            try:
                ts=int(x["open_time"]);op=float(x["open"]);hi=float(x["high"]);lo=float(x["low"]);cl=float(x["close"])
            except Exception:
                continue
            if min(op,hi,lo,cl)<=0:continue
            rows.append((ts,op,hi,lo,cl))
    return rows

def base_signals(rows):
    if len(rows)<2:return []
    first_ts=rows[0][0];start_ts=first_ts+WARMUP_MS
    trough=None;trough_ts=None;peak=None;fired=set();out=[]
    for i,(ts,op,hi,lo,cl) in enumerate(rows):
        if ts<start_ts:continue
        if trough is not None:
            x=op/trough
            for thr in BASE_THRESHOLDS:
                if thr not in fired and x>=thr:
                    fired.add(thr)
                    out.append({
                        "base_x":thr,"trough":trough,"trough_ts":trough_ts,
                        "base_entry_ts":ts,"base_entry":op,"base_entry_x":x,
                        "base_idx":i,
                    })
        if trough is None:
            trough=lo;trough_ts=ts;peak=hi;fired=set();continue
        prior_peak=peak
        if prior_peak is not None and lo<=0.5*prior_peak:
            trough=lo;trough_ts=ts;peak=hi;fired=set();continue
        if lo<trough:
            trough=lo;trough_ts=ts;peak=hi;fired=set();continue
        if peak is None or hi>peak:peak=hi
    return out

def add_trade(out,symbol,event,candidate,signal_idx,rows,extra=None):
    if signal_idx is None or signal_idx>=len(rows):return
    sts,sop,_,_,_=rows[signal_idx]
    rec={
        "symbol":symbol,"base_x":event["base_x"],"trough_ts":event["trough_ts"],
        "base_entry_ts":event["base_entry_ts"],"base_entry":event["base_entry"],
        "candidate":candidate,"signal_ts":sts,"signal_entry":sop,
        "hours_after_base":(sts-event["base_entry_ts"])/3_600_000,
        "signal_year":year_of_ms(sts),"split":split_name(sts),
    }
    if extra:rec.update(extra)
    ts_to_idx={rows[k][0]:k for k in range(max(0,signal_idx-1),min(len(rows),signal_idx+7*96+2))}
    for d in (1,3,7):
        target=sts+d*DAY_MS
        xi=ts_to_idx.get(target)
        if xi is None:
            rec[f"d{d}_gross_pct"]=None;rec[f"d{d}_net40_pct"]=None;rec[f"d{d}_mae_pct"]=None
            continue
        exit_op=rows[xi][1]
        gross=(sop-exit_op)/sop*100
        mh=max(r[2] for r in rows[signal_idx:xi+1])
        mae=max(0.0,(mh/sop-1)*100)
        rec[f"d{d}_gross_pct"]=gross
        rec[f"d{d}_net40_pct"]=gross-0.40
        rec[f"d{d}_mae_pct"]=mae
    out.append(rec)

def analyze_event(symbol,rows,event,out):
    bi=event["base_idx"]
    end_ts=event["base_entry_ts"]+LOOKAHEAD_DAYS*DAY_MS
    end=min(len(rows)-2,bi+LOOKAHEAD_DAYS*96)

    # Baseline immediate entry, for reference.
    add_trade(out,symbol,event,"BASE_IMMEDIATE",bi,rows,{"confirm":"none"})

    running_peak=max(rows[bi][1], rows[bi][2])
    fired=set()

    # Bounce-fail state per config.
    bf={cfg:{"armed":False,"low":None,"rebounded":False,"frozen_low":None} for cfg in BF_CONFIGS}
    d24_armed={dd:False for dd in D24_DD}
    mom_armed={dd:False for dd in MOM4H_DD}
    fourh=[]

    for j in range(bi,end+1):
        ts,op,hi,lo,cl=rows[j]
        if ts>end_ts:break

        # New high invalidates any prior "failed bounce" structure and becomes new reference peak.
        new_peak = hi > running_peak
        if new_peak:
            running_peak=hi
            for cfg in BF_CONFIGS:
                bf[cfg]={"armed":False,"low":None,"rebounded":False,"frozen_low":None}

        dd_close=max(0.0,1.0-cl/running_peak) if running_peak>0 else 0.0

        # 1) Simple close drawdown from running high; enter next 15m OPEN.
        for dd in DD_LEVELS:
            name=f"DD{int(dd*100)}"
            if name not in fired and dd_close>=dd and j+1<len(rows):
                fired.add(name)
                add_trade(out,symbol,event,name,j+1,rows,{
                    "confirm":f"completed close <= running high * {1-dd:.2f}",
                    "peak_at_confirm":running_peak,
                })

        # 2) Drawdown -> rebound -> frozen swing-low break.
        for cfg in BF_CONFIGS:
            dd,bounce=cfg
            name=f"BF_DD{int(dd*100)}_B{int(bounce*100)}"
            if name in fired:continue
            st=bf[cfg]
            if not st["armed"]:
                if dd_close>=dd:
                    st["armed"]=True;st["low"]=lo
            elif not st["rebounded"]:
                st["low"]=min(st["low"],lo)
                # Need a real rebound and no recovery to the old running peak.
                if hi>=st["low"]*(1+bounce) and hi<running_peak*0.995:
                    st["rebounded"]=True;st["frozen_low"]=st["low"]
            else:
                if cl<st["frozen_low"] and j+1<len(rows):
                    fired.add(name)
                    add_trade(out,symbol,event,name,j+1,rows,{
                        "confirm":f"DD{int(dd*100)} then bounce {int(bounce*100)}% then close below frozen low",
                        "peak_at_confirm":running_peak,"break_level":st["frozen_low"],
                    })

        # 3) DD armed + close below prior rolling 24h low.
        for dd in D24_DD:
            name=f"D24_DD{int(dd*100)}"
            if name in fired:continue
            if dd_close>=dd:d24_armed[dd]=True
            if d24_armed[dd] and j>=96:
                prior24=min(rows[k][3] for k in range(j-96,j))
                if cl<prior24 and j+1<len(rows):
                    fired.add(name)
                    add_trade(out,symbol,event,name,j+1,rows,{
                        "confirm":f"DD{int(dd*100)} armed + close below prior 24h low",
                        "peak_at_confirm":running_peak,"break_level":prior24,
                    })

        # 4) 4H momentum break: DD armed + 4H close below prior 4H low + lower 4H close.
        for dd in MOM4H_DD:
            if dd_close>=dd:mom_armed[dd]=True
        if (ts+BAR_MS)%H4_MS==0:
            block_start=max(0,j-15)
            h4_low=min(rows[k][3] for k in range(block_start,j+1))
            h4_close=cl
            fourh.append((ts,h4_low,h4_close))
            if len(fourh)>=2:
                prev=fourh[-2]
                for dd in MOM4H_DD:
                    name=f"MOM4H_DD{int(dd*100)}"
                    if name in fired or not mom_armed[dd]:continue
                    if h4_close<prev[1] and h4_close<prev[2] and j+1<len(rows):
                        fired.add(name)
                        add_trade(out,symbol,event,name,j+1,rows,{
                            "confirm":f"DD{int(dd*100)} armed + 4H close below prior 4H low and close",
                            "peak_at_confirm":running_peak,"break_level":prev[1],
                        })

def summarize(xs):
    vals=[x["d7_net40_pct"] for x in xs if x.get("d7_net40_pct") is not None]
    maes=[x["d7_mae_pct"] for x in xs if x.get("d7_mae_pct") is not None]
    hrs=[x["hours_after_base"] for x in xs if x.get("hours_after_base") is not None]
    if not vals:return {"n":0}
    return {
        "n":len(vals),
        "mean_net40_pct":sum(vals)/len(vals),
        "median_net40_pct":statistics.median(vals),
        "win_rate_pct":100*sum(v>0 for v in vals)/len(vals),
        "profit_factor":pf(vals),
        "worst_pct":min(vals),"best_pct":max(vals),
        "mae_mean_pct":sum(maes)/len(maes) if maes else None,
        "mae_p90_pct":pctile(maes,0.90) if maes else None,
        "mae_max_pct":max(maes) if maes else None,
        "hours_to_signal_median":statistics.median(hrs) if hrs else None,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-root",required=True)
    ap.add_argument("--out",required=True)
    args=ap.parse_args()
    paths=sorted(Path(args.data_root).rglob("*USDT.csv.gz"))
    trades=[];base_counts={str(x):0 for x in BASE_THRESHOLDS}
    for n,p in enumerate(paths,1):
        rows=load_symbol(p)
        if not rows:continue
        sym=p.name.replace(".csv.gz","")
        evs=base_signals(rows)
        for e in evs:
            base_counts[str(e["base_x"])]+=1
            analyze_event(sym,rows,e,trades)
        if n%25==0 or n==len(paths):
            print(f"processed {n}/{len(paths)} base_events={sum(base_counts.values())} fire_rows={len(trades)}",flush=True)

    candidates=sorted(set(t["candidate"] for t in trades))
    result={
        "definition":{
            "market":"Binance USD-M USDT perpetual frozen 5Y run 36095439671",
            "interval":"15m","warmup_days":90,"cycle_reset_drawdown_pct":50,
            "base_thresholds_x":BASE_THRESHOLDS,"lookahead_days_after_base":LOOKAHEAD_DAYS,
            "entry":"next 15m OPEN after completed-bar causal confirmation",
            "primary_exit":"exact +7d OPEN","cost":"40bp round-trip","funding_included":False,
            "train":"signal year <= 2024","holdout":"signal year >= 2025",
        },
        "symbols_total":len(paths),"base_event_counts":base_counts,"candidates":{}
    }
    for c in candidates:
        result["candidates"][c]={}
        for b in BASE_THRESHOLDS:
            xb=[t for t in trades if t["candidate"]==c and t["base_x"]==b]
            result["candidates"][c][str(b)]={
                "all":summarize(xb),
                "train_2021_2024":summarize([t for t in xb if t["split"]=="train_2021_2024"]),
                "holdout_2025_2026":summarize([t for t in xb if t["split"]=="holdout_2025_2026"]),
            }

    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    (out/"summary.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    fields=[
        "symbol","base_x","trough_ts","base_entry_ts","base_entry","candidate",
        "signal_ts","signal_entry","hours_after_base","signal_year","split","confirm",
        "peak_at_confirm","break_level",
        "d1_gross_pct","d1_net40_pct","d1_mae_pct",
        "d3_gross_pct","d3_net40_pct","d3_mae_pct",
        "d7_gross_pct","d7_net40_pct","d7_mae_pct"
    ]
    with (out/"trades.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        w.writeheader();w.writerows(trades)

    # Compact ranked views for logs: primary extreme base 20x and generalization base 10x.
    for b in (20.0,10.0):
        print(f"RANK_BASE_{b:g}X")
        rows_rank=[]
        for c in candidates:
            s=result["candidates"][c][str(b)]["all"]
            h=result["candidates"][c][str(b)]["holdout_2025_2026"]
            if s.get("n",0):
                rows_rank.append((s.get("mean_net40_pct",-999),c,s,h))
        for _,c,s,h in sorted(rows_rank,reverse=True):
            print(json.dumps({"candidate":c,"all":s,"holdout":h},ensure_ascii=False),flush=True)
    print("FINAL_JSON")
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
