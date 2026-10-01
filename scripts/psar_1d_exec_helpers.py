import io,urllib.request,zipfile,os,time,re
from datetime import datetime,timezone
import pandas as pd,numpy as np
from numba import njit
_ONE_MIN_CACHE={}
def _symbol(p):
    b=os.path.basename(p)
    if not b.endswith(".csv.gz"):raise RuntimeError(f"unexpected data filename {b}")
    sym=b[:-7].upper()
    if not re.fullmatch(r"[A-Z0-9]+USDT",sym):raise RuntimeError(f"cannot safely parse Binance symbol from {b}")
    return sym
def _one_min(symbol,ts):
    ym=datetime.fromtimestamp(ts/1000,tz=timezone.utc).strftime("%Y-%m"); key=(symbol,ym)
    if key in _ONE_MIN_CACHE:return _ONE_MIN_CACHE[key]
    u=f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/{symbol}-1m-{ym}.zip"
    last=None
    for attempt in range(4):
        try:
            raw=urllib.request.urlopen(u,timeout=60).read()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                members=[n for n in z.namelist() if n.lower().endswith(".csv")]
                if len(members)!=1:raise RuntimeError(f"unexpected 1m ZIP members: {members}")
                d=pd.read_csv(z.open(members[0]),header=None,dtype=str)
            if d.shape[1] < 4:raise RuntimeError(f"invalid 1m CSV schema: {d.shape}")
            # Binance monthly archives exist both with and without a CSV header.
            # Detect only a non-numeric first timestamp as a header; never drop a real candle.
            first_ts=pd.to_numeric(pd.Series([d.iat[0,0]]),errors="coerce").iat[0] if len(d) else np.nan
            if len(d) and not np.isfinite(first_ts): d=d.iloc[1:].reset_index(drop=True)
            if len(d)==0:raise RuntimeError("empty 1m archive")
            try:
                vt=pd.to_numeric(d.iloc[:,0],errors="raise").to_numpy(np.int64)
                vh=pd.to_numeric(d.iloc[:,2],errors="raise").to_numpy(float)
                vl=pd.to_numeric(d.iloc[:,3],errors="raise").to_numpy(float)
            except Exception as e:
                raise RuntimeError(f"invalid numeric data in 1m CSV: {e}") from e
            v=(vt,vh,vl)
            if len(v[0])==0: raise RuntimeError("empty 1m archive")
            if not np.all(np.isfinite(v[1])) or not np.all(np.isfinite(v[2])):raise RuntimeError("non-finite 1m high/low")
            if np.any(v[1]<=0) or np.any(v[2]<=0) or np.any(v[1]<v[2]):raise RuntimeError("invalid 1m high/low")
            if np.any(v[0]%60000!=0):raise RuntimeError("misaligned 1m timestamps")
            first_ym=datetime.fromtimestamp(int(v[0][0])/1000,tz=timezone.utc).strftime("%Y-%m")
            last_ym=datetime.fromtimestamp(int(v[0][-1])/1000,tz=timezone.utc).strftime("%Y-%m")
            if first_ym!=ym or last_ym!=ym:raise RuntimeError(f"1m archive month mismatch requested={ym} actual={first_ym}..{last_ym}")
            if len(v[0])>1 and np.any(np.diff(v[0])!=60000):
                bad=np.flatnonzero(np.diff(v[0])!=60000)[:5]
                return ("data_gap", f"1m timestamp gap/duplicate at rows {bad.tolist()}")
            _ONE_MIN_CACHE[key]=v;return v
        except Exception as e:
            last=e
            if attempt<3: time.sleep(2**attempt)
    raise RuntimeError(f"1m download failed after retries {symbol} {ym}: {last}")
def _resolve_1m(symbol,ts,tp,sl,long,entry=None,source_high=None,source_low=None):
    d=_one_min(symbol,ts)
    if isinstance(d,tuple) and len(d)==2 and d[0]=="data_gap":return "data_gap"
    t,h,l=d;a=np.searchsorted(t,ts);z=np.searchsorted(t,ts+900000)
    if z-a!=15 or a>=len(t) or t[a]!=ts or t[z-1]!=ts+840000:
        raise RuntimeError(f"incomplete 1m window {symbol} {ts}: count={z-a}")
    # Chronology windows use the official Binance 1m archive as the authority.
    # The cached 15m source may be an older Binance snapshot and can differ by ticks
    # after exchange-side historical kline corrections. Never mix snapshots by
    # rejecting or altering 1m chronology based on stale 15m H/L. Window completeness,
    # timestamp continuity and OHLC validity are enforced in _one_min above.
    entered=entry is None;entry_seen=entered
    for j in range(a,z):
        if not entered:
            touched=(l[j]<=entry) if long else (h[j]>=entry)
            if not touched:continue
            # Entry first appears inside this 1m candle. OHLC cannot prove whether
            # TP/SL in the same candle happened before or after the maker fill.
            # Any same-1m exit touch is therefore ambiguous and conservatively a loss.
            ht=h[j]>=tp if long else l[j]<=tp; hs=l[j]<=sl if long else h[j]>=sl
            if ht or hs:return "loss"
            entered=True;entry_seen=True
            continue
        ht=h[j]>=tp if long else l[j]<=tp; hs=l[j]<=sl if long else h[j]>=sl
        if ht and hs:return "loss"
        if hs:return "loss"
        if ht:return "win"
    if entry is not None:
        return "continue" if entry_seen else "entry_mismatch"
    return "exit_mismatch"

