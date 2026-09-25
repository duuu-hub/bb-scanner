#!/usr/bin/env python3
"""Replay frozen Bitget Continuation SHORT signal timestamps on full 15m OHLC.

Existing signal times come from completed run 36067424646. No signal search,
training, parameter change or completed data collection is repeated. The
previous execution erroneously used candidate-only rows as the price path.
"""
import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

OUT=Path("bitget_fullpath_results");OUT.mkdir(exist_ok=True)
BAR=900_000


def load_day(day):
    path=f"market_data_store/bitget/research_auto100_15m/{day:%Y/%m/%Y-%m-%d}.csv.gz"
    raw=subprocess.check_output(["git","show",f"FETCH_HEAD:{path}"])
    d=pd.read_csv(io.BytesIO(raw),compression="gzip",usecols=["symbol","timestamp_ms","open","high","low","close"])
    for col in ("timestamp_ms","open","high","low","close"):
        d[col]=pd.to_numeric(d[col],errors="coerce")
    return d.dropna()


def stats(df,cost=.20):
    if len(df)==0:return {"n":0}
    v=df.gross_ret_pct-cost
    return {"n":len(df),"symbols":int(df.symbol.nunique()),
            "days":int(pd.to_datetime(df.signal_ts,unit="ms",utc=True).dt.floor("D").nunique()),
            "avg_net_pct":float(v.mean()),"pf":float(v[v>0].sum()/-v[v<0].sum()),
            "win_pct":float((v>0).mean()*100),"tp":int(df.reason.eq("TP").sum()),
            "sl":int(df.reason.isin(["SL","BOTH_SL"]).sum()),
            "both_sl":int(df.reason.eq("BOTH_SL").sum()),
            "time":int(df.reason.eq("TIME").sum()),"gap_exit":int(df.reason.eq("GAP").sum())}


def fullpath(g,r):
    t=g.timestamp_ms.to_numpy(dtype="int64")
    i=np.searchsorted(t,int(r.signal_ts))
    if i>=len(t) or t[i]!=r.signal_ts:return None,"MISSING_SIGNAL"
    ei=i+1
    if ei>=len(t) or t[ei]!=r.signal_ts+BAR:return None,"MISSING_NEXT_15M"
    ep=float(g.open.iloc[ei]);deadline=t[ei]+6*3600_000
    end=min(len(t)-1,np.searchsorted(t,deadline,side="left")-1)
    reason="TIME";xi=end;xp=float(g.close.iloc[end])
    for j in range(ei,end+1):
        if j>ei and t[j]!=t[j-1]+BAR:
            reason="GAP";xi=j-1;xp=float(g.close.iloc[xi]);break
        high=float(g.high.iloc[j]);low=float(g.low.iloc[j]);hit_sl=high>=ep*1.02;hit_tp=low<=ep*.96
        if hit_sl:
            reason="BOTH_SL" if hit_tp else "SL";xi=j;xp=ep*1.02;break
        if hit_tp:
            reason="TP";xi=j;xp=ep*.96;break
    return {"symbol":r.symbol,"signal_ts":int(r.signal_ts),"entry_ts":int(t[ei]),
            "exit_ts":int(t[xi]),"gross_ret_pct":(1-xp/ep)*100,"reason":reason,
            "prior_entry_ts":int(r.entry_ts),"prior_exit_ts":int(r.exit_ts),
            "prior_gross":float(r.gross_ret_pct)},None


def once_per_symbol(d):
    d=d.sort_values(["signal_ts","symbol"])
    keep=[];busy={}
    for r in d.itertuples():
        if r.entry_ts<=busy.get(r.symbol,-1):continue
        keep.append(r.Index);busy[r.symbol]=r.exit_ts
    return d.loc[keep].copy()


def main():
    o=pd.read_csv("bitget_artifact/trades.csv.gz")
    o=o[(o.side=="SHORT")&(o.tp==4)&(o.sl==2)&(o.horizon_h==6)&(o.delay_bars==0)].copy()
    assert len(o)>1500
    lo=pd.to_datetime(o.signal_ts.min(),unit="ms",utc=True).floor("D")-pd.Timedelta(days=1)
    hi=pd.to_datetime(o.signal_ts.max(),unit="ms",utc=True).floor("D")+pd.Timedelta(days=1)
    paths=[];missing_days=[]
    for day in pd.date_range(lo,hi,tz="UTC"):
        try:paths.append(load_day(day))
        except subprocess.CalledProcessError:missing_days.append(str(day.date()))
    print("loaded days",len(paths),"missing",missing_days,flush=True)
    d=pd.concat(paths,ignore_index=True)
    d=d.drop_duplicates(["symbol","timestamp_ms"],keep="last").sort_values(["symbol","timestamp_ms"])
    groups={sym:g.reset_index(drop=True) for sym,g in d.groupby("symbol",sort=False)}
    rows=[];skipped={}
    for sym,cands in o.groupby("symbol"):
        g=groups.get(sym)
        for r in cands.itertuples():
            if g is None:skip="MISSING_SYMBOL";z=None
            else:z,skip=fullpath(g,r)
            if z is None:skipped[skip]=skipped.get(skip,0)+1
            else:rows.append(z)
    alltr=pd.DataFrame(rows)
    accepted=once_per_symbol(alltr)
    alltr.to_csv(OUT/"fullpath_raw.csv.gz",index=False,compression="gzip")
    accepted.to_csv(OUT/"fullpath_accepted.csv.gz",index=False,compression="gzip")
    thirds={}
    s=accepted.sort_values("signal_ts")
    for k,ids in enumerate(np.array_split(np.arange(len(s)),3),1):thirds[str(k)]=stats(s.iloc[ids])
    summary={"original_rows":len(o),"skipped":skipped,"missing_days":missing_days,
             "raw":stats(alltr),"accepted":stats(accepted),
             "cost_stress":{"0.00":stats(accepted,0),"0.20":stats(accepted,.2),
                            "0.45":stats(accepted,.45),"0.70":stats(accepted,.7)},
             "thirds":thirds}
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2),flush=True)


if __name__=="__main__":main()
