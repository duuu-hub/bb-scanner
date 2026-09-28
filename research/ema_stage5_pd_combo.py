#!/usr/bin/env python3
import argparse,io,zipfile,requests,pandas as pd,numpy as np,glob
from pathlib import Path

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-08-22",tz="UTC")
EMA_FULL_START=pd.Timestamp("2019-10-01",tz="UTC")
EMA_COST=0.001
STAGE5_HALF_FEE=0.00125

def norm_ts(s):
    x=pd.to_numeric(s,errors="coerce").to_numpy(dtype=float)
    x=np.where(x>1e14,x/1000.0,x)
    return pd.to_datetime(x,unit="ms",utc=True)

def dl_spot(symbol,tf,start,end,cache):
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True);out=[]
    for m in pd.date_range(start.floor("D").replace(day=1),end-pd.Timedelta(seconds=1),freq="MS"):
        ym=m.strftime("%Y-%m");fp=cache/f"{symbol}-{tf}-{ym}.csv.gz"
        if fp.exists(): q=pd.read_csv(fp,compression="gzip")
        else:
            u=f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/{tf}/{symbol}-{tf}-{ym}.zip"
            r=requests.get(u,timeout=90);r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                raw=pd.read_csv(z.open(next(n for n in z.namelist() if n.endswith(".csv"))),header=None)
            q=raw.iloc[:,[0,1,2,3,4,5]].copy();q.columns=["ts","open","high","low","close","volume"]
            q.to_csv(fp,index=False,compression="gzip")
        q["dt"]=norm_ts(q.ts)
        for c in ["open","high","low","close","volume"]:q[c]=pd.to_numeric(q[c],errors="coerce")
        out.append(q[["dt","open","high","low","close","volume"]].dropna())
    d=pd.concat(out,ignore_index=True).drop_duplicates("dt").sort_values("dt")
    return d[(d.dt>=start)&(d.dt<end)].reset_index(drop=True)

def ema_talib(s,n):
    o=pd.Series(np.nan,index=s.index);o.iloc[n-1]=s.iloc[:n].mean();a=2/(n+1)
    for i in range(n,len(s)):o.iloc[i]=a*s.iloc[i]+(1-a)*o.iloc[i-1]
    return o

