from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from precision_backtest import build_signals, calc_pf

BAR=900_000
LONG3={"L1_MOMENTUM_1H10","L2_EXPLOSIVE_4H30","L3_4H_LAG"}

def load_prices(root):
    fs=sorted(Path(root).glob("20??/??/*.csv.gz"))
    x=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
    t="timestamp_ms" if "timestamp_ms" in x else "ts"
    x[t]=pd.to_numeric(x[t],errors="coerce");x["close"]=pd.to_numeric(x["close"],errors="coerce")
    x=x.dropna(subset=[t,"symbol","close"]);x=x[x.close>0]
    return x.pivot_table(index=t,columns="symbol",values="close",aggfunc="last").sort_index()

def rs_table(px):
    out=[]
    btc=px["BTCUSDT"]
    for h,bars in [(1,4),(4,16),(24,96)]:
        r=px.pct_change(bars)*100
        rel=r.sub(r["BTCUSDT"],axis=0)
        z=rel.stack().rename(f"rs_{h}h").reset_index()
        z.columns=["ts","symbol",f"rs_{h}h"]
        out.append(z)
    q=out[0]
    for z in out[1:]:q=q.merge(z,on=["ts","symbol"],how="outer")
    return q

def summarize(x,col):
    z=x.dropna(subset=[col,"net_pct"]).copy()
    if len(z)<3:return []
    # Fixed rank buckets, not optimized thresholds.
    z["bucket"]=pd.qcut(z[col].rank(method="first"),3,labels=["LOW","MID","HIGH"])
    rows=[]
    for (strategy,bucket),g in z.groupby(["strategy","bucket"],observed=True):
        rows.append({"feature":col,"strategy":strategy,"bucket":str(bucket),"n":len(g),
          "pct_of_strategy":100*len(g)/len(z[z.strategy==strategy]),"avg_net_pct":g.net_pct.mean(),
          "win_pct":100*(g.net_pct>0).mean(),"pf":calc_pf(g.net_pct)})
    for bucket,g in z.groupby("bucket",observed=True):
        rows.append({"feature":col,"strategy":"ALL_LONG3","bucket":str(bucket),"n":len(g),
          "pct_of_strategy":100*len(g)/len(z),"avg_net_pct":g.net_pct.mean(),
          "win_pct":100*(g.net_pct>0).mean(),"pf":calc_pf(g.net_pct)})
    return rows

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",required=True)
    p.add_argument("--trades",required=True)
    p.add_argument("--root",default="market_data_store/bitget/research_auto100_15m")
    p.add_argument("--out",default="research/results/long3_relative_strength")
    a=p.parse_args()
    sig=build_signals(pd.read_csv(a.source))
    sig=sig[sig.strategy.isin(LONG3)][["symbol","ts","strategy","split"]].drop_duplicates()
    tr=pd.read_csv(a.trades)
    tr=tr[(tr.strategy.isin(LONG3)) & (tr.delay_min==1)].copy()
    # Prefer precomputed trade returns; join exact original signal timestamp.
    x=tr.merge(sig,left_on=["symbol","signal_ts","strategy"],right_on=["symbol","ts","strategy"],how="inner",suffixes=("","_sig"))
    px=load_prices(a.root); rs=rs_table(px)
    # Stored candle timestamp is the completed 15m bar. Shift one bar so no current/incomplete-bar leakage.
    rs["signal_ts"]=rs["ts"]+BAR
    x=x.merge(rs.drop(columns="ts"),on=["symbol","signal_ts"],how="left")
    rows=[]
    for c in ["rs_1h","rs_4h","rs_24h"]: rows += summarize(x,c)
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(rows).to_csv(out/"summary.csv",index=False)
    x.to_csv(out/"trades_with_rs.csv.gz",index=False,compression="gzip")
    meta={"matched_trades":len(x),"symbols":int(x.symbol.nunique()),"strategies":sorted(x.strategy.unique().tolist()),
      "rule":"Relative strength = asset trailing return - BTC trailing return. Feature uses only candles completed before signal. LOW/MID/HIGH are equal-count diagnostic buckets; no entry rule is changed.",
      "warning":"Diagnostic only. Bucket edges are sample-relative and must not be promoted to a trading filter without independent validation."}
    (out/"meta.json").write_text(json.dumps(meta,indent=2),encoding="utf-8")
    print(json.dumps(meta,indent=2));print(pd.DataFrame(rows).to_string(index=False))
if __name__=="__main__":main()