#!/usr/bin/env python3
import argparse,importlib.util
from pathlib import Path
import numpy as np,pandas as pd

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-08-22",tz="UTC")
W=.12; COST=.001

def load_mod(path):
    s=importlib.util.spec_from_file_location("m",path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def prep_hour(mod,hourly,funding):
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

def b15(mod,cache):
    raw=mod.dl_spot("BTCUSDT","15m",START,END,cache).set_index("dt").sort_index()
    idx=pd.date_range(START,END-pd.Timedelta(minutes=15),freq="15min")
    z=raw.reindex(idx);gap=z.close.isna();prev=z.close.ffill()
    for c in ["open","high","low","close"]:z[c]=z[c].where(~gap,prev)
    if z[["open","high","low","close"]].isna().any().any():raise AssertionError("leading gap")
    z["gap"]=gap;z["dt"]=z.index
    return z.reset_index(drop=True)

def orig(hour,b):
    px=b.set_index("dt");pos=False;peak=0.;pending=None;active={};trail={}
    for t,r in px.iterrows():
        gap=bool(r.gap)
        if t.minute==0 and not gap:
            if pending=="buy" and not pos:pos=True;peak=float(r.open);pending=None
            elif pending=="sell" and pos:pos=False;pending=None
        active[t]=bool(pos)
        if pos and not gap:
            peak=max(peak,float(r.high));st=peak*.85
            if float(r.low)<=st:
                trail[t]=min(float(r.open),st);pos=False;pending=None
        if t.minute==45 and not gap:
            h=t.floor("h")
            if h in hour.index:
                if not pos and bool(hour.at[h,"sig"]):pending="buy"
                elif pos and bool(hour.at[h,"xit"]):pending="sell"
    return pd.Series(active),trail

def base(path):
    idx=pd.date_range(START,END,freq="15min",inclusive="both")
    d=pd.read_csv(path,parse_dates=["time"]).sort_values("time").drop_duplicates("time",keep="last").set_index("time").reindex(idx).ffill()
    d.loc[START,"equity"]=1.
    ret=d.equity.astype(float).pct_change().fillna(0.)
    c=d.core_active.fillna(0).astype(int);p=d.open_pd.fillna(0).astype(int)
    idle=(c.eq(0)&c.shift(1).fillna(c.iloc[0]).eq(0)&p.eq(0)&p.shift(1).fillna(p.iloc[0]).eq(0)&ret.abs().le(2e-9))
    return idx,ret,idle

def gate_ok(kind,hist,t):
    if kind=="NONE": return True
    q=[r for et,r in hist if et>=t-pd.Timedelta(days=365)]
    return True if not q else sum(q)>0

def sim(kind,idx,bret,idle,b,hour,oo,ot):
    px=b.set_index("dt");E=1.;pos=shadow=False;stake=ep=peak=0.;pending=None;hist=[]
    curve=[(START,E)];taken=[];blocked=[]
    def close(t,xp):
        nonlocal E,pos,shadow,stake,ep,peak
        if not shadow:return
        ratio=float(xp)/ep;tr=ratio*(1-COST)-1-COST;hist.append((t,tr))
        if pos:E+=stake*(ratio-1)-stake*ratio*COST
        pos=False;shadow=False;stake=ep=peak=0.
    for mt in idx[1:]:
        t=mt-pd.Timedelta(minutes=15);r=px.loc[t];gap=bool(r.gap);can=bool(idle.loc[mt])
        if shadow and not can:close(t,float(r.open))
        if not gap and t.minute==0 and pending=="sell":
            if shadow:close(t,float(r.open))
            pending=None
        if can and not shadow and not gap and bool(oo.get(t,False)):
            take=gate_ok(kind,hist,t);shadow=True;ep=float(r.open);peak=ep
            if take:
                stake=W*E;E-=stake*COST;pos=True;taken.append(t)
            else:
                blocked.append(t)
        if shadow and not gap:
            peak=max(peak,float(r.high));st=peak*.85
            if t in ot:
                xp=float(ot[t])
                if float(r.low)<=st:xp=min(xp,min(float(r.open),st))
                close(t,xp)
            elif float(r.low)<=st:
                close(t,min(float(r.open),st))
        E*=1+float(bret.loc[mt])
        mark=E+(stake*(float(r.close)/ep-1) if pos else 0)
        curve.append((mt,mark))
        if t.minute==45 and not gap:
            h=t.floor("h")
            if h in hour.index and shadow and bool(hour.at[h,"xit"]):pending="sell"
    if shadow:close(px.index[-1],float(px.iloc[-1].close))
    return pd.DataFrame(curve,columns=["time","equity"]).set_index("time"),taken,blocked

def metrics(s):
    if len(s)<2:return np.nan,np.nan
    r=float(s.iloc[-1]/s.iloc[0]-1)
    x=s.to_numpy(float);p=np.maximum.accumulate(x);dd=float(((p-x)/p).max())
    return r,dd

def windows():
    out=[]
    starts=pd.date_range("2024-01-01","2026-01-01",freq="6MS",tz="UTC")
    for st in starts:
        for months in [6,12]:
            en=min(st+pd.DateOffset(months=months),END)
            if en-st>=pd.Timedelta(days=150):
                out.append((st,en,months))
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--combo-script",required=True);ap.add_argument("--funding",required=True)
    ap.add_argument("--base",required=True);ap.add_argument("--out",required=True);ap.add_argument("--cache",default="spotcache")
    a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True);m=load_mod(a.combo_script)
    h=prep_hour(m,m.dl_spot("BTCUSDT","1h",pd.Timestamp("2019-01-01",tz="UTC"),END,a.cache),a.funding)
    b=b15(m,a.cache);oo,ot=orig(h,b);idx,br,idle=base(a.base)
    curves={}
    for g in ["NONE","D365_POS"]:
        C,en,bl=sim(g,idx,br,idle,b,h,oo,ot)
        curves[g]=C
        C.reset_index().to_csv(O/f"curve_{g.lower()}.csv",index=False)
        pd.DataFrame({"entry_time":en}).to_csv(O/f"entries_{g.lower()}.csv",index=False)
        pd.DataFrame({"blocked_time":bl}).to_csv(O/f"blocked_{g.lower()}.csv",index=False)
        print("FULL",g,{"final":float(C.equity.iloc[-1]),"entries":len(en),"blocked":len(bl)})

    rows=[]
    for st,en,months in windows():
        row={"start":st,"end":en,"months":months}
        for g,C in curves.items():
            q=C[(C.index>=st)&(C.index<en)].equity
            r,dd=metrics(q)
            row[f"{g}_ret_pct"]=r*100
            row[f"{g}_mdd_pct"]=dd*100
        row["ret_delta_pp"]=row["D365_POS_ret_pct"]-row["NONE_ret_pct"]
        row["mdd_delta_pp"]=row["D365_POS_mdd_pct"]-row["NONE_mdd_pct"]
        row["return_better"]=row["ret_delta_pp"]>0
        row["mdd_better"]=row["mdd_delta_pp"]<0
        row["both_better"]=row["return_better"] and row["mdd_better"]
        rows.append(row)
    R=pd.DataFrame(rows).sort_values(["months","start"])
    R.to_csv(O/"rolling_oos.csv",index=False)
    print("ROLLING");print(R.to_string(index=False))
    for months,g in R.groupby("months"):
        s={
            "months":int(months),"windows":len(g),
            "return_better":int(g.return_better.sum()),
            "mdd_better":int(g.mdd_better.sum()),
            "both_better":int(g.both_better.sum()),
            "median_ret_delta_pp":float(g.ret_delta_pp.median()),
            "mean_ret_delta_pp":float(g.ret_delta_pp.mean()),
            "median_mdd_delta_pp":float(g.mdd_delta_pp.median()),
            "worst_ret_delta_pp":float(g.ret_delta_pp.min()),
            "best_ret_delta_pp":float(g.ret_delta_pp.max())
        }
        print("SUMMARY",s)

if __name__=="__main__":main()
