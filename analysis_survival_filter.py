from __future__ import annotations
import csv, json, math, statistics, time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import requests

PAPER=Path("paper_signals.csv")
BASE="https://api.bitget.com"
PRIORITY={"L1":0,"L2":1,"L3":2}
HOLD={"L1":720,"L2":60,"L3":720}
TPSL={"L1":(10.0,5.0),"L2":(10.0,2.5),"L3":(10.0,4.0)}
TFS=[
    ("1W","1W",7*86400000),
    ("1D","1D",86400000),
    ("12H","12H",12*3600000),
    ("4H","4H",4*3600000),
    ("1H","1H",3600000),
    ("30M","30m",1800000),
    ("15M","15m",900000),
]
CHECKPOINT_MINUTES=(1,2,3)

sess=requests.Session()
sess.headers.update({"User-Agent":"long3-survival-replay/1.0"})
last_req=0.0
cache={}

def api(path,params):
    global last_req
    gap=0.06-(time.monotonic()-last_req)
    if gap>0: time.sleep(gap)
    last_req=time.monotonic()
    r=sess.get(BASE+path,params=params,timeout=15)
    r.raise_for_status()
    p=r.json()
    if str(p.get("code"))!="00000":
        raise RuntimeError(f"{p.get('code')} {p.get('msg')}")
    return p.get("data") or []

def ms(x): return int(datetime.fromisoformat(x.replace("Z","+00:00")).timestamp()*1000)
def floor15(x): return (x//900000)*900000

def rows_selected():
    with PAPER.open("r",encoding="utf-8",newline="") as f:
        rows=list(csv.DictReader(f))
    rows=[r for r in rows if (r.get("strategy") or "").upper() in PRIORITY]
    for r in rows:
        r["strategy"]=r["strategy"].upper()
        r["_ms"]=ms(r["timestamp_utc"])
        r["_b"]=floor15(r["_ms"])
    groups={}
    for r in rows:
        groups.setdefault((r["symbol"],r["_b"]),[]).append(r)
    out=[]
    for _,v in groups.items():
        v.sort(key=lambda r:(PRIORITY[r["strategy"]],r["_ms"]))
        out.append(v[0])
    return sorted(out,key=lambda r:r["_ms"])

def fetch_tf(symbol,boundary,gran,dur):
    key=(symbol,boundary,gran)
    if key in cache: return cache[key]
    if gran=="1W":
        merged={}
        chunk=89*86400000
        for i in range(2):
            end=boundary-i*chunk
            start=end-chunk
            raw=api("/api/v2/mix/market/candles",{
                "symbol":symbol,"productType":"usdt-futures","granularity":gran,
                "startTime":str(start),"endTime":str(end),"limit":"30"})
            for r in raw:
                try: merged[int(r[0])]=r
                except: pass
        raw=[merged[k] for k in sorted(merged)]
    else:
        raw=api("/api/v2/mix/market/candles",{
            "symbol":symbol,"productType":"usdt-futures","granularity":gran,
            "startTime":str(boundary-30*dur),"endTime":str(boundary+1),"limit":"30"})
    parsed=[]
    for r in raw:
        try: parsed.append((int(r[0]),float(r[1]),float(r[4])))
        except: pass
    parsed.sort()
    cache[key]=parsed
    return parsed

def bases_for_symbol(symbol,boundary):
    tf_completed={}
    for tf,gran,dur in TFS:
        p=fetch_tf(symbol,boundary,gran,dur)
        completed=[x for x in p if x[0]+dur<=boundary]
        if len(completed)<19:
            return None
        tf_completed[tf]=[x[2] for x in completed[-19:]]
    p15=fetch_tf(symbol,boundary,"15m",900000)
    opens={ts:o for ts,o,_ in p15}
    b1=opens.get(boundary-4*900000)
    b4=opens.get(boundary-16*900000)
    current=next((x for x in p15 if x[0]==boundary),None)
    if not current or not b1 or not b4:
        return None
    return {"completed":tf_completed,"b1":b1,"b4":b4,"boundary_price":current[1]}

def classify_from_price(bases,price):
    tfres={}
    for tf,_,_ in TFS:
        window=bases["completed"][tf]+[price]
        basis=sum(window)/20.0
        sd=statistics.pstdev(window)
        upper=basis+2*sd
        tfres[tf]={"above":price>upper,"upper":upper}
    exact=[tf for tf,_,_ in TFS if tfres[tf]["above"]]
    missing=[tf for tf,_,_ in TFS if not tfres[tf]["above"]]
    exact_count=len(exact)
    rank=7 if exact_count==7 else (6 if exact_count==6 else 0)
    ret1=(price/bases["b1"]-1)*100
    ret4=(price/bases["b4"]-1)*100
    matches=[]
    if rank>=6 and ret1>=10: matches.append("L1")
    if rank>=6 and ret4>=30: matches.append("L2")
    if exact_count==6 and missing==["4H"]: matches.append("L3")
    matches=sorted(matches,key=lambda x:PRIORITY[x])
    return {
        "price":price,"exact_count":exact_count,"missing":missing,
        "ret1":ret1,"ret4":ret4,"matches":matches,
        "selected":matches[0] if matches else None,
    }

def one_min(symbol,start,end):
    raw=api("/api/v2/mix/market/candles",{
        "symbol":symbol,"productType":"usdt-futures","granularity":"1m",
        "startTime":str(start),"endTime":str(end),"limit":"1000"})
    out=[]
    for r in raw:
        try:
            out.append({"ts":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])})
        except: pass
    return sorted(out,key=lambda x:x["ts"])

