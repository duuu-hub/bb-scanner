#!/usr/bin/env python3
"""Correct the TP-first intrabar bug in the *completed* Bitget OOS trade CSV.

The frozen signal and entry time stay unchanged. Each original TP's own exit
15m candle is fetched from tracked Bitget AUTO100 OHLC; if that candle also
reaches SL, the trade is conservatively reclassified as SL at the same time.
No fresh feature training, threshold optimization or signal search is run.
"""
import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

OUT=Path("bitget_intrabar_audit_results");OUT.mkdir(exist_ok=True)


def daily(day,cache):
    key=day.strftime("%Y-%m-%d")
    if key not in cache:
        path=f"market_data_store/bitget/research_auto100_15m/{day:%Y/%m}/{key}.csv.gz"
        data=subprocess.check_output(["git","show",f"FETCH_HEAD:{path}"])
        f=pd.read_csv(io.BytesIO(data),compression="gzip",usecols=["symbol","timestamp_ms","open","high","low"])
        f.timestamp_ms=pd.to_numeric(f.timestamp_ms).astype("int64")
        cache[key]=f.set_index(["symbol","timestamp_ms"])
    return cache[key]


def stats(df,cost=.20):
    v=df.corrected_gross-cost
    gains=v[v>0].sum();loss=-v[v<0].sum()
    return {"n":len(df),"symbols":df.symbol.nunique(),"signal_days":pd.to_datetime(df.signal_ts,unit="ms",utc=True).dt.floor("D").nunique(),
            "avg_net_pct":float(v.mean()),"pf":float(gains/loss) if loss else None,
            "win_pct":float((v>0).mean()*100),"both_hit_n":int(df.both_hit.sum())}


def accepted(df):
    df=df.sort_values(["signal_ts","symbol"])
    keep=[];busy={}
    for r in df.itertuples():
        if r.entry_ts<=busy.get(r.symbol,-1):continue
        keep.append(r.Index);busy[r.symbol]=r.exit_ts
    return df.loc[keep].copy()


def main():
    d=pd.read_csv("bitget_artifact/trades.csv.gz")
    d=d[(d.side=="SHORT")&(d.tp==4)&(d.sl==2)&(d.horizon_h==6)&(d.delay_bars==0)].copy()
    assert len(d)>1500,"unexpected frozen OOS selection"
    d["corrected_gross"]=d.gross_ret_pct.copy()
    d["both_hit"]=False
    tp=d[d.exit_reason.eq("TP")].copy()
    cache={}
    missing=[]
    for i,r in tp.iterrows():
        entday=pd.to_datetime(r.entry_ts,unit="ms",utc=True)
        exday=pd.to_datetime(r.exit_ts,unit="ms",utc=True)
        try:
            entry=daily(entday,cache).loc[(r.symbol,int(r.entry_ts))]
            ex=daily(exday,cache).loc[(r.symbol,int(r.exit_ts))]
        except (KeyError,subprocess.CalledProcessError) as e:
            missing.append((r.symbol,int(r.entry_ts),str(e)))
            continue
        entry_px=float(entry.open); high=float(ex.high);low=float(ex.low)
        assert low<=entry_px*.96+1e-8,(r.symbol,r.exit_ts,"artifact TP not reproducible")
        if high>=entry_px*1.02-1e-8:
            d.loc[i,"both_hit"]=True
            d.loc[i,"corrected_gross"]=-2.
    if missing:raise RuntimeError(f"{len(missing)} missing candles, first={missing[:3]}")
    selected=accepted(d)
    row={"base_raw":stats(d.assign(corrected_gross=d.gross_ret_pct,both_hit=False)),
         "corrected_raw":stats(d),"base_accepted":stats(selected.assign(corrected_gross=selected.gross_ret_pct,both_hit=False)),
         "corrected_accepted":stats(selected),
         "cost_stress":{"0.20":stats(selected,.2),"0.45":stats(selected,.45),"0.70":stats(selected,.7)},
         "corrected_by_third":{},"corrected_by_symbol":{}}
    s=selected.sort_values("signal_ts")
    for k,g in enumerate(np.array_split(s,3),1):row["corrected_by_third"][str(k)]=stats(g)
    for sym,g in selected.groupby("symbol"):
        row["corrected_by_symbol"][sym]=stats(g)
    selected.to_csv(OUT/"corrected_accepted.csv.gz",index=False,compression="gzip")
    (OUT/"summary.json").write_text(json.dumps(row,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in row.items() if k!='corrected_by_symbol'},indent=2),flush=True)


if __name__=="__main__":main()
