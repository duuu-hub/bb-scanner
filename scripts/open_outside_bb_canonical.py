#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json, os
from collections import defaultdict
from pathlib import Path
import numpy as np, pandas as pd
import s2_current_rule_5y as bb

BAR=bb.BAR;MIN=bb.MIN
COUNTS=(5,6,7)
MIN_DISTS=(0.8,1.2,1.6)
STOPS=(2.0,3.0,4.0,5.0)
HOLDS=(8,16) # 2h,4h
CUT=int(pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000)
START=int(pd.Timestamp("2021-01-01",tz="UTC").timestamp()*1000)

def states(raw):
    ts=raw["ts"];live=raw["open"];close=raw["close"];n=len(ts)
    valid=np.ones(n,bool);count=np.zeros(n,np.int8);above={};upp={}
    for name,(dur,offset) in bb.TF.items():
        if name=="15M":ct=ts+BAR;cc=close
        else:ct,cc=bb.complete_tf(ts,close,dur,offset)
        v,a,d=bb.bb_state_vec(ts,live,ct,cc,dur,offset)
        valid &= v;count += a.astype(np.int8);above[name]=a
        u=np.full(n,np.nan);ok=v&np.isfinite(d)&(1+d/100>0);u[ok]=live[ok]/(1+d[ok]/100)
        upp[name]=u
    return valid,count,above,upp

def parent_exact(symbol,parent,target,stop,entry_parent=False):
    rows=bb.day_rows(symbol,parent)
    if rows is None:return {"status":"DATA_GAP"}
    seg=[r for r in rows if parent<=r[0]<parent+BAR]
    if len(seg)!=15 or seg[0][0]!=parent or any(seg[i][0]-seg[i-1][0]!=MIN for i in range(1,15)):
        return {"status":"DATA_GAP"}
    for idx,(t,o,h,l,c) in enumerate(seg):
        th=l<=target;sh=h>=stop
        if entry_parent and idx==0 and (th or sh):
            return {"status":"SL","exit_ts":t+MIN,"via":"entry_1m_any_touch_loss"}
        if th and sh:return {"status":"SL","exit_ts":t+MIN,"via":"same_1m_both_loss"}
        if sh:return {"status":"SL","exit_ts":t+MIN,"via":"1m"}
        if th:return {"status":"TP","exit_ts":t+MIN,"via":"1m"}
    return {"status":"NONE"}

