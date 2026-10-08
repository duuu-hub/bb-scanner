#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json, os, csv, io, zipfile, urllib.request
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timezone
import numpy as np, pandas as pd
import s2_current_rule_5y as bb

BAR=bb.BAR; MIN=bb.MIN
THRESHOLDS=(5,6)
TARGETS=("far2","far3")
SLS=(2.0,3.0,4.0,5.0)
HOLDS={"1h":4,"2h":8,"4h":16}
BASE_1M="https://data.binance.vision/data/futures/um/daily/klines"

def states(raw):
    ts=raw["ts"]; live=raw["open"]; close=raw["close"]; n=len(ts)
    upp={}; ab={}; valid=np.ones(n,bool)
    for name,(dur,off) in bb.TF.items():
        if name=="15M": ct=ts+BAR; cc=close
        else: ct,cc=bb.complete_tf(ts,close,dur,off)
        v,a,d=bb.bb_state_vec(ts,live,ct,cc,dur,off)
        valid &= v; ab[name]=a
        u=np.full(n,np.nan)
        ok=v & np.isfinite(d) & (1+d/100>0)
        u[ok]=live[ok]/(1+d[ok]/100)
        upp[name]=u
    count=np.zeros(n,np.int8)
    for name in bb.TF: count += ab[name].astype(np.int8)
    return valid,ab,upp,count

@lru_cache(maxsize=2048)
def load_1m_day(symbol,day):
    url=f"{BASE_1M}/{symbol}/1m/{symbol}-1m-{day}.zip"
    req=urllib.request.Request(url,headers={"User-Agent":"openbb-first-touch/1.0"})
    try:
        with urllib.request.urlopen(req,timeout=60) as r: raw=r.read()
        out=[]
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names=[n for n in zf.namelist() if n.endswith(".csv")]
            if not names:return None
            with io.TextIOWrapper(zf.open(names[0]),encoding="utf-8") as fh:
                for row in csv.reader(fh):
                    if len(row)<5 or not str(row[0]).isdigit():continue
                    ts=int(row[0]); ts=ts//1000 if ts>10**14 else ts
                    out.append((ts,float(row[1]),float(row[2]),float(row[3]),float(row[4])))
        return out
    except Exception:
        return None

def resolve_1m(symbol,parent_ts,tp,sl,entry_minute=True):
    day=datetime.fromtimestamp(parent_ts/1000,tz=timezone.utc).strftime("%Y-%m-%d")
    rows=load_1m_day(symbol,day)
    if rows is None:return {"status":"DATA_GAP"}
    seg=[r for r in rows if parent_ts<=r[0]<parent_ts+BAR]
    if len(seg)!=15 or seg[0][0]!=parent_ts or any(seg[i][0]-seg[i-1][0]!=MIN for i in range(1,15)):
        return {"status":"DATA_GAP"}
    for idx,(t,o,h,l,c) in enumerate(seg):
        th=l<=tp; sh=h>=sl
        if idx==0 and entry_minute and (th or sh):
            return {"status":"SL","exit_ts":t+MIN,"via":"entry_minute_conservative_loss"}
        if th and sh:return {"status":"SL","exit_ts":t+MIN,"via":"same_1m_both_loss"}
        if sh:return {"status":"SL","exit_ts":t+MIN,"via":"1m"}
        if th:return {"status":"TP","exit_ts":t+MIN,"via":"1m"}
    return {"status":"NONE"}

