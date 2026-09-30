import argparse,glob,json,io,urllib.request,zipfile,os,time,re
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

ENTRY_ATR=(0.0,.10,.20,.30,.40,.50,.60,.70,.80,.90,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.5,4.0,4.5,5.0,5.5,6.0)
ENTRY_PCT=(0.0,.1,.2,.3,.4,.5,.75,1.0,1.5,2.0,3.0,4.0,5.0,6.0,7.0,8.0,9.0,10.0)
SL_BUFFER_ATR=(0.0,.10,.20,.30,.50)
RS=(.5,.75,1.,1.25,1.5,2.,2.5,3.,3.25,3.5,3.75,4.,5.,6.,7.,8.,9.,10.,11.,12.,13.,14.,15.,16.,17.,18.,19.,20.,21.,22.,23.,24.,25.,26.,27.,28.,29.,30.)
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


CANDIDATES={
 "P0.5_SB0_TP6":("P",0.5,0.0,6.0),
 "P0.75_SB0_TP6":("P",0.75,0.0,6.0),
 "P1_SB0_TP5":("P",1.0,0.0,5.0),
 "P1.5_SB0_TP5":("P",1.5,0.0,5.0),
 "P2_SB0_TP6":("P",2.0,0.0,6.0),
}
BTC_BLACKLIST=set()

def _resolve_event(t,h,l,fs,fill,tp,sl,symbol,is_taker):
    scan_start=fs
    if not is_taker:
        fill_tp=l[fs]<=tp; fill_sl=h[fs]>=sl
        if fill_tp or fill_sl:
            rr=_resolve_1m(symbol,int(t[fs]),tp,sl,False,fill,float(h[fs]),float(l[fs]))
            if rr in ("win","loss"): return rr,int(t[fs]+900000)
            if rr in ("data_gap","entry_mismatch"): return rr,None
            if rr!="continue": raise RuntimeError(f"unexpected maker result {symbol} {int(t[fs])}: {rr}")
        scan_start=fs+1
    off,hit_tp,hit_sl=_first_exit(h,l,scan_start,tp,sl,False)
    if off<0:return "unresolved_eod",int(t[-1]+900000)
    exit_i=scan_start+int(off); exit_ts=int(t[exit_i]+900000)
    if hit_tp and hit_sl:
        rr=_resolve_1m(symbol,int(t[exit_i]),tp,sl,False,None,float(h[exit_i]),float(l[exit_i]))
        if rr in ("win","loss"):return rr,exit_ts
        if rr in ("data_gap","exit_mismatch"):return rr,None
        raise RuntimeError(f"unexpected collision result {symbol} {int(t[exit_i])}: {rr}")
    return ("win" if hit_tp else "loss"),exit_ts

