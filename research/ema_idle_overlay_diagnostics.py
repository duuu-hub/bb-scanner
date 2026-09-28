#!/usr/bin/env python3
import argparse,importlib.util
from pathlib import Path
import numpy as np,pandas as pd
START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-08-22",tz="UTC")
CFG={8:0.281,24:0.023}

def load_mod(path):
    sp=importlib.util.spec_from_file_location("combo",path)
    m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m

def mdd_stats(eq):
    x=np.asarray(eq,float);p=np.maximum.accumulate(x);dd=(p-x)/p
    i=int(np.argmax(dd));peak=float(p[i]);pi=int(np.where(x[:i+1]>=peak-1e-12)[0][-1])
    rec=np.where(x[i+1:]>=peak-1e-12)[0];ri=i+1+int(rec[0]) if len(rec) else None
    return dict(mdd=float(dd[i]),peak_i=pi,trough_i=i,recovery_i=ri)

def period_stats(eq):
    ret=eq.pct_change().fillna(0)
    out=[]
    for y,g in eq.groupby(eq.index.year):
        st=float(g.iloc[0]);en=float(g.iloc[-1]);md=mdd_stats(g.values)["mdd"]
        out.append(dict(period=str(y),return_pct=(en/st-1)*100,mdd_pct=md*100))
    for p,g in eq.groupby(eq.index.to_period("Q")):
        if len(g)<2: continue
        st=float(g.iloc[0]);en=float(g.iloc[-1]);md=mdd_stats(g.values)["mdd"]
        out.append(dict(period=str(p),return_pct=(en/st-1)*100,mdd_pct=md*100))
    return pd.DataFrame(out)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--combo-script",required=True);ap.add_argument("--funding",required=True)
    ap.add_argument("--stage5pd8",required=True);ap.add_argument("--stage5pd24",required=True)
    ap.add_argument("--out",required=True);ap.add_argument("--cache",default="spotcache")
    a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
    mod=load_mod(a.combo_script)
    h=mod.dl_spot("BTCUSDT","1h",pd.Timestamp("2019-01-01",tz="UTC"),END,a.cache)
    b15=mod.dl_spot("BTCUSDT","15m",mod.EMA_FULL_START,END,a.cache)
    orig,canon,full,emaC,er,ea,tr=mod.build_ema(h,a.funding,b15)
    assert abs(orig-17.709626335603225)<2e-8 and abs(canon-orig)<2e-8
    idx=pd.date_range(START,END,freq="15min",inclusive="both")
    ee=pd.concat([pd.Series([1.0],index=[START]),emaC.equity]).sort_index()
    ee=ee[~ee.index.duplicated(keep="last")].reindex(idx).ffill()
    eret=ee.pct_change().fillna(0.0);eactive=ea.reindex(idx).fillna(False).astype(bool)

    summary=[]
    for bps,fp in [(8,a.stage5pd8),(24,a.stage5pd24)]:
        d=pd.read_csv(fp,parse_dates=["time"]).sort_values("time").drop_duplicates("time",keep="last")
        d=d[(d.time>=START)&(d.time<=END)].set_index("time").reindex(idx).ffill()
        d.loc[START,"equity"]=1.0
        base=d.equity.astype(float);bret=base.pct_change().fillna(0.0)
        core=d.core_active.fillna(0).astype(int);opd=d.open_pd.fillna(0).astype(int)
        prev_core=core.shift(1).fillna(core.iloc[0]).astype(int);prev_pd=opd.shift(1).fillna(opd.iloc[0]).astype(int)
        mask=(core.eq(0)&prev_core.eq(0)&opd.eq(0)&prev_pd.eq(0)&eactive)
        w=CFG[bps]
        r=bret.copy();r.loc[mask]=r.loc[mask]+w*eret.loc[mask]
        eq=(1+r).cumprod()
        st=mdd_stats(eq.values);bt=mdd_stats(base.values)
        # direct overlay multiplier contribution
        overlay_only=(1+(w*eret.where(mask,0.0))).cumprod()
        # active episode count
        starts=mask & ~mask.shift(1).fillna(False)
        episodes=int(starts.sum())
        active_bars=int(mask.sum());active_days=active_bars/96.0
        peak=eq.index[st["peak_i"]];trough=eq.index[st["trough_i"]]
        recovery=eq.index[st["recovery_i"]] if st["recovery_i"] is not None else pd.NaT
        # contribution during worst DD window
        seg=(eq.index>=peak)&(eq.index<=trough)
        base_seg=(1+bret.where(seg,0.0)).cumprod()
        ov_seg=(1+(w*eret.where(mask&seg,0.0))).cumprod()
        summary.append(dict(cost_bps=bps,weight=w,final_equity=float(eq.iloc[-1]),mdd_pct=st["mdd"]*100,
                            base_final=float(base.iloc[-1]),base_mdd_pct=bt["mdd"]*100,
                            overlay_multiplier=float(overlay_only.iloc[-1]),
                            overlay_active_bars=active_bars,overlay_active_days=active_days,overlay_episodes=episodes,
                            dd_peak=peak,dd_trough=trough,dd_recovery=recovery,
                            base_component_multiplier_in_worst_dd=float(base_seg.iloc[-1]),
                            overlay_component_multiplier_in_worst_dd=float(ov_seg.iloc[-1])))
        pd.DataFrame({"time":idx,"equity":eq.values,"base_equity":base.values,"overlay_mask":mask.values,
                      "ema_ret":eret.values,"base_ret":bret.values}).to_csv(O/f"curve_{bps}bp.csv",index=False)
        period_stats(eq).to_csv(O/f"periods_{bps}bp.csv",index=False)
        # local sensitivity +/- 5 percentage points around best
        loc=[]
        for ww in np.round(np.arange(max(0,w-.05),min(1,w+.0501),.005),3):
            rr=bret.copy();rr.loc[mask]=rr.loc[mask]+ww*eret.loc[mask]
            qq=(1+rr).cumprod();mm=mdd_stats(qq.values)["mdd"]
            loc.append(dict(weight=ww,final_equity=float(qq.iloc[-1]),mdd_pct=mm*100))
        pd.DataFrame(loc).to_csv(O/f"sensitivity_{bps}bp.csv",index=False)
        print("SUMMARY",summary[-1])
        print("SENS",bps);print(pd.DataFrame(loc).to_string(index=False))
    pd.DataFrame(summary).to_csv(O/"summary.csv",index=False)

if __name__=="__main__":main()