def _design_tp(entry_target,sl,r,long):
    """TP stays anchored to the designed entry target even after a favorable OPEN fill."""
    design_risk=abs(entry_target-sl)
    return entry_target+r*design_risk if long else entry_target-r*design_risk

def _book_resolved(q,outcome,fill,tp,sl,design_risk):
    """Book realized P/L in units of the original designed risk."""
    if outcome=="win":
        q["win"]+=1
        q["gross_profit_design_R"]+=abs(tp-fill)/design_risk
    elif outcome=="loss":
        q["loss"]+=1
        q["gross_loss_design_R"]+=abs(fill-sl)/design_risk
    else:
        raise RuntimeError(f"cannot book unresolved outcome {outcome}")

ENTRY_ATR=(0.0,.10,.20,.30,.40,.50,.60,.70,.80,.90,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.5,4.0,4.5,5.0,5.5,6.0)
ENTRY_PCT=(0.0,.1,.2,.3,.4,.5,.75,1.0,1.5,2.0,3.0,4.0,5.0,6.0,7.0,8.0,9.0,10.0)
SL_BUFFER_ATR=(0.0,.10,.20,.30,.50)
RS=(.5,.75,1.,1.25,1.5,2.,2.5,3.,4.)
MIN_RISK_EPS=1e-12
PSAR_BURNIN_BARS=100

@njit(cache=True)
def _first_exit(h,l,start,tp,sl,long):
    """Return the first post-start 15m exit bar and which levels it touches.

    The legacy engine built full suffix boolean arrays for TP and SL, then compared
    their first hit indices. The earliest bar touching either level is sufficient:
    if both are touched on that same bar, the caller resolves chronology on 1m.
    """
    for j in range(start,len(h)):
        hit_tp=(h[j]>=tp) if long else (l[j]<=tp)
        hit_sl=(l[j]<=sl) if long else (h[j]>=sl)
        if hit_tp or hit_sl:
            return j-start,hit_tp,hit_sl
    return -1,False,False

def psar_open_projection(h,l,af0=.02,step=.02,afmax=.2):
    n=len(h); out=np.full(n,np.nan); bull=np.ones(n,bool)
    if n<3:return out,bull
    sar=l[0]; trend=True; ep=h[1]; af=af0
    out[1]=sar; bull[1]=trend
    for i in range(2,n):
        # value available at bar i OPEN: derived only from bars <= i-1
        z=sar+af*(ep-sar)
        if trend:z=min(z,l[i-1],l[i-2])
        else:z=max(z,h[i-1],h[i-2])
        out[i]=z; bull[i]=trend
        # only after bar i closes may its H/L change next bar's state
        if trend:
            if l[i]<z: trend=False;sar=ep;ep=l[i];af=af0
            else:
                sar=z
                if h[i]>ep:ep=h[i];af=min(af+step,afmax)
        else:
            if h[i]>z: trend=True;sar=ep;ep=h[i];af=af0
            else:
                sar=z
                if l[i]<ep:ep=l[i];af=min(af+step,afmax)
    return out,bull

def load(p):
    d=pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]).sort_values("open_time")
    t=d["open_time"].to_numpy(np.int64)
    if len(t)==0:raise RuntimeError("empty 15m input")
    if np.any(t%900000!=0):raise RuntimeError("misaligned 15m timestamps")
    if len(t)>1 and np.any(np.diff(t)<=0):
        bad=np.flatnonzero(np.diff(t)<=0)[:5]
        raise RuntimeError(f"15m timestamp duplicate/non-monotonic at rows {bad.tolist()}")
    vals=tuple(d[x].to_numpy(float) for x in ["open","high","low","close"])
    o,h,l,c=vals
    if not all(np.all(np.isfinite(x)) for x in vals):raise RuntimeError("non-finite OHLC")
    if any(np.any(x<=0) for x in vals):raise RuntimeError("non-positive OHLC")
    if np.any(h<np.maximum.reduce([o,l,c])) or np.any(l>np.minimum.reduce([o,h,c])):
        raise RuntimeError("invalid OHLC geometry")
    return (t,)+vals

def contiguous_segments(t):
    if len(t)==0:return []
    cut=np.r_[0,np.flatnonzero(np.diff(t)!=900000)+1,len(t)]
    return [(int(a),int(b)) for a,b in zip(cut[:-1],cut[1:])]

def resample(t,o,h,l,c,m=16):
    bucket=t//(900000*m); cut=np.r_[0,np.flatnonzero(bucket[1:]!=bucket[:-1])+1,len(t)]
    st=cut[:-1];en=cut[1:];good=(en-st)==m
    st=st[good];en=en[good]
    if len(st):
        span=900000*m
        ok=np.array([t[a]%span==0 and np.all(np.diff(t[a:b])==900000) for a,b in zip(st,en)],dtype=bool)
        st=st[ok];en=en[ok]
    rh=np.array([np.max(h[a:b]) for a,b in zip(st,en)],dtype=float)
    rl=np.array([np.min(l[a:b]) for a,b in zip(st,en)],dtype=float)
    return t[st],o[st],rh,rl,c[en-1]


