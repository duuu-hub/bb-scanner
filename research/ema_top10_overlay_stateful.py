#!/usr/bin/env python3
import argparse,importlib.util
from pathlib import Path
import numpy as np
import pandas as pd

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-08-22",tz="UTC")
COST=.001

def load_mod(path):
    sp=importlib.util.spec_from_file_location("combo",path)
    m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m

def prep_hourly(mod,hourly,funding):
    d=hourly.set_index("dt").copy()
    f=pd.read_feather(funding);f["date"]=pd.to_datetime(f.date,utc=True)
    fr=f.set_index("date").sort_index()["funding"]
    d["funding"]=fr.reindex(d.index,method="ffill")
    d["f3"]=d.funding.rolling(72,min_periods=24).mean()
    d["fpct"]=d.f3.rolling(24*180,min_periods=24*30).rank(pct=True)*100
    d["ema"]=mod.ema_talib(d.close,600)
    d["sig"]=(d.close>d.ema)&(d.close.shift(1)<=d.ema.shift(1))&((d.fpct<55)|d.fpct.isna())
    d["xit"]=(d.close<d.ema*.98)&(d.close.shift(1)>=d.ema.shift(1)*.98)
    return d

def fill_b15(b15):
    want=pd.date_range(START,END-pd.Timedelta(minutes=15),freq="15min",tz="UTC")
    px=b15.set_index("dt").sort_index().reindex(want)
    miss=px.close.isna()
    runs=(miss.astype(int).groupby((~miss).cumsum()).sum() if miss.any() else pd.Series([0]))
    max_run=int(runs.max()) if len(runs) else 0;nmiss=int(miss.sum())
    if max_run>4 or nmiss>100:
        raise AssertionError(("BTC15 gap too large",nmiss,max_run))
    prev=px.close.ffill()
    for c in ["open","high","low","close"]:
        px[c]=px[c].where(~miss,prev)
    if px[["open","high","low","close"]].isna().any().any():
        raise AssertionError("cannot fill leading BTC15 gap")
    return px,nmiss,max_run

def original_active(hour,px):
    pos=False;peak=0.;pending=None;active={};trail_exit={}
    for t,r in px.iterrows():
        if t.minute==0:
            if pending=="buy" and not pos:
                pos=True;peak=float(r.open);pending=None
            elif pending=="sell" and pos:
                pos=False;pending=None
        active[t]=bool(pos)
        if pos:
            peak=max(peak,float(r.high));stop=peak*.85
            if float(r.low)<=stop:
                xp=min(float(r.open),stop);pos=False;pending=None;trail_exit[t]=xp
        if t.minute==45:
            h=t.floor("h")
            if h in hour.index:
                if not pos and bool(hour.at[h,"sig"]):pending="buy"
                elif pos and bool(hour.at[h,"xit"]):pending="sell"
    return pd.Series(active,dtype=bool),trail_exit

def build_idle(base):
    idx=pd.date_range(START,END,freq="15min",inclusive="both")
    d=pd.read_csv(base,parse_dates=["time"]).sort_values("time").drop_duplicates("time",keep="last")
    d=d[(d.time>=START)&(d.time<=END)].set_index("time").reindex(idx).ffill()
    d.loc[START,"equity"]=1.0
    eq=d.equity.astype(float);ret=eq.pct_change().fillna(0.0)
    core=d.core_active.fillna(0).astype(int)
    opd=d.open_pd.fillna(0).astype(int)
    pc=core.shift(1).fillna(core.iloc[0]).astype(int)
    pp=opd.shift(1).fillna(opd.iloc[0]).astype(int)
    idle=(core.eq(0)&pc.eq(0)&opd.eq(0)&pp.eq(0))
    return idx,d,eq,ret,idle

def sim(mode,w,idx,base_ret,idle,px,hour,orig_open,orig_trail):
    E=1.;pos=False;stake=0.;ep=0.;peak=0.;pending=None
    curve=[(START,E)];trades=0
    for mt in idx[1:]:
        t=mt-pd.Timedelta(minutes=15)
        r=px.loc[t]
        can_idle=bool(idle.loc[mt])

        if pos and not can_idle:
            ratio=float(r.open)/ep
            E+=stake*(ratio-1)-stake*ratio*COST
            pos=False;stake=0.;ep=0.;peak=0.

        if t.minute==0 and pending=="sell":
            if pos:
                ratio=float(r.open)/ep
                E+=stake*(ratio-1)-stake*ratio*COST
                pos=False;stake=0.;ep=0.;peak=0.
            pending=None

        if t.minute==0 and pending=="buy":
            if mode=="SIGNAL_ONLY" and can_idle and not pos:
                stake=w*E;E-=stake*COST;ep=float(r.open);peak=ep;pos=True;trades+=1
            pending=None

        if mode=="REGIME_REENTRY" and can_idle and not pos and bool(orig_open.get(t,False)):
            stake=w*E;E-=stake*COST;ep=float(r.open);peak=ep;pos=True;trades+=1

        if pos:
            peak=max(peak,float(r.high));stop=peak*.85
            hit=float(r.low)<=stop
            if mode=="REGIME_REENTRY" and t in orig_trail:
                opx=float(orig_trail[t])
                if hit:opx=min(opx,min(float(r.open),stop))
                ratio=opx/ep;E+=stake*(ratio-1)-stake*ratio*COST
                pos=False;stake=0.;ep=0.;peak=0.
            elif hit:
                xp=min(float(r.open),stop);ratio=xp/ep
                E+=stake*(ratio-1)-stake*ratio*COST
                pos=False;stake=0.;ep=0.;peak=0.

        br=float(base_ret.loc[mt])
        if pos and abs(br)>2e-9:
            raise AssertionError(("overlay overlaps base return",mode,t,br))
        E*=1+br

        mark=E
        if pos:mark+=stake*(float(r.close)/ep-1)
        curve.append((mt,mark))

        if t.minute==45:
            h=t.floor("h")
            if h in hour.index:
                if mode=="SIGNAL_ONLY":
                    if not pos and bool(hour.at[h,"sig"]):pending="buy"
                    elif pos and bool(hour.at[h,"xit"]):pending="sell"
                else:
                    if pos and bool(hour.at[h,"xit"]):pending="sell"

    if pos:
        r=px.iloc[-1];ratio=float(r.close)/ep
        E+=stake*(ratio-1)-stake*ratio*COST
    C=pd.DataFrame(curve,columns=["time","equity"]).set_index("time")
    x=C.equity.to_numpy(float);p=np.maximum.accumulate(x);dd=(p-x)/p
    return dict(final_equity=float(E),mtm_mdd=float(dd.max()),trades=trades),C

