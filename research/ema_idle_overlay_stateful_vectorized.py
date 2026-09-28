#!/usr/bin/env python3
import argparse,importlib.util
from pathlib import Path
import numpy as np,pandas as pd

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-08-22",tz="UTC")
COST=.001
WEIGHTS=np.round(np.arange(0.0,0.5001,0.001),3)

def load_mod(path):
    sp=importlib.util.spec_from_file_location("combo",path)
    m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m

def repair_15m(d,start,end):
    x=d.set_index("dt").sort_index().copy()
    idx=pd.date_range(start,end-pd.Timedelta(minutes=15),freq="15min")
    miss=~idx.isin(x.index)
    md=pd.Series(miss,index=idx).groupby(idx.floor("D")).sum()
    if len(md) and int(md.max())>8: raise AssertionError(f"too many missing bars/day: {int(md.max())}")
    x=x.reindex(idx)
    prev=x["close"].ffill()
    for c in ["open","high","low","close"]: x[c]=x[c].fillna(prev)
    if x[["open","high","low","close"]].isna().any().any(): raise AssertionError("leading gap")
    x["dt"]=x.index
    return x.reset_index(drop=True),int(miss.sum())

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

def original_regime(hour,b15):
    px=b15.set_index("dt").sort_index()
    pos=False;peak=0.;pending=None;active={};trail_exit={}
    for t,r in px.iterrows():
        if t.minute==0:
            if pending=="buy" and not pos: pos=True;peak=float(r.open);pending=None
            elif pending=="sell" and pos: pos=False;pending=None
        active[t]=bool(pos)
        if pos:
            peak=max(peak,float(r.high));stop=peak*.85
            if float(r.low)<=stop:
                xp=min(float(r.open),stop);pos=False;pending=None;trail_exit[t]=xp
        if t.minute==45:
            h=t.floor("h")
            if h in hour.index:
                if not pos and bool(hour.at[h,"sig"]): pending="buy"
                elif pos and bool(hour.at[h,"xit"]): pending="sell"
    return pd.Series(active),trail_exit

def prep_base(path):
    idx=pd.date_range(START,END,freq="15min",inclusive="both")
    d=pd.read_csv(path,parse_dates=["time"]).sort_values("time").drop_duplicates("time",keep="last")
    d=d[(d.time>=START)&(d.time<=END)].set_index("time").reindex(idx).ffill()
    d.loc[START,"equity"]=1.0
    eq=d.equity.astype(float);ret=eq.pct_change().fillna(0.0)
    core=d.core_active.fillna(0).astype(int);opd=d.open_pd.fillna(0).astype(int)
    pc=core.shift(1).fillna(core.iloc[0]).astype(int);pp=opd.shift(1).fillna(opd.iloc[0]).astype(int)
    idle=(core.eq(0)&pc.eq(0)&opd.eq(0)&pp.eq(0)&(ret.abs()<=2e-9))
    return idx,eq,ret,idle

