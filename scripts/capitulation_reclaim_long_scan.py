import argparse,glob,json,io,urllib.request,zipfile,os,time,re
from datetime import datetime,timezone
import pandas as pd,numpy as np
from numba import njit

_ONE_MIN_CACHE={}
RS=(2.0,3.0,4.0,5.0,6.0)
PS=(("R4H",-4.0,4,16),("R4H",-6.0,4,16),("R4H",-8.0,4,16),
    ("R24H",-8.0,96,16),("R24H",-12.0,96,16),("R24H",-16.0,96,16))
RECLAIMS=(4,8)          # prior 1h / 2h high
WAIT_BARS=(16,32)       # 4h / 8h from crash event
MIN_STOP_PCT=0.35
MAX_STOP_PCT=8.0
MIN_RISK_EPS=1e-12

def _symbol(p):
    b=os.path.basename(p)
    if not b.endswith(".csv.gz"): raise RuntimeError(f"bad filename {b}")
    s=b[:-7].upper()
    if not re.fullmatch(r"[A-Z0-9]+USDT",s): raise RuntimeError(f"bad symbol {b}")
    return s

def load(p):
    d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]).sort_values("open_time")
    t=d.open_time.to_numpy(np.int64);o=d.open.to_numpy(float);h=d.high.to_numpy(float);l=d.low.to_numpy(float);c=d.close.to_numpy(float)
    if not len(t): raise RuntimeError("empty")
    if np.any(t%900000!=0) or (len(t)>1 and np.any(np.diff(t)<=0)): raise RuntimeError("bad time")
    if any(np.any(~np.isfinite(x)) or np.any(x<=0) for x in (o,h,l,c)): raise RuntimeError("bad OHLC")
    if np.any(h<np.maximum.reduce([o,l,c])) or np.any(l>np.minimum.reduce([o,h,c])): raise RuntimeError("bad geometry")
    return t,o,h,l,c

def segments(t):
    cut=np.r_[0,np.flatnonzero(np.diff(t)!=900000)+1,len(t)]
    return [(int(a),int(b)) for a,b in zip(cut[:-1],cut[1:])]

def _one_min(symbol,ts):
    ym=datetime.fromtimestamp(ts/1000,tz=timezone.utc).strftime("%Y-%m");key=(symbol,ym)
    if key in _ONE_MIN_CACHE:return _ONE_MIN_CACHE[key]
    u=f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/{symbol}-1m-{ym}.zip"
    last=None
    for attempt in range(4):
        try:
            raw=urllib.request.urlopen(u,timeout=60).read()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                mem=[n for n in z.namelist() if n.lower().endswith(".csv")]
                if len(mem)!=1:raise RuntimeError(f"unexpected members {mem}")
                d=pd.read_csv(z.open(mem[0]),header=None,dtype=str)
            if len(d) and not np.isfinite(pd.to_numeric(pd.Series([d.iat[0,0]]),errors="coerce").iat[0]):
                d=d.iloc[1:].reset_index(drop=True)
            if d.empty or d.shape[1]<4:raise RuntimeError("bad 1m")
            t=pd.to_numeric(d.iloc[:,0],errors="raise").to_numpy(np.int64)
            h=pd.to_numeric(d.iloc[:,2],errors="raise").to_numpy(float)
            l=pd.to_numeric(d.iloc[:,3],errors="raise").to_numpy(float)
            if np.any(t%60000!=0) or np.any(h<l) or np.any(~np.isfinite(h)) or np.any(~np.isfinite(l)):
                raise RuntimeError("bad 1m geometry")
            if len(t)>1 and np.any(np.diff(t)!=60000):
                _ONE_MIN_CACHE[key]=("data_gap","gap");return _ONE_MIN_CACHE[key]
            _ONE_MIN_CACHE[key]=(t,h,l);return _ONE_MIN_CACHE[key]
        except Exception as e:
            last=e
            if attempt<3:time.sleep(2**attempt)
    raise RuntimeError(f"1m fail {symbol} {ym}: {last}")

def resolve_1m(symbol,ts,tp,sl):
    d=_one_min(symbol,ts)
    if isinstance(d,tuple) and len(d)==2 and d[0]=="data_gap":return "data_gap"
    t,h,l=d;a=np.searchsorted(t,ts);z=np.searchsorted(t,ts+900000)
    if z-a!=15 or a>=len(t) or t[a]!=ts or t[z-1]!=ts+840000:return "data_gap"
    for j in range(a,z):
        ht=h[j]>=tp;hs=l[j]<=sl
        if ht and hs:return "loss"
        if hs:return "loss"
        if ht:return "win"
    return "exit_mismatch"

@njit(cache=True)
def first_exit(h,l,start,tp,sl):
    for j in range(start,len(h)):
        ht=h[j]>=tp;hs=l[j]<=sl
        if ht or hs:return j-start,ht,hs
    return -1,False,False

def rolling_prev_max(x,n):
    s=pd.Series(x)
    return s.shift(1).rolling(n,min_periods=n).max().to_numpy()

