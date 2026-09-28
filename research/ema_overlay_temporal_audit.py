#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import numpy as np,pandas as pd

def trade_stats(g):
    if len(g)==0:return dict(n=0,wr=np.nan,pf=np.nan,mean=np.nan,sum=np.nan)
    w=g.loc[g.trade_ret>0,"trade_ret"].sum()
    l=-g.loc[g.trade_ret<0,"trade_ret"].sum()
    return dict(n=len(g),wr=float((g.trade_ret>0).mean()),pf=float(w/l) if l>0 else np.inf,
                mean=float(g.trade_ret.mean()),sum=float(g.trade_ret.sum()))

def period_ret(s,start,end):
    q=s[(s.index>=pd.Timestamp(start,tz="UTC"))&(s.index<pd.Timestamp(end,tz="UTC"))]
    if len(q)<2:return np.nan
    return float(q.iloc[-1]/q.iloc[0]-1)

def maxdd(s):
    x=s.to_numpy(float);p=np.maximum.accumulate(x)
    return float(((p-x)/p).max())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--canon",required=True)
    ap.add_argument("--costgrid",required=True)
    ap.add_argument("--out",required=True)
    a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)

    T=pd.read_csv(Path(a.canon)/"trades_8bp_w226.csv",parse_dates=["entry_time","exit_time"])
    rows=[]
    cuts=[
      ("2023", "2023-01-01","2024-01-01"),
      ("2024", "2024-01-01","2025-01-01"),
      ("2025", "2025-01-01","2026-01-01"),
      ("2026_YTD", "2026-01-01","2026-08-22"),
      ("EARLY_23_24","2023-01-01","2025-01-01"),
      ("LATE_25_26","2025-01-01","2026-08-22"),
    ]
    for name,st,en in cuts:
        g=T[(T.entry_time>=st)&(T.entry_time<en)]
        rows.append(dict(period=name,**trade_stats(g)))
    TS=pd.DataFrame(rows);TS.to_csv(O/"ema_trade_temporal_stats.csv",index=False)
    print("TRADE_TEMPORAL");print(TS.to_string(index=False))

    base=pd.read_csv(Path(a.costgrid)/"base/equity_curve_8bp_risk080.csv",parse_dates=["time"]).set_index("time").equity
    prows=[]
    for fp in sorted(glob.glob(str(Path(a.canon)/"curve_8bp_w*.csv"))):
        w=int(Path(fp).stem.split("w")[-1])/1000
        ov=pd.read_csv(fp,parse_dates=["time"]).set_index("time").equity
        idx=base.index.intersection(ov.index);b=base.loc[idx];o=ov.loc[idx]
        for name,st,en in cuts:
            br=period_ret(b,st,en);orr=period_ret(o,st,en)
            if np.isnan(br) or np.isnan(orr):continue
            prows.append(dict(weight=w,period=name,base_ret_pct=br*100,overlay_ret_pct=orr*100,
                              relative_improvement_pct=((1+orr)/(1+br)-1)*100,
                              overlay_mdd_pct=maxdd(o[(o.index>=st)&(o.index<pd.Timestamp(en,tz="UTC"))])*100))
    P=pd.DataFrame(prows);P.to_csv(O/"portfolio_temporal_comparison.csv",index=False)
    print("PORTFOLIO_TEMPORAL")
    print(P[P.period.isin(["EARLY_23_24","LATE_25_26"])].to_string(index=False))

    # 6m rolling EMA trade PF, non-overlapping half-years for interpretability
    T["half"]=T.entry_time.dt.to_period("2Q")
    h=[]
    for k,g in T.groupby("half"):
        h.append(dict(period=str(k),**trade_stats(g)))
    H=pd.DataFrame(h);H.to_csv(O/"ema_halfyear_trade_stats.csv",index=False)
    print("HALFYEAR");print(H.to_string(index=False))

if __name__=="__main__":main()
