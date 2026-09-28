#!/usr/bin/env python3
import argparse,importlib.util
from pathlib import Path
import numpy as np,pandas as pd

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-08-22",tz="UTC")
EMA_COST=.001
W8=[0.15,0.20,0.226,0.25]
W24=[0.0,0.012,0.02,0.05]

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

def build_b15(mod,cache):
    raw=mod.dl_spot("BTCUSDT","15m",START,END,cache).set_index("dt").sort_index()
    idx=pd.date_range(START,END-pd.Timedelta(minutes=15),freq="15min")
    z=raw.reindex(idx)
    missing=z.close.isna()
    # canonical bounded-gap rule: zero-return synthetic bar, PRESERVE strategy state.
    prev=z.close.ffill()
    for c in ["open","high","low","close"]:
        z[c]=z[c].where(~missing,prev)
    if z[["open","high","low","close"]].isna().any().any(): raise AssertionError("leading BTC gap")
    z["gap"]=missing
    z["dt"]=z.index
    return z.reset_index(drop=True),int(missing.sum())

def original_regime(hour,b15):
    px=b15.set_index("dt").sort_index()
    pos=False;peak=0.;pending=None;active={};trail_exit={}
    for t,r in px.iterrows():
        if t.minute==0:
            if pending=="buy" and not pos: pos=True;peak=float(r.open);pending=None
            elif pending=="sell" and pos: pos=False;pending=None
        active[t]=bool(pos)
        if pos and not bool(r.gap):
            peak=max(peak,float(r.high));stop=peak*.85
            if float(r.low)<=stop:
                xp=min(float(r.open),stop);pos=False;pending=None;trail_exit[t]=xp
        if (not bool(r.gap)) and t.minute==45:
            h=t.floor("h")
            if h in hour.index:
                if not pos and bool(hour.at[h,"sig"]):pending="buy"
                elif pos and bool(hour.at[h,"xit"]):pending="sell"
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

def sim(weight,idx,base_ret,idle,b15,hour,orig_open,orig_trail,gap_mode="HOLD"):
    px=b15.set_index("dt").sort_index()
    E=1.;pos=False;stake=0.;ep=0.;peak=0.;pending=None
    curve=[(START,E)];events=[];entry_t=None
    for mt in idx[1:]:
        t=mt-pd.Timedelta(minutes=15);r=px.loc[t]
        can_idle=bool(idle.loc[mt]);is_gap=bool(r.gap)

        if is_gap and gap_mode=="FORCE_FLAT":
            if pos:
                ratio=float(r.open)/ep
                E += stake*(ratio-1)-stake*ratio*EMA_COST
                events.append((entry_t,t,ep,float(r.open),"GAP_FORCE",ratio*(1-EMA_COST)-1-EMA_COST))
                pos=False;stake=0.;ep=0.;peak=0.;entry_t=None
            pending=None

        if pos and not can_idle:
            ratio=float(r.open)/ep
            E += stake*(ratio-1)-stake*ratio*EMA_COST
            events.append((entry_t,t,ep,float(r.open),"BASE_ON",ratio*(1-EMA_COST)-1-EMA_COST))
            pos=False;stake=0.;ep=0.;peak=0.;entry_t=None

        if (not is_gap) and t.minute==0 and pending=="sell":
            if pos:
                ratio=float(r.open)/ep
                E += stake*(ratio-1)-stake*ratio*EMA_COST
                events.append((entry_t,t,ep,float(r.open),"XIT",ratio*(1-EMA_COST)-1-EMA_COST))
                pos=False;stake=0.;ep=0.;peak=0.;entry_t=None
            pending=None

        if can_idle and not pos and bool(orig_open.get(t,False)) and not is_gap:
            stake=weight*E;E-=stake*EMA_COST;ep=float(r.open);peak=ep;pos=True;entry_t=t

        if pos and not is_gap:
            peak=max(peak,float(r.high));stop=peak*.85
            if t in orig_trail:
                xp=float(orig_trail[t])
                if float(r.low)<=stop:xp=min(xp,min(float(r.open),stop))
                ratio=xp/ep;E += stake*(ratio-1)-stake*ratio*EMA_COST
                events.append((entry_t,t,ep,xp,"ORIG_TRAIL",ratio*(1-EMA_COST)-1-EMA_COST))
                pos=False;stake=0.;ep=0.;peak=0.;entry_t=None
            elif float(r.low)<=stop:
                xp=min(float(r.open),stop);ratio=xp/ep
                E += stake*(ratio-1)-stake*ratio*EMA_COST
                events.append((entry_t,t,ep,xp,"TRAIL",ratio*(1-EMA_COST)-1-EMA_COST))
                pos=False;stake=0.;ep=0.;peak=0.;entry_t=None

        br=float(base_ret.loc[mt]);E*=1+br
        mark=E+(stake*(float(r.close)/ep-1) if pos else 0)
        curve.append((mt,mark))

        if (not is_gap) and t.minute==45:
            h=t.floor("h")
            if h in hour.index and pos and bool(hour.at[h,"xit"]):pending="sell"

    if pos:
        r=px.iloc[-1];xp=float(r.close);ratio=xp/ep
        E += stake*(ratio-1)-stake*ratio*EMA_COST
        events.append((entry_t,px.index[-1],ep,xp,"END",ratio*(1-EMA_COST)-1-EMA_COST))
    C=pd.DataFrame(curve,columns=["time","equity"]).set_index("time")
    T=pd.DataFrame(events,columns=["entry_time","exit_time","entry","exit","reason","trade_ret"])
    return E,C,T

