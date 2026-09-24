from __future__ import annotations
import argparse, math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import numpy as np, pandas as pd
from precision_backtest import fetch_range, rows_to_df, MIN, FEE_PCT

TPS=[4.0,6.0,8.0,10.0,12.0]
SLS=[2.0,3.0,4.0,5.0,6.0]
HORIZONS=[480,720,1440]  # 8h,12h,24h

def parse_args():
    p=argparse.ArgumentParser()
    p.add_argument("--signals-old",required=True)
    p.add_argument("--signals-new",required=True)
    p.add_argument("--regime",required=True)
    p.add_argument("--outdir",default="l3_tpsl_sweep_results")
    p.add_argument("--workers",type=int,default=8)
    return p.parse_args()

def pf(vals):
    x=pd.Series(vals,dtype=float).dropna()
    pos=float(x[x>0].sum()); neg=float(-x[x<0].sum())
    if neg<=0: return float("inf") if pos>0 else float("nan")
    return pos/neg

def net_short(entry,exit_price):
    return (1.0-exit_price/entry)*100.0 - FEE_PCT

def load_signals(path,universe,breadth):
    x=pd.read_csv(path)
    x=x[x.variant.isin(["STRICT","ADJACENT_RANK5_ONLY"])].copy()
    x=x.merge(breadth[["ts","regime_60_40"]].drop_duplicates("ts"),on="ts",how="left")
    x=x[x.regime_60_40=="BEAR"].copy().rename(columns={"ts":"signal_ts"})
    x["universe"]=universe
    return x

def merged_windows(signals,horizon_min=1440):
    out={}
    for sym,g in signals.groupby("symbol"):
        arr=sorted((int(t),int(t)+(horizon_min+10)*MIN) for t in g.signal_ts)
        if not arr: continue
        merged=[]; cs,ce=arr[0]
        for s,e in arr[1:]:
            if s<=ce+5*MIN: ce=max(ce,e)
            else: merged.append((cs,ce)); cs,ce=s,e
        merged.append((cs,ce)); out[sym]=merged
    return out

def fetch_symbol_1m(sym,windows):
    parts=[]
    for s,e in windows:
        z=rows_to_df(fetch_range(sym,"1m",1,s,e),1)[["ts","open","high","low","close"]]
        parts.append(z)
    if not parts:return sym,pd.DataFrame()
    return sym,pd.concat(parts,ignore_index=True).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)

def fetch_base(signals,workers):
    out={}; ws=merged_windows(signals,1440)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut={ex.submit(fetch_symbol_1m,s,w):s for s,w in ws.items()}
        for f in as_completed(fut):
            sym=fut[f]
            try:
                s,z=f.result(); out[s]=z; print(f"[1M] {s} rows={len(z)}")
            except Exception as e:
                print(f"[1M-ERR] {sym}: {e}"); out[sym]=pd.DataFrame()
    return out

def get_entry(row,base,delay):
    md=base.get(row.symbol)
    if md is None or md.empty:return None
    candle_ts=int(row.signal_ts)+(delay-1)*MIN
    q=md[md.ts==candle_ts]
    if q.empty:return None
    return float(q.iloc[-1].close), int(row.signal_ts)+delay*MIN

def scan_1m(path,entry,tp_pct,sl_pct):
    tp=entry*(1-tp_pct/100.0); sl=entry*(1+sl_pct/100.0)
    for bar in path.itertuples(index=False):
        ht=float(bar.low)<=tp; hs=float(bar.high)>=sl
        if ht and hs:return "SL",sl,int(bar.ts)+MIN
        if hs:return "SL",sl,int(bar.ts)+MIN
        if ht:return "TP",tp,int(bar.ts)+MIN
    return None

def finite_trade(row,base,delay,tp,sl,hmin):
    ep=get_entry(row,base,delay)
    if ep is None:return None
    entry,entry_ts=ep
    md=base[row.symbol]
    path=md[(md.ts>=entry_ts)&(md.ts<entry_ts+hmin*MIN)]
    if path.empty:return None
    hit=scan_1m(path,entry,tp,sl)
    if hit:
        outcome,px,xt=hit
    else:
        outcome="TIME"; px=float(path.iloc[-1].close); xt=int(path.iloc[-1].ts)+MIN
    return {
        "universe":row.universe,"variant":row.variant,"symbol":row.symbol,"signal_ts":int(row.signal_ts),
        "delay_min":delay,"horizon_min":hmin,"tp_pct":tp,"sl_pct":sl,"outcome":outcome,
        "entry_price":entry,"entry_ts":entry_ts,"exit_price":px,"exit_ts":xt,
        "net_pct":net_short(entry,px),"hold_h":(xt-entry_ts)/3600000.0
    }

def fetch_hourly(sym,start,end):
    try:
        z=rows_to_df(fetch_range(sym,"1H",60,start,end),60)[["ts","open","high","low","close"]]
        return sym,z
    except Exception as e:
        print(f"[1H-ERR] {sym}: {e}"); return sym,pd.DataFrame()

