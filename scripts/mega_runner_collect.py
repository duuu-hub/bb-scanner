#!/usr/bin/env python3
"""Build long-history altcoin OHLCV dataset for mega-runner precursor research.

Primary source: Binance Spot public API. Pulls USDT pairs from listing-era forward,
so history can extend multiple years where the exchange has it.
"""
import argparse, csv, json, time, urllib.parse, urllib.request
from pathlib import Path
BASE="https://api.binance.com"
def get(path, params=None):
    u=BASE+path
    if params: u+="?"+urllib.parse.urlencode(params)
    with urllib.request.urlopen(u, timeout=30) as r: return json.load(r)
def symbols():
    x=get("/api/v3/exchangeInfo")
    bad=("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")
    return sorted(s["symbol"] for s in x["symbols"] if s["quoteAsset"]=="USDT" and s["status"]=="TRADING" and not s["symbol"].endswith(bad))
def fetch(sym, interval, start_ms, end_ms):
    cur=start_ms
    while cur<end_ms:
        rows=get("/api/v3/klines",{"symbol":sym,"interval":interval,"startTime":cur,"endTime":end_ms,"limit":1000})
        if not rows: break
        for r in rows: yield r
        nxt=int(rows[-1][0])+1
        if nxt<=cur: break
        cur=nxt; time.sleep(.06)
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--years",type=int,default=5); ap.add_argument("--interval",default="1h")
    ap.add_argument("--out",default="research/mega_runner/data/binance_spot_1h")
    ap.add_argument("--max-symbols",type=int,default=0)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    end=int(time.time()*1000); start=end-int(a.years*365.25*86400000)
    syms=symbols(); syms=syms[:a.max_symbols] if a.max_symbols else syms
    manifest=[]
    for i,s in enumerate(syms,1):
        p=out/f"{s}.csv"; n=0; first=last=None
        try:
            with p.open("w",newline="") as f:
                w=csv.writer(f); w.writerow(["open_time","open","high","low","close","volume","quote_volume","trades","taker_buy_base","taker_buy_quote"])
                for r in fetch(s,a.interval,start,end):
                    w.writerow([r[0],r[1],r[2],r[3],r[4],r[5],r[7],r[8],r[9],r[10]])
                    n+=1; first=first or r[0]; last=r[0]
            manifest.append({"symbol":s,"rows":n,"first":first,"last":last})
            print(f"[{i}/{len(syms)}] {s}: {n}")
        except Exception as e:
            manifest.append({"symbol":s,"error":str(e)}); print("ERR",s,e)
    (out/"manifest.json").write_text(json.dumps(manifest,indent=2))
if __name__=="__main__": main()