def build_ema(hourly,funding,b15):
    d=hourly.set_index("dt").copy()
    f=pd.read_feather(funding);f["date"]=pd.to_datetime(f.date,utc=True);fr=f.set_index("date").sort_index()["funding"]
    d["funding"]=fr.reindex(d.index,method="ffill")
    d["f3"]=d.funding.rolling(72,min_periods=24).mean()
    d["fpct"]=d.f3.rolling(24*180,min_periods=24*30).rank(pct=True)*100
    d["ema"]=ema_talib(d.close,600)
    d["sig"]=(d.close>d.ema)&(d.close.shift(1)<=d.ema.shift(1))&((d.fpct<55)|d.fpct.isna())
    d["xit"]=(d.close<d.ema*.98)&(d.close.shift(1)>=d.ema.shift(1)*.98)

    # Reproduce original 1H engine exactly for parity.
    x=d.loc[EMA_FULL_START:"2026-08-21"].copy();cap=1.;pos=False;ep=0.;peak=0.;pending=None;tr=[]
    for dt,r in x.iterrows():
        if pending=="buy" and not pos:
            ep=float(r.open);peak=ep;cap*=1-EMA_COST;pos=True;ent=dt;pending=None
        elif pending=="sell" and pos:
            cap*=float(r.open)/ep*(1-EMA_COST);tr.append((ent,dt));pos=False;pending=None
        if pos:
            peak=max(peak,float(r.high));stop=peak*.85
            if float(r.low)<=stop:
                px=min(float(r.open),stop);cap*=px/ep*(1-EMA_COST);tr.append((ent,dt));pos=False;pending=None
        if not pos and bool(r.sig):pending="buy"
        elif pos and bool(r.xit):pending="sell"
    if pos:cap*=float(x.close.iloc[-1])/ep*(1-EMA_COST);pos=False
    original_full=cap
    assert abs(original_full-17.709626335603225)<2e-8, original_full

    # Canonicalize intrabar trailing on 15m sequence while keeping 1H signals/next-open execution.
    b=b15.set_index("dt").sort_index()
    grid=b[(b.index>=EMA_FULL_START)&(b.index<END)]
    cap=1.;pos=False;ep=0.;peak=0.;pending=None;marks=[];trades=[];ent=None
    for dt,r in grid.iterrows():
        if dt.minute==0:
            if pending=="buy" and not pos:
                ep=float(r.open);peak=ep;cap*=1-EMA_COST;pos=True;ent=dt;pending=None
            elif pending=="sell" and pos:
                cap*=float(r.open)/ep*(1-EMA_COST);trades.append((ent,dt,"XIT"));pos=False;pending=None
        active_open=bool(pos)
        if pos:
            peak=max(peak,float(r.high));stop=peak*.85
            if float(r.low)<=stop:
                px=min(float(r.open),stop);cap*=px/ep*(1-EMA_COST);trades.append((ent,dt+pd.Timedelta(minutes=15),"TRAIL"));pos=False;pending=None
        mark=cap*(float(r.close)/ep if pos else 1.0)
        marks.append((dt+pd.Timedelta(minutes=15),mark,active_open))
        if dt.minute==45:
            h=dt.floor("h")
            if h in d.index:
                if not pos and bool(d.at[h,"sig"]):pending="buy"
                elif pos and bool(d.at[h,"xit"]):pending="sell"
    if pos:
        last=grid.iloc[-1];cap*=float(last.close)/ep*(1-EMA_COST);trades.append((ent,grid.index[-1]+pd.Timedelta(minutes=15),"END"));pos=False
        marks[-1]=(marks[-1][0],cap,marks[-1][2])
    M=pd.DataFrame(marks,columns=["time","equity","active_open"]).drop_duplicates("time",keep="last").set_index("time")
    # normalize common-period curve
    pre=M[M.index<=START]
    base=float(pre.equity.iloc[-1]) if len(pre) else float(M.iloc[0].equity)
    C=M[(M.index>START)&(M.index<=END)].copy();C["equity"]=C.equity/base
    prev_eq=pd.Series([1.0],index=[START])
    eq=pd.concat([prev_eq,C.equity]).sort_index()
    rets=eq.pct_change().fillna(0)
    active=C.active_open.astype(bool)
    return original_full,cap,M,C,rets,active,pd.DataFrame(trades,columns=["entry","exit","reason"])

def build_stage5(stage5,b15,e15):
    C=pd.read_csv(stage5,parse_dates=["datetime_utc"]).sort_values("datetime_utc").copy()
    C["held_day"]=C.datetime_utc.dt.floor("D")+pd.Timedelta(days=1)
    C["state"]=(C.position>0).astype(int)
    C["gross_pct"]=C.state*(C.BTCUSDT_fwd.fillna(0)+C.ETHUSDT_fwd.fillna(0))/2.0
    H=C[(C.held_day>=START)&(C.held_day<END)].copy()
    before=C[C.held_day<START];prev=int(before.state.iloc[-1]) if len(before) else 0
    amap=H.set_index("held_day").state.to_dict();gmap=H.set_index("held_day").gross_pct.to_dict()
    b=b15.set_index("dt").sort_index();e=e15.set_index("dt").sort_index()
    cash=1.;stake=0.;day=None;daygross=0.;rows=[]

    for t in pd.date_range(START,END,freq="15min",inclusive="both"):
        if t.floor("D")==t:
            if day is not None and int(amap.get(day,0)):
                cash+=stake*daygross
            if t>=END:
                rows.append((t,cash,False));break
            st=int(amap.get(t,0));base=cash
            fee=abs(st-prev)*STAGE5_HALF_FEE
            cash-=base*fee;stake=base if st else 0.;day=t;daygross=0.;prev=st

        st=int(amap.get(t.floor("D"),0))
        active=bool(st)
        if active:
            d=t.floor("D")
            bo=float(b.loc[d,"open"]) if d in b.index else float(b[b.index<d].close.iloc[-1])
            eo=float(e.loc[d,"open"]) if d in e.index else float(e[e.index<d].close.iloc[-1])
            bc=float(b.loc[t,"close"]) if t in b.index else float(b[b.index<t].close.iloc[-1])
            ec=float(e.loc[t,"close"]) if t in e.index else float(e[e.index<t].close.iloc[-1])
            raw=.5*((bc/bo-1)+(ec/eo-1))
            de=d+pd.Timedelta(hours=23,minutes=45)
            bce=float(b.loc[de,"close"]) if de in b.index else float(b[b.index<=de].close.iloc[-1])
            ece=float(e.loc[de,"close"]) if de in e.index else float(e[e.index<=de].close.iloc[-1])
            raw_end=.5*((bce/bo-1)+(ece/eo-1))
            target=float(gmap.get(d,0.0))/100.0
            daygross=raw*(target/raw_end if abs(raw_end)>1e-15 else 1.)
        eq=cash+stake*daygross
        rows.append((t+pd.Timedelta(minutes=15),eq,active))

    M=pd.DataFrame(rows,columns=["time","equity","active_open"]).drop_duplicates("time",keep="last").set_index("time")
    M=M[(M.index>START)&(M.index<=END)]
    eq=pd.concat([pd.Series([1.0],index=[START]),M.equity]).sort_index()
    ret=eq.pct_change().fillna(0)
    return M,ret,M.active_open.astype(bool),amap

