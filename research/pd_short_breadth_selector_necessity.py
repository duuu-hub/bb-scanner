#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import numpy as np
import pandas as pd

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-09-01",tz="UTC")
FIXED={2023:51,2024:71,2025:71,2026:71}
EVENT_RISK=.10

def pf(v):
    v=np.asarray(v,float)
    gp=v[v>0].sum(); gl=-v[v<0].sum()
    return gp/gl if gl>0 else np.inf

def event_curve(v):
    v=np.asarray(v,float)
    mult=1.0+EVENT_RISK*v
    if (mult<=0).any():
        return 0.0,1.0
    eq=np.cumprod(mult)
    curve=np.r_[1.0,eq]
    peak=np.maximum.accumulate(curve)
    mdd=float(np.max((peak-curve)/peak))
    return float(eq[-1]),mdd

def pick(g,mode,n=None):
    q=g.sort_values(["break_atr","symbol"],ascending=[False,True])
    if mode=="all":
        return q
    if mode=="spread5":
        ix=np.unique(np.rint(np.linspace(0,len(q)-1,5)).astype(int))
        return q.iloc[ix]
    return q.head(int(n))

def self_test():
    g=pd.DataFrame({"symbol":[f"S{i}" for i in range(10)],"break_atr":np.arange(10),"r24":np.arange(10,dtype=float)})
    assert list(pick(g,"top",5).symbol)==["S9","S8","S7","S6","S5"]
    assert len(pick(g,"spread5"))==5
    assert len(pick(g,"all"))==10
    e,m=event_curve([1,-1,2])
    assert abs(e-(1.1*.9*1.2))<1e-12 and 0<=m<=1
    print("SELF_TEST_PASS")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--self-test",action="store_true")
    ap.add_argument("--input")
    ap.add_argument("--canonical")
    ap.add_argument("--core")
    ap.add_argument("--out",default="out")
    ap.add_argument("--sims",type=int,default=10000)
    ap.add_argument("--seed",type=int,default=20260928)
    a=ap.parse_args()
    if a.self_test:
        self_test(); return

    C=pd.read_csv(a.core,compression="gzip",parse_dates=["dt"])
    C["dt"]=pd.to_datetime(C.dt,utc=True)
    C=C[(C.dt>=START)&(C.dt<END)].copy()
    cmap=C.set_index("dt").base.to_dict()

    T=pd.read_csv(a.canonical,parse_dates=["signal_time","entry_time","exit_time"])
    T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy()
    T["year"]=T.entry_time.dt.year

    fs=[]; paths={}
    need=set(T.symbol)
    for fn in sorted(glob.glob(a.input+"/**/*.csv.gz",recursive=True)):
        sym=Path(fn).name.replace(".csv.gz","")
        if sym not in need: continue
        paths[sym]=fn
        d=pd.read_csv(fn,compression="gzip")
        tc="open_time" if "open_time" in d else "timestamp_ms"
        d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
        for c in ["open","high","low","close"]:
            d[c]=pd.to_numeric(d[c],errors="coerce")
        d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")
        x=d.resample("4h",label="left",closed="left").agg(
            open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),bars=("close","count"))
        x=x[x.bars==16]
        rl=x.low.shift(1).rolling(320).min()
        atr=(x.high-x.low).shift(1).rolling(14).mean()
        q=pd.DataFrame({"signal_time":x.index,"break_atr":(rl-x.close)/atr,"riskdist":atr})
        q["symbol"]=sym
        fs.append(q)

    F=pd.concat(fs,ignore_index=True)
    T=T.merge(F,on=["symbol","signal_time"],how="left")
    assert T.break_atr.notna().mean()>.99
    T["signals"]=T.groupby("entry_time").symbol.transform("nunique")

    Z=[]
    for y,th in FIXED.items():
        q=T[(T.year==y)&(T.signals>=th)].copy()
        q["wf_threshold"]=th
        Z.append(q)
    S=pd.concat(Z).sort_values(["entry_time","symbol"]).copy()
    assert S.entry_time.nunique()==42 and len(S)==4632,(S.entry_time.nunique(),len(S))

    out=[]
    for sym,g in S.groupby("symbol"):
        fn=paths.get(sym)
        if fn is None: continue
        d=pd.read_csv(fn,compression="gzip")
        tc="open_time" if "open_time" in d else "timestamp_ms"
        d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
        for c in ["open","high","low","close"]:
            d[c]=pd.to_numeric(d[c],errors="coerce")
        d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")
        for r in g.itertuples():
            rd=float(r.riskdist); ep=float(r.entry)
            sl=ep+rd; tp=ep-3*rd
            w=d[(d.index>=r.entry_time)&(d.index<r.entry_time+pd.Timedelta(hours=24))]
            if len(w)!=96: continue
            xp=float(w.iloc[-1].close); reason="TIME"
            for ot,b in w.iterrows():
                hs=b.high>=sl; ht=b.low<=tp
                if hs and ht:
                    xp=sl; reason="SL_AMBIG_15M"; break
                if hs:
                    xp=sl; reason="SL"; break
                if ht:
                    xp=tp; reason="TP"; break
            riskpx=rd/ep
            r24=((ep-xp)/ep-.0024)/riskpx
            out.append(dict(symbol=sym,entry_time=r.entry_time,year=r.year,
                            break_atr=r.break_atr,r24=r24,reason=reason))
    U=pd.DataFrame(out)
    assert len(U)==4632 and U.entry_time.nunique()==42,(len(U),U.entry_time.nunique())
    U["core_active"]=U.entry_time.dt.floor("D").map(cmap).fillna(0).astype(int)
    U=U[U.core_active==0].copy()
    assert U.entry_time.nunique()==40,U.entry_time.nunique()

    O=Path(a.out); O.mkdir(parents=True,exist_ok=True)
    U.to_csv(O/"coreoff_candidate_universe.csv",index=False)

    modes=[("break_top5","top",5),("break_top10","top",10),("break_top20","top",20),
           ("spread5","spread5",None),("all_equalrisk","all",None)]
    erows=[]; summary=[]
    for name,mode,n in modes:
        vals=[]
        for et,g in U.groupby("entry_time",sort=True):
            q=pick(g,mode,n)
            vals.append(dict(selector=name,entry_time=et,year=int(g.year.iloc[0]),candidates=len(g),
                             selected=len(q),event_r=float(q.r24.mean()),trade_pf=pf(q.r24),
                             trade_wr=float((q.r24>0).mean())))
        E=pd.DataFrame(vals)
        eq,mdd=event_curve(E.event_r)
        summary.append(dict(selector=name,events=len(E),avg_event_r=E.event_r.mean(),
                            event_pf=pf(E.event_r),event_wr=(E.event_r>0).mean(),
                            eventblock_equity=eq,eventblock_mdd=mdd))
        erows.append(E)
    ER=pd.concat(erows,ignore_index=True)
    ER.to_csv(O/"event_selector_results.csv",index=False)
    D=pd.DataFrame(summary)
    D.to_csv(O/"selector_eventblock_summary.csv",index=False)

    Y=ER.groupby(["selector","year"]).agg(
        events=("entry_time","size"),avg_event_r=("event_r","mean"),
        event_wr=("event_r",lambda x:float((x>0).mean())),
        event_pf=("event_r",lambda x:pf(x))
    ).reset_index()
    Y.to_csv(O/"selector_yearly.csv",index=False)

    rng=np.random.default_rng(a.seed)
    groups=[g for _,g in U.groupby("entry_time",sort=True)]
    eqs=np.empty(a.sims);mdds=np.empty(a.sims);means=np.empty(a.sims);pfs=np.empty(a.sims)
    per_event=np.empty((a.sims,len(groups)))
    for i in range(a.sims):
        rs=[]
        for j,g in enumerate(groups):
            k=min(5,len(g))
            idx=rng.choice(len(g),size=k,replace=False)
            rr=float(g.iloc[idx].r24.mean())
            rs.append(rr);per_event[i,j]=rr
        eqs[i],mdds[i]=event_curve(rs);means[i]=np.mean(rs);pfs[i]=pf(rs)
    R=dict(
        sims=a.sims,
        equity_p01=np.quantile(eqs,.01),equity_p05=np.quantile(eqs,.05),equity_p50=np.quantile(eqs,.50),
        equity_p95=np.quantile(eqs,.95),equity_p99=np.quantile(eqs,.99),
        mdd_p05=np.quantile(mdds,.05),mdd_p50=np.quantile(mdds,.50),mdd_p95=np.quantile(mdds,.95),mdd_p99=np.quantile(mdds,.99),
        avg_event_r_p05=np.quantile(means,.05),avg_event_r_p50=np.quantile(means,.50),avg_event_r_p95=np.quantile(means,.95),
        event_pf_p05=np.quantile(pfs,.05),event_pf_p50=np.quantile(pfs,.50),event_pf_p95=np.quantile(pfs,.95),
        prob_loss=float((eqs<1).mean()),prob_mdd_ge50=float((mdds>=.50).mean())
    )
    for name in D.selector:
        row=D[D.selector==name].iloc[0]
        R[name+"_equity_pct"]=float((eqs<=row.eventblock_equity).mean())
        R[name+"_mdd_pct"]=float((mdds<=row.eventblock_mdd).mean())
        R[name+"_avgR_pct"]=float((means<=row.avg_event_r).mean())
    pd.DataFrame([R]).to_csv(O/"random5_eventblock_distribution.csv",index=False)

    # Event-by-event: where deterministic selectors sit versus random5 for that same event.
    comp=[]
    top5=ER[ER.selector=="break_top5"].set_index("entry_time")
    allr=ER[ER.selector=="all_equalrisk"].set_index("entry_time")
    spread=ER[ER.selector=="spread5"].set_index("entry_time")
    for j,g in enumerate(groups):
        et=g.entry_time.iloc[0]
        rv=per_event[:,j]
        comp.append(dict(entry_time=et,year=int(g.year.iloc[0]),candidates=len(g),
                         random5_mean=float(rv.mean()),random5_p05=float(np.quantile(rv,.05)),
                         random5_p50=float(np.quantile(rv,.50)),random5_p95=float(np.quantile(rv,.95)),
                         break_top5=float(top5.loc[et].event_r),
                         break_top5_pct=float((rv<=top5.loc[et].event_r).mean()),
                         spread5=float(spread.loc[et].event_r),
                         spread5_pct=float((rv<=spread.loc[et].event_r).mean()),
                         all_equalrisk=float(allr.loc[et].event_r)))
    Cmp=pd.DataFrame(comp)
    Cmp.to_csv(O/"event_random5_comparison.csv",index=False)

    print("SELECTOR_EVENTBLOCK")
    print(D.to_string(index=False))
    print("YEARLY")
    print(Y.to_string(index=False))
    print("RANDOM5")
    print(pd.DataFrame([R]).to_string(index=False))
    print("EVENT_PERCENTILES",
          {"break_top5_median_pct":float(Cmp.break_top5_pct.median()),
           "break_top5_events_above_random_median":int((Cmp.break_top5_pct>.5).sum()),
           "spread5_median_pct":float(Cmp.spread5_pct.median()),
           "all_positive_years":bool((Y.groupby("selector").event_pf.min()>1).all())})

if __name__=="__main__":
    main()
