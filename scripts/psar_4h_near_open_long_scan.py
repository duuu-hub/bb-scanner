import argparse,glob,json,io,urllib.request,zipfile,os,time,re
from datetime import datetime,timezone
import pandas as pd,numpy as np
from numba import njit

_ONE_MIN_CACHE={}
RS=(2.0,3.0,4.0,5.0,6.0,8.0)
PSAR_BURNIN_BARS=100
MAX_DIST_PCT=3.0
MIN_RISK_EPS=1e-12

def _symbol(p):
    b=os.path.basename(p)
    if not b.endswith(".csv.gz"): raise RuntimeError(f"unexpected data filename {b}")
    sym=b[:-7].upper()
    if not re.fullmatch(r"[A-Z0-9]+USDT",sym): raise RuntimeError(f"cannot parse symbol {b}")
    return sym

def _one_min(symbol,ts):
    ym=datetime.fromtimestamp(ts/1000,tz=timezone.utc).strftime("%Y-%m"); key=(symbol,ym)
    if key in _ONE_MIN_CACHE:return _ONE_MIN_CACHE[key]
    url=f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/{symbol}-1m-{ym}.zip"
    last=None
    for attempt in range(4):
        try:
            raw=urllib.request.urlopen(url,timeout=60).read()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                mem=[n for n in z.namelist() if n.lower().endswith(".csv")]
                if len(mem)!=1:raise RuntimeError(f"unexpected 1m members {mem}")
                d=pd.read_csv(z.open(mem[0]),header=None,dtype=str)
            if len(d) and not np.isfinite(pd.to_numeric(pd.Series([d.iat[0,0]]),errors="coerce").iat[0]):
                d=d.iloc[1:].reset_index(drop=True)
            if d.empty or d.shape[1]<4:raise RuntimeError("invalid/empty 1m")
            t=pd.to_numeric(d.iloc[:,0],errors="raise").to_numpy(np.int64)
            h=pd.to_numeric(d.iloc[:,2],errors="raise").to_numpy(float)
            l=pd.to_numeric(d.iloc[:,3],errors="raise").to_numpy(float)
            if np.any(t%60000!=0) or np.any(~np.isfinite(h)) or np.any(~np.isfinite(l)) or np.any(h<l):
                raise RuntimeError("invalid 1m geometry")
            if len(t)>1 and np.any(np.diff(t)!=60000):
                _ONE_MIN_CACHE[key]=("data_gap","1m gap/duplicate");return _ONE_MIN_CACHE[key]
            _ONE_MIN_CACHE[key]=(t,h,l);return _ONE_MIN_CACHE[key]
        except Exception as e:
            last=e
            if attempt<3:time.sleep(2**attempt)
    raise RuntimeError(f"1m download failed {symbol} {ym}: {last}")

def _resolve_1m(symbol,ts,tp,sl):
    d=_one_min(symbol,ts)
    if isinstance(d,tuple) and len(d)==2 and d[0]=="data_gap":return "data_gap"
    t,h,l=d;a=np.searchsorted(t,ts);z=np.searchsorted(t,ts+900000)
    if z-a!=15 or a>=len(t) or t[a]!=ts or t[z-1]!=ts+840000:return "data_gap"
    for j in range(a,z):
        hit_tp=h[j]>=tp;hit_sl=l[j]<=sl
        if hit_tp and hit_sl:return "loss"
        if hit_sl:return "loss"
        if hit_tp:return "win"
    return "exit_mismatch"

@njit(cache=True)
def _first_exit(h,l,start,tp,sl):
    for j in range(start,len(h)):
        hit_tp=h[j]>=tp;hit_sl=l[j]<=sl
        if hit_tp or hit_sl:return j-start,hit_tp,hit_sl
    return -1,False,False

def psar_open_projection(h,l,af0=.02,step=.02,afmax=.2):
    n=len(h);out=np.full(n,np.nan);bull=np.ones(n,bool)
    if n<3:return out,bull
    sar=l[0];trend=True;ep=h[1];af=af0
    out[1]=sar;bull[1]=trend
    for i in range(2,n):
        z=sar+af*(ep-sar)
        if trend:z=min(z,l[i-1],l[i-2])
        else:z=max(z,h[i-1],h[i-2])
        out[i]=z;bull[i]=trend
        if trend:
            if l[i]<z:trend=False;sar=ep;ep=l[i];af=af0
            else:
                sar=z
                if h[i]>ep:ep=h[i];af=min(af+step,afmax)
        else:
            if h[i]>z:trend=True;sar=ep;ep=h[i];af=af0
            else:
                sar=z
                if l[i]<ep:ep=l[i];af=min(af+step,afmax)
    return out,bull

def load(p):
    d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]).sort_values("open_time")
    t=d.open_time.to_numpy(np.int64);o=d.open.to_numpy(float);h=d.high.to_numpy(float);l=d.low.to_numpy(float);c=d.close.to_numpy(float)
    if not len(t):raise RuntimeError("empty 15m")
    if np.any(t%900000!=0) or (len(t)>1 and np.any(np.diff(t)<=0)):raise RuntimeError("bad 15m time")
    if any(np.any(~np.isfinite(x)) or np.any(x<=0) for x in (o,h,l,c)):raise RuntimeError("bad OHLC")
    if np.any(h<np.maximum.reduce([o,l,c])) or np.any(l>np.minimum.reduce([o,h,c])):raise RuntimeError("bad OHLC geometry")
    return t,o,h,l,c

