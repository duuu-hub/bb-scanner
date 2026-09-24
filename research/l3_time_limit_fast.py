from __future__ import annotations
import argparse, math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import numpy as np, pandas as pd
from precision_backtest import fetch_range, rows_to_df, MIN, FEE_PCT

HORIZONS=[60,120,240,480,720,1440,2880]

def args():
    p=argparse.ArgumentParser()
    p.add_argument("--signals-old",required=True); p.add_argument("--signals-new",required=True)
    p.add_argument("--regime",required=True); p.add_argument("--outdir",default="l3_time_limit_fast_results")
    p.add_argument("--workers",type=int,default=8); return p.parse_args()

def pf(x):
    x=pd.Series(x,dtype=float).dropna()
    p=float(x[x>0].sum()); n=float(-x[x<0].sum())
    return float("inf") if n<=0 and p>0 else (p/n if n>0 else np.nan)

def gp(entry,px,short=True):
    return ((1-px/entry)*100 if short else (px/entry-1)*100)-FEE_PCT

def hit_in_bar(bar,entry,short=True):
    if short:
        tp=entry*.90; sl=entry*1.04
        ht=float(bar.low)<=tp; hs=float(bar.high)>=sl
    else:
        tp=entry*1.10; sl=entry*.96
        ht=float(bar.high)>=tp; hs=float(bar.low)<=sl
    return ht,hs,tp,sl

def resolve_1m(path,entry,short=True):
    for bar in path.itertuples(index=False):
        ht,hs,tp,sl=hit_in_bar(bar,entry,short)
        if ht and hs:return "SL",sl,int(bar.ts)+MIN
        if hs:return "SL",sl,int(bar.ts)+MIN
        if ht:return "TP",tp,int(bar.ts)+MIN
    return None

def load(path,u,b):
    x=pd.read_csv(path)
    x=x[x.variant.isin(["STRICT","ADJACENT_RANK5_ONLY"])].copy()
    x=x.merge(b[["ts","regime_60_40"]].drop_duplicates("ts"),on="ts",how="left")
    x=x[x.regime_60_40=="BEAR"].copy().rename(columns={"ts":"signal_ts"})
    x["universe"]=u
    return x

def merge_windows(sig,h=2880):
    out={}
    for s,g in sig.groupby("symbol"):
        arr=sorted((int(t),(int(t)+(h+70)*MIN)) for t in g.signal_ts) # +70m covers first overlapping hour
        m=[]
        cs,ce=arr[0]
        for a,b in arr[1:]:
            if a<=ce+5*MIN: ce=max(ce,b)
            else:m.append((cs,ce));cs,ce=a,b
        m.append((cs,ce));out[s]=m
    return out

def fetch_symbol_1m(sym,ws):
    z=[]
    for a,b in ws:
        z.append(rows_to_df(fetch_range(sym,"1m",1,a,b),1)[["ts","open","high","low","close"]])
    return sym,pd.concat(z,ignore_index=True).drop_duplicates("ts").sort_values("ts")

def fetch_base(sig,workers):
    ws=merge_windows(sig)
    out={}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut={ex.submit(fetch_symbol_1m,s,w):s for s,w in ws.items()}
        for f in as_completed(fut):
            s=fut[f]
            try:
                sym,z=f.result();out[sym]=z;print("[1M]",sym,len(z))
            except Exception as e: print("[1MERR]",s,e);out[s]=pd.DataFrame()
    return out

def entry(row,base,delay):
    z=base.get(row.symbol)
    if z is None or z.empty:return None
    t=int(row.signal_ts)+(delay-1)*MIN
    q=z[z.ts==t]
    if q.empty:return None
    return float(q.iloc[-1].close),int(row.signal_ts)+delay*MIN

def finite(row,base,delay,h):
    ep=entry(row,base,delay)
    if not ep:return None
    e,et=ep; z=base[row.symbol]
    p=z[(z.ts>=et)&(z.ts<et+h*MIN)]
    if p.empty:return None
    r=resolve_1m(p,e,True)
    if r:o,px,xt=r
    else:o="TIME";px=float(p.iloc[-1].close);xt=int(p.iloc[-1].ts)+MIN
    fc=float(p.iloc[-1].close)
    return dict(outcome=o,net=gp(e,px),fixed_close=gp(e,fc),hold_h=(xt-et)/3600000,entry=e,entry_ts=et)

