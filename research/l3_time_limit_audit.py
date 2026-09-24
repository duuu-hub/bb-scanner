from __future__ import annotations
import argparse, math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import numpy as np, pandas as pd

from precision_backtest import fetch_range, rows_to_df, MIN, FEE_PCT

HORIZONS_MIN=[60,120,240,480,720,1440,2880]

def parse_args():
    p=argparse.ArgumentParser()
    p.add_argument("--signals-old",required=True)
    p.add_argument("--signals-new",required=True)
    p.add_argument("--regime",required=True)
    p.add_argument("--outdir",default="l3_time_limit_results")
    p.add_argument("--workers",type=int,default=8)
    return p.parse_args()

def pf(vals):
    x=pd.Series(vals,dtype=float).dropna()
    pos=float(x[x>0].sum()); neg=float(-x[x<0].sum())
    if neg<=0:return float("inf") if pos>0 else float("nan")
    return pos/neg

def resolve_hits(path,entry,direction,tp_pct=10.0,sl_pct=4.0):
    if path.empty:return None
    if direction=="SHORT":
        tp=entry*(1-tp_pct/100); sl=entry*(1+sl_pct/100)
    else:
        tp=entry*(1+tp_pct/100); sl=entry*(1-sl_pct/100)
    for bar in path.itertuples(index=False):
        if direction=="SHORT":
            hit_tp=float(bar.low)<=tp; hit_sl=float(bar.high)>=sl
        else:
            hit_tp=float(bar.high)>=tp; hit_sl=float(bar.low)<=sl
        if hit_tp and hit_sl:
            return ("SL",sl,int(bar.ts)+MIN)
        if hit_sl:return ("SL",sl,int(bar.ts)+MIN)
        if hit_tp:return ("TP",tp,int(bar.ts)+MIN)
    return None

def gross(entry,exit_price,direction):
    return ((1-exit_price/entry)*100) if direction=="SHORT" else ((exit_price/entry-1)*100)

def merged_windows(signals,horizon_min=2880):
    out={}
    for sym,g in signals.groupby("symbol"):
        arr=sorted((int(t), int(t)+(horizon_min+10)*MIN) for t in g.signal_ts)
        merged=[]
        if not arr: continue
        cs,ce=arr[0]
        for s,e in arr[1:]:
            if s<=ce+5*MIN: ce=max(ce,e)
            else: merged.append((cs,ce)); cs,ce=s,e
        merged.append((cs,ce)); out[sym]=merged
    return out

def fetch_sym(sym,windows):
    frames=[]
    for s,e in windows:
        r=fetch_range(sym,"1m",1,s,e)
        frames.append(rows_to_df(r,1)[["ts","open","high","low","close"]])
    if not frames:return sym,pd.DataFrame()
    x=pd.concat(frames,ignore_index=True).drop_duplicates("ts").sort_values("ts")
    return sym,x

def fetch_base(signals,workers):
    windows=merged_windows(signals,2880)
    out={}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fut={pool.submit(fetch_sym,s,w):s for s,w in windows.items()}
        for f in as_completed(fut):
            s=fut[f]
            try:
                sym,x=f.result(); out[sym]=x
                print(f"[BASE1M] {sym} rows={len(x)}")
            except Exception as e:
                print(f"[BASE1M-ERR] {s}: {e}"); out[s]=pd.DataFrame()
    return out

def entry_from_base(row,base,delay):
    md=base.get(row.symbol)
    if md is None or md.empty:return None
    candle_ts=int(row.signal_ts)+(delay-1)*MIN
    q=md[md.ts==candle_ts]
    if q.empty:return None
    entry=float(q.iloc[-1].close)
    entry_ts=int(row.signal_ts)+delay*MIN
    return entry,entry_ts

def finite_eval(row,base,delay,direction,hmin):
    ep=entry_from_base(row,base,delay)
    if ep is None:return None
    entry,entry_ts=ep
    md=base[row.symbol]
    path=md[(md.ts>=entry_ts)&(md.ts<entry_ts+hmin*MIN)]
    if path.empty:return None
    hit=resolve_hits(path,entry,direction)
    if hit:
        outcome,exit_price,exit_ts=hit
    else:
        outcome="TIME"; exit_price=float(path.iloc[-1].close); exit_ts=int(path.iloc[-1].ts)+MIN
    g=gross(entry,exit_price,direction); net=g-FEE_PCT
    # pure fixed-close diagnostic, ignores both TP and SL
    fc_price=float(path.iloc[-1].close)
    fc_net=gross(entry,fc_price,direction)-FEE_PCT
    return dict(outcome=outcome,entry_price=entry,entry_ts=entry_ts,exit_price=exit_price,exit_ts=exit_ts,
                gross_pct=g,net_pct=net,fixed_close_net_pct=fc_net,
                hold_hours=(exit_ts-entry_ts)/3600_000)

