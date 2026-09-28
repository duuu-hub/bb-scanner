#!/usr/bin/env python3
import argparse, glob
from pathlib import Path
import numpy as np
import pandas as pd
import pd_breakdown_core_mtm as cm

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-09-01",tz="UTC")
FIXED={2023:51,2024:71,2025:71,2026:71}
COSTS=[24,32,40,50]
SLIPS=[0,5,10,20]  # adverse entry slippage in bp for SHORT: fill = raw_open*(1-slip)

def build_base(input_dir,canonical_path,core):
    T=pd.read_csv(canonical_path,parse_dates=["signal_time","entry_time","exit_time"])
    T=T[(T.n==320)&(T.signal=="FIRST_BREAKDOWN")&(T.stop_atr==1)&(T.rr==3)].copy()
    T["year"]=T.entry_time.dt.year
    need=set(T.symbol); paths={}; feats=[]
    for fn in sorted(glob.glob(str(Path(input_dir)/"**/*.csv.gz"),recursive=True)):
        sym=Path(fn).name.replace(".csv.gz","")
        if sym not in need: continue
        paths[sym]=fn
        d=pd.read_csv(fn,compression="gzip")
        tc="open_time" if "open_time" in d else "timestamp_ms"
        d["dt"]=cm.normalize_ms(d[tc])
        for c in ["open","high","low","close"]:
            d[c]=pd.to_numeric(d[c],errors="coerce")
        d=d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")
        x=d.resample("4h",label="left",closed="left").agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),bars=("close","count"))
        x=x[x.bars==16]
        rl=x.low.shift(1).rolling(320).min()
        atr=(x.high-x.low).shift(1).rolling(14).mean()
        q=pd.DataFrame({"signal_time":x.index,"break_atr":(rl-x.close)/atr,"risk_dist":atr})
        q["symbol"]=sym; feats.append(q)
    F=pd.concat(feats,ignore_index=True)
    T=T.merge(F,on=["symbol","signal_time"],how="left")
    assert T.break_atr.notna().mean()>.99
    T["signals"]=T.groupby("entry_time").symbol.transform("nunique")
    z=[]
    for y,th in FIXED.items():
        q=T[(T.year==y)&(T.signals>=th)].copy()
        q["wf_threshold"]=th; z.append(q)
    U=pd.concat(z).sort_values(["entry_time","break_atr","symbol"],ascending=[True,False,True]).copy()
    assert U.entry_time.nunique()==42 and len(U)==4632,(U.entry_time.nunique(),len(U))
    U["selector_rank"]=U.groupby("entry_time").cumcount()+1
    U=U[U.selector_rank<=10].copy()
    assert len(U)==420 and U.entry_time.nunique()==42
    cmap=core.set_index("dt").base.to_dict()
    U["core_active"]=U.entry_time.dt.floor("D").map(cmap).fillna(0).astype(int)
    return U,paths