def outcome(symbol,raw,i,tp,sl_pct,hold_bars):
    ts=raw["ts"];op=raw["open"];hi=raw["high"];lo=raw["low"];cl=raw["close"]
    entry=float(op[i]); et=int(ts[i]); sl=entry*(1+sl_pct/100)
    if not (0<tp<entry):return {"status":"BAD_TARGET"}
    # parent entry bar
    th=lo[i]<=tp; sh=hi[i]>=sl
    if th or sh:
        r=resolve_1m(symbol,et,tp,sl,True)
        if r["status"]=="DATA_GAP":return r
        if r["status"]=="TP":
            return {"status":"TP","gross_pct":(entry-tp)/entry*100,"exit_ts":r["exit_ts"]}
        if r["status"]=="SL":
            return {"status":"SL","gross_pct":-sl_pct,"exit_ts":r["exit_ts"]}
        return {"status":"EXIT_MISMATCH"}

    for k in range(1,hold_bars):
        j=i+k
        expected=et+k*BAR
        if j>=len(ts) or int(ts[j])!=expected:return {"status":"DATA_GAP"}
        th=lo[j]<=tp; sh=hi[j]>=sl
        if th and sh:
            r=resolve_1m(symbol,expected,tp,sl,False)
            if r["status"]=="DATA_GAP":return r
            if r["status"]=="TP":
                return {"status":"TP","gross_pct":(entry-tp)/entry*100,"exit_ts":r["exit_ts"]}
            if r["status"]=="SL":
                return {"status":"SL","gross_pct":-sl_pct,"exit_ts":r["exit_ts"]}
            return {"status":"EXIT_MISMATCH"}
        if sh:return {"status":"SL","gross_pct":-sl_pct,"exit_ts":expected+BAR}
        if th:return {"status":"TP","gross_pct":(entry-tp)/entry*100,"exit_ts":expected+BAR}

    deadline=et+hold_bars*BAR
    j=i+hold_bars
    if j>=len(ts) or int(ts[j])!=deadline:return {"status":"DATA_GAP"}
    exit_px=float(op[j])
    return {"status":"TIME","gross_pct":(entry-exit_px)/entry*100,"exit_ts":deadline}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--raw",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    files=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    rows=[];meta={"signals":{},"excluded":{}}
    for n in THRESHOLDS:
        meta["signals"][str(n)]=0
    for pidx,p in enumerate(files,1):
        sym=bb.sym_from_path(p); raw=bb.load_raw(p)
        ts=raw["ts"];op=raw["open"]
        valid,ab,upp,count=states(raw)
        prev=np.roll(count,1);prev[0]=-1
        cont=np.zeros(len(ts),bool);cont[1:]=(ts[1:]-ts[:-1])==BAR
        for n in THRESHOLDS:
            sig=np.where(valid & cont & (count>=n) & (prev<n))[0]
            meta["signals"][str(n)]+=int(len(sig))
            # build ranked targets once
            events=[]
            for i in sig:
                if i+16>=len(ts):continue
                bands=sorted([float(upp[name][i]) for name in bb.TF if ab[name][i] and np.isfinite(upp[name][i])],reverse=True)
                if len(bands)<n or len(bands)<3:continue
                tmap={"far2":bands[-2],"far3":bands[-3]}
                events.append((i,tmap))
            for target_name in TARGETS:
                for slp in SLS:
                    for hold_name,hold_bars in HOLDS.items():
                        busy=-1
                        for i,tmap in events:
                            et=int(ts[i])
                            if et<busy:continue
                            r=outcome(sym,raw,i,float(tmap[target_name]),slp,hold_bars)
                            st=r.get("status")
                            if st in {"DATA_GAP","EXIT_MISMATCH","BAD_TARGET"} or r.get("gross_pct") is None:
                                key=f"{n}:{target_name}:{slp}:{hold_name}:{st}"
                                meta["excluded"][key]=meta["excluded"].get(key,0)+1
                                continue
                            busy=int(r["exit_ts"])
                            rows.append({
                                "threshold":n,"target":target_name,"sl_pct":slp,"hold":hold_name,
                                "symbol":sym,"signal_ts":et,"entry":float(op[i]),"target_px":float(tmap[target_name]),
                                "target_dist_pct":(float(op[i])-float(tmap[target_name]))/float(op[i])*100,
                                "exit_ts":int(r["exit_ts"]),"status":st,"gross_pct":float(r["gross_pct"])
                            })
        print("FT_PROGRESS",pidx,"/",len(files),sym,flush=True)
    pd.DataFrame(rows).to_csv(out/"trades.csv.gz",index=False,compression="gzip")
    (out/"meta.json").write_text(json.dumps(meta,indent=2))
    print("FT_DONE rows",len(rows),"meta",json.dumps(meta),flush=True)

if __name__=="__main__":main()