def evaluate_segment(t,o,h,l,c,symbol):
    rows=[]
    ret4=(c/np.r_[np.full(16,np.nan),c[:-16]]-1.0)*100.0
    ret24=(c/np.r_[np.full(96,np.nan),c[:-96]]-1.0)*100.0
    prev_high={n:rolling_prev_max(h,n) for n in RECLAIMS}
    N=len(t)
    for fam,thr,retbars,_ in PS:
        ret=ret4 if fam=="R4H" else ret24
        cond=np.isfinite(ret)&(ret<=thr)
        for reclaim_n in RECLAIMS:
            ph=prev_high[reclaim_n]
            for wait in WAIT_BARS:
                active=False;start=-1;event_low=np.nan;prev_cond=False
                cfg=f"{fam}{abs(thr):g}_RECLAIM{reclaim_n*15}M_WAIT{wait*15}M"
                for i in range(max(97,reclaim_n+1),N-1):
                    now=bool(cond[i])
                    if now and not prev_cond:
                        active=True;start=i;event_low=float(l[i])
                    prev_cond=now
                    if not active:continue
                    event_low=min(event_low,float(l[i]))
                    if i-start>wait:
                        active=False
                        continue
                    if not np.isfinite(ph[i]) or not (float(c[i])>float(ph[i])):
                        continue
                    # first reclaim after crash; enter next 15m OPEN.
                    ei=i+1
                    if int(t[ei])!=int(t[i])+900000:
                        active=False;continue
                    entry=float(o[ei]);sl=float(event_low)
                    if not sl<entry:
                        active=False;continue
                    stop_pct=(entry-sl)/entry*100.0
                    if stop_pct<MIN_STOP_PCT or stop_pct>MAX_STOP_PCT:
                        active=False;continue
                    sig_ts=int(t[i]+900000)   # decision available at this exact boundary
                    crash_ts=int(t[start])
                    for r in RS:
                        tp=entry+r*(entry-sl)
                        off,ht,hs=first_exit(h,l,ei,tp,sl)
                        if off<0:
                            out="unresolved_eod";xt=int(t[-1]+900000);pnl=np.nan
                        else:
                            j=ei+int(off);xt=int(t[j]+900000)
                            if ht and hs: out=resolve_1m(symbol,int(t[j]),tp,sl)
                            elif ht: out="win"
                            else: out="loss"
                            if out=="win":pnl=(tp-entry)/entry*100.0
                            elif out=="loss":pnl=(sl-entry)/entry*100.0
                            else:pnl=np.nan
                        rows.append({"config":cfg,"symbol":symbol,"crash_ts":crash_ts,"signal_ts":sig_ts,
                                     "entry_ts":int(t[ei]),"exit_ts":xt,"r":r,"outcome":out,
                                     "entry":entry,"sl":sl,"tp":tp,"stop_pct":stop_pct,"pnl_pct":pnl,
                                     "ret4_at_crash":float(ret4[start]) if np.isfinite(ret4[start]) else np.nan,
                                     "ret24_at_crash":float(ret24[start]) if np.isfinite(ret24[start]) else np.nan,
                                     "reclaim_level":float(ph[i]),"signal_close":float(c[i])})
                    active=False
    return rows

def smoke():
    e=100.;sl=96.
    for r in RS:
        tp=e+r*(e-sl)
        assert sl<e<tp and abs((tp-e)/(e-sl)-r)<1e-12
    print("CAP_RECLAIM_SMOKE_PASS",flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--events",default="events.csv.gz");ap.add_argument("--meta",default="meta.json");a=ap.parse_args()
    smoke();files=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert files
    rows=[];started=time.time()
    for z,p in enumerate(files,1):
        sym=_symbol(p)
        if sym=="BTCUSDT":continue
        dat=load(p)
        for aa,bb in segments(dat[0]):
            if bb-aa<220:continue
            rows.extend(evaluate_segment(*(x[aa:bb] for x in dat),sym))
        _ONE_MIN_CACHE.clear()
        print(f"PROGRESS file={z}/{len(files)} symbol={sym} events={len(rows)} elapsed_min={(time.time()-started)/60:.1f}",flush=True)
    pd.DataFrame(rows).to_csv(a.events,index=False,compression="gzip")
    meta={"definition":{
      "side":"LONG","tf":"15m execution","crash_families":[list(x) for x in PS],
      "reclaim":"first completed 15m close above prior 1h/2h rolling high after crash transition",
      "wait_bars":WAIT_BARS,"entry":"next 15m OPEN","sl":"lowest 15m low from crash trigger through reclaim signal",
      "stop_filter_pct":[MIN_STOP_PCT,MAX_STOP_PCT],"tp":"entry + R*(entry-SL)","R":RS,
      "chronology":"15m; same-bar TP+SL -> official Binance 1m; same-1m ambiguity LOSS",
      "btc_excluded":True,"costs":"not applied in ledger"},"files":len(files),"events":len(rows)}
    json.dump(meta,open(a.meta,"w"),indent=2)
    print("CAP_RECLAIM_SCAN_PASS",len(files),len(rows),flush=True)
if __name__=="__main__":main()