def hour_hit(bar,entry,tp,sl):
    tp_px=entry*(1-tp/100.0); sl_px=entry*(1+sl/100.0)
    return float(bar.low)<=tp_px,float(bar.high)>=sl_px,tp_px,sl_px

def exact_ambiguous(sym,start,end,entry,tp,sl,cache):
    key=(sym,int(start),int(end))
    if key not in cache:
        try:
            z=rows_to_df(fetch_range(sym,"1m",1,start,end),1)[["ts","open","high","low","close"]]
            cache[key]=z[(z.ts>=start)&(z.ts<end)].copy()
        except Exception as e:
            print(f"[AMBIG-ERR] {sym} {start}: {e}"); cache[key]=pd.DataFrame()
    z=cache[key]
    if z.empty:return None
    return scan_1m(z,entry,tp,sl)

def unlimited_trade(row,base,hourly,delay,tp,sl,study_end,ambig_cache):
    ep=get_entry(row,base,delay)
    if ep is None:return None
    entry,entry_ts=ep
    md=base[row.symbol]
    boundary=min(study_end,entry_ts+1440*MIN)
    first=md[(md.ts>=entry_ts)&(md.ts<boundary)]
    if first.empty:return None
    hit=scan_1m(first,entry,tp,sl)
    if hit:
        outcome,px,xt=hit
        return dict(universe=row.universe,variant=row.variant,symbol=row.symbol,signal_ts=int(row.signal_ts),
                    delay_min=delay,tp_pct=tp,sl_pct=sl,outcome=outcome,censored=False,
                    entry_price=entry,entry_ts=entry_ts,exit_price=px,exit_ts=xt,
                    net_pct=net_short(entry,px),hold_h=(xt-entry_ts)/3600000.0)
    hz=hourly.get(row.symbol,pd.DataFrame())
    hz=hz[(hz.ts>=boundary)&(hz.ts<study_end)]
    for bar in hz.itertuples(index=False):
        ht,hs,tp_px,sl_px=hour_hit(bar,entry,tp,sl)
        if not (ht or hs):continue
        if ht and hs:
            ex=exact_ambiguous(row.symbol,int(bar.ts),min(study_end,int(bar.ts)+3600000),entry,tp,sl,ambig_cache)
            if ex is None:continue
            outcome,px,xt=ex
        elif hs:
            outcome,px,xt="SL",sl_px,int(bar.ts)+3600000
        else:
            outcome,px,xt="TP",tp_px,int(bar.ts)+3600000
        return dict(universe=row.universe,variant=row.variant,symbol=row.symbol,signal_ts=int(row.signal_ts),
                    delay_min=delay,tp_pct=tp,sl_pct=sl,outcome=outcome,censored=False,
                    entry_price=entry,entry_ts=entry_ts,exit_price=px,exit_ts=xt,
                    net_pct=net_short(entry,px),hold_h=(xt-entry_ts)/3600000.0)
    return dict(universe=row.universe,variant=row.variant,symbol=row.symbol,signal_ts=int(row.signal_ts),
                delay_min=delay,tp_pct=tp,sl_pct=sl,outcome="CENSORED",censored=True,
                entry_price=entry,entry_ts=entry_ts,exit_price=np.nan,exit_ts=np.nan,
                net_pct=np.nan,hold_h=np.nan)

def summarize_finite(df):
    rows=[]
    for keys,g in df.groupby(["universe","variant","delay_min","horizon_min","tp_pct","sl_pct"]):
        u,v,d,h,tp,sl=keys; x=g.net_pct.dropna()
        rows.append(dict(universe=u,variant=v,delay_min=d,horizon_min=h,tp_pct=tp,sl_pct=sl,
            rr=tp/sl,n=len(x),symbols=g.symbol.nunique(),avg=x.mean(),sum=x.sum(),pf=pf(x),
            pf_cost025=pf(x-.25),pf_cost050=pf(x-.50),win_pct=(x>0).mean()*100,
            tp_rate=(g.outcome=="TP").mean()*100,sl_rate=(g.outcome=="SL").mean()*100,time_rate=(g.outcome=="TIME").mean()*100))
    return pd.DataFrame(rows)

def summarize_unlim(df):
    rows=[]
    for keys,g in df.groupby(["universe","variant","delay_min","tp_pct","sl_pct"]):
        u,v,d,tp,sl=keys; cl=g[~g.censored]; x=cl.net_pct.dropna()
        rows.append(dict(universe=u,variant=v,delay_min=d,horizon_min="UNLIMITED",tp_pct=tp,sl_pct=sl,rr=tp/sl,
            total_n=len(g),symbols=g.symbol.nunique(),closed_n=len(cl),censored_n=int(g.censored.sum()),
            avg=x.mean() if len(x) else np.nan,pf=pf(x),pf_cost025=pf(x-.25),pf_cost050=pf(x-.50),
            win_pct=(x>0).mean()*100 if len(x) else np.nan,tp_rate=(cl.outcome=="TP").mean()*100 if len(cl) else np.nan,
            sl_rate=(cl.outcome=="SL").mean()*100 if len(cl) else np.nan,
            median_hold_h=cl.hold_h.median() if len(cl) else np.nan,p90_hold_h=cl.hold_h.quantile(.9) if len(cl) else np.nan))
    return pd.DataFrame(rows)

