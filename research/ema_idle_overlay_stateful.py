#!/usr/bin/env python3
import argparse,importlib.util,requests
from pathlib import Path
import numpy as np,pandas as pd

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-08-22",tz="UTC")
COST=.001

def load_mod(path):
    sp=importlib.util.spec_from_file_location("combo",path);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m

def repair_15m(d,start,end):
    x=d.set_index("dt").sort_index().copy()
    idx=pd.date_range(start,end-pd.Timedelta(minutes=15),freq="15min")
    miss=~idx.isin(x.index)
    # Reject large data holes; allow only bounded archive gaps and model them as zero-return candles.
    md=pd.Series(miss,index=idx).groupby(idx.floor("D")).sum()
    if len(md) and int(md.max())>8: raise AssertionError(f"too many missing 15m bars in a day: {int(md.max())}")
    x=x.reindex(idx)
    prev=x["close"].ffill()
    for c in ["open","high","low","close"]:
        x[c]=x[c].fillna(prev)
    if x[["open","high","low","close"]].isna().any().any(): raise AssertionError("unfillable leading 15m gap")
    x["dt"]=x.index
    return x.reset_index(drop=True),int(miss.sum())

def prep_hourly(mod,hourly,funding):
    d=hourly.set_index("dt").copy()
    f=pd.read_feather(funding);f["date"]=pd.to_datetime(f.date,utc=True);fr=f.set_index("date").sort_index()["funding"]
    d["funding"]=fr.reindex(d.index,method="ffill");d["f3"]=d.funding.rolling(72,min_periods=24).mean()
    d["fpct"]=d.f3.rolling(24*180,min_periods=24*30).rank(pct=True)*100;d["ema"]=mod.ema_talib(d.close,600)
    d["sig"]=(d.close>d.ema)&(d.close.shift(1)<=d.ema.shift(1))&((d.fpct<55)|d.fpct.isna())
    d["xit"]=(d.close<d.ema*.98)&(d.close.shift(1)>=d.ema.shift(1)*.98)
    return d

def original_active(hour,b15):
    # exact original/canonical strategy state at each 15m OPEN; used only for REGIME_REENTRY.
    grid=b15[(b15.dt>=START)&(b15.dt<END)].set_index("dt").sort_index()
    pos=False;peak=0.;pending=None;active={};trail_exit={}
    for t,r in grid.iterrows():
        if t.minute==0:
            if pending=="buy" and not pos: pos=True;peak=float(r.open);pending=None
            elif pending=="sell" and pos: pos=False;pending=None
        active[t]=bool(pos)
        if pos:
            peak=max(peak,float(r.high));stop=peak*.85
            if float(r.low)<=stop:
                px=min(float(r.open),stop);pos=False;pending=None;trail_exit[t]=px
        if t.minute==45:
            h=t.floor("h")
            if h in hour.index:
                if not pos and bool(hour.at[h,"sig"]):pending="buy"
                elif pos and bool(hour.at[h,"xit"]):pending="sell"
    return pd.Series(active),trail_exit

def build_idle(base):
    idx=pd.date_range(START,END,freq="15min",inclusive="both")
    d=pd.read_csv(base,parse_dates=["time"]).sort_values("time").drop_duplicates("time",keep="last")
    d=d[(d.time>=START)&(d.time<=END)].set_index("time").reindex(idx).ffill();d.loc[START,"equity"]=1.0
    eq=d.equity.astype(float);ret=eq.pct_change().fillna(0.0)
    core=d.core_active.fillna(0).astype(int);opd=d.open_pd.fillna(0).astype(int)
    pc=core.shift(1).fillna(core.iloc[0]).astype(int);pp=opd.shift(1).fillna(opd.iloc[0]).astype(int)
    idle=(core.eq(0)&pc.eq(0)&opd.eq(0)&pp.eq(0)&(ret.abs()<=2e-9))
    return idx,d,eq,ret,idle

