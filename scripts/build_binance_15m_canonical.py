#!/usr/bin/env python3
"""Build a reusable canonical Binance 15m Parquet dataset from collected CSV.gz shards.

Inputs are completed Spot and USD-M Futures archive collectors. This stage:
- reuses already-downloaded history (no historical redownload),
- normalizes mixed ms/us timestamps,
- appends missing full days from Binance daily archive,
- attempts API supplementation only for the current UTC day,
- sorts/deduplicates and validates 15m alignment/gaps,
- writes one ZSTD Parquet file per symbol,
- bundles Parquet files into deterministic release shards.
"""
from __future__ import annotations
import argparse, csv, hashlib, io, json, math, os, time, urllib.parse, urllib.request, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ARCHIVE="https://data.binance.vision/"
SPOT_API="https://data-api.binance.vision/api/v3/klines"
UM_API="https://fapi.binance.com/fapi/v1/klines"
INTERVAL_MS=15*60*1000
COLUMNS=["open_time","open","high","low","close","volume","quote_volume","trades","taker_buy_base","taker_buy_quote"]
NUMERIC=["open","high","low","close","volume","quote_volume","taker_buy_base","taker_buy_quote"]

def norm_ts(v):
    x=int(v)
    return x//1000 if x>100_000_000_000_000 else x

def http_bytes(url, timeout=45):
    req=urllib.request.Request(url,headers={"User-Agent":"bb-scanner-canonical-data/1.0"})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return r.read()

def archive_daily_key(market,sym,day):
    root="data/spot" if market=="spot" else "data/futures/um"
    return f"{root}/daily/klines/{sym}/15m/{sym}-15m-{day:%Y-%m-%d}.zip"