def evaluate_ledger(t,o,h,l,c,m,symbol):
    rt,ro,rh,rl,rc=resample(t,o,h,l,c,m); sar,bull=psar_open_projection(rh,rl); n=len(rt)
    prev=np.r_[np.nan,rc[:-1]]
    tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prev),np.abs(rl-prev)))
    atr_closed=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()
    atr_open=np.r_[np.nan,atr_closed[:-1]]
    pos=np.searchsorted(t,rt)
    if len(pos) and (np.any(pos>=len(t)) or np.any(t[pos]!=rt)):raise RuntimeError("strategy-bar mapping mismatch")
    rows=[]; counts={k:{"setup":0,"fills":0,"win":0,"loss":0,"data_gap":0,"entry_mismatch":0,"exit_mismatch":0,"unresolved_eod":0} for k in CANDIDATES}
    for i in range(max(PSAR_BURNIN_BARS,15),n):
        if bool(bull[i]):continue
        s=float(sar[i]);a0=float(atr_open[i])
        if not np.isfinite(s) or not np.isfinite(a0) or a0<=0:continue
        start=int(pos[i]); signal_ts=int(rt[i])
        for name,(typ,ent,sb,r) in CANDIDATES.items():
            counts[name]["setup"]+=1
            e=s-ent*a0 if typ=="E" else s*(1-ent/100.0)
            crossed=bool(ro[i]>=e)
            if crossed:
                fs=start;fill=float(ro[i]);order="TAKER"
            else:
                hits=np.flatnonzero((l[start:min(start+m,len(t))]<=e)&(h[start:min(start+m,len(t))]>=e))
                if not hits.size:continue
                fs=start+int(hits[0]);fill=float(e);order="MAKER"
            sl=s+sb*a0
            if not fill<sl:continue
            risk=abs(fill-sl)
            if risk<=MIN_RISK_EPS*max(1.,abs(fill),abs(sl)):continue
            tp=s*(1-r/100.0)
            outcome,exit_ts=_resolve_event(t,h,l,fs,fill,tp,sl,symbol,crossed)
            if outcome=="entry_mismatch":
                counts[name]["entry_mismatch"]+=1;continue
            counts[name]["fills"]+=1
            counts[name][outcome]+=1
            if outcome in ("data_gap","exit_mismatch"):continue
            pnl_pct=None
            if outcome in ("win","loss"):
                exit_px=tp if outcome=="win" else sl
                pnl_pct=(fill-exit_px)/fill*100.0
            rows.append({
              "variant":name,"symbol":symbol,"signal_ts":signal_ts,"fill_ts":int(t[fs]),
              "exit_ts":exit_ts,"order":order,"outcome":outcome,
              "entry_type":typ,"entry_param":ent,"sb_atr":sb,"r":r,
              "fill":fill,"sl":sl,"tp":tp,"stop_pct":risk/fill*100.0,"pnl_pct":pnl_pct
            })
    return rows,counts

ap=argparse.ArgumentParser()
ap.add_argument("--data",default="data");ap.add_argument("--events",default="events.csv.gz");ap.add_argument("--meta",default="meta.json")
ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);ap.add_argument("--max-files",type=int,default=0)
a=ap.parse_args()
all_files=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert all_files
files=[p for j,p in enumerate(all_files) if j%a.shards==a.shard]
if a.max_files>0:files=files[:a.max_files]
events=[]; agg={k:{kk:0 for kk in ("setup","fills","win","loss","data_gap","entry_mismatch","exit_mismatch","unresolved_eod")} for k in CANDIDATES}
started=time.time()
for z,p in enumerate(files,1):
    sym=_symbol(p)
    if sym in BTC_BLACKLIST:
        print("SKIP_BLACKLIST",sym,flush=True);continue
    data=load(p);rr=[];cc=None
    for aa,bb in contiguous_segments(data[0]):
        if bb-aa<4*(PSAR_BURNIN_BARS+1):continue
        part=tuple(x[aa:bb] for x in data)\n        if len(resample(*part,4)[0])<=PSAR_BURNIN_BARS:continue\n        x,cnt=evaluate_ledger(*part,4,sym);rr.extend(x)
        for k,v in cnt.items():
            for kk,vv in v.items():agg[k][kk]+=vv
    _ONE_MIN_CACHE.clear();events.extend(rr)
    print(f"PROGRESS anchor-ledger shard={a.shard}/{a.shards} file={z}/{len(files)} symbol={sym} events={len(events)} elapsed_min={(time.time()-started)/60:.1f}",flush=True)
pd.DataFrame(events).to_csv(a.events,index=False,compression="gzip")
meta={"definition":{
 "source_engine_file":"scripts/psar_1h_psar_anchor_compare.py","source_engine_blob":os.environ.get("PSAR_SOURCE_BLOB","unknown"),
 "source_data_run":"36095439671","tf":"1h","side":"SHORT","candidates":CANDIDATES,
 "btc_blacklist":["BTCUSDT"],"order_live":"same 1H OPEN",
 "tp":"PSAR_ref*(1-TP_pct/100); actual fill never moves TP","sl":"open-time PSAR_ref + SB*prior ATR",
 "chronology":"official Binance 1m authority for ambiguous fill/exit bars; same-1m ambiguity=LOSS",
 "exit_ts":"parent 15m bar end for conservative capital reuse","costs":"not applied in ledger; portfolio stage applies stress"
 },"files":len(files),"events":len(events),"counts":agg}
json.dump(meta,open(a.meta,"w"),indent=2)
print("ANCHOR_LEDGER_PASS",len(files),len(events),flush=True)