def sim(mode,w,idx,base_ret,idle,b15,hour,orig_open,orig_trail):
    px=b15.set_index("dt").sort_index()
    want=pd.date_range(START,END-pd.Timedelta(minutes=15),freq="15min",tz="UTC")
    px=px.reindex(want)
    if "gap" not in px: px["gap"]=False
    if px.close.isna().any():
        raise AssertionError(("unrepaired BTC15 gaps",int(px.close.isna().sum()),list(px.index[px.close.isna()][:10])))
    E=1.;pos=False;stake=0.;ep=0.;peak=0.;pending=None;curve=[(START,E)]
    trades=0
    for mt in idx[1:]:
        t=mt-pd.Timedelta(minutes=15)
        r=px.loc[t]
        can_idle=bool(idle.loc[mt])
        is_gap=bool(r.get("gap",False))
        if is_gap:
            if pos:
                ratio=float(r.open)/ep
                E += stake*(ratio-1)-stake*ratio*COST
                pos=False;stake=0.;ep=0.;peak=0.
            pending=None
            br=float(base_ret.loc[mt]);E*=1+br
            curve.append((mt,E))
            continue

        # force-close at interval OPEN when base ceases to be strictly idle
        if pos and not can_idle:
            ratio=float(r.open)/ep
            E += stake*(ratio-1)-stake*ratio*COST
            pos=False;stake=0.;ep=0.;peak=0.

        # scheduled regular exit from hourly xit for our own position
        if t.minute==0 and pending=="sell":
            if pos:
                ratio=float(r.open)/ep;E += stake*(ratio-1)-stake*ratio*COST
                pos=False;stake=0.;ep=0.;peak=0.
            pending=None

        # scheduled signal-only entry
        if t.minute==0 and pending=="buy":
            if mode=="SIGNAL_ONLY" and can_idle and not pos:
                stake=w*E;E-=stake*COST;ep=float(r.open);peak=ep;pos=True;trades+=1
            pending=None

        # regime reentry: only if original strategy was active at this bar open
        if mode=="REGIME_REENTRY" and can_idle and not pos and bool(orig_open.get(t,False)):
            stake=w*E;E-=stake*COST;ep=float(r.open);peak=ep;pos=True;trades+=1

        if pos:
            peak=max(peak,float(r.high));stop=peak*.85
            hit=float(r.low)<=stop
            # For reentry, original canonical trail ending the regime also ends overlay at its exact stop if earlier.
            if mode=="REGIME_REENTRY" and t in orig_trail:
                opx=float(orig_trail[t])
                if hit: opx=min(opx,min(float(r.open),stop))
                ratio=opx/ep;E += stake*(ratio-1)-stake*ratio*COST
                pos=False;stake=0.;ep=0.;peak=0.
            elif hit:
                xp=min(float(r.open),stop);ratio=xp/ep
                E += stake*(ratio-1)-stake*ratio*COST
                pos=False;stake=0.;ep=0.;peak=0.

        # apply base strategy return during this interval. Assert overlay doesn't coexist with material base movement.
        br=float(base_ret.loc[mt])
        if pos and abs(br)>2e-9:
            raise AssertionError(("overlay overlaps base return",mode,t,br))
        E*=1+br

        mark=E
        if pos: mark += stake*(float(r.close)/ep-1)
        curve.append((mt,mark))

        # hourly signal becomes actionable next hour open
        if t.minute==45:
            h=t.floor("h")
            if h in hour.index:
                if mode=="SIGNAL_ONLY":
                    if not pos and bool(hour.at[h,"sig"]): pending="buy"
                    elif pos and bool(hour.at[h,"xit"]): pending="sell"
                else:
                    # regime mode uses original state for entry but original xit should close at next open
                    if pos and bool(hour.at[h,"xit"]): pending="sell"

    if pos:
        # END mark already contains open PnL; force realization only for final book equity equivalence
        r=px.iloc[-1];ratio=float(r.close)/ep;E += stake*(ratio-1)-stake*ratio*COST
    C=pd.DataFrame(curve,columns=["time","equity"]).set_index("time")
    x=C.equity.to_numpy(float);p=np.maximum.accumulate(x);dd=(p-x)/p;i=int(np.argmax(dd))
    return dict(final_equity=float(E),mtm_mdd=float(dd[i]),trades=trades,peak=C.index[int(np.where(x[:i+1]>=p[i]-1e-12)[0][-1])],trough=C.index[i]),C

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--combo-script",required=True);ap.add_argument("--funding",required=True)
    ap.add_argument("--base8",required=True);ap.add_argument("--base24",required=True);ap.add_argument("--out",required=True);ap.add_argument("--cache",default="spotcache")
    a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True);mod=load_mod(a.combo_script)
    h=mod.dl_spot("BTCUSDT","1h",pd.Timestamp("2019-01-01",tz="UTC"),END,a.cache)
    b15=mod.dl_spot("BTCUSDT","15m",START,END,a.cache)
    full_open_idx=pd.date_range(START,END-pd.Timedelta(minutes=15),freq="15min",tz="UTC")
    raw=b15.set_index("dt").sort_index().reindex(full_open_idx)
    miss=raw.close.isna()
    if miss.any():
        ms=raw.index[miss]
        one=mod.dl_spot("BTCUSDT","1m",ms.min(),ms.max()+pd.Timedelta(minutes=15),a.cache)
        agg=one.set_index("dt").resample("15min",label="left",closed="left").agg(
            open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("volume","sum"),bars=("close","count"))
        for t in ms:
            if t in agg.index and int(agg.at[t,"bars"])==15:
                for col in ["open","high","low","close","volume"]:
                    raw.at[t,col]=float(agg.at[t,col])
        rem=raw.close.isna()
        raw["gap"]=rem
        if rem.any():
            prev=raw.close.ffill()
            for col in ["open","high","low","close"]:
                raw[col]=raw[col].where(~rem,prev)
            if raw[["open","high","low","close"]].isna().any().any():
                raise AssertionError(("BTC15 leading gap",list(raw.index[raw.close.isna()][:10])))
    else:
        raw["gap"]=False
    b15=raw.reset_index().rename(columns={"index":"dt"})
    print("BTC15_GAPS_GUARDED",{"missing_15m":int(miss.sum()),"unresolved_1m":int(raw.gap.sum())})
    hour=prep_hourly(mod,h,a.funding);orig_open,orig_trail=original_active(hour,b15)
    orig_open=orig_open.reindex(full_open_idx).ffill().fillna(False).astype(bool)

    allrows=[]
    for bps,base in [(8,a.base8),(24,a.base24)]:
        idx,d,beq,bret,idle=build_idle(base)
        # strict idle means base must be flat in eligible intervals
        bad=(idle & (bret.abs()>2e-9))
        print("BASE",bps,float(beq.iloc[-1]),"idle_bars",int(idle.sum()),"idle_nonzero_base_ret",int(bad.sum()))
        # coarse then fine search per mode
        for mode in ["SIGNAL_ONLY","REGIME_REENTRY"]:
            coarse=[]
            for w in np.round(np.arange(0,0.501,0.01),3):
                r,_=sim(mode,float(w),idx,bret,idle,b15,hour,orig_open,orig_trail);r.update(cost_bps=bps,mode=mode,weight=float(w));coarse.append(r)
            qc=pd.DataFrame(coarse);under=qc[qc.mtm_mdd<=.30+1e-12]
            center=float(under.sort_values("final_equity",ascending=False).iloc[0].weight) if len(under) else 0.
            lo=max(0,center-.015);hi=min(.5,center+.015)
            fine=[]
            for w in np.round(np.arange(lo,hi+.00001,.001),3):
                r,c=sim(mode,float(w),idx,bret,idle,b15,hour,orig_open,orig_trail);r.update(cost_bps=bps,mode=mode,weight=float(w));fine.append(r)
            q=pd.DataFrame(fine);best=q[q.mtm_mdd<=.30+1e-12].sort_values("final_equity",ascending=False).iloc[0]
            print("BEST",bps,mode,best.to_dict())
            q.to_csv(O/f"fine_{bps}bp_{mode.lower()}.csv",index=False)
            allrows.extend(coarse);allrows.extend(fine)
    pd.DataFrame(allrows).drop_duplicates(["cost_bps","mode","weight"],keep="last").to_csv(O/"stateful_overlay_sweep.csv",index=False)

if __name__=="__main__":main()