def outcome(symbol,raw,i,target,stop_pct,hold_bars):
    entry=float(raw["open"][i]);stop=entry*(1+stop_pct/100);entry_ts=int(raw["ts"][i])
    if not (0<target<entry):return {"status":"BAD_TARGET"}
    # Entry parent: inspect 1m only when parent OHLC says target/stop can be touched.
    th=float(raw["low"][i])<=target;sh=float(raw["high"][i])>=stop
    if th or sh:
        r=parent_exact(symbol,entry_ts,target,stop,True)
        if r["status"]=="TP":
            return {"status":"TP","gross_pct":(entry-target)/entry*100,"exit_ts":r["exit_ts"],"via":r["via"]}
        if r["status"]=="SL":
            return {"status":"SL","gross_pct":-stop_pct,"exit_ts":r["exit_ts"],"via":r["via"]}
        if r["status"]!="NONE":return r
        return {"status":"EXIT_MISMATCH"}
    end=i+hold_bars
    if end>=len(raw["ts"]):return {"status":"DATA_GAP"}
    for j in range(i+1,end):
        expected=entry_ts+(j-i)*BAR
        if int(raw["ts"][j])!=expected:return {"status":"DATA_GAP"}
        th=float(raw["low"][j])<=target;sh=float(raw["high"][j])>=stop
        if th and sh:
            r=parent_exact(symbol,int(raw["ts"][j]),target,stop,False)
            if r["status"]=="TP":return {"status":"TP","gross_pct":(entry-target)/entry*100,"exit_ts":r["exit_ts"],"via":"collision_1m"}
            if r["status"]=="SL":return {"status":"SL","gross_pct":-stop_pct,"exit_ts":r["exit_ts"],"via":"collision_1m"}
            return r
        if sh:return {"status":"SL","gross_pct":-stop_pct,"exit_ts":int(raw["ts"][j])+BAR,"via":"15m"}
        if th:return {"status":"TP","gross_pct":(entry-target)/entry*100,"exit_ts":int(raw["ts"][j])+BAR,"via":"15m"}
    deadline=entry_ts+hold_bars*BAR
    if int(raw["ts"][end])!=deadline:return {"status":"DATA_GAP"}
    exitp=float(raw["open"][end]);gross=(entry-exitp)/entry*100
    return {"status":"TIME","gross_pct":gross,"exit_ts":deadline,"via":"deadline_open"}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--raw",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    agg=defaultdict(lambda:{"n":0,"wins":0,"gross":0.0,"gp20":0.0,"gl20":0.0,"gp40":0.0,"gl40":0.0,
                            "tp":0,"sl":0,"time":0,"entry1m":0,"dist":0.0,"excluded":0})
    for k,p in enumerate(fs,1):
        sym=bb.sym_from_path(p);raw=bb.load_raw(p);ts=raw["ts"]
        valid,count,above,upp=states(raw)
        prev=np.roll(count,1);prev[0]=0
        cont=np.zeros(len(ts),bool);cont[1:]=(ts[1:]-ts[:-1])==BAR
        for n in COUNTS:
            ix=np.where(valid&cont&(count>=n)&(prev<n))[0]
            candidates=[]
            for i in ix:
                st=int(ts[i])
                if st<START:continue
                bands=[upp[name][i] for name in bb.TF if above[name][i] and np.isfinite(upp[name][i])]
                if len(bands)<n:continue
                target=float(np.median(np.asarray(bands,float)));entry=float(raw["open"][i])
                dist=(entry-target)/entry*100
                if dist<=0:continue
                candidates.append((i,target,dist))
            for mind in MIN_DISTS:
                cc=[x for x in candidates if x[2]>=mind]
                for stop in STOPS:
                    for hold in HOLDS:
                        busy=-1
                        for i,target,dist in cc:
                            st=int(ts[i])
                            if st<busy:continue
                            r=outcome(sym,raw,i,target,stop,hold);status=r.get("status")
                            if status in {"DATA_GAP","EXIT_MISMATCH","BAD_TARGET"} or r.get("gross_pct") is None:
                                continue
                            busy=int(r["exit_ts"]);gross=float(r["gross_pct"])
                            split="TRAIN" if st<CUT and busy<CUT else ("SEEN" if st>=CUT else "BOUNDARY")
                            year=int(pd.Timestamp(st,unit="ms",tz="UTC").year)
                            for seg in (split,f"Y{year}","ALL"):
                                q=agg[(n,mind,stop,hold,seg)];q["n"]+=1;q["wins"]+=int(gross>0);q["gross"]+=gross;q["dist"]+=dist
                                q["tp"]+=int(status=="TP");q["sl"]+=int(status=="SL");q["time"]+=int(status=="TIME")
                                q["entry1m"]+=int(r.get("via")=="entry_1m_any_touch_loss")
                                for cost,pk,nk in ((.20,"gp20","gl20"),(.40,"gp40","gl40")):
                                    net=gross-cost
                                    if net>0:q[pk]+=net
                                    elif net<0:q[nk]+=-net
        print("CANON_PROGRESS",k,"/",len(fs),sym,"cache",bb.load_1m_day.cache_info(),flush=True)
    rows=[]
    for (n,mind,stop,hold,seg),q in agg.items():
        rows.append({"fresh_atleast":n,"min_target_dist_pct":mind,"stop_pct":stop,"hold_h":hold/4,"segment":seg,**q,
                     "wr_pct":100*q["wins"]/q["n"] if q["n"] else None,
                     "avg_gross_pct":q["gross"]/q["n"] if q["n"] else None,
                     "avg_target_dist_pct":q["dist"]/q["n"] if q["n"] else None,
                     "pf20":q["gp20"]/q["gl20"] if q["gl20"] else None,
                     "pf40":q["gp40"]/q["gl40"] if q["gl40"] else None,
                     "tp_pct":100*q["tp"]/q["n"] if q["n"] else None,
                     "sl_pct":100*q["sl"]/q["n"] if q["n"] else None,
                     "time_pct":100*q["time"]/q["n"] if q["n"] else None,
                     "entry1m_loss_pct":100*q["entry1m"]/q["n"] if q["n"] else None})
    pd.DataFrame(rows).to_csv(out/"canonical.csv",index=False)
    print("CANON_DONE",len(rows),flush=True)
if __name__=="__main__":main()
