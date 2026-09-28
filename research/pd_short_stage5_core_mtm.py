#!/usr/bin/env python3
import argparse, glob, io, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-09-01",tz="UTC")
CORE_HALF_FEE=0.00125

def normalize_ms(s):
    x=pd.to_numeric(s,errors="coerce").to_numpy(dtype="float64")
    x=np.where(x>1e14,x/1000.0,x)
    return pd.to_datetime(x,unit="ms",utc=True)

def download_spot_15m(symbol,start,end,cache_dir):
    cache=Path(cache_dir);cache.mkdir(parents=True,exist_ok=True);frames=[]
    for month in pd.date_range(start.floor("D").replace(day=1),end-pd.Timedelta(seconds=1),freq="MS"):
        ym=month.strftime("%Y-%m"); fp=cache/f"{symbol}-15m-{ym}.csv.gz"
        if fp.exists():
            q=pd.read_csv(fp,compression="gzip")
        else:
            url=f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/15m/{symbol}-15m-{ym}.zip"
            r=requests.get(url,timeout=90);r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                name=next(n for n in z.namelist() if n.endswith(".csv"))
                raw=pd.read_csv(z.open(name),header=None)
            q=raw.iloc[:,[0,1,4]].copy();q.columns=["open_time","open","close"]
            q.to_csv(fp,index=False,compression="gzip")
        q["dt"]=normalize_ms(q.open_time)
        q["open"]=pd.to_numeric(q.open,errors="coerce");q["close"]=pd.to_numeric(q.close,errors="coerce")
        frames.append(q[["dt","open","close"]].dropna())
    d=pd.concat(frames,ignore_index=True).drop_duplicates("dt").sort_values("dt")
    return d[(d.dt>=start)&(d.dt<end)].reset_index(drop=True)

def load_um(root,symbols):
    found={}
    for fn in glob.glob(str(Path(root)/"**/*.csv.gz"),recursive=True):
        sym=Path(fn).name.replace(".csv.gz","")
        if sym in symbols: found[sym]=fn
    miss=sorted(set(symbols)-set(found))
    if miss: raise AssertionError(f"missing UM symbols: {miss[:10]}")
    out={}
    for sym,fn in found.items():
        d=pd.read_csv(fn,compression="gzip")
        tc="open_time" if "open_time" in d.columns else "timestamp_ms"
        d["dt"]=normalize_ms(d[tc]);d["close"]=pd.to_numeric(d.close,errors="coerce")
        out[sym]=d[["dt","close"]].dropna().sort_values("dt").drop_duplicates("dt").set_index("dt")
    return out

def prepare_core(stage5):
    C=pd.read_csv(stage5,parse_dates=["datetime_utc"]).sort_values("datetime_utc").copy()
    C["held_day"]=C.datetime_utc.dt.floor("D")+pd.Timedelta(days=1)
    C["core_long"]=(C.position>0).astype(int)
    gross=C.core_long*(C.BTCUSDT_fwd.fillna(0)+C.ETHUSDT_fwd.fillna(0))/2.0
    chg=C.core_long.diff().abs().fillna(C.core_long.abs())
    C["core_long_net_pct"]=gross-chg*(0.25/2.0)
    H=C[(C.held_day>=START)&(C.held_day<END)].copy()
    assert H.held_day.is_unique
    return H