def floor_hour(ms): return (ms//3600000)*3600000

def fetch_hourly(sym,start,end):
    try:return rows_to_df(fetch_range(sym,"1H",60,start,end),60)[["ts","open","high","low","close"]]
    except Exception as e: print("[1HERR]",sym,e);return pd.DataFrame()

def exact_hour(sym,start,end,entry_price):
    try:
        z=rows_to_df(fetch_range(sym,"1m",1,start,end),1)[["ts","open","high","low","close"]]
        z=z[(z.ts>=start)&(z.ts<end)]
        return resolve_1m(z,entry_price,True),z
    except Exception as e:
        print("[AMBIG1MERR]",sym,e);return None,pd.DataFrame()

def main():
    a=args();out=Path(a.outdir);out.mkdir(parents=True,exist_ok=True)
    b=pd.read_csv(a.regime)
    sig=pd.concat([load(a.signals_old,"AUTO50_OVERLAP",b),load(a.signals_new,"NEW66_HOLDOUT",b)],ignore_index=True)
    sig=sig.drop_duplicates(["universe","variant","symbol","signal_ts"])
    print("BREADTH_RANGE",pd.to_datetime(b.ts.min(),unit="ms",utc=True),pd.to_datetime(b.ts.max(),unit="ms",utc=True))
    print(sig.groupby(["universe","variant"]).agg(n=("signal_ts","size"),symbols=("symbol","nunique")).to_string())
    study_end=int(b.ts.max())+15*MIN
    base=fetch_base(sig,a.workers)

    fin=[]; unresolved=[]
    for r in sig.itertuples(index=False):
      for d in (1,2,3):
        for h in HORIZONS:
            z=finite(r,base,d,h)
            if z:fin.append(dict(universe=r.universe,variant=r.variant,symbol=r.symbol,signal_ts=r.signal_ts,delay=d,horizon=h,**z))
        ep=entry(r,base,d)
        if not ep:continue
        e,et=ep
        p=base[r.symbol][(base[r.symbol].ts>=et)&(base[r.symbol].ts<et+2880*MIN)]
        rr=resolve_1m(p,e,True)
        if rr:
            o,px,xt=rr
            unresolved.append(dict(universe=r.universe,variant=r.variant,symbol=r.symbol,signal_ts=r.signal_ts,delay=d,
                entry=e,entry_ts=et,base_end=et+2880*MIN,done=True,outcome=o,px=px,xt=xt,last_close=px))
        else:
            lc=float(p.iloc[-1].close) if len(p) else np.nan
            unresolved.append(dict(universe=r.universe,variant=r.variant,symbol=r.symbol,signal_ts=r.signal_ts,delay=d,
                entry=e,entry_ts=et,base_end=et+2880*MIN,done=False,outcome=None,px=np.nan,xt=np.nan,last_close=lc))
    U=pd.DataFrame(unresolved)

    # One hourly history fetch per unresolved symbol, from earliest 48h boundary to study end.
    hourly={}
    todo=U[~U.done]
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        fut={}
        for sym,g in todo.groupby("symbol"):
            st=floor_hour(int(g.base_end.min()))
            fut[ex.submit(fetch_hourly,sym,st,study_end)]=sym
        for f in as_completed(fut):
            sym=fut[f]
            try:hourly[sym]=f.result();print("[1H]",sym,len(hourly[sym]))
            except Exception as e:print("[1HERR2]",sym,e);hourly[sym]=pd.DataFrame()

    results=[]
    for i,r in U.iterrows():
        if bool(r.done):
            results.append(dict(**r.to_dict(),censored=False,net=gp(r.entry,r.px),mtm=gp(r.entry,r.px),
                                hold_h=(r.xt-r.entry_ts)/3600000))
            continue
        hz=hourly.get(r.symbol,pd.DataFrame())
        cursor=int(r.base_end)
        hz=hz[(hz.ts+3600000>cursor)&(hz.ts<study_end)]
        found=None; last=float(r.last_close) if math.isfinite(float(r.last_close)) else np.nan
        for bar in hz.itertuples(index=False):
            ht,hs,tp,sl=hit_in_bar(bar,float(r.entry),True)
            if not (ht or hs):
                last=float(bar.close);continue
            # First overlapping hour or both barriers in same hour: resolve with 1m only inside this hour,
            # filtering away any pre-base portion.
            if int(bar.ts)<cursor or (ht and hs):
                st=max(cursor,int(bar.ts)); en=min(study_end,int(bar.ts)+3600000)
                exact,z=exact_hour(r.symbol,st,en,float(r.entry))
                if len(z):last=float(z.iloc[-1].close)
                if exact:
                    found=exact;break
                else:
                    continue
            if hs: found=("SL",sl,int(bar.ts)+3600000);break
            if ht: found=("TP",tp,int(bar.ts)+3600000);break
        if found:
            o,px,xt=found
            dct=r.to_dict()
            dct.update({"done":True,"outcome":o,"px":px,"xt":xt,"last_close":px,"censored":False,
                        "net":gp(r.entry,px),"mtm":gp(r.entry,px),"hold_h":(xt-r.entry_ts)/3600000})
            results.append(dct)
        else:
            dct=r.to_dict()
            dct.update({"censored":True,"net":np.nan,
                        "mtm":gp(r.entry,last) if math.isfinite(last) else np.nan,"hold_h":np.nan})
            results.append(dct)
    UR=pd.DataFrame(results)

    F=pd.DataFrame(fin)
    fs=[]
    for keys,g in F.groupby(["universe","variant","delay","horizon"]):
        u,v,d,h=keys
        for mode,col in [("TP10_SL4_TIME","net"),("FIXED_CLOSE_NO_TPSL","fixed_close")]:
            x=g[col].dropna()
            fs.append(dict(universe=u,variant=v,delay=d,horizon=h,mode=mode,n=len(x),symbols=g.symbol.nunique(),
                avg=x.mean(),sum=x.sum(),pf=pf(x),win=(x>0).mean()*100,pf_cost025=pf(x-.25),pf_cost050=pf(x-.50),
                tp=(g.outcome=="TP").mean()*100 if mode=="TP10_SL4_TIME" else np.nan,
                sl=(g.outcome=="SL").mean()*100 if mode=="TP10_SL4_TIME" else np.nan,
                time=(g.outcome=="TIME").mean()*100 if mode=="TP10_SL4_TIME" else np.nan))
    FS=pd.DataFrame(fs)

    us=[]
    for keys,g in UR.groupby(["universe","variant","delay"]):
        u,v,d=keys;cl=g[~g.censored];x=cl.net.dropna();mtm=g.mtm.dropna()
        us.append(dict(universe=u,variant=v,delay=d,total=len(g),symbols=g.symbol.nunique(),closed=len(cl),
            censored=int(g.censored.sum()),tp=int((cl.outcome=="TP").sum()),sl=int((cl.outcome=="SL").sum()),
            pf=pf(x),avg=x.mean() if len(x) else np.nan,pf_cost025=pf(x-.25),pf_cost050=pf(x-.50),
            median_h=cl.hold_h.median() if len(cl) else np.nan,p90_h=cl.hold_h.quantile(.9) if len(cl) else np.nan,
            mtm_pf=pf(mtm),mtm_avg=mtm.mean() if len(mtm) else np.nan))
    US=pd.DataFrame(us)

    F.to_csv(out/"finite_trades.csv.gz",index=False,compression="gzip");FS.to_csv(out/"finite_summary.csv",index=False)
    UR.to_csv(out/"unlimited_trades.csv.gz",index=False,compression="gzip");US.to_csv(out/"unlimited_summary.csv",index=False)
    print("\n=== FINITE +1m ===")
    print(FS[FS.delay==1].to_string(index=False))
    print("\n=== UNLIMITED ===")
    print(US.to_string(index=False))
    print("[DONE]")
if __name__=="__main__":main()