def trade_outcome(strategy,entry_ms,entry,cs,now):
    tp_pct,sl_pct=TPSL[strategy]
    tp=entry*(1+tp_pct/100)
    sl=entry*(1-sl_pct/100)
    deadline=entry_ms+HOLD[strategy]*60000
    end=min(deadline,now)
    for c in cs:
        if c["ts"]<entry_ms or c["ts"]>=end: continue
        th=c["high"]>=tp
        sh=c["low"]<=sl
        if th and sh: return {"status":"AMBIGUOUS","ret":None}
        if th: return {"status":"TP","ret":(tp/entry-1)*100}
        if sh: return {"status":"SL","ret":(sl/entry-1)*100}
    if now>=deadline:
        prev=[c for c in cs if entry_ms<=c["ts"]<deadline]
        if not prev: return {"status":"NO_EXIT_DATA","ret":None}
        ex=prev[-1]["close"]
        return {"status":"TIME","ret":(ex/entry-1)*100}
    prev=[c for c in cs if entry_ms<=c["ts"]<=now]
    return {"status":"OPEN","ret":((prev[-1]["close"]/entry-1)*100 if prev else None)}

def pf(vals):
    wins=sum(x for x in vals if x>0)
    losses=abs(sum(x for x in vals if x<0))
    return wins/losses if losses else (math.inf if wins>0 else None)

def summarize(records):
    closed=[r["outcome"]["ret"] for r in records if r["outcome"]["status"] in {"TP","SL","TIME"} and r["outcome"]["ret"] is not None]
    eq=1.0
    for v in closed:
        eq*=1+(0.30*v)/100
    return {
        "entries":len(records),
        "outcomes":dict(Counter(r["outcome"]["status"] for r in records)),
        "closed":len(closed),
        "avg_closed_pct":sum(closed)/len(closed) if closed else None,
        "pf":pf(closed),
        "win_rate_pct":(sum(v>0 for v in closed)/len(closed)*100) if closed else None,
        "weighted30_compounded_pct":(eq-1)*100,
    }