def load_pd(path,um):
    S=pd.read_csv(path,parse_dates=["entry_time","exit_time"]).copy()
    S=S[(S.entry_time>=START)&(S.entry_time<END)].sort_values(["entry_time","event_rank","symbol"])
    S["risk_pct"]=S.net_return/S.r_net;S["pid"]=np.arange(len(S))
    found={}
    for fn in glob.glob(str(Path(um)/"**/*.csv.gz"),recursive=True):
        sym=Path(fn).name.replace(".csv.gz","")
        if sym in set(S.symbol):found[sym]=fn
    miss=set(S.symbol)-set(found)
    if miss:raise AssertionError(f"missing UM {sorted(miss)[:5]}")
    px={}
    for sym,fn in found.items():
        q=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in q.columns else "timestamp_ms"
        q["dt"]=norm_ts(q[tc]);q["close"]=pd.to_numeric(q.close,errors="coerce")
        px[sym]=q[["dt","close"]].dropna().drop_duplicates("dt").set_index("dt").sort_index()
    return S,px

def pd_marks(S,px,bps):
    out={}
    for r in S.itertuples():
        cost_r=(bps/10000.0)/float(r.risk_pct);m={r.entry_time:-cost_r}
        w=px[r.symbol][(px[r.symbol].index>=r.entry_time)&(px[r.symbol].index+pd.Timedelta(minutes=15)<r.exit_time)]
        for ot,z in w.iterrows():m[ot+pd.Timedelta(minutes=15)]=(float(r.entry)-float(z.close))/float(r.riskdist)-cost_r
        out[int(r.pid)]=m
    return out

def mdd(eq):
    p=np.maximum.accumulate(np.asarray(eq,float));return float(np.max((p-np.asarray(eq,float))/p))