def fetch_extension_until_hit(row,entry,entry_ts,direction,start_ts,study_end):
    # Continue after the 48h base window in 7-day chunks until TP/SL or data end.
    cursor=start_ts
    last_close=None; last_ts=None
    while cursor<study_end:
        end=min(study_end,cursor+7*24*60*MIN)
        try:
            rr=fetch_range(row.symbol,"1m",1,cursor,end)
            md=rows_to_df(rr,1)[["ts","open","high","low","close"]]
        except Exception as e:
            print(f"[EXT-ERR] {row.symbol} {cursor}..{end}: {e}")
            return None,last_close,last_ts
        md=md[(md.ts>=cursor)&(md.ts<end)]
        if not md.empty:
            hit=resolve_hits(md,entry,direction)
            last_close=float(md.iloc[-1].close); last_ts=int(md.iloc[-1].ts)+MIN
            if hit:return hit,last_close,last_ts
        cursor=end
    return None,last_close,last_ts

def unlimited_eval(row,base,delay,direction,study_end):
    ep=entry_from_base(row,base,delay)
    if ep is None:return None
    entry,entry_ts=ep
    md=base[row.symbol]
    base_end=min(study_end,entry_ts+2880*MIN)
    path=md[(md.ts>=entry_ts)&(md.ts<base_end)]
    if path.empty:return None
    hit=resolve_hits(path,entry,direction)
    last_close=float(path.iloc[-1].close); last_ts=int(path.iloc[-1].ts)+MIN
    if hit:
        outcome,px,xt=hit
        return dict(outcome=outcome,entry_price=entry,entry_ts=entry_ts,exit_price=px,exit_ts=xt,
                    net_pct=gross(entry,px,direction)-FEE_PCT,hold_hours=(xt-entry_ts)/3600_000,
                    censored=False,mtm_net_pct=gross(entry,px,direction)-FEE_PCT)
    if base_end<study_end:
        hit,last2,ts2=fetch_extension_until_hit(row,entry,entry_ts,direction,base_end,study_end)
        if last2 is not None: last_close,last_ts=last2,ts2
        if hit:
            outcome,px,xt=hit
            return dict(outcome=outcome,entry_price=entry,entry_ts=entry_ts,exit_price=px,exit_ts=xt,
                        net_pct=gross(entry,px,direction)-FEE_PCT,hold_hours=(xt-entry_ts)/3600_000,
                        censored=False,mtm_net_pct=gross(entry,px,direction)-FEE_PCT)
    # no forced exit: censored. mtm is reported separately, never mixed into realized PF.
    mtm=(gross(entry,last_close,direction)-FEE_PCT) if last_close is not None else np.nan
    return dict(outcome="CENSORED",entry_price=entry,entry_ts=entry_ts,exit_price=np.nan,exit_ts=np.nan,
                net_pct=np.nan,hold_hours=np.nan,censored=True,mtm_net_pct=mtm)

def summarize_finite(df):
    rows=[]
    for keys,g in df.groupby(["universe","variant","direction","delay_min","horizon_min"]):
        u,v,dire,delay,h=keys
        for mode,col in [("TP10_SL4_TIME","net_pct"),("FIXED_CLOSE_NO_TPSL","fixed_close_net_pct")]:
            x=pd.to_numeric(g[col],errors="coerce").dropna()
            rows.append(dict(universe=u,variant=v,direction=dire,delay_min=delay,horizon_min=h,mode=mode,
                n=len(x),symbols=g.loc[x.index,"symbol"].nunique(),avg=x.mean(),sum=x.sum(),pf=pf(x),
                win_pct=(x>0).mean()*100,pf_cost025=pf(x-.25),pf_cost050=pf(x-.50),
                tp_pct=(g.outcome=="TP").mean()*100 if mode=="TP10_SL4_TIME" else np.nan,
                sl_pct=(g.outcome=="SL").mean()*100 if mode=="TP10_SL4_TIME" else np.nan,
                time_pct=(g.outcome=="TIME").mean()*100 if mode=="TP10_SL4_TIME" else np.nan))
    return pd.DataFrame(rows)

