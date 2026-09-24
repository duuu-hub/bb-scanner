#!/usr/bin/env python3
"""Efficient sharded Binance 15m archive collector for frozen Rank5 research.

Supports Spot and USD-M Futures through data.binance.vision monthly archives.
Stores one gzip CSV per symbol and a manifest with coverage/integrity diagnostics.
"""
from __future__ import annotations
import argparse, csv, gzip, io, json, urllib.parse, urllib.request, zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

BASE="https://data.binance.vision/"
LIST_BASE="https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
INTERVAL_MS={"15m":15*60*1000}
HEADER=["open_time","open","high","low","close","volume","quote_volume","trades","taker_buy_base","taker_buy_quote"]

def urlopen(url,timeout=90):
    req=urllib.request.Request(url,headers={"User-Agent":"rank5-long-history-research/1.0"})
    return urllib.request.urlopen(req,timeout=timeout)

def list_prefix(prefix,delimiter=None):
    token=None
    while True:
        q={"list-type":"2","prefix":prefix}
        if delimiter:q["delimiter"]=delimiter
        if token:q["continuation-token"]=token
        with urlopen(LIST_BASE+"?"+urllib.parse.urlencode(q)) as r:
            root=ET.fromstring(r.read())
        ns={"s3":"http://s3.amazonaws.com/doc/2006-03-01/"}
        for x in root.findall("s3:CommonPrefixes/s3:Prefix",ns):
            yield "prefix",x.text
        for x in root.findall("s3:Contents/s3:Key",ns):
            yield "key",x.text
        if root.findtext("s3:IsTruncated",default="false",namespaces=ns)!="true":
            break
        token=root.findtext("s3:NextContinuationToken",namespaces=ns)

def market_prefix(market):
    return "data/spot/monthly/klines/" if market=="spot" else "data/futures/um/monthly/klines/"

def all_usdt_symbols(market):
    pref=market_prefix(market)
    out=[]
    for typ,val in list_prefix(pref,"/"):
        if typ!="prefix":continue
        sym=val[len(pref):].strip("/")
        if sym.endswith("USDT") and not any(x in sym for x in ("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")):
            out.append(sym)
    return sorted(set(out))

def month_keys(market,sym,interval,start_ym):
    pref=f"{market_prefix(market)}{sym}/{interval}/"
    keys=[]
    for typ,key in list_prefix(pref):
        if typ!="key" or not key.endswith(".zip") or key.endswith(".CHECKSUM"):continue
        name=key.rsplit("/",1)[-1]
        parts=name[:-4].split("-")
        if len(parts)<3:continue
        ym="-".join(parts[-2:])
        if ym>=start_ym:keys.append(key)
    return sorted(keys)

def read_zip_rows(key):
    with urlopen(BASE+key) as r: raw=r.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=[n for n in z.namelist() if n.endswith(".csv")]
        if not names:return []
        return list(csv.reader(io.TextIOWrapper(z.open(names[0]),encoding="utf-8")))

def ym_years_ago(years):
    now=datetime.now(timezone.utc)
    return f"{now.year-years:04d}-{now.month:02d}"

def collect_symbol(market,sym,interval,start_ym,outdir):
    keys=month_keys(market,sym,interval,start_ym)
    p=outdir/f"{sym}.csv.gz"
    n=0; first=None; last=None; prev=None
    gaps=0; dupes=0; unaligned=0; errors=[]; seen=set()
    with gzip.open(p,"wt",newline="",encoding="utf-8",compresslevel=6) as fh:
        w=csv.writer(fh,lineterminator="\n");w.writerow(HEADER)
        for key in keys:
            try:
                rows=read_zip_rows(key)
            except Exception as e:
                errors.append(f"{key}:{e}")
                continue
            for r in rows:
                if len(r)<11 or not str(r[0]).isdigit():continue
                raw_ts=int(r[0])
                # Binance Spot archive uses microsecond timestamps in newer files;
                # normalize mixed historical units to milliseconds.
                ts=raw_ts//1000 if raw_ts>100_000_000_000_000 else raw_ts
                if ts in seen:
                    dupes+=1;continue
                seen.add(ts)
                if ts%INTERVAL_MS[interval]!=0:unaligned+=1
                if prev is not None and ts>prev+INTERVAL_MS[interval]:
                    gaps += (ts-prev)//INTERVAL_MS[interval]-1
                if prev is not None and ts<prev:
                    errors.append(f"out_of_order:{prev}->{ts}")
                prev=ts
                w.writerow([str(ts),r[1],r[2],r[3],r[4],r[5],r[7],r[8],r[9],r[10]])
                n+=1;first=first or ts;last=ts
    if n==0:
        p.unlink(missing_ok=True)
    return {
        "symbol":sym,"rows":n,"first":first,"last":last,"months":len(keys),
        "gaps":int(gaps),"duplicates_skipped":int(dupes),"unaligned":int(unaligned),
        "errors":errors[:10],"file":p.name if n else None
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--market",choices=["spot","um"],required=True)
    ap.add_argument("--years",type=int,default=5)
    ap.add_argument("--interval",choices=["15m"],default="15m")
    ap.add_argument("--out",required=True)
    ap.add_argument("--shard-count",type=int,default=1)
    ap.add_argument("--shard-index",type=int,default=0)
    ap.add_argument("--only-symbol",action="append",default=[])
    args=ap.parse_args()
    if not (0<=args.shard_index<args.shard_count):raise SystemExit("invalid shard")
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    start_ym=ym_years_ago(args.years)
    syms=all_usdt_symbols(args.market)
    if args.only_symbol:
        want=set(args.only_symbol);syms=[s for s in syms if s in want]
    else:
        syms=[s for i,s in enumerate(syms) if i%args.shard_count==args.shard_index]
    manifest=[]
    print(json.dumps({"market":f"binance_{args.market}","interval":args.interval,"years":args.years,
                      "start_ym":start_ym,"shard_index":args.shard_index,"shard_count":args.shard_count,
                      "selected_symbols":len(syms)}),flush=True)
    for i,s in enumerate(syms,1):
        try:r=collect_symbol(args.market,s,args.interval,start_ym,out)
        except Exception as e:r={"symbol":s,"rows":0,"first":None,"last":None,"months":0,"gaps":0,"duplicates_skipped":0,"unaligned":0,"errors":[str(e)],"file":None}
        manifest.append(r)
        print(f"[{i}/{len(syms)}] {s} rows={r['rows']} months={r['months']} gaps={r['gaps']} errors={len(r['errors'])}",flush=True)
    summary={
        "market":f"binance_{args.market}","interval":args.interval,"years":args.years,"start_ym":start_ym,
        "shard_index":args.shard_index,"shard_count":args.shard_count,
        "symbols_selected":len(syms),"symbols_with_data":sum(x["rows"]>0 for x in manifest),
        "rows":sum(x["rows"] for x in manifest),"symbols_with_errors":sum(bool(x["errors"]) for x in manifest),
        "symbols_with_gaps":sum(x["gaps"]>0 for x in manifest),
        "unaligned_total":sum(x["unaligned"] for x in manifest)
    }
    (out/f"manifest_shard_{args.shard_index:02d}.json").write_text(json.dumps({"summary":summary,"symbols":manifest},indent=2),encoding="utf-8")
    print("SUMMARY "+json.dumps(summary),flush=True)

if __name__=="__main__":main()
