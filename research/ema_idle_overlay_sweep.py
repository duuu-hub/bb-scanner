#!/usr/bin/env python3
import argparse,importlib.util
from pathlib import Path
import numpy as np,pandas as pd

START=pd.Timestamp("2023-01-01",tz="UTC")
END=pd.Timestamp("2026-08-22",tz="UTC")

def load_mod(path):
    sp=importlib.util.spec_from_file_location("combo",path)
    m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
    return m

def mdd(eq):
    x=np.asarray(eq,float);p=np.maximum.accumulate(x);dd=(p-x)/p
    i=int(np.argmax(dd));peak=float(p[i]);pi=int(np.where(x[:i+1]>=peak-1e-12)[0][-1])
    rec=np.where(x[i+1:]>=peak-1e-12)[0];ri=i+1+int(rec[0]) if len(rec) else None
    return float(dd[i]),pi,i,ri

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
    ema_ret=ee.pct_change().fillna(0.0)
    ema_active=ea.reindex(idx).fillna(False).astype(bool)

    rows=[]
    for bps,fp in [(8,a.stage5pd8),(24,a.stage5pd24)]:
        d=pd.read_csv(fp,parse_dates=["time"]).sort_values("time").drop_duplicates("time",keep="last")
        d=d[(d.time>=START)&(d.time<=END)].set_index("time").reindex(idx).ffill()
        d.loc[START,"equity"]=1.0
        base_eq=d.equity.astype(float);base_ret=base_eq.pct_change().fillna(0.0)
        core=d.core_active.fillna(0).astype(int)
        opd=d.open_pd.fillna(0).astype(int)

        # Use both ends of the interval to avoid treating a transition/exit bar as idle.
        prev_core=core.shift(1).fillna(core.iloc[0]).astype(int)
        prev_pd=opd.shift(1).fillna(opd.iloc[0]).astype(int)
        strict_idle=(core.eq(0)&prev_core.eq(0)&opd.eq(0)&prev_pd.eq(0)&ema_active)
        coreoff=(core.eq(0)&prev_core.eq(0)&ema_active)

        for mode,mask in [("STRICT_IDLE",strict_idle),("CORE_OFF_OVERLAY",coreoff)]:
            for wi in range(0,1001):
                w=wi/1000.0
                r=base_ret.copy()
                # Overlay only when selected mask is true; EMA sleeve is funded at fraction w of equity.
                r.loc[mask]=r.loc[mask]+w*ema_ret.loc[mask]
                eq=(1+r).cumprod()
                md,pi,ti,ri=mdd(eq.values)
                rows.append(dict(cost_bps=bps,mode=mode,ema_overlay_weight=w,final_equity=float(eq.iloc[-1]),
                                 mtm_mdd=md,overlay_bars=int(mask.sum()),
                                 peak_time=eq.index[pi],trough_time=eq.index[ti],
                                 recovery_time=(eq.index[ri] if ri is not None else pd.NaT)))
        print("BASE",bps,float(base_eq.iloc[-1]),mdd(base_eq.values)[0],
              "strict_idle_bars",int(strict_idle.sum()),"coreoff_bars",int(coreoff.sum()))

    R=pd.DataFrame(rows);R.to_csv(O/"overlay_sweep.csv",index=False)
    for bps in [8,24]:
      for mode in ["STRICT_IDLE","CORE_OFF_OVERLAY"]:
        q=R[(R.cost_bps==bps)&(R["mode"].eq(mode))& (R.mtm_mdd<=.30+1e-12)].copy()
        if len(q):
            z=q.sort_values(["final_equity","mtm_mdd"],ascending=[False,True]).iloc[0]
            print("BEST_UNDER_30",bps,mode,z.to_dict())
            # fixed reference weights
            refs=R[(R.cost_bps==bps)&R["mode"].eq(mode)&R.ema_overlay_weight.isin([0,.25,.5,.75,1.0])]
            print("REFS",bps,mode);print(refs[["ema_overlay_weight","final_equity","mtm_mdd"]].to_string(index=False))
        else: print("NO_UNDER_30",bps,mode)

if __name__=="__main__":main()