def build_core_paths(H,btc,eth):
    b=btc.rename(columns={"open":"b_open","close":"b_close"})
    e=eth.rename(columns={"open":"e_open","close":"e_close"})
    x=b.merge(e,on="dt",how="inner").sort_values("dt");x["day"]=x.dt.dt.floor("D")
    rows=[];par=[]
    prev=0
    for r in H.itertuples():
        day=r.held_day;state=int(r.core_long);fee=abs(state-prev)*CORE_HALF_FEE
        if not state:
            expected=float(r.core_long_net_pct)/100.0
            if abs(expected+fee)>2e-10: raise AssertionError(("inactive parity",day,expected,fee))
            par.append((day,state,0,0.0,0.0,fee));prev=state;continue
        d=x[x.day==day].copy()
        if len(d)!=96: raise AssertionError(f"spot 15m bars {day}: {len(d)}")
        expected_times=pd.date_range(day,day+pd.Timedelta(hours=23,minutes=45),freq="15min")
        if not d.dt.reset_index(drop=True).equals(pd.Series(expected_times)): raise AssertionError(f"spot gap {day}")
        bo=float(d.iloc[0].b_open);eo=float(d.iloc[0].e_open)
        d["mark_time"]=d.dt+pd.Timedelta(minutes=15)
        d["gross"]=0.5*((d.b_close/bo-1)+(d.e_close/eo-1))
        expected=float(r.core_long_net_pct)/100.0+fee
        raw=float(d.iloc[-1].gross)
        if abs(raw-expected)>2e-5: raise AssertionError(("core endpoint",day,raw,expected))
        scale=expected/raw if abs(raw)>1e-15 else 1.0
        d["gross"]=d.gross*scale
        for z in d.itertuples(): rows.append((day,z.mark_time,float(z.gross)))
        par.append((day,state,96,raw,expected,fee));prev=state
    return pd.DataFrame(rows,columns=["day","mark_time","gross"]),pd.DataFrame(par,columns=["day","state","bars","raw_final","expected_final","fee"])

def prepare_pd(path,H):
    S=pd.read_csv(path,parse_dates=["entry_time","exit_time"]).copy()
    S=S[(S.entry_time>=START)&(S.entry_time<END)].sort_values(["entry_time","event_rank","symbol"])
    assert len(S)>0 and S.event_rank.max()<=10 and S.groupby("entry_time").size().max()<=10
    amap=H.set_index("held_day").core_long.to_dict()
    S["core_active"]=S.entry_time.dt.floor("D").map(amap).fillna(0).astype(int)
    S["risk_pct"]=S.net_return/S.r_net
    return S,amap

def admit_pd(S,rf,bps,amap):
    cash=1.0;active=[];accepted=[];pid=0
    # This admission pass is only for identity; stake is recomputed in combined simulator.
    for et,g in S.groupby("entry_time",sort=True):
        active=[p for p in active if p["exit_time"]>et]
        if int(amap.get(et.floor("D"),0)): continue
        free=10-len(active)
        for r in g.head(max(0,free)).itertuples():
            accepted.append(dict(pid=pid,symbol=r.symbol,entry_time=r.entry_time,exit_time=r.exit_time,
                                 entry=float(r.entry),riskdist=float(r.riskdist),r_net=float(r.r_net),
                                 net_return=float(r.net_return),risk_pct=float(r.risk_pct),event_rank=int(r.event_rank)))
            active.append(accepted[-1]);pid+=1
    return accepted

def build_pd_marks(acc,px,bps):
    rows=[]
    for p in acc:
        ser=px[p["symbol"]]
        w=ser[(ser.index>=p["entry_time"])&(ser.index+pd.Timedelta(minutes=15)<p["exit_time"])]
        cost_r=(bps/10000.0)/p["risk_pct"]
        rows.append((p["pid"],p["entry_time"],-cost_r))
        for ot,z in w.iterrows():
            mt=ot+pd.Timedelta(minutes=15)
            gross_r=(p["entry"]-float(z.close))/p["riskdist"]
            rows.append((p["pid"],mt,gross_r-cost_r))
    return pd.DataFrame(rows,columns=["pid","mark_time","mark_r"])