def main():
    rows=rows_selected()
    now=int(time.time()*1000)
    boundary_valid=[]
    detailed=[]
    checkpoint_records={m:[] for m in CHECKPOINT_MINUTES}
    anylong_records={m:[] for m in CHECKPOINT_MINUTES}

    for i,r in enumerate(rows,1):
        b=r["_b"]
        bases=bases_for_symbol(r["symbol"],b)
        if not bases:
            continue
        snap0=classify_from_price(bases,bases["boundary_price"])
        if not snap0["selected"]:
            continue
        boundary_valid.append((r,bases,snap0))
        max_hold=max(HOLD.values())
        cs=one_min(r["symbol"],b,min(now,b+max_hold*60000+5*60000))
        item={"n":i,"symbol":r["symbol"],"boundary":b,"original_recorded":r["strategy"],"boundary_selected":snap0["selected"],"boundary_price":bases["boundary_price"],"checkpoints":{}}

        # Baseline: immediate boundary entry.
        base_out=trade_outcome(snap0["selected"],b,bases["boundary_price"],cs,now)
        item["baseline"]=base_out

        for minute in CHECKPOINT_MINUTES:
            t=b+minute*60000
            candle=next((c for c in cs if c["ts"]==t),None)
            if not candle:
                item["checkpoints"][str(minute)]={"available":False}
                continue
            snap=classify_from_price(bases,candle["open"])
            same_selected=(snap["selected"]==snap0["selected"])
            original_active=(snap0["selected"] in snap["matches"])
            any_long=bool(snap["selected"])
            cp={
                "available":True,"price":candle["open"],"selected":snap["selected"],
                "matches":snap["matches"],"same_selected":same_selected,
                "original_active":original_active,"any_long":any_long,
                "ret1":snap["ret1"],"ret4":snap["ret4"],"exact_count":snap["exact_count"],
            }
            if same_selected:
                out=trade_outcome(snap0["selected"],t,candle["open"],cs,now)
                cp["same_selected_outcome"]=out
                checkpoint_records[minute].append({"strategy":snap0["selected"],"symbol":r["symbol"],"outcome":out})
            if any_long:
                out_any=trade_outcome(snap["selected"],t,candle["open"],cs,now)
                cp["any_long_outcome"]=out_any
                anylong_records[minute].append({"strategy":snap["selected"],"symbol":r["symbol"],"outcome":out_any})
            item["checkpoints"][str(minute)]=cp
        detailed.append(item)

    baseline_records=[]
    for r,bases,snap0 in boundary_valid:
        cs=one_min(r["symbol"],r["_b"],min(now,r["_b"]+HOLD[snap0["selected"]]*60000+60000))
        baseline_records.append({"strategy":snap0["selected"],"symbol":r["symbol"],"outcome":trade_outcome(snap0["selected"],r["_b"],bases["boundary_price"],cs,now)})

    print("[BASELINE]",json.dumps({"boundary_valid":len(boundary_valid),**summarize(baseline_records)},ensure_ascii=False,sort_keys=True))
    for m in CHECKPOINT_MINUTES:
        same=checkpoint_records[m]
        anyr=anylong_records[m]
        by_st={}
        for st in ("L1","L2","L3"):
            rr=[x for x in same if x["strategy"]==st]
            by_st[st]=summarize(rr) if rr else {"entries":0}
        print("[SAME_SELECTED]",m,json.dumps({
            "boundary_valid":len(boundary_valid),
            "survivors":len(same),
            "survival_rate_pct":len(same)/len(boundary_valid)*100 if boundary_valid else None,
            **summarize(same),
            "by_strategy":by_st,
        },ensure_ascii=False,sort_keys=True))
        print("[ANY_LONG3]",m,json.dumps({
            "boundary_valid":len(boundary_valid),
            "survivors":len(anyr),
            "survival_rate_pct":len(anyr)/len(boundary_valid)*100 if boundary_valid else None,
            **summarize(anyr),
        },ensure_ascii=False,sort_keys=True))
    for d in detailed:
        print("[DETAIL]",json.dumps(d,ensure_ascii=False,sort_keys=True))

if __name__=="__main__":
    main()
