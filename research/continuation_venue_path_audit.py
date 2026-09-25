#!/usr/bin/env python3
"""Audit matched Bitget/Binance continuation SHORT fills on original candles.

Reads two *completed* transaction artifacts; only downloads three symbols of
August 2026 Binance 15m archive and the needed tracked Bitget daily files.
No signal thresholds, exits or previously finished research are reoptimized.
"""
import gzip
import io
import json
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OUT=Path("venue_audit_results"); OUT.mkdir(exist_ok=True)
SYMS=("BEATUSDT","VELVETUSDT","UAIUSDT")


def canonical_bitget_trades():
    b=pd.read_csv("bitget_artifact/trades.csv.gz")
    b=b[(b.side=="SHORT")&(b.tp==4)&(b.sl==2)&(b.horizon_h==6)&(b.delay_bars==0)].sort_values(["signal_ts","symbol"])
    accepted=[]; busy={}
    for r in b.itertuples():
        if r.entry_ts<=busy.get(r.symbol,-1): continue
        accepted.append(r.Index); busy[r.symbol]=r.exit_ts
    b=b.loc[accepted].copy()
    b["entry_time"]=pd.to_datetime(b.entry_ts,unit="ms",utc=True)
    return b


def binance_month(sym,month="2026-08"):
    url=f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/15m/{sym}-15m-{month}.zip"
    r=requests.get(url,timeout=120);r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        csv=next(n for n in z.namelist() if n.endswith(".csv"))
        d=pd.read_csv(z.open(csv),header=None,usecols=[0,1,2,3,4],names=["timestamp_ms","open","high","low","close"])
    d["timestamp_ms"]=pd.to_numeric(d.timestamp_ms,errors="coerce")
    d.loc[d.timestamp_ms>1e14,"timestamp_ms"]/=1000
    return d.dropna().sort_values("timestamp_ms").drop_duplicates("timestamp_ms").reset_index(drop=True)


def bitget_day(day):
    ym=day.strftime("%Y/%m");stamp=day.strftime("%Y-%m-%d")
    path=f"market_data_store/bitget/research_auto100_15m/{ym}/{stamp}.csv.gz"
    raw=subprocess.check_output(["git","show",f"FETCH_HEAD:{path}"])
    return pd.read_csv(io.BytesIO(raw),compression="gzip",usecols=["symbol","timestamp_ms","open","high","low","close"])


def replay(d,entry_ms):
    d=d[(d.timestamp_ms>=entry_ms)&(d.timestamp_ms<entry_ms+6*3_600_000)].sort_values("timestamp_ms")
    if d.empty or d.timestamp_ms.iloc[0]!=entry_ms:return {"result":"MISSING"}
    ep=d.open.iloc[0]
    both=0
    for row in d.itertuples():
        hit_sl=row.high>=ep*1.02
        hit_tp=row.low<=ep*.96
        if hit_tp and hit_sl: both+=1
        if hit_sl:return {"result":"SL","exit_bar_ms":int(row.timestamp_ms),"both_first_exit":hit_tp,
                           "entry_px":float(ep),"hold_bars":int((row.timestamp_ms-entry_ms)/900000)+1}
        if hit_tp:return {"result":"TP","exit_bar_ms":int(row.timestamp_ms),"both_first_exit":False,
                           "entry_px":float(ep),"hold_bars":int((row.timestamp_ms-entry_ms)/900000)+1}
    return {"result":"TIME","exit_bar_ms":int(d.timestamp_ms.iloc[-1]),"both_first_exit":False,
            "entry_px":float(ep),"hold_bars":len(d)}


def main():
    b=canonical_bitget_trades()
    t=pd.read_csv("transfer_artifact/frozen_candidates.csv.gz",parse_dates=["entry_time"])
    t=t[(t.module=="CONT")&(t.symbol.isin(SYMS))&(t.entry_time.dt.strftime("%Y-%m")=="2026-08")]
    z=b.merge(t,on=["symbol","entry_time"])
    flips=z[(z.gross_ret_pct>=3.99)&(z.ret<=-2.19)].copy()
    assert len(flips)>0,"No paired TP-to-SL reversals, stop the audit"
    dates=set()
    for entry in flips.entry_time:
        day=entry.floor("D")
        dates.add(day);dates.add(day+pd.Timedelta(days=1))
    # The Bitget AUTO100 research data is committed on its own branch.
    bit={day:bitget_day(day) for day in sorted(dates) if day.month==8}
    bn={sym:binance_month(sym) for sym in SYMS}
    rows=[]
    for r in flips.itertuples():
        day=r.entry_time.floor("D");ms=int(r.entry_time.timestamp()*1000)
        bg=pd.concat([bit[x] for x in (day,day+pd.Timedelta(days=1)) if x in bit],ignore_index=True)
        bg=bg[bg.symbol==r.symbol]
        br=replay(bg,ms);nr=replay(bn[r.symbol],ms)
        rows.append({"symbol":r.symbol,"entry_time":r.entry_time,
                     "bitget_record":"SL" if r.exit_reason=="BOTH_SL" else r.exit_reason,
                     "binance_record":r.reason,
                     "bitget_replay":br.get("result"),"binance_replay":nr.get("result"),
                     "binance_both_first_exit":nr.get("both_first_exit"),
                     "bitget_both_first_exit":br.get("both_first_exit"),
                     "bitget_entry_px":br.get("entry_px"),"binance_entry_px":nr.get("entry_px"),
                     "bitget_first_exit_bar_ms":br.get("exit_bar_ms"),
                     "binance_first_exit_bar_ms":nr.get("exit_bar_ms"),
                     "bitget_hold_bars":br.get("hold_bars"),"binance_hold_bars":nr.get("hold_bars")})
    out=pd.DataFrame(rows)
    out["entry_px_gap_pct"]=(out.binance_entry_px/out.bitget_entry_px-1)*100
    out.to_csv(OUT/"matched_flips.csv",index=False)
    summary={"n":len(out),"symbols":out.symbol.value_counts().to_dict(),
             "both_hit_binance_first_exit":int(out.binance_both_first_exit.fillna(False).sum()),
             "both_hit_bitget_first_exit":int(out.bitget_both_first_exit.fillna(False).sum()),
             "bitget_replay_matches_artifact":int((out.bitget_replay==out.bitget_record).sum()),
             "binance_replay_matches_artifact":int((out.binance_replay==out.binance_record).sum()),
             "median_abs_entry_price_gap_pct":float(out.entry_px_gap_pct.abs().median()),
             "median_binance_hold_bars":float(out.binance_hold_bars.median()),
             "median_bitget_hold_bars":float(out.bitget_hold_bars.median())}
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2),flush=True)


if __name__=="__main__":main()