def simulate(H,S,core_marks,pd_marks,rf,bps):
    hmap=H.set_index("held_day")
    amap=hmap.core_long.to_dict();rmap=hmap.core_long_net_pct.to_dict()
    cm={d:g.set_index("mark_time").gross.to_dict() for d,g in core_marks.groupby("day")}
    pmark={pid:g.set_index("mark_time").mark_r.to_dict() for pid,g in pd_marks.groupby("pid")}
    cash=1.;book_peak=1.;book_mdd=0.;mtm_peak=1.;mtm_mdd=0.;openp=[];accepted=[];pid=0
    core_day=None;core_stake=0.;core_gross=0.;prev_state=0
    entries={et:g for et,g in S.groupby("entry_time",sort=True)}
    grid=pd.date_range(START,END,freq="15min",inclusive="both")

    def realize_pd(t):
        nonlocal cash,openp,book_peak,book_mdd
        done=[p for p in openp if p["exit_time"]<=t]
        for p in sorted(done,key=lambda x:(x["exit_time"],x["pid"])):
            cash+=p["stake"]*p["r_real"];openp.remove(p)
            book_peak=max(book_peak,cash);book_mdd=max(book_mdd,(book_peak-cash)/book_peak)

    def mark(t):
        nonlocal mtm_peak,mtm_mdd
        eq=cash
        if core_day is not None and int(amap.get(core_day,0)):
            eq+=core_stake*core_gross
        for p in openp:
            m=pmark[p["pid"]];ks=[k for k in m if k<=t]
            rr=m[max(ks)] if ks else 0.0
            eq+=p["stake"]*rr
        mtm_peak=max(mtm_peak,eq);mtm_mdd=max(mtm_mdd,(mtm_peak-eq)/mtm_peak)
        return eq

    for t in grid:
        if t.floor("D")==t:
            # Final mark & book prior core day at 00:00.
            if core_day is not None and int(amap.get(core_day,0)):
                core_gross=cm[core_day][t];mark(t)
                cash+=core_stake*core_gross
                book_peak=max(book_peak,cash);book_mdd=max(book_mdd,(book_peak-cash)/book_peak)
            realize_pd(t)
            if t>=END: break
            state=int(amap.get(t,0));base=cash;fee=abs(state-prev_state)*CORE_HALF_FEE
            if fee:
                cash-=base*fee;book_peak=max(book_peak,cash);book_mdd=max(book_mdd,(book_peak-cash)/book_peak)
            core_day=t;core_stake=base if state else 0.;core_gross=0.;prev_state=state
        else:
            realize_pd(t)
            if core_day is not None and int(amap.get(core_day,0)):
                core_gross=cm[core_day][t]

        if t in entries and int(amap.get(t.floor("D"),0))==0:
            g=entries[t];free=10-len(openp);base=cash;new=[]
            for r in g.head(max(0,free)).itertuples():
                rr=(float(r.net_return)+.0008-bps/10000.0)/float(r.risk_pct)
                p=dict(pid=pid,symbol=r.symbol,entry_time=r.entry_time,exit_time=r.exit_time,stake=base*rf,r_real=rr)
                pid+=1;new.append(p);accepted.append(p)
            openp.extend(new)
        mark(t)

    if openp: raise AssertionError(f"open PD remains {len(openp)}")
    return dict(final_equity=cash,booked_mdd=book_mdd,mtm_mdd=mtm_mdd,accepted=len(accepted),
                accepted_events=len(set(p["entry_time"] for p in accepted)),max_pid=pid)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--core",required=True);ap.add_argument("--selected",required=True);ap.add_argument("--um",required=True)
    ap.add_argument("--out",required=True);ap.add_argument("--spot-cache",default="spot15m")
    a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
    H=prepare_core(a.core);S,amap=prepare_pd(a.selected,H)
    btc=download_spot_15m("BTCUSDT",START,END,a.spot_cache);eth=download_spot_15m("ETHUSDT",START,END,a.spot_cache)
    core_marks,par=build_core_paths(H,btc,eth);par.to_csv(O/"core_15m_parity.csv",index=False)
    px=load_um(a.um,set(S.symbol))

    rows=[]
    for bps in [8,24]:
        # Build mark paths from the exact candidate IDs that can be admitted; all candidates are cheap enough to mark.
        allacc=[]
        pid=0
        for r in S.itertuples():
            allacc.append(dict(pid=pid,symbol=r.symbol,entry_time=r.entry_time,exit_time=r.exit_time,entry=float(r.entry),
                               riskdist=float(r.riskdist),risk_pct=float(r.risk_pct)));pid+=1
        # simulator assigns pid by admitted order; create a candidate-order pid map compatible with admissions
        # Re-map candidate pids sequentially per actual admission by building marks on demand-compatible order below.
        # Simpler: mark each selected row using unique key and let admission copy its mark id.
        # We achieve this by replacing event_rank order with a stable candidate id.
        S2=S.copy();S2["mark_pid"]=np.arange(len(S2))
        marks=[]
        for r in S2.itertuples():
            ser=px[r.symbol];cost_r=(bps/10000.0)/float(r.risk_pct)
            marks.append((int(r.mark_pid),r.entry_time,-cost_r))
            w=ser[(ser.index>=r.entry_time)&(ser.index+pd.Timedelta(minutes=15)<r.exit_time)]
            for ot,z in w.iterrows():
                marks.append((int(r.mark_pid),ot+pd.Timedelta(minutes=15),(float(r.entry)-float(z.close))/float(r.riskdist)-cost_r))
        PM=pd.DataFrame(marks,columns=["pid","mark_time","mark_r"])
        # convert simulator pid assignment to candidate mark_pid by storing mark_pid as event rank order index
        def sim_with_markpid():
            hmap=H.set_index("held_day");am=hmap.core_long.to_dict()
            cm={d:g.set_index("mark_time").gross.to_dict() for d,g in core_marks.groupby("day")}
            pm={pid:g.set_index("mark_time").mark_r.to_dict() for pid,g in PM.groupby("pid")}
            cash=1.;bp=1.;bm=0.;mp=1.;mm=0.;openp=[];acc=[];core_day=None;core_stake=0.;core_gross=0.;prev=int(H.attrs.get("initial_prev_state",0))
            ent={et:g for et,g in S2.groupby("entry_time",sort=True)}
            def rex(t):
                nonlocal cash,bp,bm,openp
                done=[p for p in openp if p["exit_time"]<=t]
                for p in sorted(done,key=lambda x:(x["exit_time"],x["mark_pid"])):
                    cash+=p["stake"]*p["r_real"];openp.remove(p);bp=max(bp,cash);bm=max(bm,(bp-cash)/bp)
            def mk(t):
                nonlocal mp,mm
                eq=cash
                if core_day is not None and int(am.get(core_day,0)): eq+=core_stake*core_gross
                for p in openp:
                    m=pm[p["mark_pid"]];ks=[k for k in m if k<=t];rr=m[max(ks)] if ks else 0.;eq+=p["stake"]*rr
                mp=max(mp,eq);mm=max(mm,(mp-eq)/mp)
            for t in pd.date_range(START,END,freq="15min",inclusive="both"):
                if t.floor("D")==t:
                    if core_day is not None and int(am.get(core_day,0)):
                        core_gross=cm[core_day][t];mk(t);cash+=core_stake*core_gross;bp=max(bp,cash);bm=max(bm,(bp-cash)/bp)
                    rex(t)
                    if t>=END: break
                    st=int(am.get(t,0));base=cash;fee=abs(st-prev)*CORE_HALF_FEE
                    if fee: cash-=base*fee;bp=max(bp,cash);bm=max(bm,(bp-cash)/bp)
                    core_day=t;core_stake=base if st else 0.;core_gross=0.;prev=st
                else:
                    rex(t)
                    if core_day is not None and int(am.get(core_day,0)): core_gross=cm[core_day][t]
                if t in ent and int(am.get(t.floor("D"),0))==0:
                    g=ent[t];free=10-len(openp);base=cash
                    for r in g.head(max(0,free)).itertuples():
                        rr=(float(r.net_return)+.0008-bps/10000.0)/float(r.risk_pct)
                        p=dict(mark_pid=int(r.mark_pid),entry_time=r.entry_time,exit_time=r.exit_time,stake=base*RF,r_real=rr)
                        openp.append(p);acc.append(p)
                mk(t)
            if openp: raise AssertionError(f"open PD remains {len(openp)}")
            return cash,bm,mm,len(acc),len(set(p["entry_time"] for p in acc))
        for RF in [.005,.0075,.01,.0125,.015]:
            cash,bm,mm,n,ne=sim_with_markpid()
            rows.append(dict(cost_bps=bps,risk=RF,final_equity=cash,booked_mdd=bm,mtm_mdd=mm,pd_trades=n,pd_events=ne))

    R=pd.DataFrame(rows);R.to_csv(O/"stage5_pd_15m_mtm.csv",index=False)
    print(R.to_string(index=False))

if __name__=="__main__": main()
