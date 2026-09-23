from __future__ import annotations
import csv, json, math, statistics, time
from collections import Counter
from datetime import datetime
from pathlib import Path
import requests

PAPER=Path("paper_signals.csv")
BASE="https://api.bitget.com"
PRIORITY={"L1":0,"L2":1,"L3":2}
HOLD={"L1":720,"L2":60,"L3":720}
TPSL={"L1":(10.0,5.0),"L2":(10.0,2.5),"L3":(10.0,4.0)}
TFS=[("1W","1W",7*86400000),("1D","1D",86400000),("12H","12H",12*3600000),("4H","4H",4*3600000),("1H","1H",3600000),("30M","30m",1800000),("15M","15m",900000)]
sess=requests.Session(); sess.headers.update({"User-Agent":"boundary-eligibility-replay/1.0"})
last_req=0.0
cache={}

def api(path,params):
    global last_req
    gap=0.06-(time.monotonic()-last_req)
    if gap>0: time.sleep(gap)
    last_req=time.monotonic()
    r=sess.get(BASE+path,params=params,timeout=15); r.raise_for_status()
    p=r.json()
    if str(p.get("code"))!="00000": raise RuntimeError(f"{p.get('code')} {p.get('msg')}")
    return p.get("data") or []

def ms(x): return int(datetime.fromisoformat(x.replace("Z","+00:00")).timestamp()*1000)
def floor15(x): return (x//900000)*900000
def ceilmin(x): return ((x+59999)//60000)*60000

def rows_selected():
    with PAPER.open("r",encoding="utf-8",newline="") as f: rows=list(csv.DictReader(f))
    rows=[r for r in rows if (r.get("strategy") or "").upper() in PRIORITY]
    for r in rows:
        r["strategy"]=r["strategy"].upper(); r["_ms"]=ms(r["timestamp_utc"]); r["_b"]=floor15(r["_ms"])
    g={}
    for r in rows: g.setdefault((r["symbol"],r["_b"]),[]).append(r)
    out=[]
    for _,v in g.items():
        v.sort(key=lambda r:(PRIORITY[r["strategy"]],r["_ms"]))
        out.append(v[0])
    return sorted(out,key=lambda r:r["_ms"])

def fetch_tf(symbol,boundary,tf,gran,dur):
    key=(symbol,boundary,gran)
    if key in cache: return cache[key]
    if gran=="1W":
        merged={}
        day=86400000; chunk=89*day
        for i in range(2):
            end=boundary-i*chunk
            start=end-chunk
            for r in api("/api/v2/mix/market/candles",{"symbol":symbol,"productType":"usdt-futures","granularity":gran,"startTime":str(start),"endTime":str(end),"limit":"30"}):
                try: merged[int(r[0])]=r
                except: pass
        raw=[merged[k] for k in sorted(merged)]
    else:
        start=boundary-30*dur
        raw=api("/api/v2/mix/market/candles",{"symbol":symbol,"productType":"usdt-futures","granularity":gran,"startTime":str(start),"endTime":str(boundary+1),"limit":"30"})
    parsed=[]
    for r in raw:
        try: parsed.append((int(r[0]),float(r[1]),float(r[4])))
        except: pass
    parsed.sort()
    cache[key]=parsed
    return parsed

def snapshot(symbol,boundary):
    # exact boundary price = 15m candle open
    p15=fetch_tf(symbol,boundary,"15M","15m",900000)
    current=next((x for x in p15 if x[0]==boundary),None)
    if not current: return None
    px=current[1]
    tfres={}
    for tf,gran,dur in TFS:
        p=fetch_tf(symbol,boundary,tf,gran,dur)
        completed=[x for x in p if x[0]+dur<=boundary]
        if len(completed)<19: return None
        window=[x[2] for x in completed[-19:]]+[px]
        basis=sum(window)/20.0; sd=statistics.pstdev(window); upper=basis+2*sd
        tfres[tf]={"above":px>upper,"upper":upper}
    exact=[tf for tf,_,_ in TFS if tfres[tf]["above"]]
    missing=[tf for tf,_,_ in TFS if not tfres[tf]["above"]]
    exact_count=len(exact)
    rank=7 if exact_count==7 else (6 if exact_count==6 else 0)
    opens={ts:o for ts,o,_ in p15}
    b1=opens.get(boundary-4*900000); b4=opens.get(boundary-16*900000)
    ret1=(px/b1-1)*100 if b1 else None; ret4=(px/b4-1)*100 if b4 else None
    matches=[]
    if rank>=6 and ret1 is not None and ret1>=10: matches.append("L1")
    if rank>=6 and ret4 is not None and ret4>=30: matches.append("L2")
    if exact_count==6 and missing==["4H"]: matches.append("L3")
    matches=sorted(matches,key=lambda x:PRIORITY[x])
    return {"price":px,"exact_count":exact_count,"missing":missing,"ret1":ret1,"ret4":ret4,"matches":matches,"selected":matches[0] if matches else None}

def one_min(symbol,start,end):
    raw=api("/api/v2/mix/market/candles",{"symbol":symbol,"productType":"usdt-futures","granularity":"1m","startTime":str(start),"endTime":str(end),"limit":"1000"})
    out=[]
    for r in raw:
        try: out.append({"ts":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])})
        except: pass
    return sorted(out,key=lambda x:x["ts"])