def mdd(s):
    x=s.to_numpy(float);p=np.maximum.accumulate(x);dd=(p-x)/p;i=int(np.argmax(dd))
    pi=int(np.where(x[:i+1]>=p[i]-1e-12)[0][-1])
    return float(dd[i]),s.index[pi],s.index[i]

def period_rows(eq,cost_bps,w):
    rows=[]
    for label,groups in [("YEAR",eq.groupby(eq.index.year)),("QUARTER",eq.groupby(eq.index.to_period("Q")))]:
        for k,g in groups:
            if len(g)<2:continue
            r=float(g.iloc[-1]/g.iloc[0]-1);md,_,_=mdd(g)
            rows.append(dict(cost_bps=cost_bps,weight=w,kind=label,period=str(k),return_pct=r*100,mdd_pct=md*100))
    return rows

def trade_stats(T):
    if T.empty:return dict(trades=0,win_rate=np.nan,pf=np.nan,mean_trade=np.nan)
    wins=T.loc[T.trade_ret>0,"trade_ret"].sum();loss=-T.loc[T.trade_ret<0,"trade_ret"].sum()
    return dict(trades=len(T),win_rate=float((T.trade_ret>0).mean()),pf=float(wins/loss) if loss>0 else np.inf,mean_trade=float(T.trade_ret.mean()))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--combo-script",required=True);ap.add_argument("--funding",required=True)
    ap.add_argument("--base8",required=True);ap.add_argument("--base24",required=True)
    ap.add_argument("--out",required=True);ap.add_argument("--cache",default="spotcache")
    a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True);mod=load_mod(a.combo_script)

    h=mod.dl_spot("BTCUSDT","1h",pd.Timestamp("2019-01-01",tz="UTC"),END,a.cache)
    b15,nmiss=build_b15(mod,a.cache);hour=prep_hourly(mod,h,a.funding)
    orig_open,orig_trail=original_regime(hour,b15)
    print("CANON_GAPS",nmiss)

    # Prove the 74/75 discrepancy source using identical 22.6% replay.
    idx,_,bret,idle=prep_base(a.base8)
    holdE,holdC,holdT=sim(.226,idx,bret,idle,b15,hour,orig_open,orig_trail,"HOLD")
    flatE,flatC,flatT=sim(.226,idx,bret,idle,b15,hour,orig_open,orig_trail,"FORCE_FLAT")
    hold_entries=set(holdT.entry_time.astype(str));flat_entries=set(flatT.entry_time.astype(str))
    diff=sorted(flat_entries.symmetric_difference(hold_entries))
    print("GAP_AUDIT",{"hold_trades":len(holdT),"force_flat_trades":len(flatT),"hold_final":holdE,"force_flat_final":flatE,"entry_diff":diff})
    holdT.to_csv(O/"trades_gap_hold.csv",index=False);flatT.to_csv(O/"trades_gap_force_flat.csv",index=False)

    summ=[];periods=[]
    for bps,path,weights in [(8,a.base8,W8),(24,a.base24,W24)]:
        idx,beq,bret,idle=prep_base(path)
        for w in weights:
            E,C,T=sim(w,idx,bret,idle,b15,hour,orig_open,orig_trail,"HOLD")
            md,pk,tr=mdd(C.equity);ts=trade_stats(T)
            q=dict(cost_bps=bps,weight=w,final_equity=E,mtm_mdd_pct=md*100,peak=pk,trough=tr,**ts)
            summ.append(q);periods.extend(period_rows(C.equity,bps,w))
            C.reset_index().to_csv(O/f"curve_{bps}bp_w{int(round(w*1000)):03d}.csv",index=False)
            T.to_csv(O/f"trades_{bps}bp_w{int(round(w*1000)):03d}.csv",index=False)
            print("ROBUST",q)
    S=pd.DataFrame(summ);P=pd.DataFrame(periods)
    S.to_csv(O/"summary.csv",index=False);P.to_csv(O/"periods.csv",index=False)
    for bps in [8,24]:
        q=P[(P.cost_bps==bps)&(P.kind=="QUARTER")].groupby("weight").agg(
            positive_quarters=("return_pct",lambda x:int((x>0).sum())),
            negative_quarters=("return_pct",lambda x:int((x<0).sum())),
            worst_quarter_pct=("return_pct","min"),
            median_quarter_pct=("return_pct","median")).reset_index()
        print("QUARTER_ROBUSTNESS",bps);print(q.to_string(index=False))

if __name__=="__main__":main()