def read_archive_zip_bytes(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=[n for n in z.namelist() if n.endswith(".csv")]
        if not names:return pd.DataFrame(columns=COLUMNS)
        rows=list(csv.reader(io.TextIOWrapper(z.open(names[0]),encoding="utf-8")))
    out=[]
    for r in rows:
        if len(r)<11 or not str(r[0]).isdigit():continue
        out.append([norm_ts(r[0]),r[1],r[2],r[3],r[4],r[5],r[7],r[8],r[9],r[10]])
    return pd.DataFrame(out,columns=COLUMNS)

def fetch_daily_archive(market,sym,day):
    key=archive_daily_key(market,sym,day)
    try:
        raw=http_bytes(ARCHIVE+key)
        # ZIP CRC is verified by zipfile during extraction; also hash bytes for provenance.
        sha=hashlib.sha256(raw).hexdigest()
        df=read_archive_zip_bytes(raw)
        return df,sha,None
    except Exception as e:
        return pd.DataFrame(columns=COLUMNS),None,str(e)

def fetch_api_today(market,sym,start_ms,end_ms):
    if start_ms>=end_ms:return pd.DataFrame(columns=COLUMNS),None
    base=SPOT_API if market=="spot" else UM_API
    rows=[];cursor=start_ms;err=None
    while cursor<end_ms:
        q={"symbol":sym,"interval":"15m","startTime":str(cursor),"endTime":str(end_ms),"limit":"1000"}
        try:
            raw=http_bytes(base+"?"+urllib.parse.urlencode(q),timeout=30)
            data=json.loads(raw)
            if not isinstance(data,list):raise RuntimeError(str(data)[:300])
            if not data:break
            for r in data:
                if len(r)<11:continue
                rows.append([norm_ts(r[0]),r[1],r[2],r[3],r[4],r[5],r[7],r[8],r[9],r[10]])
            nxt=norm_ts(data[-1][0])+INTERVAL_MS
            if nxt<=cursor:break
            cursor=nxt
            if len(data)<1000:break
        except Exception as e:
            err=str(e);break
    return pd.DataFrame(rows,columns=COLUMNS),err

def clean_frame(df):
    if df.empty:return df
    df=df.copy()
    df["open_time"]=df["open_time"].map(norm_ts).astype("int64")
    for c in NUMERIC:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    df["trades"]=pd.to_numeric(df["trades"],errors="coerce").fillna(0).astype("int64")
    df=df.dropna(subset=["open","high","low","close"])
    df=df.sort_values("open_time").drop_duplicates("open_time",keep="last").reset_index(drop=True)
    return df

def validate(df):
    if df.empty:return {"rows":0,"duplicates":0,"unaligned":0,"gaps":0,"bad_ohlc":0}
    ts=df.open_time.to_numpy()
    unaligned=int(((ts%INTERVAL_MS)!=0).sum())
    dif=ts[1:]-ts[:-1]
    gaps=int(sum(max(0,int(d//INTERVAL_MS)-1) for d in dif if d>INTERVAL_MS and d%INTERVAL_MS==0))
    bad=int(((df.high < df[["open","close","low"]].max(axis=1)) | (df.low > df[["open","close","high"]].min(axis=1))).sum())
    return {"rows":len(df),"duplicates":0,"unaligned":unaligned,"gaps":gaps,"bad_ohlc":bad}

def process_symbol(market,path,out_root,tail_days_limit=45):
    sym=path.name.replace(".csv.gz","")
    df=pd.read_csv(path)
    df=clean_frame(df)
    base_rows=len(df)
    daily_rows=0;daily_ok=0;daily_miss=0;hashes=[]
    if len(df):
        last_dt=datetime.fromtimestamp(int(df.open_time.max())/1000,tz=timezone.utc)
        today=datetime.now(timezone.utc).date()
        day=last_dt.date()+timedelta(days=1)
        max_start=today-timedelta(days=tail_days_limit)
        if day<max_start:day=max_start
        while day<today:
            d,sha,err=fetch_daily_archive(market,sym,day)
            if len(d):
                df=pd.concat([df,d],ignore_index=True);daily_rows+=len(d);daily_ok+=1;hashes.append((day.isoformat(),sha))
            else:
                daily_miss+=1
            day+=timedelta(days=1)
    df=clean_frame(df)

    # Current UTC day only; failure is non-fatal and recorded.
    now=datetime.now(timezone.utc)
    current_start=int(datetime(now.year,now.month,now.day,tzinfo=timezone.utc).timestamp()*1000)
    api_start=max(current_start, int(df.open_time.max())+INTERVAL_MS if len(df) else current_start)
    latest_completed=(int(now.timestamp()*1000)//INTERVAL_MS)*INTERVAL_MS-INTERVAL_MS
    api_df,api_err=fetch_api_today(market,sym,api_start,latest_completed)
    api_rows=len(api_df)
    if api_rows:
        df=clean_frame(pd.concat([df,api_df],ignore_index=True))

    stats=validate(df)
    outdir=out_root/market
    outdir.mkdir(parents=True,exist_ok=True)
    outp=outdir/f"{sym}.parquet"
    table=pa.Table.from_pandas(df,preserve_index=False)
    pq.write_table(table,outp,compression="zstd",compression_level=6,use_dictionary=True)
    return {
        "market":market,"symbol":sym,"rows":len(df),"base_rows":base_rows,
        "daily_rows_added":daily_rows,"daily_days_ok":daily_ok,"daily_days_missing":daily_miss,
        "api_rows_added":api_rows,"api_error":api_err,
        "first":int(df.open_time.min()) if len(df) else None,
        "last":int(df.open_time.max()) if len(df) else None,
        "gaps":stats["gaps"],"unaligned":stats["unaligned"],"bad_ohlc":stats["bad_ohlc"],
        "parquet_bytes":outp.stat().st_size,"parquet_file":str(outp),
        "daily_archive_hashes":hashes[-5:]
    }

def bundle_market(out_root,dist_root,market,shards=16):
    files=sorted((out_root/market).glob("*.parquet"))
    groups={i:[] for i in range(shards)}
    for p in files:
        idx=int(hashlib.sha1(p.stem.encode()).hexdigest(),16)%shards
        groups[idx].append(p)
    assets=[]
    for idx,ps in groups.items():
        if not ps:continue
        zpath=dist_root/f"binance-{market}-15m-5y-parquet-shard-{idx:02d}.zip"
        with zipfile.ZipFile(zpath,"w",compression=zipfile.ZIP_STORED,allowZip64=True) as z:
            for p in ps:z.write(p,arcname=f"{market}/{p.name}")
        assets.append({"market":market,"shard":idx,"files":len(ps),"bytes":zpath.stat().st_size,"asset":zpath.name})
    return assets

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--spot-root",required=True)
    ap.add_argument("--um-root",required=True)
    ap.add_argument("--out",default="canonical_parquet")
    ap.add_argument("--dist",default="canonical_dist")
    ap.add_argument("--workers",type=int,default=8)
    args=ap.parse_args()
    out=Path(args.out);dist=Path(args.dist);out.mkdir(parents=True,exist_ok=True);dist.mkdir(parents=True,exist_ok=True)
    tasks=[]
    for market,root in [("spot",Path(args.spot_root)),("um",Path(args.um_root))]:
        files=sorted(root.rglob("*.csv.gz"))
        seen=set()
        for p in files:
            sym=p.name.replace(".csv.gz","")
            if sym in seen:continue
            seen.add(sym);tasks.append((market,p))
        print(f"[INPUT] {market} symbols={len(seen)}",flush=True)

    rows=[]
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        fut={ex.submit(process_symbol,m,p,out):(m,p.name) for m,p in tasks}
        for i,f in enumerate(as_completed(fut),1):
            m,n=fut[f]
            try:
                r=f.result();rows.append(r)
                print(f"[{i}/{len(tasks)}] {m} {r['symbol']} rows={r['rows']} +daily={r['daily_rows_added']} +api={r['api_rows_added']} gaps={r['gaps']}",flush=True)
            except Exception as e:
                rows.append({"market":m,"symbol":n.replace(".csv.gz",""),"fatal_error":str(e)})
                print(f"[ERR] {m} {n}: {e}",flush=True)

    manifest=pd.DataFrame(rows)
    manifest.to_csv(dist/"manifest.csv",index=False)
    good=manifest[manifest.get("fatal_error",pd.Series(index=manifest.index,dtype=object)).isna()].copy() if len(manifest) else manifest
    summary={}
    for market in ["spot","um"]:
        g=good[good.market==market] if len(good) and "market" in good else pd.DataFrame()
        summary[market]={
            "symbols":int(len(g)),"rows":int(g.rows.sum()) if len(g) else 0,
            "parquet_bytes":int(g.parquet_bytes.sum()) if len(g) else 0,
            "gaps":int(g.gaps.sum()) if len(g) else 0,
            "unaligned":int(g.unaligned.sum()) if len(g) else 0,
            "bad_ohlc":int(g.bad_ohlc.sum()) if len(g) else 0,
            "daily_rows_added":int(g.daily_rows_added.sum()) if len(g) else 0,
            "api_rows_added":int(g.api_rows_added.sum()) if len(g) else 0,
            "api_errors":int(g.api_error.notna().sum()) if len(g) else 0,
        }
    bundles=bundle_market(out,dist,"spot",16)+bundle_market(out,dist,"um",16)
    index={"generated_utc":datetime.now(timezone.utc).isoformat(),"summary":summary,"bundles":bundles,
           "fatal_errors":rows and sum(1 for r in rows if "fatal_error" in r) or 0}
    (dist/"dataset_index.json").write_text(json.dumps(index,indent=2),encoding="utf-8")
    total_bundle=sum(x["bytes"] for x in bundles)
    print("FINAL "+json.dumps({"summary":summary,"bundle_bytes":total_bundle,"assets":len(bundles),"fatal_errors":index["fatal_errors"]}),flush=True)
    if index["fatal_errors"]:
        raise SystemExit("fatal symbol conversion errors present")
    if summary["spot"]["symbols"]<100 or summary["um"]["symbols"]<100:
        raise SystemExit("unexpectedly small symbol universe")
    if summary["spot"]["unaligned"] or summary["um"]["unaligned"] or summary["spot"]["bad_ohlc"] or summary["um"]["bad_ohlc"]:
        raise SystemExit("alignment/OHLC validation failed")

if __name__=="__main__":main()