def ideal_boundary(r,snap,now):
    st=snap["selected"]; boundary=r["_b"]; entry=snap["price"]
    tp_pct,sl_pct=TPSL[st]; tp=entry*(1+tp_pct/100); sl=entry*(1-sl_pct/100)
    deadline=boundary+HOLD[st]*60000
    cs=one_min(r["symbol"],boundary,min(now,deadline)+60000)
    for c in cs:
        if c["ts"]<boundary or c["ts"]>=min(deadline,now): continue
        th=c["high"]>=tp; sh=c["low"]<=sl
        if th and sh: return {"status":"AMBIGUOUS","ret":None}
        if th: return {"status":"TP","ret":(tp/entry-1)*100}
        if sh: return {"status":"SL","ret":(sl/entry-1)*100}
    if now>=deadline:
        prev=[c for c in cs if boundary<=c["ts"]<deadline]
        if not prev: return {"status":"NO_EXIT_DATA","ret":None}
        return {"status":"TIME","ret":(prev[-1]["close"]/entry-1)*100}
    prev=[c for c in cs if c["ts"]>=boundary and c["ts"]<=now]
    return {"status":"OPEN","ret":((prev[-1]["close"]/entry-1)*100 if prev else None)}


def maker3(r,snap,now):
    st=snap["selected"]; boundary=r["_b"]; emitted=r["_ms"]; entry=snap["price"]
    tp_pct,sl_pct=TPSL[st]; tp=entry*(1+tp_pct/100); sl=entry*(1-sl_pct/100)
    deadline=boundary+HOLD[st]*60000
    active=ceilmin(emitted); expiry=active+180000
    cs=one_min(r["symbol"],boundary,min(now,deadline)+60000)
    fill=None
    for c in cs:
        if c["ts"]<active: continue
        if c["ts"]>=min(expiry,deadline,now): break
        if c["low"]<=entry:
            fill=c["ts"]; break
        if c["high"]>=tp or c["low"]<=sl:
            return {"status":"CANCEL_PREENTRY_RESOLVED","fill":False,"ret":None}
    if fill is None: return {"status":"NO_FILL","fill":False,"ret":None}
    for c in cs:
        if c["ts"]<fill or c["ts"]>=min(deadline,now): continue
        th=c["high"]>=tp; sh=c["low"]<=sl
        if th and sh: return {"status":"AMBIGUOUS","fill":True,"ret":None}
        if th: return {"status":"TP","fill":True,"ret":(tp/entry-1)*100}
        if sh: return {"status":"SL","fill":True,"ret":(sl/entry-1)*100}
    if now>=deadline:
        prev=[c for c in cs if fill<=c["ts"]<deadline]
        if not prev: return {"status":"NO_EXIT_DATA","fill":True,"ret":None}
        return {"status":"TIME","fill":True,"ret":(prev[-1]["close"]/entry-1)*100}
    prev=[c for c in cs if c["ts"]>=fill and c["ts"]<=now]
    return {"status":"OPEN","fill":True,"ret":((prev[-1]["close"]/entry-1)*100 if prev else None)}

def pf(v):
    w=sum(x for x in v if x>0); l=abs(sum(x for x in v if x<0))
    return w/l if l else (math.inf if w>0 else None)

def main():
    rows=rows_selected(); now=int(time.time()*1000); valid=[]; all_detail=[]
    for i,r in enumerate(rows,1):
        try: snap=snapshot(r["symbol"],r["_b"])
        except Exception as e:
            print("[WARN]",r["symbol"],e); snap=None
        d={"n":i,"symbol":r["symbol"],"time":r["timestamp_utc"],"old_strategy":r["strategy"],"boundary":r["_b"],"snapshot":snap}
        if snap and snap["selected"]:
            ideal=ideal_boundary(r,snap,now)
            res=maker3(r,snap,now); d["ideal"]=ideal; d["maker3"]=res; valid.append((r,snap,res,ideal))
        all_detail.append(d)
    same=sum(1 for r,s,_,_ in valid if s["selected"]==r["strategy"])
    changed=sum(1 for r,s,_,_ in valid if s["selected"]!=r["strategy"])
    invalid=len(rows)-len(valid)
    results=[x[2] for x in valid]; fills=[x for x in results if x["fill"]]
    ideals=[x[3] for x in valid]
    closed=[x["ret"] for x in results if x["status"] in {"TP","SL","TIME"} and x["ret"] is not None]
    eq=1.0
    for v in closed: eq*=1+(0.30*v)/100
    ideal_closed=[x["ret"] for x in ideals if x["status"] in {"TP","SL","TIME"} and x["ret"] is not None]
    ideal_eq=1.0
    for v in ideal_closed: ideal_eq*=1+(0.30*v)/100
    summary={"old_signals":len(rows),"boundary_valid":len(valid),"same_selected":same,"changed_selected":changed,"boundary_invalid":invalid,
      "fills":len(fills),"fill_rate_of_valid_pct":len(fills)/len(valid)*100 if valid else None,
      "outcomes":dict(Counter(x["status"] for x in results)),"closed":len(closed),
      "avg_closed_pct":sum(closed)/len(closed) if closed else None,"pf":pf(closed),"weighted30_compounded_pct":(eq-1)*100,
      "ideal_outcomes":dict(Counter(x["status"] for x in ideals)),"ideal_closed":len(ideal_closed),
      "ideal_avg_closed_pct":sum(ideal_closed)/len(ideal_closed) if ideal_closed else None,
      "ideal_pf":pf(ideal_closed),"ideal_weighted30_compounded_pct":(ideal_eq-1)*100}
    print("[SUMMARY]",json.dumps(summary,ensure_ascii=False,sort_keys=True))
    for d in all_detail: print("[DETAIL]",json.dumps(d,ensure_ascii=False,sort_keys=True))

if __name__=="__main__": main()
