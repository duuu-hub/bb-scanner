#!/usr/bin/env python3
import argparse,glob,importlib.util
from pathlib import Path
import numpy as np,pandas as pd

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-08-22",tz="UTC")
COST=.001
WEIGHTS=np.round(np.arange(0.0,0.3001,0.001),3)
GATES=["NONE","LAST6_POS","LAST10_POS","D180_POS","D365_POS"]

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
    z=raw.reindex(idx);gap=z.close.isna();prev=z.close.ffill()
    for c in ["open","high","low","close"]:z[c]=z[c].where(~gap,prev)
    if z[["open","high","low","close"]].isna().any().any():raise AssertionError("leading gap")
    z["gap"]=gap;z["dt"]=z.index
    return z.reset_index(drop=True)

def original_regime(hour,b15):
    px=b15.set_index("dt").sort_index();pos=False;peak=0.;pending=None;active={};trail={}
    for t,r in px.iterrows():
        gap=bool(r.gap)
        if t.minute==0 and not gap:
            if pending=="buy" and not pos:pos=True;peak=float(r.open);pending=None
            elif pending=="sell" and pos:pos=False;pending=None
        active[t]=bool(pos)
        if pos and not gap:
            peak=max(peak,float(r.high));stop=peak*.85
            if float(r.low)<=stop:
                xp=min(float(r.open),stop);pos=False;pending=None;trail[t]=xp
        if t.minute==45 and not gap:
            h=t.floor("h")
            if h in hour.index:
                if not pos and bool(hour.at[h,"sig"]):pending="buy"
                elif pos and bool(hour.at[h,"xit"]):pending="sell"
    return pd.Series(active),trail

def prep_base(path):
    idx=pd.date_range(START,END,freq="15min",inclusive="both")
    d=pd.read_csv(path,parse_dates=["time"]).sort_values("time").drop_duplicates("time",keep="last")
    d=d[(d.time>=START)&(d.time<=END)].set_index("time").reindex(idx).ffill();d.loc[START,"equity"]=1.
    eq=d.equity.astype(float);ret=eq.pct_change().fillna(0.)
    core=d.core_active.fillna(0).astype(int);opd=d.open_pd.fillna(0).astype(int)
    idle=(core.eq(0)&core.shift(1).fillna(core.iloc[0]).eq(0)&opd.eq(0)&opd.shift(1).fillna(opd.iloc[0]).eq(0)&(ret.abs()<=2e-9))
    return idx,eq,ret,idle

def gate_ok(kind,hist,t):
    if kind=="NONE":return True
    if kind=="LAST6_POS":
        if len(hist)<6:return True
        return sum(x[1] for x in hist[-6:])>0
    if kind=="LAST10_POS":
        if len(hist)<10:return True
        return sum(x[1] for x in hist[-10:])>0
    days=180 if kind=="D180_POS" else 365
    q=[r for et,r in hist if et>=t-pd.Timedelta(days=days)]
    return True if not q else sum(q)>0

