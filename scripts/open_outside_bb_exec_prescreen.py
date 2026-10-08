#!/usr/bin/env python3
from __future__ import annotations
import argparse, glob, json, os
from collections import defaultdict
from pathlib import Path
import numpy as np, pandas as pd
import s2_current_rule_5y as bb

BAR=bb.BAR
THRESH=(4,5,6,7)
STOPS=(2.0,3.0,4.0,5.0)
HOLDS=(4,8,16)  # 1h,2h,4h in 15m bars
COSTS=(0.20,0.40)
CUT=int(pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000)
START=int(pd.Timestamp("2021-01-01",tz="UTC").timestamp()*1000)

def states(raw):
    ts=raw["ts"];live=raw["open"];close=raw["close"];n=len(ts)
    above={};upp={};valid=np.ones(n,bool);count=np.zeros(n,np.int8)
    for name,(dur,offset) in bb.TF.items():
        if name=="15M":ct=ts+BAR;cc=close
        else:ct,cc=bb.complete_tf(ts,close,dur,offset)
        v,a,d=bb.bb_state_vec(ts,live,ct,cc,dur,offset)
        valid &= v;above[name]=a;count += a.astype(np.int8)
        u=np.full(n,np.nan);ok=v&np.isfinite(d)&(1+d/100>0);u[ok]=live[ok]/(1+d[ok]/100)
        upp[name]=u
    return valid,above,upp,count

def outcome(raw,i,target,stop_pct,hold_bars):
    entry=float(raw["open"][i]); stop=entry*(1+stop_pct/100)
    if target<=0 or target>=entry:return None
    end=i+hold_bars
    if end>=len(raw["ts"]):return None
    # Ultra-conservative entry parent: any target OR stop touch counts as loss.
    if float(raw["low"][i])<=target or float(raw["high"][i])>=stop:
        return -stop_pct,"ENTRY_BAR_CONSERVATIVE_LOSS",int(raw["ts"][i])+BAR
    for j in range(i+1,end):
        th=float(raw["low"][j])<=target;sh=float(raw["high"][j])>=stop
        if th and sh:return -stop_pct,"SAME15M_BOTH_LOSS",int(raw["ts"][j])+BAR
        if sh:return -stop_pct,"SL",int(raw["ts"][j])+BAR
        if th:
            gross=(entry-target)/entry*100.0
            return gross,"TARGET",int(raw["ts"][j])+BAR
    # time exit at next bar open at exact deadline
    exit_i=end
    if int(raw["ts"][exit_i])!=int(raw["ts"][i])+hold_bars*BAR:return None
    exitp=float(raw["open"][exit_i])
    return (entry-exitp)/entry*100.0,"TIME",int(raw["ts"][exit_i])

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--raw",required=True);ap.add_argument("--out",required=True)
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    fs=sorted(glob.glob(os.path.join(a.raw,"**","*USDT.csv.gz"),recursive=True))
    agg=defaultdict(lambda:{"n":0,"wins":0,"gross_sum":0.0,"gp20":0.0,"gl20":0.0,"gp40":0.0,"gl40":0.0,
                            "target":0,"sl":0,"time":0,"entryloss":0,"dist_sum":0.0})
    for k,p in enumerate(fs,1):
        sym=bb.sym_from_path(p);raw=bb.load_raw(p);ts=raw["ts"]
        valid,above,upp,count=states(raw)
        prev=np.roll(count,1);prev[0]=0
        cont=np.zeros(len(ts),bool);cont[1:]=(ts[1:]-ts[:-1])==BAR
        for n in THRESH:
            sig=np.where(valid & cont & (count>=n) & (prev<n))[0]
            # median of all currently breached upper bands, frozen at entry.
            entries=[]
            for i in sig:
                bands=[upp[name][i] for name in bb.TF if above[name][i] and np.isfinite(upp[name][i])]
                if len(bands)<n:continue
                target=float(np.median(np.asarray(bands,float)))
                dist=(float(raw["open"][i])-target)/float(raw["open"][i])*100.0
                if dist<=0:continue
                entries.append((i,target,dist))
            for stop in STOPS:
                for hold in HOLDS:
                    busy=-1
                    for i,target,dist in entries:
                        st=int(ts[i])
                        if st<START or st<busy:continue
                        o=outcome(raw,i,target,stop,hold)
                        if o is None:continue
                        gross,status,exit_ts=o;busy=exit_ts
                        split="TRAIN" if st<CUT and exit_ts<CUT else ("SEEN" if st>=CUT else "BOUNDARY")
                        year=int(pd.Timestamp(st,unit="ms",tz="UTC").year)
                        for lab in (split,f"Y{year}","ALL"):
                            key=(n,stop,hold,lab)
                            q=agg[key];q["n"]+=1;q["wins"]+=int(gross>0);q["gross_sum"]+=gross;q["dist_sum"]+=dist
                            q["target"]+=int(status=="TARGET");q["sl"]+=int(status in ("SL","SAME15M_BOTH_LOSS"))
                            q["time"]+=int(status=="TIME");q["entryloss"]+=int(status=="ENTRY_BAR_CONSERVATIVE_LOSS")
                            for cost,pk,nk in ((.20,"gp20","gl20"),(.40,"gp40","gl40")):
                                net=gross-cost
                                if net>0:q[pk]+=net
                                elif net<0:q[nk]+=-net
        print("EXEC_PROGRESS",k,"/",len(fs),sym,flush=True)
    rows=[]
    for (n,stop,hold,lab),q in agg.items():
        r={"fresh_atleast":n,"stop_pct":stop,"hold_h":hold/4,"segment":lab,**q}
        r["wr_pct"]=100*q["wins"]/q["n"] if q["n"] else None
        r["avg_gross_pct"]=q["gross_sum"]/q["n"] if q["n"] else None
        r["avg_target_dist_pct"]=q["dist_sum"]/q["n"] if q["n"] else None
        r["pf20"]=q["gp20"]/q["gl20"] if q["gl20"] else None
        r["pf40"]=q["gp40"]/q["gl40"] if q["gl40"] else None
        r["target_pct"]=100*q["target"]/q["n"] if q["n"] else None
        r["entryloss_pct"]=100*q["entryloss"]/q["n"] if q["n"] else None
        rows.append(r)
    pd.DataFrame(rows).to_csv(out/"prescreen.csv",index=False)
    print("PRESCREEN_DONE",len(rows),flush=True)
if __name__=="__main__":main()
