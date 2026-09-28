#!/usr/bin/env python3
import argparse,glob,importlib.util
from pathlib import Path
import numpy as np,pandas as pd

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-08-22",tz="UTC")
EMA_COST=.001
WEIGHTS=np.round(np.arange(0.0,0.3001,0.001),3)

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
    gap=z.close.isna()
    prev=z.close.ffill()
    for c in ["open","high","low","close"]:
        z[c]=z[c].where(~gap,prev)
    if z[["open","high","low","close"]].isna().any().any(): raise AssertionError("leading BTC gap")
    z["gap"]=gap
    z["dt"]=z.index
    return z.reset_index(drop=True),int(gap.sum())

def original_regime(hour,b15):
    px=b15.set_index("dt").sort_index()
    pos=False;peak=0.;pending=None;active={};trail_exit={}
    for t,r in px.iterrows():
        is_gap=bool(r.gap)
        if t.minute==0 and not is_gap:
            if pending=="buy" and not pos: pos=True;peak=float(r.open);pending=None
            elif pending=="sell" and pos: pos=False;pending=None
        active[t]=bool(pos)
        if pos and not is_gap:
            peak=max(peak,float(r.high));stop=peak*.85
            if float(r.low)<=stop:
                xp=min(float(r.open),stop);pos=False;pending=None;trail_exit[t]=xp
        if t.minute==45 and not is_gap:
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

def sim_all(idx,base_ret,idle,b15,hour,orig_open,orig_trail):
    px=b15.set_index("dt").sort_index()
    n=len(WEIGHTS)
    E=np.ones(n,float);stake=np.zeros(n,float)
    peak_eq=np.ones(n,float);maxdd=np.zeros(n,float)
    pos=False;ep=0.;peak=0.;pending=None;trades=0
    peak_i=np.zeros(n,int);trough_i=np.zeros(n,int);run_peak_i=np.zeros(n,int)

    for k,mt in enumerate(idx[1:],start=1):
        t=mt-pd.Timedelta(minutes=15);r=px.loc[t]
        can_idle=bool(idle.loc[mt]);is_gap=bool(r.gap)

        if pos and not can_idle:
            ratio=float(r.open)/ep
            E += stake*(ratio-1)-stake*ratio*EMA_COST
            pos=False;stake[:]=0.;ep=0.;peak=0.

        if (not is_gap) and t.minute==0 and pending=="sell":
            if pos:
                ratio=float(r.open)/ep
                E += stake*(ratio-1)-stake*ratio*EMA_COST
                pos=False;stake[:]=0.;ep=0.;peak=0.
            pending=None

        if can_idle and not pos and (not is_gap) and bool(orig_open.get(t,False)):
            stake=WEIGHTS*E
            E -= stake*EMA_COST
            ep=float(r.open);peak=ep;pos=True;trades+=1

        if pos and not is_gap:
            peak=max(peak,float(r.high));stop=peak*.85
            if t in orig_trail:
                xp=float(orig_trail[t])
                if float(r.low)<=stop: xp=min(xp,min(float(r.open),stop))
                ratio=xp/ep
                E += stake*(ratio-1)-stake*ratio*EMA_COST
                pos=False;stake[:]=0.;ep=0.;peak=0.
            elif float(r.low)<=stop:
                xp=min(float(r.open),stop);ratio=xp/ep
                E += stake*(ratio-1)-stake*ratio*EMA_COST
                pos=False;stake[:]=0.;ep=0.;peak=0.

        br=float(base_ret.loc[mt]);E*=1.0+br
        mark=E.copy()
        if pos: mark += stake*(float(r.close)/ep-1.0)

        newpeak=mark>peak_eq
        peak_eq[newpeak]=mark[newpeak];run_peak_i[newpeak]=k
        dd=(peak_eq-mark)/peak_eq
        upd=dd>maxdd
        maxdd[upd]=dd[upd];peak_i[upd]=run_peak_i[upd];trough_i[upd]=k

        if t.minute==45 and not is_gap:
            h=t.floor("h")
            if h in hour.index and pos and bool(hour.at[h,"xit"]): pending="sell"

    if pos:
        r=px.iloc[-1];ratio=float(r.close)/ep
        E += stake*(ratio-1)-stake*ratio*EMA_COST

    return pd.DataFrame({"weight":WEIGHTS,"final_equity":E,"mtm_mdd":maxdd,
                         "peak_i":peak_i,"trough_i":trough_i,"trades":trades})

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--combo-script",required=True);ap.add_argument("--funding",required=True)
    ap.add_argument("--base-dir",required=True);ap.add_argument("--out",required=True);ap.add_argument("--cache",default="spotcache")
    a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True);mod=load_mod(a.combo_script)

    h=mod.dl_spot("BTCUSDT","1h",pd.Timestamp("2019-01-01",tz="UTC"),END,a.cache)
    b15,nmiss=build_b15(mod,a.cache);hour=prep_hourly(mod,h,a.funding)
    orig_open,orig_trail=original_regime(hour,b15)
    print("CANON_GAPS",nmiss)

    rows=[]
    for fp in sorted(glob.glob(str(Path(a.base_dir)/"equity_curve_*bp_risk080.csv"))):
        bps=int(Path(fp).name.split("_")[2].replace("bp",""))
        idx,beq,bret,idle=prep_base(fp)
        q=sim_all(idx,bret,idle,b15,hour,orig_open,orig_trail)
        q["cost_bps"]=bps;q["base_final"]=float(beq.iloc[-1])
        under=q[q.mtm_mdd<=.30+1e-12]
        best=under.sort_values(["final_equity","mtm_mdd"],ascending=[False,True]).iloc[0]
        refs=q[q.weight.isin([0,.10,.15,.20,.226,.25,.30])][["weight","final_equity","mtm_mdd"]]
        print("BEST",bps,best.to_dict())
        print("REFS",bps);print(refs.to_string(index=False))
        rows.append(q)
    R=pd.concat(rows,ignore_index=True).sort_values(["cost_bps","weight"])
    R.to_csv(O/"ema_overlay_cost_grid.csv",index=False)
    B=R[R.mtm_mdd<=.30+1e-12].sort_values(["cost_bps","final_equity"],ascending=[True,False]).groupby("cost_bps").head(1)
    B.to_csv(O/"best_under_30.csv",index=False)
    print("BEST_TABLE");print(B[["cost_bps","weight","final_equity","mtm_mdd","base_final","trades"]].to_string(index=False))

if __name__=="__main__":main()
