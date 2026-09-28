#!/usr/bin/env python3
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import ema_top10_overlay_stateful as h

def sim_many(mode,weights,idx,base_ret,idle,px,hour,orig_open,orig_trail):
    weights=np.asarray(weights,float)
    E=np.ones(len(weights),float)
    stake=np.zeros(len(weights),float)
    mtm_peak=np.ones(len(weights),float)
    mtm_mdd=np.zeros(len(weights),float)
    pos=False;ep=0.;peakpx=0.;pending=None;trades=0

    def close_at(price):
        nonlocal E,stake,pos,ep,peakpx
        ratio=float(price)/ep
        E += stake*(ratio-1)-stake*ratio*h.COST
        stake=np.zeros_like(stake);pos=False;ep=0.;peakpx=0.

    for mt in idx[1:]:
        t=mt-pd.Timedelta(minutes=15)
        r=px.loc[t]
        can_idle=bool(idle.loc[mt])
        is_gap=bool(r.get("gap",False))

        if is_gap:
            if pos: close_at(float(r.open))
            pending=None
            E *= 1+float(base_ret.loc[mt])
            mtm_peak=np.maximum(mtm_peak,E)
            mtm_mdd=np.maximum(mtm_mdd,(mtm_peak-E)/mtm_peak)
            continue

        if pos and not can_idle:
            close_at(float(r.open))

        if t.minute==0 and pending=="sell":
            if pos: close_at(float(r.open))
            pending=None

        if t.minute==0 and pending=="buy":
            if mode=="SIGNAL_ONLY" and can_idle and not pos:
                stake=weights*E
                E-=stake*h.COST
                ep=float(r.open);peakpx=ep;pos=True;trades+=1
            pending=None

        if mode=="REGIME_REENTRY" and can_idle and not pos and bool(orig_open.get(t,False)):
            stake=weights*E
            E-=stake*h.COST
            ep=float(r.open);peakpx=ep;pos=True;trades+=1

        if pos:
            peakpx=max(peakpx,float(r.high));stop=peakpx*.85
            hit=float(r.low)<=stop
            if mode=="REGIME_REENTRY" and t in orig_trail:
                opx=float(orig_trail[t])
                if hit:opx=min(opx,min(float(r.open),stop))
                close_at(opx)
            elif hit:
                close_at(min(float(r.open),stop))

        br=float(base_ret.loc[mt])
        if pos and abs(br)>2e-9:
            raise AssertionError(("overlay overlaps base return",mode,t,br))
        E*=1+br

        mark=E.copy()
        if pos:mark += stake*(float(r.close)/ep-1)
        mtm_peak=np.maximum(mtm_peak,mark)
        mtm_mdd=np.maximum(mtm_mdd,(mtm_peak-mark)/mtm_peak)

        if t.minute==45:
            hh=t.floor("h")
            if hh in hour.index:
                if mode=="SIGNAL_ONLY":
                    if not pos and bool(hour.at[hh,"sig"]):pending="buy"
                    elif pos and bool(hour.at[hh,"xit"]):pending="sell"
                else:
                    if pos and bool(hour.at[hh,"xit"]):pending="sell"

    if pos:
        close_at(float(px.iloc[-1].close))
        mtm_peak=np.maximum(mtm_peak,E)
        mtm_mdd=np.maximum(mtm_mdd,(mtm_peak-E)/mtm_peak)
    return pd.DataFrame({"weight":weights,"final_equity":E,"mtm_mdd":mtm_mdd,"trades":trades})

def self_test():
    w=np.array([0.,.1,.2])
    E=np.ones(3);stake=w*E;E-=stake*.001
    assert E[0]==1 and E[2]<E[1]<E[0]
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
    mod=h.load_mod(a.combo_script)
    hourly=mod.dl_spot("BTCUSDT","1h",pd.Timestamp("2019-01-01",tz="UTC"),h.END,a.cache)
    b15=mod.dl_spot("BTCUSDT","15m",h.START,h.END,a.cache)
    px,nmiss,nunresolved=h.fill_b15(mod,b15,a.cache)
    hour=h.prep_hourly(mod,hourly,a.funding)
    orig_open,orig_trail=h.original_active(hour,px)
    idx,d,beq,bret,idle=h.build_idle(a.base24)

    base_final=float(beq.iloc[-1])
    arr=beq.to_numpy(float);pp=np.maximum.accumulate(arr);base_mdd=float(np.max((pp-arr)/pp))
    weights=np.round(np.arange(0,0.5001,.001),3)

    rows=[];best=[]
    for mode in ["SIGNAL_ONLY","REGIME_REENTRY"]:
        q=sim_many(mode,weights,idx,bret,idle,px,hour,orig_open,orig_trail)
        z0=q[q.weight==0].iloc[0]
        if abs(float(z0.final_equity)-base_final)>2e-8 or abs(float(z0.mtm_mdd)-base_mdd)>2e-8:
            raise AssertionError(("weight0 parity",mode,z0.to_dict(),base_final,base_mdd))
        q["mode"]=mode
        q["gain_vs_base"]=q.final_equity/base_final-1
        q["mdd_delta"]=q.mtm_mdd-base_mdd
        q.to_csv(O/f"vector_sweep_24bp_{mode.lower()}.csv",index=False)
        rows.append(q)
        for ceiling in [.30,.32,.35]:
            u=q[q.mtm_mdd<=ceiling+1e-12]
            if len(u):
                z=u.sort_values("final_equity",ascending=False).iloc[0]
                best.append(dict(mode=mode,mdd_ceiling=ceiling,feasible=True,
                                 weight=float(z.weight),final_equity=float(z.final_equity),
                                 mtm_mdd=float(z.mtm_mdd),gain_vs_base=float(z.gain_vs_base),
                                 mdd_delta=float(z.mdd_delta),trades=int(z.trades)))
            else:
                best.append(dict(mode=mode,mdd_ceiling=ceiling,feasible=False,
                                 weight=np.nan,final_equity=np.nan,mtm_mdd=np.nan,
                                 gain_vs_base=np.nan,mdd_delta=np.nan,trades=0))
        z=q.sort_values("final_equity",ascending=False).iloc[0]
        best.append(dict(mode=mode,mdd_ceiling=np.nan,feasible=True,
                         weight=float(z.weight),final_equity=float(z.final_equity),
                         mtm_mdd=float(z.mtm_mdd),gain_vs_base=float(z.gain_vs_base),
                         mdd_delta=float(z.mdd_delta),trades=int(z.trades)))
    R=pd.concat(rows,ignore_index=True);B=pd.DataFrame(best)
    R.to_csv(O/"vector_sweep_24bp.csv",index=False)
    B.to_csv(O/"vector_best_24bp.csv",index=False)
    print("BASE",{"final_equity":base_final,"mtm_mdd":base_mdd,
                  "idle_bars":int(idle.sum()),"missing15":nmiss,"guarded_gap_bars":nunresolved})
    print("BEST")
    print(B.to_string(index=False))

if __name__=="__main__":
    main()