def sim_all(mode,idx,base_ret,idle,b15,hour,orig_open,orig_trail):
    px=b15.set_index("dt").sort_index()
    n=len(WEIGHTS)
    E=np.ones(n,float);stake=np.zeros(n,float)
    peak_eq=np.ones(n,float);maxdd=np.zeros(n,float)
    pos=False;ep=0.;peak=0.;pending=None;trades=0
    peak_i=np.zeros(n,int);trough_i=np.zeros(n,int);run_peak_i=np.zeros(n,int)

    for k,mt in enumerate(idx[1:],start=1):
        t=mt-pd.Timedelta(minutes=15);r=px.loc[t]
        can_idle=bool(idle.loc[mt])

        if pos and not can_idle:
            ratio=float(r.open)/ep
            E += stake*(ratio-1)-stake*ratio*COST
            pos=False;stake[:]=0.;ep=0.;peak=0.

        if t.minute==0 and pending=="sell":
            if pos:
                ratio=float(r.open)/ep
                E += stake*(ratio-1)-stake*ratio*COST
                pos=False;stake[:]=0.;ep=0.;peak=0.
            pending=None

        if t.minute==0 and pending=="buy":
            if mode=="SIGNAL_ONLY" and can_idle and not pos:
                stake=WEIGHTS*E
                E -= stake*COST
                ep=float(r.open);peak=ep;pos=True;trades+=1
            pending=None

        if mode=="REGIME_REENTRY" and can_idle and not pos and bool(orig_open.get(t,False)):
            stake=WEIGHTS*E
            E -= stake*COST
            ep=float(r.open);peak=ep;pos=True;trades+=1

        if pos:
            peak=max(peak,float(r.high));stop=peak*.85
            hit=float(r.low)<=stop
            if mode=="REGIME_REENTRY" and t in orig_trail:
                xp=float(orig_trail[t])
                if hit: xp=min(xp,min(float(r.open),stop))
                ratio=xp/ep
                E += stake*(ratio-1)-stake*ratio*COST
                pos=False;stake[:]=0.;ep=0.;peak=0.
            elif hit:
                xp=min(float(r.open),stop);ratio=xp/ep
                E += stake*(ratio-1)-stake*ratio*COST
                pos=False;stake[:]=0.;ep=0.;peak=0.

        br=float(base_ret.loc[mt]);E*=1.0+br
        mark=E.copy()
        if pos: mark += stake*(float(r.close)/ep-1.0)

        newpeak=mark>peak_eq
        peak_eq[newpeak]=mark[newpeak];run_peak_i[newpeak]=k
        dd=(peak_eq-mark)/peak_eq
        upd=dd>maxdd
        maxdd[upd]=dd[upd];peak_i[upd]=run_peak_i[upd];trough_i[upd]=k

        if t.minute==45:
            h=t.floor("h")
            if h in hour.index:
                if mode=="SIGNAL_ONLY":
                    if not pos and bool(hour.at[h,"sig"]): pending="buy"
                    elif pos and bool(hour.at[h,"xit"]): pending="sell"
                elif pos and bool(hour.at[h,"xit"]): pending="sell"

    if pos:
        r=px.iloc[-1];ratio=float(r.close)/ep
        E += stake*(ratio-1)-stake*ratio*COST

    out=pd.DataFrame({"weight":WEIGHTS,"final_equity":E,"mtm_mdd":maxdd,
                      "peak_i":peak_i,"trough_i":trough_i})
    out["trades"]=trades
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--combo-script",required=True);ap.add_argument("--funding",required=True)
    ap.add_argument("--base8",required=True);ap.add_argument("--base24",required=True)
    ap.add_argument("--out",required=True);ap.add_argument("--cache",default="spotcache")
    a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True);mod=load_mod(a.combo_script)

    h=mod.dl_spot("BTCUSDT","1h",pd.Timestamp("2019-01-01",tz="UTC"),END,a.cache)
    b15=mod.dl_spot("BTCUSDT","15m",START,END,a.cache)
    b15,nmiss=repair_15m(b15,START,END);print("REPAIRED_15M_GAPS",nmiss)
    hour=prep_hourly(mod,h,a.funding);orig_open,orig_trail=original_regime(hour,b15)

    allout=[]
    for bps,path in [(8,a.base8),(24,a.base24)]:
        idx,beq,bret,idle=prep_base(path)
        print("BASE",bps,float(beq.iloc[-1]),"idle_bars",int(idle.sum()))
        for mode in ["SIGNAL_ONLY","REGIME_REENTRY"]:
            q=sim_all(mode,idx,bret,idle,b15,hour,orig_open,orig_trail)
            q["cost_bps"]=bps;q["mode"]=mode
            under=q[q.mtm_mdd<=.30+1e-12]
            best=under.sort_values(["final_equity","mtm_mdd"],ascending=[False,True]).iloc[0]
            print("BEST",bps,mode,best.to_dict())
            print("REFS",bps,mode)
            print(q[q.weight.isin([0,.05,.10,.15,.20,.25,.30,.40,.50])][["weight","final_equity","mtm_mdd","trades"]].to_string(index=False))
            q.to_csv(O/f"sweep_{bps}bp_{mode.lower()}.csv",index=False);allout.append(q)
    pd.concat(allout,ignore_index=True).to_csv(O/"all_stateful_vectorized.csv",index=False)

if __name__=="__main__": main()
