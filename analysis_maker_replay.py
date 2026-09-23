from __future__ import annotations
import csv, json, math, time
from datetime import datetime
from pathlib import Path
import requests

PAPER=Path("paper_signals.csv")
BASE="https://api.bitget.com"
PRIORITY={"L1":0,"L2":1,"L3":2}
HOLD={"L1":720,"L2":60,"L3":720}
WAITS={"1m":1,"3m":3,"5m":5,"to_next_15m":None}

s=requests.Session(); s.headers.update({"User-Agent":"maker-replay/1.0"})
_last=0.0
def api(path,params):
    global _last
    d=0.07-(time.monotonic()-_last)
    if d>0: time.sleep(d)
    _last=time.monotonic()
    r=s.get(BASE+path,params=params,timeout=15); r.raise_for_status()
    p=r.json()
    if str(p.get("code"))!="00000": raise RuntimeError(p)
    return p.get("data") or []

def ms(x): return int(datetime.fromisoformat(x.replace("Z","+00:00")).timestamp()*1000)
def floor15(x): return (x//900000)*900000

def load():
    with PAPER.open("r",encoding="utf-8",newline="") as f: rows=list(csv.DictReader(f))
    rows=[r for r in rows if (r.get("strategy") or "").upper() in PRIORITY]
    for r in rows:
        r["strategy"]=r["strategy"].upper(); r["_ms"]=ms(r["timestamp_utc"]); r["_b"]=floor15(r["_ms"])
    g={}
    for r in rows: g.setdefault((r["symbol"],r["_b"]),[]).append(r)
    out=[]
    for k,v in g.items():
        v.sort(key=lambda r:(PRIORITY[r["strategy"]],r["_ms"]))
        out.append(v[0])
    return sorted(out,key=lambda r:r["_ms"])

def candles(symbol,start,end):
    rows=api("/api/v2/mix/market/candles",{
        "symbol":symbol,"productType":"usdt-futures","granularity":"1m",
        "startTime":str(start),"endTime":str(end),"limit":"1000"})
    out=[]
    for r in rows:
        try: out.append({"ts":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])})
        except: pass
    return sorted(out,key=lambda x:x["ts"])

def evaluate(r,cs,wait_name,wait_min,now):
    signal=r["_ms"]; px=float(r["entry_price"]); tp=float(r["tp_price"]); sl=float(r["sl_price"])
    if wait_min is None:
        expiry=((signal//900000)+1)*900000
    else:
        expiry=signal+wait_min*60000
    deadline=signal+HOLD[r["strategy"]]*60000
    expiry=min(expiry,deadline,now)

    fill=None
    for c in cs:
        if c["ts"]+60000 < signal: continue
        if c["ts"] >= expiry: break
        # LONG maker buy at px fills if traded down to or through limit.
        if c["low"] <= px:
            fill={"ts":c["ts"],"price":px}
            break
        # if strategy already resolved before fill, don't enter later
        if c["high"] >= tp or c["low"] <= sl:
            return {"status":"CANCEL_PREENTRY_RESOLVED","fill":False,"ret":None}
    if not fill:
        return {"status":"NO_FILL","fill":False,"ret":None}

    # original horizon preserved from signal boundary
    for c in cs:
        if c["ts"] < fill["ts"] or c["ts"] >= min(deadline,now): continue
        th=c["high"]>=tp; sh=c["low"]<=sl
        if th and sh: return {"status":"AMBIGUOUS","fill":True,"ret":None,"fill_ms":fill["ts"]}
        if th: return {"status":"TP","fill":True,"ret":(tp/px-1)*100,"fill_ms":fill["ts"]}
        if sh: return {"status":"SL","fill":True,"ret":(sl/px-1)*100,"fill_ms":fill["ts"]}
    if now>=deadline:
        prev=[c for c in cs if fill["ts"]<=c["ts"]<deadline]
        if not prev: return {"status":"NO_EXIT_DATA","fill":True,"ret":None,"fill_ms":fill["ts"]}
        ex=prev[-1]["close"]
        return {"status":"TIME","fill":True,"ret":(ex/px-1)*100,"fill_ms":fill["ts"]}
    last=[c for c in cs if c["ts"]>=fill["ts"] and c["ts"]<=now]
    if not last: return {"status":"OPEN_NO_MARK","fill":True,"ret":None,"fill_ms":fill["ts"]}
    return {"status":"OPEN","fill":True,"ret":(last[-1]["close"]/px-1)*100,"fill_ms":fill["ts"]}

def pf(v):
    w=sum(x for x in v if x>0); l=abs(sum(x for x in v if x<0))
    return (w/l) if l else (math.inf if w>0 else None)

def summary(rows):
    fills=[x for x in rows if x["fill"]]
    closed=[x["ret"] for x in rows if x["status"] in ("TP","SL","TIME") and x["ret"] is not None]
    eq=1.0
    for r in closed: eq*=1+(0.30*r)/100
    from collections import Counter
    return {
        "n":len(rows),"fills":len(fills),"fill_rate_pct":len(fills)/len(rows)*100 if rows else None,
        "counts":dict(Counter(x["status"] for x in rows)),
        "closed":len(closed),"avg_closed_ret_pct":sum(closed)/len(closed) if closed else None,
        "pf":pf(closed),"weighted30_compounded_pct":(eq-1)*100
    }

def main():
    rows=load(); now=int(time.time()*1000)
    results={k:[] for k in WAITS}
    details=[]
    for r in rows:
        start=max(0,r["_ms"]-60000)
        end=min(now,r["_ms"]+HOLD[r["strategy"]]*60000+60000)
        cs=candles(r["symbol"],start,end)
        d={"time":r["timestamp_utc"],"symbol":r["symbol"],"strategy":r["strategy"],"limit":float(r["entry_price"])}
        for name,w in WAITS.items():
            e=evaluate(r,cs,name,w,now); results[name].append(e); d[name]=e
        details.append(d)
    print("[META]",json.dumps({"signals":len(rows)},ensure_ascii=False))
    for k in WAITS: print("[SUMMARY]",k,json.dumps(summary(results[k]),ensure_ascii=False,sort_keys=True))
    print("[DETAIL_BEGIN]")
    for d in details: print(json.dumps(d,ensure_ascii=False,sort_keys=True))
    print("[DETAIL_END]")
if __name__=="__main__": main()