def summarize_unlim(df):
    rows=[]
    for keys,g in df.groupby(["universe","variant","direction","delay_min"]):
        u,v,dire,delay=keys
        closed=g[~g.censored].copy(); x=pd.to_numeric(closed.net_pct,errors="coerce").dropna()
        mtm=pd.to_numeric(g.mtm_net_pct,errors="coerce").dropna()
        rows.append(dict(universe=u,variant=v,direction=dire,delay_min=delay,
            total_n=len(g),symbols=g.symbol.nunique(),closed_n=len(closed),censored_n=int(g.censored.sum()),
            censored_pct=float(g.censored.mean()*100),tp_n=int((closed.outcome=="TP").sum()),sl_n=int((closed.outcome=="SL").sum()),
            realized_avg=x.mean() if len(x) else np.nan,realized_pf=pf(x),realized_pf_cost025=pf(x-.25) if len(x) else np.nan,
            realized_pf_cost050=pf(x-.50) if len(x) else np.nan,
            median_hours_to_barrier=closed.hold_hours.median() if len(closed) else np.nan,
            p90_hours_to_barrier=closed.hold_hours.quantile(.9) if len(closed) else np.nan,
            mtm_including_censored_avg=mtm.mean() if len(mtm) else np.nan,mtm_including_censored_pf=pf(mtm)))
    return pd.DataFrame(rows)

def load_signals(path,universe,breadth):
    x=pd.read_csv(path)
    x=x[x.variant.isin(["STRICT","ADJACENT_RANK5_ONLY"])].copy()
    x=x.merge(breadth[["ts","regime_60_40"]].drop_duplicates("ts"),left_on="ts",right_on="ts",how="left")
    x=x[x.regime_60_40=="BEAR"].copy()
    x=x.rename(columns={"ts":"signal_ts"})
    x["universe"]=universe
    return x

def main():
    a=parse_args(); out=Path(a.outdir); out.mkdir(parents=True,exist_ok=True)
    b=pd.read_csv(a.regime)
    print("BREADTH_RANGE",int(b.ts.min()),int(b.ts.max()),"rows",len(b))
    old=load_signals(a.signals_old,"AUTO50_OVERLAP",b)
    new=load_signals(a.signals_new,"NEW66_HOLDOUT",b)
    sig=pd.concat([old,new],ignore_index=True).drop_duplicates(["universe","variant","symbol","signal_ts"])
    print("BEAR_SIGNAL_COUNTS")
    print(sig.groupby(["universe","variant"]).agg(n=("signal_ts","size"),symbols=("symbol","nunique")).to_string())
    study_end=int(b.ts.max())+15*MIN
    base=fetch_base(sig,a.workers)

    finite=[]
    unlim=[]
    for r in sig.itertuples(index=False):
        for direction in ("SHORT","LONG"):
            for delay in (1,2,3):
                for h in HORIZONS_MIN:
                    z=finite_eval(r,base,delay,direction,h)
                    if z is not None:
                        finite.append(dict(universe=r.universe,variant=r.variant,symbol=r.symbol,signal_ts=r.signal_ts,
                                           direction=direction,delay_min=delay,horizon_min=h,**z))
                z=unlimited_eval(r,base,delay,direction,study_end)
                if z is not None:
                    unlim.append(dict(universe=r.universe,variant=r.variant,symbol=r.symbol,signal_ts=r.signal_ts,
                                      direction=direction,delay_min=delay,**z))
    F=pd.DataFrame(finite); U=pd.DataFrame(unlim)
    FS=summarize_finite(F); US=summarize_unlim(U)
    F.to_csv(out/"finite_trades.csv.gz",index=False,compression="gzip")
    U.to_csv(out/"unlimited_trades.csv.gz",index=False,compression="gzip")
    FS.to_csv(out/"finite_summary.csv",index=False)
    US.to_csv(out/"unlimited_summary.csv",index=False)
    sig.to_csv(out/"bear_signals.csv",index=False)

    print("\n=== FINITE SHORT +1m ===")
    q=FS[(FS.direction=="SHORT")&(FS.delay_min==1)]
    print(q.to_string(index=False))
    print("\n=== FINITE SHORT ALL DELAYS COMPACT ===")
    q=FS[(FS.direction=="SHORT") & (FS["mode"]=="TP10_SL4_TIME")][["universe","variant","delay_min","horizon_min","n","avg","pf","pf_cost050","win_pct","tp_pct","sl_pct","time_pct"]]
    print(q.to_string(index=False))
    print("\n=== NO TP/SL FIXED-CLOSE SHORT ===")
    q=FS[(FS.direction=="SHORT") & (FS["mode"]=="FIXED_CLOSE_NO_TPSL")][["universe","variant","delay_min","horizon_min","n","avg","pf","win_pct"]]
    print(q.to_string(index=False))
    print("\n=== UNLIMITED BARRIER-ONLY ===")
    print(US.to_string(index=False))
    print("[DONE]")

if __name__=="__main__": main()