def sim_gate(kind,idx,base_ret,idle,b15,hour,orig_open,orig_trail):
    px=b15.set_index("dt").sort_index();n=len(WEIGHTS)
    E=np.ones(n);stake=np.zeros(n);peakE=np.ones(n);maxdd=np.zeros(n)
    pos=False;shadow=False;ep=peak=0.;pending=None;take=False
    hist=[];taken=0;shadow_n=0;gate_blocks=0
    for mt in idx[1:]:
        t=mt-pd.Timedelta(minutes=15);r=px.loc[t];gap=bool(r.gap);can=bool(idle.loc[mt])

        def close_shadow_actual(xp,reason):
            nonlocal pos,shadow,ep,peak,take,taken,shadow_n,hist,E,stake
            if not shadow:return
            ratio=float(xp)/ep;tr=ratio*(1-COST)-1-COST
            hist.append((t,tr));shadow_n+=1
            if pos:
                E += stake*(ratio-1)-stake*ratio*COST
            pos=False;shadow=False;take=False;stake[:]=0.;ep=0.;peak=0.

        if shadow and not can: close_shadow_actual(float(r.open),"BASE_ON")
        if (not gap) and t.minute==0 and pending=="sell":
            if shadow:close_shadow_actual(float(r.open),"XIT")
            pending=None

        # A new shadow opportunity is the only point where the live sleeve may enter.
        if can and not shadow and (not gap) and bool(orig_open.get(t,False)):
            take=gate_ok(kind,hist,t)
            if not take:gate_blocks+=1
            ep=float(r.open);peak=ep;shadow=True
            if take:
                stake=WEIGHTS*E;E-=stake*COST;pos=True;taken+=1

        if shadow and not gap:
            peak=max(peak,float(r.high));stop=peak*.85
            if t in orig_trail:
                xp=float(orig_trail[t])
                if float(r.low)<=stop:xp=min(xp,min(float(r.open),stop))
                close_shadow_actual(xp,"ORIG_TRAIL")
            elif float(r.low)<=stop:
                close_shadow_actual(min(float(r.open),stop),"TRAIL")

        br=float(base_ret.loc[mt]);E*=1+br
        mark=E.copy()
        if pos:mark += stake*(float(r.close)/ep-1)
        peakE=np.maximum(peakE,mark);maxdd=np.maximum(maxdd,(peakE-mark)/peakE)

        if t.minute==45 and not gap:
            h=t.floor("h")
            if h in hour.index and shadow and bool(hour.at[h,"xit"]):pending="sell"

    if shadow:
        r=px.iloc[-1];close_shadow_actual(float(r.close),"END")
    return pd.DataFrame({"weight":WEIGHTS,"final_equity":E,"mtm_mdd":maxdd,
                         "taken":taken,"shadow_trades":shadow_n,"gate_blocks":gate_blocks})

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--combo-script",required=True);ap.add_argument("--funding",required=True)
    ap.add_argument("--base-dir",required=True);ap.add_argument("--out",required=True);ap.add_argument("--cache",default="spotcache")
    a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True);mod=load_mod(a.combo_script)
    h=mod.dl_spot("BTCUSDT","1h",pd.Timestamp("2019-01-01",tz="UTC"),END,a.cache)
    b15=build_b15(mod,a.cache);hour=prep_hourly(mod,h,a.funding);oo,ot=original_regime(hour,b15)
    rows=[]
    for fp in sorted(glob.glob(str(Path(a.base_dir)/"equity_curve_*bp_risk080.csv"))):
        bps=int(Path(fp).name.split("_")[2].replace("bp",""))
        if bps not in [8,12,16,20]:continue
        idx,beq,bret,idle=prep_base(fp)
        for gate in GATES:
            q=sim_gate(gate,idx,bret,idle,b15,hour,oo,ot);q["cost_bps"]=bps;q["gate"]=gate;q["base_final"]=float(beq.iloc[-1])
            under=q[q.mtm_mdd<=.30+1e-12]
            best=under.sort_values("final_equity",ascending=False).iloc[0]
            w12=q.iloc[np.abs(q.weight-.12).argmin()]
            print("RESULT",bps,gate,"BEST",best.to_dict(),"W12",w12.to_dict())
            rows.append(q)
    R=pd.concat(rows,ignore_index=True);R.to_csv(O/"gate_sweep.csv",index=False)
    B=R[R.mtm_mdd<=.30+1e-12].sort_values(["cost_bps","gate","final_equity"],ascending=[True,True,False]).groupby(["cost_bps","gate"]).head(1)
    B.to_csv(O/"best_under30_by_gate.csv",index=False)
    print("BEST_TABLE")
    print(B[["cost_bps","gate","weight","final_equity","mtm_mdd","taken","shadow_trades","gate_blocks","base_final"]].to_string(index=False))

if __name__=="__main__":main()
