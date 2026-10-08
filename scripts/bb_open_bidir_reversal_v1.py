#!/usr/bin/env python3
"""Exploratory symmetrical open-outside BB reversal with canonical first-touch resolution."""
from __future__ import annotations
import argparse, glob, json, os, csv, io, zipfile, urllib.request
from functools import lru_cache
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import s2_current_rule_5y as base

BAR=base.BAR; MIN=base.MIN
TF=base.TF
CONFIRMS=("FIRST_BODY","BREAK_SETUP_EXTREME","RETURN_15M")
TARGETS=("far2","far3")
STOPS=(3.0,5.0)
THRESHOLDS=(5,6)
SIDES=("SHORT","LONG")
HOLD_BARS=16

def both_bands(ts,live,ct,cl,dur,offset):
    """Complete prior 19 TF bars, plus current exactly observed open."""
    idx=np.searchsorted(ct,ts,side="right")
    valid=np.zeros(len(ts),bool)
    up=np.full(len(ts),np.nan)
    dn=np.full(len(ts),np.nan)
    ii=np.where(idx>=19)[0]
    if len(ii)==0:return valid,up,dn
    j=idx[ii]
    expected=((ts[ii]-offset)//dur)*dur+offset
    keep=(ct[j-1]==expected)&(ct[j-1]-ct[j-19]==18*dur)
    ii=ii[keep]; j=idx[ii]
    if len(ii)==0:return valid,up,dn
    p=np.zeros(len(cl)+1);p2=np.zeros(len(cl)+1)
    np.cumsum(cl,out=p[1:]);np.cumsum(cl*cl,out=p2[1:])
    total=p[j]-p[j-19]+live[ii]
    sq=p2[j]-p2[j-19]+live[ii]**2
    mu=total/20.0
    sd=np.sqrt(np.maximum(0.0,sq/20.0-mu**2))
    upper=mu+2*sd; lower=mu-2*sd
    ok=(upper>0)&(lower>0)&np.isfinite(upper)&np.isfinite(lower)
    valid[ii[ok]]=True;up[ii[ok]]=upper[ok];dn[ii[ok]]=lower[ok]
    return valid,up,dn

def build_states(raw):
    ts=raw["ts"];op=raw["open"];cl=raw["close"];N=len(ts)
    up={};dn={};valid=np.ones(N,bool)
    for name,(dur,off) in TF.items():
        if name=="15M": ct=ts+BAR; cc=cl
        else:ct,cc=base.complete_tf(ts,cl,dur,off)
        v,u,d=both_bands(ts,op,ct,cc,dur,off)
        valid &= v;up[name]=u;dn[name]=d
    high=np.zeros(N,np.int8);low=np.zeros(N,np.int8)
    for name in TF:
        high+=(op>up[name]).astype(np.int8)
        low+=(op<dn[name]).astype(np.int8)
    cont=np.zeros(N,bool)
    cont[1:]=(ts[1:]-ts[:-1]==BAR)
    validprev=np.zeros(N,bool);validprev[1:]=valid[:-1]
    return valid,validprev,cont,high,low,up,dn

@lru_cache(maxsize=768)
def minute_day(symbol,day):
    url=f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/1m/{symbol}-1m-{day}.zip"
    req=urllib.request.Request(url,headers={"User-Agent":"bb-bidir-reversal-research/1.0"})
    try:
        with urllib.request.urlopen(req,timeout=25) as r:b=r.read()
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            names=[x for x in z.namelist() if x.endswith(".csv")]
            if not names:return None
            lines=[]
            with io.TextIOWrapper(z.open(names[0]),encoding="utf-8") as f:
                for x in csv.reader(f):
                    if len(x)<5 or not x[0].isdigit(): continue
                    t=int(x[0]);t=t//1000 if t>10**14 else t
                    lines.append((t,float(x[1]),float(x[2]),float(x[3]),float(x[4])))
        return lines
    except Exception:
        return None

def exit_1m(symbol,parent,entry,sl,tp,direction,entry_bar):
    """Market entry at parent OPEN; entry 1m any touch LOSS. Else chronology of 1m bars."""
    day=datetime.fromtimestamp(parent/1000,timezone.utc).strftime("%Y-%m-%d")
    rows=minute_day(symbol,day)
    if rows is None:return ("DATA_GAP",None)
    seg=[r for r in rows if parent<=r[0]<parent+BAR]
    if len(seg)!=15 or seg[0][0]!=parent or any(seg[j][0]-seg[j-1][0]!=MIN for j in range(1,15)):
        return ("DATA_GAP",None)
    for j,(ts,o,h,l,c) in enumerate(seg):
        hit_tp=(l<=tp) if direction=="SHORT" else (h>=tp)
        hit_sl=(h>=sl) if direction=="SHORT" else (l<=sl)
        if j==0 and entry_bar and (hit_sl or hit_tp):
            return ("SL",ts+MIN)
        if hit_sl:return ("SL",ts+MIN)
        if hit_tp:return ("TP",ts+MIN)
    return ("NONE",None)

def outcome(sym,raw,entry_idx,tp,direction,sl_pct):
    ts=raw["ts"];op=raw["open"];hi=raw["high"];lo=raw["low"]
    entry=float(op[entry_idx]);enter=int(ts[entry_idx])
    if entry<=0 or not np.isfinite(tp):return ("BAD_TARGET",None,None)
    if direction=="SHORT":
        if tp>=entry:return ("TARGET_ALREADY_PASSED",None,None)
        sl=entry*(1+sl_pct/100)
    else:
        if tp<=entry:return ("TARGET_ALREADY_PASSED",None,None)
        sl=entry*(1-sl_pct/100)
    def touch(j):
        return ((lo[j]<=tp,hi[j]>=sl) if direction=="SHORT" else (hi[j]>=tp,lo[j]<=sl))
    for k in range(HOLD_BARS):
        j=entry_idx+k
        if j>=len(ts) or int(ts[j])!=enter+k*BAR:return ("DATA_GAP",None,None)
        ht,hs=touch(j)
        if not ht and not hs:continue
        if k==0 or (ht and hs):
            status,exit_ts=exit_1m(sym,int(ts[j]),entry,sl,tp,direction,k==0)
            if status not in ("TP","SL"):return ("EXIT_MISMATCH" if status=="NONE" else status,None,None)
            x=exit_ts
        else:
            status="TP" if ht else "SL";x=int(ts[j])+BAR
        gross=(abs(entry-tp)/entry*100) if status=="TP" else -sl_pct
        return (status,gross,x)
    xidx=entry_idx+HOLD_BARS; deadline=enter+HOLD_BARS*BAR
    if xidx>=len(ts) or int(ts[xidx])!=deadline:return ("DATA_GAP",None,None)
    px=float(op[xidx])
    gross=((entry-px)/entry*100) if direction=="SHORT" else ((px-entry)/entry*100)
    return ("TIME",gross,deadline)

def find_entry(raw,idx,confirm,direction,band15):
    ts=raw["ts"];op=raw["open"];lo=raw["low"];hi=raw["high"];cl=raw["close"]
    sign=-1 if direction=="SHORT" else 1
    for j in range(idx, min(idx+4,len(ts)-1)):
        if int(ts[j])!=int(ts[idx])+(j-idx)*BAR or int(ts[j+1])!=int(ts[idx])+(j-idx+1)*BAR:
            return None
        if confirm=="FIRST_BODY":
            ok=(cl[j]<op[j]) if sign<0 else (cl[j]>op[j])
        elif confirm=="BREAK_SETUP_EXTREME":
            ok=(j>idx and cl[j]<lo[idx]) if sign<0 else (j>idx and cl[j]>hi[idx])
        elif confirm=="RETURN_15M":
            if not np.isfinite(band15):return None
            ok=(cl[j]<band15) if sign<0 else (cl[j]>band15)
        else:raise RuntimeError(confirm)
        if ok:return j+1
    return None

def self_test():
    ts=1_800_000_000_000
    rows=lambda specials: [(ts+k*MIN,100.0, specials.get(k,(101.0,99.0))[0],specials.get(k,(101.0,99.0))[1],100.0) for k in range(15)]
    original=globals()["minute_day"]
    try:
        def check(side,hit,expected,entry):
            globals()["minute_day"]=lambda s,d: rows(hit)
            tp,sl=(90.,104.) if side=="SHORT" else (110.,96.)
            r=exit_1m("XUSDT",ts,100,sl,tp,side,entry)
            assert r[0]==expected,(side,hit,entry,r)
        check("SHORT",{0:(101,89)},"SL",True)  # entry-minute TP-only => loss
        check("SHORT",{0:(105,99)},"SL",True)
        check("SHORT",{0:(105,89)},"SL",True)
        check("SHORT",{0:(105,89)},"SL",False)
        check("SHORT",{3:(101,89)},"TP",True)
        check("SHORT",{3:(105,99)},"SL",True)
        check("LONG",{0:(111,99)},"SL",True)
        check("LONG",{0:(101,95)},"SL",True)
        check("LONG",{0:(111,95)},"SL",True)
        check("LONG",{0:(111,95)},"SL",False)
        check("LONG",{3:(111,99)},"TP",True)
        check("LONG",{3:(101,95)},"SL",True)
        globals()["minute_day"]=lambda s,d:None
        assert exit_1m("XUSDT",ts,100,96,110,"LONG",True)[0]=="DATA_GAP"
    finally: globals()["minute_day"]=original
    print("BIDIR_CHRONOLOGY_SMOKE_PASS 13/13",flush=True)

def main():
    pa=argparse.ArgumentParser();pa.add_argument("--raw",default="raw");pa.add_argument("--out",default="out")
    pa.add_argument("--self-test",action="store_true")
    args=pa.parse_args()
    if args.self_test:self_test();return
    os.makedirs(args.out,exist_ok=True)
    files=sorted(glob.glob(args.raw+"/**/*USDT.csv.gz",recursive=True))
    if not files:raise RuntimeError("raw empty")
    trades=[];diagnostics=[];counts=Counter();excludes=Counter()
    for seq,path in enumerate(files,1):
        sym=base.sym_from_path(path);raw=base.load_raw(path)
        ts=raw["ts"];op=raw["open"];lo=raw["low"];hi=raw["high"]
        v,vprev,cont,uppercount,lowercount,upp,dn=build_states(raw)
        for side in SIDES:
            count=uppercount if side=="SHORT" else lowercount
            bands=upp if side=="SHORT" else dn
            for n in THRESHOLDS:
                pcount=np.roll(count,1);pcount[0]=-1
                idxs=np.where(v & vprev & cont &(count>=n)&(pcount<n))[0]
                counts[f"raw_{side}_{n}"]+=len(idxs)
                events=[]
                for i in idxs:
                    if i+HOLD_BARS+5>=len(ts):continue
                    levels=[float(bands[name][i]) for name in TF if ((op[i]>upp[name][i]) if side=="SHORT" else (op[i]<dn[name][i]))]
                    if len(levels)<n:continue
                    levels.sort(reverse=(side=="SHORT")) # short nearest highest, long nearest lowest
                    tg={"far2":levels[-2],"far3":levels[-3]}
                    if any(not np.isfinite(x) for x in tg.values()):continue
                    events.append((i,tg))
                    # Raw touch diagnostic is NOT executable return
                    for k,px in tg.items():
                        diagnostics.append({"side":side,"threshold":n,"target":k,"symbol":sym,"signal_ts":int(ts[i]),
                           "target_distance_pct":abs(float(op[i])-px)/float(op[i])*100,
                           "raw_hit_1h":int(((lo[i:i+4]<=px).any()) if side=="SHORT" else ((hi[i:i+4]>=px).any())),
                           "raw_hit_4h":int(((lo[i:i+16]<=px).any()) if side=="SHORT" else ((hi[i:i+16]>=px).any())),
                           "max_adverse_4h_pct":(float(np.max(hi[i:i+16]))/float(op[i])-1)*100 if side=="SHORT" else (1-float(np.min(lo[i:i+16]))/float(op[i]))*100})
                for conf in CONFIRMS:
                    for tg in TARGETS:
                        for sl in STOPS:
                            busy=-1
                            for i,targets in events:
                                if conf=="RETURN_15M":
                                    is_out=(op[i]>upp["15M"][i]) if side=="SHORT" else (op[i]<dn["15M"][i])
                                    if not is_out:continue
                                ei=find_entry(raw,i,conf,side,float(bands["15M"][i]))
                                if ei is None:
                                    counts[f"no_confirmation_{side}_{n}_{conf}"]+=1
                                    continue
                                et=int(ts[ei])
                                if et<busy:
                                    counts[f"overlap_{side}_{n}_{conf}_{tg}_{sl}"]+=1
                                    continue
                                px=float(targets[tg])
                                st,gross,x=outcome(sym,raw,ei,px,side,sl)
                                if gross is None:
                                    excludes[f"{side}_{n}_{conf}_{tg}_{sl}_{st}"]+=1
                                    continue
                                busy=x
                                trades.append({"symbol":sym,"side":side,"threshold":n,"confirmation":conf,"target":tg,"sl_pct":sl,"hold_h":4,
                                    "signal_ts":int(ts[i]),"entry_ts":et,"exit_ts":x,"entry":float(op[ei]),"target_px":px,
                                    "status":st,"gross_pct":gross})
        print("BIDIR_PROGRESS",seq,"/",len(files),sym,"trades",len(trades),flush=True)
    pd.DataFrame(trades).to_csv(Path(args.out)/"trades.csv.gz",index=False,compression="gzip")
    pd.DataFrame(diagnostics).to_csv(Path(args.out)/"diagnostics.csv.gz",index=False,compression="gzip")
    (Path(args.out)/"meta.json").write_text(json.dumps({"counts":dict(counts),"exclusions":dict(excludes),"files":len(files)},indent=2))
    print("BIDIR_SHARD_DONE",len(trades),len(diagnostics),minute_day.cache_info(),flush=True)

if __name__=="__main__":main()