def load_symbol(fn):
    d=pd.read_csv(fn,compression="gzip")
    tc="open_time" if "open_time" in d else "timestamp_ms"
    d["dt"]=cm.normalize_ms(d[tc])
    for c in ["open","high","low","close"]:
        d[c]=pd.to_numeric(d[c],errors="coerce")
    return d.dropna(subset=["dt","open","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")

def refine(U,paths,cost_bp,slip_bp):
    out=[]
    cache={}
    for sym,g in U.groupby("symbol"):
        d=cache.setdefault(sym,load_symbol(paths[sym]))
        for r in g.itertuples():
            raw=float(r.entry)
            fill=raw*(1-slip_bp/10000.0)
            rd=float(r.risk_dist)
            sl=fill+rd; tp=fill-3*rd
            w=d[(d.index>=r.entry_time)&(d.index<r.entry_time+pd.Timedelta(hours=24))]
            if len(w)!=96:
                raise AssertionError(f"bad 15m window {sym} {r.entry_time} {len(w)}")
            xp=float(w.iloc[-1].close); xt=r.entry_time+pd.Timedelta(hours=24); reason="TIME"
            for ot,b in w.iterrows():
                hs=float(b.high)>=sl; ht=float(b.low)<=tp
                if hs and ht:
                    xp=sl; xt=ot+pd.Timedelta(minutes=15); reason="SL_AMBIG_15M"; break
                if hs:
                    xp=sl; xt=ot+pd.Timedelta(minutes=15); reason="SL"; break
                if ht:
                    xp=tp; xt=ot+pd.Timedelta(minutes=15); reason="TP"; break
            riskpx=rd/fill
            gross=(fill-xp)/fill
            rnet=(gross-cost_bp/10000.0)/riskpx
            out.append(dict(symbol=sym,entry_time=r.entry_time,exit15=xt,entry=fill,raw_entry=raw,
                            risk_dist=rd,selector_rank=int(r.selector_rank),break_atr=float(r.break_atr),
                            r_net=float(rnet),reason=reason,core_active=int(r.core_active),
                            cost_bp=cost_bp,slip_bp=slip_bp))
    R=pd.DataFrame(out).sort_values(["entry_time","selector_rank","symbol"]).reset_index(drop=True)
    assert len(R)==420 and R.entry_time.nunique()==42
    return R

def short_paths(S,px):
    rows=[]
    for r in S.itertuples():
        d=px[r.symbol]
        rows.append((r.symbol,r.entry_time,r.entry_time,0.0))
        w=d[(d.index>=r.entry_time)&((d.index+pd.Timedelta(minutes=15))<r.exit15)]
        for ot,b in w.iterrows():
            mt=ot+pd.Timedelta(minutes=15)
            gross_r=(float(r.entry)-float(b.close))/float(r.risk_dist)
            rows.append((r.symbol,r.entry_time,mt,gross_r))
    return pd.DataFrame(rows,columns=["symbol","entry_time","mark_time","gross_r"])

def simulate(core,S,core_marks,sp,rf=.01,max_slots=10,use_core=True,use_shorts=True,initial_prev_base=0):
    core=core.sort_values("dt").reset_index(drop=True)
    cdict={r.dt:r for r in core.itertuples()}
    cmaps={d:g.set_index("mark_time").gross.to_dict() for d,g in core_marks.groupby("day")}
    paths={(sym,et):g.set_index("mark_time").gross_r.to_dict() for (sym,et),g in sp.groupby(["symbol","entry_time"])}
    groups={et:g for et,g in S.groupby("entry_time",sort=True)}
    cash=1.; peak=1.; mtm=0.; bpeak=1.; bmdd=0.; openp=[]; acc=[]; max_open=0
    core_stake=0.; current_day=None; core_gross=0.; prev=int(initial_prev_base)

    def realize(t):
        nonlocal cash,openp,bpeak,bmdd
        done=[p for p in openp if p["exit15"]<=t]
        if done:
            cash+=sum(p["stake"]*p["r_net"] for p in done)
            openp=[p for p in openp if p["exit15"]>t]
            bpeak=max(bpeak,cash); bmdd=max(bmdd,(bpeak-cash)/bpeak)

    def mark(t):
        nonlocal peak,mtm
        eq=cash
        if use_core and current_day is not None and int(cdict[current_day].base):
            eq+=core_stake*core_gross
        for p in openp:
            path=paths[(p["symbol"],p["entry_time"])]
            ks=[k for k in path if k<=t]
            gr=path[max(ks)] if ks else 0.
            # current mark gross R minus full round-trip cost in R units
            eq+=p["stake"]*(gr-p["cost_r"])
        peak=max(peak,eq); mtm=max(mtm,(peak-eq)/peak)

    for t in pd.date_range(START,END,freq="15min",inclusive="both"):
        if t.minute==0 and t.hour==0:
            if current_day is not None and use_core and int(cdict[current_day].base):
                core_gross=cmaps[current_day][t]; mark(t); cash+=core_stake*core_gross
                bpeak=max(bpeak,cash); bmdd=max(bmdd,(bpeak-cash)/bpeak)
            realize(t)
            if t>=END: break
            day=t; row=cdict[day]; state=int(row.base); base_before=cash
            if use_core:
                fee=abs(state-prev)*cm.CORE_HALF_FEE
                if fee: cash-=base_before*fee
                core_stake=base_before if state else 0.
            else:
                core_stake=0.
            current_day=day; core_gross=0.; prev=state
            bpeak=max(bpeak,cash); bmdd=max(bmdd,(bpeak-cash)/bpeak)
        else:
            realize(t)
            day=t.floor("D"); row=cdict[day]
            core_gross=cmaps[day][t] if (use_core and int(row.base)) else 0.

        day=t.floor("D"); row=cdict[day]
        if use_shorts and t in groups and int(row.base)==0:
            g=groups[t]; free=max_slots-len(openp)
            if free>0:
                base=cash
                for r in g.head(free).itertuples():
                    riskpx=float(r.risk_dist)/float(r.entry)
                    p=dict(symbol=r.symbol,entry_time=r.entry_time,exit15=r.exit15,
                           stake=base*rf,r_net=float(r.r_net),
                           cost_r=(float(r.cost_bp)/10000.0)/riskpx)
                    openp.append(p); acc.append(p)
        max_open=max(max_open,len(openp)); mark(t)

    if openp: raise AssertionError(f"open shorts remain {len(openp)}")
    A=pd.DataFrame(acc)
    return dict(final_equity=cash,booked_mdd=bmdd,mtm_mdd=mtm,
                accepted=len(A),events=(A.entry_time.nunique() if len(A) else 0),max_open=max_open)

def self_test():
    raw=100.; slip=10; fill=raw*(1-slip/10000.)
    assert abs(fill-99.9)<1e-12
    rd=1.; riskpx=rd/fill
    r=((fill-(fill-3*rd))/fill-.0024)/riskpx
    assert abs(r-(3-.0024/riskpx))<1e-12
    print("SELF_TEST_PASS",{"fill":fill,"r":r})

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--self-test",action="store_true")
    ap.add_argument("--core"); ap.add_argument("--canonical"); ap.add_argument("--um")
    ap.add_argument("--out",default="out"); ap.add_argument("--spot-cache",default="spot15m")
    a=ap.parse_args()
    if a.self_test:
        self_test(); return

    allcore=pd.read_csv(a.core,compression="gzip",parse_dates=["dt"])
    allcore["dt"]=pd.to_datetime(allcore.dt,utc=True)
    prev=int(allcore.loc[allcore.dt<START,"base"].iloc[-1]) if (allcore.dt<START).any() else 0
    core=allcore[(allcore.dt>=START)&(allcore.dt<END)].copy().reset_index(drop=True)

    U,paths=build_base(a.um,a.canonical,core)
    btc=cm.download_spot_15m("BTCUSDT",START,END,a.spot_cache)
    eth=cm.download_spot_15m("ETHUSDT",START,END,a.spot_cache)
    core_marks,par=cm.build_core_marks(core,btc,eth,prev)

    px={}
    for sym in set(U.symbol):
        d=load_symbol(paths[sym])
        px[sym]=d[["close"]]

    O=Path(a.out); O.mkdir(parents=True,exist_ok=True)
    par.to_csv(O/"core_spot15m_parity.csv",index=False)

    rows=[]
    baseline_reason=None
    for slip in SLIPS:
        for cost in COSTS:
            S=refine(U,paths,cost,slip)
            q=S[S.core_active==0].copy()
            sp=short_paths(q,px)
            combo=simulate(core,q,core_marks,sp,.01,10,True,True,prev)
            short=simulate(core,q,core_marks,sp,.01,10,False,True,prev)
            rc=q.reason.value_counts().to_dict()
            if slip==0 and cost==24:
                baseline_reason=rc
            rows.append(dict(cost_bp=cost,entry_slip_bp=slip,
                             combo_equity=combo["final_equity"],combo_booked_mdd=combo["booked_mdd"],combo_mtm_mdd=combo["mtm_mdd"],
                             combo_trades=combo["accepted"],combo_events=combo["events"],combo_max_open=combo["max_open"],
                             short_equity=short["final_equity"],short_booked_mdd=short["booked_mdd"],short_mtm_mdd=short["mtm_mdd"],
                             short_trades=short["accepted"],short_events=short["events"],short_max_open=short["max_open"],
                             tp=rc.get("TP",0),sl=rc.get("SL",0),ambig15=rc.get("SL_AMBIG_15M",0),timeout=rc.get("TIME",0)))
    R=pd.DataFrame(rows)
    R.to_csv(O/"top10_cost_slippage_stress.csv",index=False)
    print(R.to_string(index=False))
    print("BASELINE_REASON",baseline_reason)

if __name__=="__main__":
    main()