def self_test():
    x=np.array([1.,1.1,.9,1.2]);p=np.maximum.accumulate(x);dd=(p-x)/p
    assert abs(dd.max()-(1.1-.9)/1.1)<1e-12
    print("SELF_TEST_PASS")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--self-test",action="store_true")
    ap.add_argument("--combo-script");ap.add_argument("--funding");ap.add_argument("--base24")
    ap.add_argument("--out",default="out");ap.add_argument("--cache",default="spotcache")
    a=ap.parse_args()
    if a.self_test:
        self_test();return

    O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
    mod=load_mod(a.combo_script)
    h=mod.dl_spot("BTCUSDT","1h",pd.Timestamp("2019-01-01",tz="UTC"),END,a.cache)
    b15=mod.dl_spot("BTCUSDT","15m",START,END,a.cache)
    px,nmiss,maxrun=fill_b15(b15)
    hour=prep_hourly(mod,h,a.funding)
    orig_open,orig_trail=original_active(hour,px)

    idx,d,beq,bret,idle=build_idle(a.base24)
    base_final=float(beq.iloc[-1])
    arr=beq.to_numpy(float);pp=np.maximum.accumulate(arr);base_mdd=float(np.max((pp-arr)/pp))
    print("BASE",{"final_equity":base_final,"mtm_mdd":base_mdd,
                  "idle_bars":int(idle.sum()),"btc15_missing":nmiss,"btc15_max_run":maxrun})

    rows=[]
    bestrows=[]
    for mode in ["SIGNAL_ONLY","REGIME_REENTRY"]:
        coarse=[]
        for w in np.round(np.arange(0,0.501,0.01),3):
            r,_=sim(mode,float(w),idx,bret,idle,px,hour,orig_open,orig_trail)
            r.update(mode=mode,weight=float(w));coarse.append(r)
        qc=pd.DataFrame(coarse)
        candidates=set(qc.weight.tolist())
        for ceiling in [.30,.32,.35]:
            u=qc[qc.mtm_mdd<=ceiling+1e-12]
            if len(u):
                center=float(u.sort_values("final_equity",ascending=False).iloc[0].weight)
                lo=max(0,center-.015);hi=min(.5,center+.015)
                candidates.update(np.round(np.arange(lo,hi+.00001,.001),3).tolist())
        fine=[]
        for w in sorted(candidates):
            r,_=sim(mode,float(w),idx,bret,idle,px,hour,orig_open,orig_trail)
            r.update(mode=mode,weight=float(w));fine.append(r)
        q=pd.DataFrame(fine).drop_duplicates(["mode","weight"]).sort_values("weight")
        q.to_csv(O/f"sweep_24bp_{mode.lower()}.csv",index=False)
        rows.append(q)
        for ceiling in [.30,.32,.35]:
            u=q[q.mtm_mdd<=ceiling+1e-12]
            if len(u):
                z=u.sort_values("final_equity",ascending=False).iloc[0]
                bestrows.append(dict(mode=mode,mdd_ceiling=ceiling,feasible=True,
                                     weight=float(z.weight),final_equity=float(z.final_equity),
                                     mtm_mdd=float(z.mtm_mdd),trades=int(z.trades)))
            else:
                bestrows.append(dict(mode=mode,mdd_ceiling=ceiling,feasible=False,
                                     weight=np.nan,final_equity=np.nan,mtm_mdd=np.nan,trades=0))
        z=q.sort_values("final_equity",ascending=False).iloc[0]
        bestrows.append(dict(mode=mode,mdd_ceiling=np.nan,feasible=True,
                             weight=float(z.weight),final_equity=float(z.final_equity),
                             mtm_mdd=float(z.mtm_mdd),trades=int(z.trades)))

    R=pd.concat(rows,ignore_index=True)
    B=pd.DataFrame(bestrows)
    R.to_csv(O/"top10_ema_stateful_sweep_24bp.csv",index=False)
    B.to_csv(O/"top10_ema_stateful_best_24bp.csv",index=False)
    print("BEST")
    print(B.to_string(index=False))

if __name__=="__main__":
    main()