def contiguous_segments(t):
    if len(t)==0:return []
    cut=np.r_[0,np.flatnonzero(np.diff(t)!=900000)+1,len(t)]
    return [(int(a),int(b)) for a,b in zip(cut[:-1],cut[1:])]

def resample(t,o,h,l,c,m=16):
    bucket=t//(900000*m);cut=np.r_[0,np.flatnonzero(bucket[1:]!=bucket[:-1])+1,len(t)]
    st=cut[:-1];en=cut[1:];good=(en-st)==m;st=st[good];en=en[good]
    if len(st):
        span=900000*m
        ok=np.array([t[a]%span==0 and np.all(np.diff(t[a:b])==900000) for a,b in zip(st,en)],bool)
        st=st[ok];en=en[ok]
    return t[st],o[st],np.array([h[a:b].max() for a,b in zip(st,en)]),np.array([l[a:b].min() for a,b in zip(st,en)]),c[en-1]

def evaluate_segment(t,o,h,l,c,symbol):
    rt,ro,rh,rl,rc=resample(t,o,h,l,c,16)
    sar,bull=psar_open_projection(rh,rl);n=len(rt)
    prev=np.r_[np.nan,rc[:-1]]
    tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prev),np.abs(rl-prev)))
    atr_closed=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()
    atr_open=np.r_[np.nan,atr_closed[:-1]]
    pos=np.searchsorted(t,rt);setups=[];events=[]
    for i in range(max(PSAR_BURNIN_BARS,18),n):
        if not bool(bull[i]):continue
        s=float(sar[i]);a0=float(atr_open[i]);fill=float(ro[i])
        if not np.isfinite(s) or not np.isfinite(a0) or a0<=0 or fill<=0:continue
        signal_ts=int(rt[i]);dist=(fill-s)/fill*100.0;atr_pct=a0/fill*100.0
        setups.append({"symbol":symbol,"signal_ts":signal_ts,"dist_pct":dist,"atr_pct":atr_pct})
        if not (dist>0 and dist<=MAX_DIST_PCT):continue
        risk=fill-s
        if risk<=MIN_RISK_EPS*max(1.,abs(fill),abs(s)):continue
        fs=int(pos[i])
        if fs>=len(t) or int(t[fs])!=signal_ts:raise RuntimeError("4H->15m mapping mismatch")
        for r in RS:
            tp=fill+r*risk;sl=s
            off,hit_tp,hit_sl=_first_exit(h,l,fs,tp,sl)
            if off<0:
                outcome="unresolved_eod";exit_ts=int(t[-1]+900000)
            else:
                ei=fs+int(off);exit_ts=int(t[ei]+900000)
                if hit_tp and hit_sl:outcome=_resolve_1m(symbol,int(t[ei]),tp,sl)
                elif hit_tp:outcome="win"
                else:outcome="loss"
            pnl=None
            if outcome=="win":pnl=(tp-fill)/fill*100.0
            elif outcome=="loss":pnl=(sl-fill)/fill*100.0
            events.append({"symbol":symbol,"signal_ts":signal_ts,"fill_ts":signal_ts,"exit_ts":exit_ts,
                           "r":r,"outcome":outcome,"fill":fill,"psar":s,"tp":tp,
                           "dist_pct":dist,"atr_pct":atr_pct,"pnl_pct":pnl})
    return setups,events

def smoke():
    fill=100.;sl=98.;risk=fill-sl
    for r in RS:
        tp=fill+r*risk
        assert sl<fill<tp and abs((tp-fill)/(fill-sl)-r)<1e-12
    print("NEAR_LONG_SMOKE_PASS",flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--setups",default="setups.csv.gz");ap.add_argument("--events",default="events.csv.gz");ap.add_argument("--meta",default="meta.json");a=ap.parse_args()
    smoke();files=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert files
    setups=[];events=[];started=time.time()
    for z,p in enumerate(files,1):
        sym=_symbol(p)
        if sym=="BTCUSDT":continue
        dat=load(p);ss=[];ee=[]
        for aa,bb in contiguous_segments(dat[0]):
            if bb-aa<16*(PSAR_BURNIN_BARS+1):continue
            s,e=evaluate_segment(*(x[aa:bb] for x in dat),sym);ss.extend(s);ee.extend(e)
        setups.extend(ss);events.extend(ee);_ONE_MIN_CACHE.clear()
        print(f"PROGRESS file={z}/{len(files)} symbol={sym} setups={len(setups)} events={len(events)} elapsed_min={(time.time()-started)/60:.1f}",flush=True)
    pd.DataFrame(setups).to_csv(a.setups,index=False,compression="gzip")
    pd.DataFrame(events).to_csv(a.events,index=False,compression="gzip")
    meta={"definition":{"tf":"4h","side":"LONG","entry":"strategy 4H OPEN market/taker only","psar":"projected at OPEN from closed history only",
      "atr":"SMA14 true range through prior closed 4H bar","eligible_actual_distance":"0< (fill-PSAR_ref)/fill <=3%",
      "sl":"frozen PSAR_ref","tp":"actual_fill + R*abs(actual_fill-SL)","R":RS,"btc_excluded":True,
      "chronology":"15m; TP+SL same parent bar resolved with official Binance 1m; same 1m ambiguity=LOSS","costs":"not applied in ledger"},
      "files":len(files),"setups":len(setups),"events":len(events)}
    json.dump(meta,open(a.meta,"w"),indent=2)
    print("NEAR_LONG_SCAN_PASS",len(files),len(setups),len(events),flush=True)
if __name__=="__main__":main()
