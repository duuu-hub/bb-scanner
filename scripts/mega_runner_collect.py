#!/usr/bin/env python3
"""Build long-history crypto spot OHLCV from Binance public data archive.

Uses data.binance.vision rather than api.binance.com because GitHub-hosted
runners can receive HTTP 451 from the trading API. Archive discovery also
includes historical/delisted spot symbols, reducing survivorship bias.
"""
import argparse,csv,io,json,time,urllib.parse,urllib.request,zipfile,xml.etree.ElementTree as ET
from datetime import datetime,timezone
from pathlib import Path
BASE="https://data.binance.vision/"\nLIST_BASE="https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
def urlopen(url,timeout=60):
    req=urllib.request.Request(url,headers={"User-Agent":"mega-runner-research/1.0"})
    return urllib.request.urlopen(req,timeout=timeout)
def list_prefix(prefix, delimiter=None):
    token=None
    while True:
        q={"list-type":"2","prefix":prefix}
        if delimiter:q["delimiter"]=delimiter
        if token:q["continuation-token"]=token
        with urlopen(LIST_BASE+"?"+urllib.parse.urlencode(q)) as r: root=ET.fromstring(r.read())
        ns={"s3":"http://s3.amazonaws.com/doc/2006-03-01/"}
        for x in root.findall("s3:CommonPrefixes/s3:Prefix",ns): yield ("prefix",x.text)
        for x in root.findall("s3:Contents/s3:Key",ns): yield ("key",x.text)
        trunc=(root.findtext("s3:IsTruncated",default="false",namespaces=ns)=="true")
        if not trunc:break
        token=root.findtext("s3:NextContinuationToken",namespaces=ns)
def symbols():
    pref="data/spot/monthly/klines/"
    out=[]
    for typ,val in list_prefix(pref,"/"):
        if typ!="prefix":continue
        sym=val[len(pref):].strip("/")
        if sym.endswith("USDT") and not any(x in sym for x in ("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT")): out.append(sym)
    return sorted(set(out))
def month_files(sym,interval,start_ym):
    pref=f"data/spot/monthly/klines/{sym}/{interval}/"
    for typ,key in list_prefix(pref):
        if typ!="key" or not key.endswith(".zip") or key.endswith(".CHECKSUM"):continue
        name=key.rsplit("/",1)[-1]
        try: ym="-".join(name[:-4].split("-")[-2:])
        except: continue
        if ym>=start_ym: yield key
def read_zip(key):
    with urlopen(BASE+key) as r: raw=r.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=[n for n in z.namelist() if n.endswith(".csv")]
        if not names:return []
        return list(csv.reader(io.TextIOWrapper(z.open(names[0]),encoding="utf-8")))
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--years",type=int,default=5); ap.add_argument("--interval",default="1d"); ap.add_argument("--out",default="research/mega_runner/data/binance_spot_1d"); ap.add_argument("--max-symbols",type=int,default=0); a=ap.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    now=datetime.now(timezone.utc); y=now.year-a.years; start_ym=f"{y:04d}-{now.month:02d}"
    syms=symbols(); syms=syms[:a.max_symbols] if a.max_symbols else syms
    manifest=[]
    for i,s in enumerate(syms,1):
        p=out/f"{s}.csv"; n=0; first=last=None; errors=[]
        with p.open("w",newline="") as f:
            w=csv.writer(f); w.writerow(["open_time","open","high","low","close","volume","quote_volume","trades","taker_buy_base","taker_buy_quote"])
            try: keys=list(month_files(s,a.interval,start_ym))
            except Exception as e: keys=[]; errors.append("list:"+str(e))
            for key in keys:
                try:
                    for r in read_zip(key):
                        if len(r)<11 or not r[0].isdigit():continue
                        w.writerow([r[0],r[1],r[2],r[3],r[4],r[5],r[7],r[8],r[9],r[10]])
                        n+=1; first=first or r[0]; last=r[0]
                except Exception as e: errors.append(key+":"+str(e))
        manifest.append({"symbol":s,"rows":n,"first":first,"last":last,"errors":errors[:5]})
        print(f"[{i}/{len(syms)}] {s}: {n} rows",flush=True)
    (out/"manifest.json").write_text(json.dumps(manifest,indent=2))
if __name__=="__main__":main()