def simulate_combo(name,S,pmarks,ema_ret,ema_active,s5_ret,s5_active,bps,rf=.008):
    times=pd.date_range(START+pd.Timedelta(minutes=15),END,freq="15min")
    er=ema_ret.reindex(times).fillna(0.0);ea=ema_active.reindex(times).fillna(False).astype(bool)
    sr=s5_ret.reindex(times).fillna(0.0);sa=s5_active.reindex(times).fillna(False).astype(bool)
    entries={et:g for et,g in S.groupby("entry_time",sort=True)}
    cash=1.;openp=[];curve=[];accepted=[];overlap=0
    for mt in times:
        ot=mt-pd.Timedelta(minutes=15)
        # Admission is decided at interval OPEN. Positions exiting at mt still occupy a slot at ot.
        long_on=bool(ea.loc[mt]) if name=="EMA_PD" else bool(ea.loc[mt] or sa.loc[mt])
        if ot in entries and not long_on:
            free=10-len(openp);base=cash
            for r in entries[ot].head(max(0,free)).itertuples():
                rr=(float(r.net_return)+.0008-bps/10000.0)/float(r.risk_pct)
                p=dict(pid=int(r.pid),entry_time=r.entry_time,exit_time=r.exit_time,stake=base*rf,r_real=rr)
                openp.append(p);accepted.append(p)
        if long_on and openp:overlap+=1

        if name=="EMA_PD":
            lr=float(er.loc[mt]) if bool(ea.loc[mt]) else 0.
        else:
            vals=[]
            if bool(ea.loc[mt]):vals.append(float(er.loc[mt]))
            if bool(sa.loc[mt]):vals.append(float(sr.loc[mt]))
            lr=float(np.mean(vals)) if vals else 0.
        cash*=1+lr

        # Outcomes inside this 15m interval become known only at its close mt.
        done=[p for p in openp if p["exit_time"]<=mt]
        for p in sorted(done,key=lambda x:(x["exit_time"],x["pid"])):
            cash+=p["stake"]*p["r_real"];openp.remove(p)

        unr=0.
        for p in openp:
            mm=pmarks[p["pid"]];ks=[k for k in mm if k<=mt]
            rr=mm[max(ks)] if ks else 0.;unr+=p["stake"]*rr
        curve.append((mt,cash+unr,cash,len(openp),int(bool(ea.loc[mt])),int(bool(sa.loc[mt]))))
    # realize anything exactly at END
    for p in sorted(openp,key=lambda x:x["exit_time"]):
        cash+=p["stake"]*p["r_real"]
    C=pd.DataFrame(curve,columns=["time","equity","cash","open_pd","ema_active","stage5_active"])
    return dict(name=name,cost_bps=bps,risk=rf,final_equity=float(cash),mtm_mdd=mdd(C.equity),
                pd_trades=len(accepted),pd_events=len(set(p["entry_time"] for p in accepted)),
                overlap_15m_bars=overlap),C

def standalone(eqseries):
    x=np.asarray(eqseries,float);return float(x[-1]),mdd(x)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--funding",required=True);ap.add_argument("--stage5",required=True)
    ap.add_argument("--selected",required=True);ap.add_argument("--um",required=True);ap.add_argument("--out",required=True)
    ap.add_argument("--cache",default="spotcache");a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
    h=dl_spot("BTCUSDT","1h",pd.Timestamp("2019-01-01",tz="UTC"),END,a.cache)
    b15=dl_spot("BTCUSDT","15m",EMA_FULL_START,END,a.cache);e15=dl_spot("ETHUSDT","15m",START,END,a.cache)
    orig,canon_full,emafull,emaC,er,ea,tr=build_ema(h,a.funding,b15)
    s5M,sr,sa,amap=build_stage5(a.stage5,b15[b15.dt>=START],e15)
    S,px=load_pd(a.selected,a.um)
    rows=[]
    # standalone diagnostics common period
    e_eq=pd.concat([pd.Series([1.0],index=[START]),emaC.equity]).sort_index()
    s_eq=pd.concat([pd.Series([1.0],index=[START]),s5M.equity]).sort_index()
    efin,emdd=standalone(e_eq.values);sfin,smdd=standalone(s_eq.values)
    meta=dict(original_1h_full_equity=orig,canonical_15m_full_equity=canon_full,
              common_start=str(START),common_end=str(END),ema_common_equity=efin,ema_common_mdd=emdd,
              stage5_common_equity=sfin,stage5_common_mdd=smdd,ema_trades=len(tr))
    print("META",meta)
    for bps in [8,24]:
        pm=pd_marks(S,px,bps)
        for name in ["EMA_PD","EMA_STAGE5_PD"]:
            r,c=simulate_combo(name,S,pm,er,ea,sr,sa,bps,.008);rows.append(r)
            c.to_csv(O/f"{name.lower()}_{bps}bp_curve.csv",index=False)
            print("RESULT",r)
    pd.DataFrame(rows).to_csv(O/"combo_summary.csv",index=False)
    pd.DataFrame([meta]).to_csv(O/"meta.csv",index=False)
    tr.to_csv(O/"ema_canonical_trades.csv",index=False)

if __name__=="__main__":main()
