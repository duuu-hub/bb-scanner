from __future__ import annotations
import json, time, urllib.parse, urllib.request
from pathlib import Path
import pandas as pd
import continuation_execution_validation as ev

OUT=Path("continuation_1m_results"); OUT.mkdir(exist_ok=True)
SRC=Path("continuation_execution_results/ambiguous_15m.csv")
API="https://api.bitget.com/api/v3/market/history-candles"

def fetch_1m(symbol, ts):
    # Ask only for the ambiguous 15m interval; API may return boundary extras.
    q=urllib.parse.urlencode(dict(category="USDT-FUTURES",symbol=symbol,interval="1m",
        startTime=str(int(ts)),endTime=str(int(ts)+15*60_000-1),type="market",limit="100"))
    req=urllib.request.Request(API+"?"+q,headers={"User-Agent":"bb-scanner-research/1.0"})
    with urllib.request.urlopen(req,timeout=30) as r:
        obj=json.load(r)
    if str(obj.get("code"))!="00000":
        raise RuntimeError(f"{symbol} {ts}: {obj}")
    rows=[]
    for x in obj.get("data",[]):
        t=int(x[0])
        if int(ts)<=t<int(ts)+15*60_000:
            rows.append((t,float(x[1]),float(x[2]),float(x[3]),float(x[4])))
    return sorted(rows)

def resolve(row,bars):
    ep=float(row.entry_px); tp=ep*0.95; sl=ep*1.03
    if not bars: return "NO_DATA",None,None
    expected=list(range(int(row.exit_ts),int(row.exit_ts)+15*60_000,60_000))
    got={x[0] for x in bars}
    if any(t not in got for t in expected): return "INCOMPLETE_1M",None,len(got)
    for t,o,h,l,c in bars:
        ht=l<=tp; hs=h>=sl
        if ht and hs: return "BOTH_1M_SL",t,len(got)
        if ht: return "TP_FIRST",t,len(got)
        if hs: return "SL_FIRST",t,len(got)
    return "NO_TOUCH_1M",None,len(got)

def main():
    a=pd.read_csv(SRC)
    out=[]
    cache={}
    for i,r in enumerate(a.itertuples(),1):
        key=(r.symbol,int(r.exit_ts))
        try:
            if key not in cache:
                cache[key]=fetch_1m(*key); time.sleep(.06)
            verdict,first_ts,n=resolve(r,cache[key])
            err=""
        except Exception as e:
            verdict,first_ts,n,err="FETCH_ERROR",None,None,repr(e)
        out.append({**r._asdict(),"verdict_1m":verdict,"first_touch_1m_ts":first_ts,"n_1m":n,"error":err})
        print(i,len(a),r.symbol,r.exit_ts,verdict,flush=True)
    z=pd.DataFrame(out); z.to_csv(OUT/"ambiguous_1m_resolution.csv",index=False)
    counts=z.verdict_1m.value_counts(dropna=False).rename_axis("verdict").reset_index(name="n")
    counts.to_csv(OUT/"resolution_counts.csv",index=False)

    # Recompute final trade outcomes; only a verified TP-first changes BOTH_SL from -3 to +5.
    d=pd.read_csv("continuation_execution_results/audit_trades.csv.gz")
    tpkeys=set(zip(z.loc[z.verdict_1m=="TP_FIRST","symbol"],z.loc[z.verdict_1m=="TP_FIRST","exit_ts"].astype(int)))
    changed=0
    for ix,r in d[d.exit_reason=="BOTH_SL"].iterrows():
        if (r.symbol,int(r.exit_ts)) in tpkeys:
            d.at[ix,"gross_ret_pct"]=5.0; d.at[ix,"exit_reason"]="TP_1M"; changed+=1
    s=ev.stats(d,0.20)
    summary={"ambiguous_15m":len(z),"changed_to_tp":changed,
      "unresolved":int((~z.verdict_1m.isin(["TP_FIRST","SL_FIRST","BOTH_1M_SL"])).sum()),**s}
    pd.DataFrame([summary]).to_csv(OUT/"final_summary.csv",index=False)
    print("=== RESOLUTION COUNTS ==="); print(counts.to_string(index=False))
    print("=== FINAL SUMMARY ==="); print(pd.DataFrame([summary]).to_string(index=False))

if __name__=="__main__": main()