def stability(summary):
    rows=[]
    for keys,g in summary.groupby(["universe","variant","delay_min","horizon_min"]):
        u,v,d,h=keys
        q=g.copy()
        target=q[(q.tp_pct==10)&(q.sl_pct==4)]
        rows.append(dict(universe=u,variant=v,delay_min=d,horizon_min=h,combos=len(q),
            combos_pf_gt1=int((q.pf>1).sum()),combos_pf_gt125=int((q.pf>1.25).sum()),
            combos_cost050_pf_gt1=int((q.pf_cost050>1).sum()),
            median_pf=float(q.pf.replace([np.inf,-np.inf],np.nan).median()),
            min_pf=float(q.pf.replace([np.inf,-np.inf],np.nan).min()),
            max_pf=float(q.pf.replace([np.inf,-np.inf],np.nan).max()),
            tp10_sl4_pf=float(target.pf.iloc[0]) if len(target) else np.nan,
            tp10_sl4_cost050_pf=float(target.pf_cost050.iloc[0]) if len(target) else np.nan))
    return pd.DataFrame(rows)

def main():
    a=parse_args(); out=Path(a.outdir); out.mkdir(parents=True,exist_ok=True)
    b=pd.read_csv(a.regime)
    old=load_signals(a.signals_old,"AUTO50_OVERLAP",b)
    new=load_signals(a.signals_new,"NEW66_HOLDOUT",b)
    sig=pd.concat([old,new],ignore_index=True).drop_duplicates(["universe","variant","symbol","signal_ts"])
    print("SIGNALS\n"+sig.groupby(["universe","variant"]).agg(n=("signal_ts","size"),symbols=("symbol","nunique")).to_string())
    study_end=int(b.ts.max())+15*MIN
    base=fetch_base(sig,a.workers)

    # Hourly extension for unlimited mode, once per symbol.
    hourly={}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        fut={}
        for sym,g in sig.groupby("symbol"):
            st=int(g.signal_ts.min())+1440*MIN
            fut[ex.submit(fetch_hourly,sym,st,study_end)]=sym
        for f in as_completed(fut):
            sym=fut[f]
            try:
                s,z=f.result(); hourly[s]=z; print(f"[1H] {s} rows={len(z)}")
            except Exception as e:
                print(f"[1H-ERR2] {sym}: {e}"); hourly[sym]=pd.DataFrame()

    finite=[]; unlim=[]; ambig_cache={}
    for r in sig.itertuples(index=False):
        for d in (1,2,3):
            for tp in TPS:
                for sl in SLS:
                    for h in HORIZONS:
                        z=finite_trade(r,base,d,tp,sl,h)
                        if z is not None: finite.append(z)
                    z=unlimited_trade(r,base,hourly,d,tp,sl,study_end,ambig_cache)
                    if z is not None: unlim.append(z)

    F=pd.DataFrame(finite); U=pd.DataFrame(unlim)
    FS=summarize_finite(F); US=summarize_unlim(U)
    ST=pd.concat([stability(FS),stability(US.rename(columns={"total_n":"n"}))],ignore_index=True)
    F.to_csv(out/"finite_trades.csv.gz",index=False,compression="gzip")
    U.to_csv(out/"unlimited_trades.csv.gz",index=False,compression="gzip")
    FS.to_csv(out/"finite_summary.csv",index=False)
    US.to_csv(out/"unlimited_summary.csv",index=False)
    ST.to_csv(out/"stability_summary.csv",index=False)

    print("\n=== STABILITY ==="); print(ST.to_string(index=False))
    print("\n=== RANK5 NEW66 +1m FINITE GRID ===")
    q=FS[(FS.universe=="NEW66_HOLDOUT")&(FS.variant=="ADJACENT_RANK5_ONLY")&(FS.delay_min==1)]
    print(q[["horizon_min","tp_pct","sl_pct","rr","n","avg","pf","pf_cost050","win_pct","tp_rate","sl_rate","time_rate"]].to_string(index=False))
    print("\n=== RANK5 NEW66 +1m UNLIMITED GRID ===")
    q=US[(US.universe=="NEW66_HOLDOUT")&(US.variant=="ADJACENT_RANK5_ONLY")&(US.delay_min==1)]
    print(q[["tp_pct","sl_pct","rr","total_n","closed_n","censored_n","avg","pf","pf_cost050","win_pct","median_hold_h","p90_hold_h"]].to_string(index=False))
    print("[DONE]")

if __name__=="__main__":main()
