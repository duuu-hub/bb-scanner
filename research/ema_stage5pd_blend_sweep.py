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

def clean_curve(df,col="equity"):
    d=df.copy()
    d["time"]=pd.to_datetime(d["time"],utc=True)
    d=d[(d.time>=START)&(d.time<=END)].sort_values("time").drop_duplicates("time",keep="last")
    s=d.set_index("time")[col].astype(float)
    idx=pd.date_range(START,END,freq="15min",inclusive="both")
    s=pd.concat([pd.Series([1.0],index=[START]),s]).sort_index()
    s=s[~s.index.duplicated(keep="last")].reindex(idx).ffill()
    if s.isna().any(): raise AssertionError("curve has leading NaN")
    return s

def mdd(s):
    x=s.to_numpy(float);p=np.maximum.accumulate(x)
    dd=(p-x)/p
    i=int(np.argmax(dd));peak=float(p[i])
    pi=int(np.where(x[:i+1]>=peak-1e-12)[0][-1])
    rec=np.where(x[i+1:]>=peak-1e-12)[0]
    ri=(i+1+int(rec[0])) if len(rec) else None
    return float(dd[i]),s.index[pi],s.index[i],(s.index[ri] if ri is not None else pd.NaT)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--combo-script",required=True)
    ap.add_argument("--funding",required=True)
    ap.add_argument("--stage5pd8",required=True)
    ap.add_argument("--stage5pd24",required=True)
    ap.add_argument("--out",required=True)
    ap.add_argument("--cache",default="spotcache")
    a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
    mod=load_mod(a.combo_script)

    h=mod.dl_spot("BTCUSDT","1h",pd.Timestamp("2019-01-01",tz="UTC"),END,a.cache)
    b15=mod.dl_spot("BTCUSDT","15m",mod.EMA_FULL_START,END,a.cache)
    orig,canon_full,emafull,emaC,er,ea,tr=mod.build_ema(h,a.funding,b15)
    assert abs(orig-17.709626335603225)<2e-8
    assert abs(canon_full-orig)<2e-8

    ema=clean_curve(emaC.reset_index())
    em,epk,etr,erc=mdd(ema)
    assert abs(float(ema.iloc[-1])-3.8790089302947797)<2e-8
    assert abs(em-0.324615632694219)<2e-8

    meta=[];rows=[]
    for bps,fp in [(8,a.stage5pd8),(24,a.stage5pd24)]:
        st=clean_curve(pd.read_csv(fp))
        sm,spk,strough,srec=mdd(st)
        meta.append(dict(cost_bps=bps,ema_final=float(ema.iloc[-1]),ema_mdd=em,
                         stage5pd_final=float(st.iloc[-1]),stage5pd_mdd=sm))
        for wi in range(1001):
            w=wi/1000.0
            port=w*ema+(1-w)*st
            md,pk,trough,rec=mdd(port)
            rows.append(dict(cost_bps=bps,ema_weight=w,stage5pd_weight=1-w,
                             final_equity=float(port.iloc[-1]),mtm_mdd=md,
                             peak_time=pk,trough_time=trough,recovery_time=rec))
        pd.DataFrame({"time":ema.index,"ema_equity":ema.values,"stage5pd_equity":st.values}).to_csv(O/f"aligned_sleeves_{bps}bp.csv",index=False)

    R=pd.DataFrame(rows)
    R.to_csv(O/"blend_sweep.csv",index=False)
    M=pd.DataFrame(meta);M.to_csv(O/"standalone_common.csv",index=False)
    for bps in [8,24]:
        q=R[(R.cost_bps==bps)&(R.mtm_mdd<=.30+1e-12)].copy()
        if len(q):
            best=q.sort_values(["final_equity","mtm_mdd"],ascending=[False,True]).iloc[0]
            print("BEST_UNDER_30",bps,best.to_dict())
            near=R[(R.cost_bps==bps)&(R.ema_weight.between(max(0,best.ema_weight-.03),min(1,best.ema_weight+.03)))]
            print("NEAR",bps)
            print(near.iloc[::5][["ema_weight","stage5pd_weight","final_equity","mtm_mdd"]].to_string(index=False))
        else: print("NO_POINT_UNDER_30",bps)
    print("STANDALONE")
    print(M.to_string(index=False))

if __name__=="__main__":main()
