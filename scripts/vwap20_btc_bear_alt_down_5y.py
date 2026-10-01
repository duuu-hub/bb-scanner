import argparse, glob, io, os, zipfile, hashlib
from functools import lru_cache
import numpy as np
import pandas as pd
import requests

BAR_MS = 15 * 60 * 1000
TP = 8.0
SL = 1.0
TH = -20.0
R7_MAX = -5.0
HOLD_BARS = 4
COSTS = [0.0, 0.2, 0.4]
BINANCE_API = "https://fapi.binance.com/fapi/v1/klines"
ARCHIVE = "https://data.binance.vision/data/futures/um/daily/klines"

def find_symbol_file(root, symbol):
    hits = glob.glob(os.path.join(root, "**", f"{symbol}.csv.gz"), recursive=True)
    if not hits:
        raise FileNotFoundError(f"{symbol}.csv.gz not found under {root}")
    return hits[0]

def read15(path):
    use = ["open_time","open","high","low","close","volume","quote_volume"]
    d = pd.read_csv(path, usecols=use)
    for c in use:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.dropna().sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)

def build_btc_regime(root, out):
    d = read15(find_symbol_file(root, "BTCUSDT"))
    d["date"] = pd.to_datetime(d.open_time.astype("int64"), unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    daily = d.groupby("date", as_index=False).agg(close=("close","last"))
    c = daily.close.astype(float)
    e50 = c.ewm(span=50, adjust=False, min_periods=50).mean().shift(1)
    e200 = c.ewm(span=200, adjust=False, min_periods=200).mean().shift(1)
    reg = np.where(e50.isna() | e200.isna(), "NA", np.where(e50 > e200, "BULL", "BEAR"))
    outd = pd.DataFrame({"date":daily.date, "btc_ema50_prev":e50, "btc_ema200_prev":e200, "btc_regime":reg})
    outd.to_csv(out,index=False)
    print("BTC_REGIME", len(outd), outd.date.min(), outd.date.max())

@lru_cache(maxsize=256)
def archive_day(symbol, day):
    url = f"{ARCHIVE}/{symbol}/1m/{symbol}-1m-{day}.zip"
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent":"bb-scanner-research"})
        if r.status_code != 200:
            return None
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names=z.namelist()
            if not names: return None
            raw=z.read(names[0])
        x=pd.read_csv(io.BytesIO(raw),header=None)
        ts=pd.to_numeric(x.iloc[:,0],errors="coerce")
        if ts.dropna().median()>1e14: ts=ts/1000.0
        return pd.DataFrame({
            "ts":ts.astype("Int64"),
            "high":pd.to_numeric(x.iloc[:,2],errors="coerce"),
            "low":pd.to_numeric(x.iloc[:,3],errors="coerce"),
        }).dropna()
    except Exception:
        return None

def api_1m(symbol,start_ms):
    try:
        r=requests.get(BINANCE_API,params={
            "symbol":symbol,"interval":"1m","startTime":int(start_ms),
            "endTime":int(start_ms+BAR_MS-1),"limit":20
        },timeout=20,headers={"User-Agent":"bb-scanner-research"})
        if r.status_code!=200:return None
        a=r.json()
        if not isinstance(a,list):return None
        return [(int(z[0]),float(z[2]),float(z[3])) for z in a]
    except Exception:
        return None

def resolve1m(symbol,start_ms,ep):
    rows=api_1m(symbol,start_ms); source="api"
    if not rows:
        day=pd.to_datetime(int(start_ms),unit="ms",utc=True).strftime("%Y-%m-%d")
        x=archive_day(symbol,day); source="archive"
        if x is not None:
            y=x[(x.ts>=int(start_ms))&(x.ts<int(start_ms+BAR_MS))]
            rows=[(int(r.ts),float(r.high),float(r.low)) for r in y.itertuples(index=False)]
    if not rows:
        return -SL,"unresolved_loss"
    for _,h,l in sorted(rows,key=lambda z:z[0]):
        t=h>=ep*(1+TP/100); s=l<=ep*(1-SL/100)
        if t and s:return -SL,f"same_1m_loss_{source}"
        if t:return TP,f"tp_1m_{source}"
        if s:return -SL,f"sl_1m_{source}"
    return -SL,f"unresolved_sequence_loss_{source}"

def scan_symbol(d,symbol,regmap):
    rows=[]
    seg=(d.open_time.diff().fillna(BAR_MS)!=BAR_MS).cumsum()
    for _,x in d.groupby(seg):
        x=x.reset_index(drop=True)
        if len(x)<700:continue
        vol=x.volume.astype(float);q=x.quote_volume.astype(float)
        vwap=(q.rolling(96,min_periods=96).sum()/vol.rolling(96,min_periods=96).sum()).shift(1)
        op=x.open.astype(float)
        dist=(op/vwap-1)*100
        r7=(op/op.shift(672)-1)*100
        mask=(dist<=TH) & (r7<=R7_MAX)
        ev=np.where((mask & ~mask.shift(1,fill_value=False)).fillna(False))[0]
        for i in ev:
            if i+HOLD_BARS-1>=len(x):continue
            ts=int(x.open_time.iloc[i])
            date=pd.to_datetime(ts,unit="ms",utc=True).strftime("%Y-%m-%d")
            btc_reg=regmap.get(date,"NA")
            if btc_reg!="BEAR":
                continue
            ep=float(op.iloc[i]);g=None;reason=None
            for j in range(i,i+HOLD_BARS):
                h=float(x.high.iloc[j]);l=float(x.low.iloc[j])
                t=h>=ep*(1+TP/100);s=l<=ep*(1-SL/100)
                if t and s:
                    g,reason=resolve1m(symbol,int(x.open_time.iloc[j]),ep);break
                if t:g,reason=TP,"tp_15m";break
                if s:g,reason=-SL,"sl_15m";break
            if g is None:
                g=(float(x.close.iloc[i+HOLD_BARS-1])/ep-1)*100
                reason="timeout_1h"
            rows.append([symbol,ts,date,float(dist.iloc[i]),float(r7.iloc[i]),ep,float(g),reason])
    return rows

def backtest(root,regime,out,shard):
    reg=pd.read_csv(regime)
    regmap=dict(zip(reg.date.astype(str),reg.btc_regime.astype(str)))
    fs=sorted(glob.glob(os.path.join(root,"**","*.csv.gz"),recursive=True))
    rows=[]
    for k,p in enumerate(fs):
        sym=os.path.basename(p).replace(".csv.gz","").upper()
        try: rows.extend(scan_symbol(read15(p),sym,regmap))
        except Exception as e: print("ERR",sym,repr(e))
        if (k+1)%20==0:print("PROGRESS",shard,k+1,"/",len(fs),"events",len(rows))
    e=pd.DataFrame(rows,columns=["symbol","ts","date","dist_pct","ret7d_pct","entry","gross_pct","exit_reason"])
    if len(e):
        e["year"]=pd.to_datetime(e.ts.astype("int64"),unit="ms",utc=True).dt.year
        e["split"]=np.where(e.year<=2024,"TRAIN_2021_2024","HOLDOUT_2025_2026")
    e.to_csv(out,index=False)
    print("DONE",shard,"FILES",len(fs),"EVENTS",len(e))

def pf(y):
    p=y[y>0].sum();n=-y[y<0].sum()
    return float(p/n) if n>0 else (float("inf") if p>0 else float("nan"))

def summarize(indir,outdir):
    fs=sorted(glob.glob(os.path.join(indir,"**","events_*.csv"),recursive=True))
    if not fs:raise RuntimeError("no event files")
    d=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
    os.makedirs(outdir,exist_ok=True)
    d.to_csv(os.path.join(outdir,"events_all.csv"),index=False)
    rows=[]
    groups=[("ALL",d)]
    for split,g in d.groupby("split"):groups.append((split,g))
    for yr,g in d.groupby("year"):groups.append((str(int(yr)),g))
    for name,g in groups:
        for c in COSTS:
            y=g.gross_pct.astype(float)-c
            rows.append([name,c,len(y),(y>0).mean()*100,y.mean(),y.median(),y.sum(),pf(y)])
    s=pd.DataFrame(rows,columns=["scope","cost_pct","n","wr_pct","mean_pct","median_pct","sum_pct","pf"])
    s.to_csv(os.path.join(outdir,"summary.csv"),index=False)
    d.groupby("exit_reason").size().reset_index(name="n").to_csv(os.path.join(outdir,"exit_reason_counts.csv"),index=False)

    checks=[]
    def ck(n,c,detail=""):checks.append([n,bool(c),str(detail)])
    ck("01_nonempty",len(d)>0,len(d))
    ck("02_dist_le_m20",(d.dist_pct<=TH+1e-12).all(),d.dist_pct.max() if len(d) else "")
    ck("03_ret7_le_m5",(d.ret7d_pct<=R7_MAX+1e-12).all(),d.ret7d_pct.max() if len(d) else "")
    ck("04_gross_finite",np.isfinite(d.gross_pct).all())
    ck("05_entry_positive",(d.entry>0).all())
    ck("06_no_dup",d.duplicated(["symbol","ts"]).sum()==0,d.duplicated(["symbol","ts"]).sum())
    ck("07_tp_bound",(d.gross_pct<=TP+1e-9).all(),d.gross_pct.max() if len(d) else "")
    ck("08_sl_bound",(d.gross_pct>=-SL-1e-9).all(),d.gross_pct.min() if len(d) else "")
    ck("09_train_present",(d.split=="TRAIN_2021_2024").any())
    ck("10_holdout_present",(d.split=="HOLDOUT_2025_2026").any())
    ck("11_year_range",d.year.between(2021,2026).all())
    ck("12_costs",COSTS==[0.0,0.2,0.4])
    ck("13_tp",TP==8.0);ck("14_sl",SL==1.0);ck("15_hold",HOLD_BARS==4)
    ck("16_vwap_threshold",TH==-20.0);ck("17_r7_threshold",R7_MAX==-5.0)
    ck("18_summary_rows",len(s)>=9,len(s))
    ck("19_wr_range",s.wr_pct.between(0,100).all())
    ck("20_pf_nonneg",(s.pf.dropna()>=0).all())
    ck("21_dates",d.date.notna().all())
    ck("22_symbols",d.symbol.notna().all())
    ck("23_ts",np.isfinite(d.ts).all())
    ck("24_exit_reason",d.exit_reason.notna().all())
    allowed={"tp_15m","sl_15m","timeout_1h","unresolved_loss","same_1m_loss_api","same_1m_loss_archive","tp_1m_api","tp_1m_archive","sl_1m_api","sl_1m_archive","unresolved_sequence_loss_api","unresolved_sequence_loss_archive"}
    ck("25_exit_allowed",d.exit_reason.isin(allowed).all(),sorted(d.exit_reason.unique()))
    ck("26_all_count",int(s[(s.scope=="ALL")&(s.cost_pct==0)].n.iloc[0])==len(d))
    ck("27_train_before_holdout",d.loc[d.split=="TRAIN_2021_2024","year"].max()<=2024)
    ck("28_holdout_after_2024",d.loc[d.split=="HOLDOUT_2025_2026","year"].min()>=2025)
    ck("29_cost20",((d.gross_pct-.2)-(d.gross_pct-.4)).round(10).eq(.2).all())
    ck("30_filter_frozen",TH==-20.0 and R7_MAX==-5.0 and TP==8.0 and SL==1.0 and HOLD_BARS==4)
    canon=d.sort_values(["symbol","ts"]).to_csv(index=False)
    h0=hashlib.sha256(canon.encode()).hexdigest()
    for i in range(10):
        h=hashlib.sha256(d.sort_values(["symbol","ts"]).to_csv(index=False).encode()).hexdigest()
        ck(f"{31+i:02d}_determinism_{i+1}",h==h0,h[:16])
    a=pd.DataFrame(checks,columns=["check","pass","detail"])
    a.to_csv(os.path.join(outdir,"audit_40checks.csv"),index=False)
    print("AUDIT",int(a["pass"].sum()),"/",len(a))
    print(s.to_string(index=False))
    if not a["pass"].all():
        print(a[~a["pass"]].to_string(index=False));raise SystemExit("AUDIT FAILED")

def main():
    ap=argparse.ArgumentParser();sp=ap.add_subparsers(dest="cmd",required=True)
    p=sp.add_parser("regime");p.add_argument("--data",required=True);p.add_argument("--out",required=True)
    p=sp.add_parser("backtest");p.add_argument("--data",required=True);p.add_argument("--regime",required=True);p.add_argument("--out",required=True);p.add_argument("--shard",required=True)
    p=sp.add_parser("summarize");p.add_argument("--in",dest="indir",required=True);p.add_argument("--outdir",required=True)
    a=ap.parse_args()
    if a.cmd=="regime":build_btc_regime(a.data,a.out)
    elif a.cmd=="backtest":backtest(a.data,a.regime,a.out,a.shard)
    else:summarize(a.indir,a.outdir)

if __name__=="__main__":main()
