import argparse,glob,json,io,urllib.request,zipfile,os,time,re
from datetime import datetime,timezone
import pandas as pd,numpy as np
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
    return "continue" if entry is not None and entry_seen else "data_error"

ENTRY_ATR=(0.0,.10,.20,.30,.40,.50,.60,.70,.80,.90,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.5,4.0,4.5,5.0,5.5,6.0)
ENTRY_PCT=(0.0,.1,.2,.3,.4,.5,.75,1.0,1.5,2.0,3.0,4.0,5.0,6.0,7.0,8.0,9.0,10.0)
SL_BUFFER_ATR=(0.0,.10,.20,.30,.50)
RS=(.5,.75,1.,1.25,1.5,2.,2.5,3.,4.)
MIN_RISK_EPS=1e-12
PSAR_BURNIN_BARS=100

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

def evaluate(t,o,h,l,c,m,symbol=None):
    rt,ro,rh,rl,rc=resample(t,o,h,l,c,m); sar,bull=psar_open_projection(rh,rl); n=len(rt)
    # ATR available at bar i open = ATR14 through bar i-1 only
    prev=np.r_[np.nan,rc[:-1]]
    tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prev),np.abs(rl-prev)))
    atr_closed=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()
    atr_open=np.r_[np.nan,atr_closed[:-1]]
    pos=np.searchsorted(t,rt)
    if len(pos) and (np.any(pos>=len(t)) or np.any(t[pos]!=rt)):raise RuntimeError("strategy-bar to 15m timestamp mapping mismatch")
    out={}
    for i in range(max(15,PSAR_BURNIN_BARS),n):
        s=sar[i];a0=atr_open[i]
        if not np.isfinite(s) or not np.isfinite(a0) or a0<=0:continue
        b=bool(bull[i]);side="LONG" if b else "SHORT"; start=pos[i]; end=len(t)
        # spider is live immediately from this bar open
        for em in ENTRY_ATR:
            e=s+(em*a0 if b else -em*a0)
            # Canonical open-time execution: favorable crossed target becomes taker at OPEN.
            # Buy limit must be below open; sell limit must be above open.
            crossed=(b and ro[i]<=e) or ((not b) and ro[i]>=e)
            if crossed:
                fs=start; fill=ro[i]
            else:
                hits=np.flatnonzero((l[start:min(start+m,len(t))]<=e)&(h[start:min(start+m,len(t))]>=e))
                if not hits.size:continue
                fs=start+int(hits[0]); fill=e
            ph=h[fs:end];pl=l[fs:end]
            is_taker=bool(crossed)
            for sb in SL_BUFFER_ATR:
                sl=s-(sb*a0 if b else -sb*a0)
                if (b and not fill>sl) or ((not b) and not fill<sl):continue
                risk=abs(fill-sl)
                if risk<=MIN_RISK_EPS*max(1.,abs(fill),abs(sl)):continue
                for r in RS:
                    tp=fill+r*risk if b else fill-r*risk
                    th=(ph>=tp) if b else (pl<=tp); sh=(pl<=sl) if b else (ph>=sl)
                    ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 10**9;is_=si[0] if si.size else 10**9
                    k=f"E{em:g}|SB{sb:g}|R{r:g}|{side}";q=out.setdefault(k,{"fills":0,"taker":0,"maker":0,"win":0,"loss":0,"collision_15m":0,"resolved_1m":0,"collision_1m_loss":0,"data_gap":0,"unresolved_eod":0})
                    q["fills"]+=1; q["taker" if is_taker else "maker"]+=1
                    # Taker fills at strategy-TF OPEN, so the full 15m fill bar is post-entry.
                    # Maker fills intrabar. With 15m OHLC only, a TP touch on the fill bar
                    # may have occurred before entry. Never credit that as a win.
                    if not is_taker:
                        fill_tp=bool(th[0]); fill_sl=bool(sh[0])
                        if fill_tp or fill_sl:
                            # Exact maker chronology: no fill-bar TP/SL may count before the entry touch.
                            # Resolve every fill-bar exit candidate on official 1m data starting at entry.
                            q["collision_15m"]+=int(fill_tp and fill_sl)
                            rr=_resolve_1m(symbol,int(t[fs]),tp,sl,b,fill,float(h[fs]),float(l[fs]))
                            q["resolved_1m"]+=int(rr in ("win","loss")); q["collision_1m_loss"]+=int(rr=="loss" and fill_tp and fill_sl)
                            if rr=="win":q["win"]+=1;continue
                            if rr=="loss":q["loss"]+=1;continue
                            if rr=="data_gap":q["data_gap"]+=1;continue
                            if rr=="data_error":raise RuntimeError(f"1m chronology mismatch {symbol} {int(t[fs])}")
                            # entry was established but no post-entry exit occurred in the fill bar:
                            # continue tracking from the next 15m bar instead of inventing a loss.
                            th=th.copy(); sh=sh.copy(); th[0]=False; sh[0]=False
                            ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 10**9;is_=si[0] if si.size else 10**9
                        th=th.copy(); sh=sh.copy(); th[0]=False; sh[0]=False
                        ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 10**9;is_=si[0] if si.size else 10**9
                    if it==is_ and it<10**9:q["collision_15m"]+=1; rr=_resolve_1m(symbol,int(t[fs+it]),tp,sl,b,None,float(h[fs+it]),float(l[fs+it])); q["resolved_1m"]+=int(rr in ("win","loss")); q["collision_1m_loss"]+=int(rr=="loss"); q["win"]+=int(rr=="win"); q["loss"]+=int(rr=="loss"); (_ for _ in ()).throw(RuntimeError(f"1m collision unresolved {symbol} {int(t[fs+it])}")) if rr not in ("win","loss") else None
                    elif it<is_:q["win"]+=1
                    elif is_<it:q["loss"]+=1
                    else:q["unresolved_eod"]+=1
        # Fixed-percent distance from open-time PSAR, same execution semantics.
        for pct in ENTRY_PCT:
            e=s*(1+pct/100.0) if b else s*(1-pct/100.0)
            crossed=(b and ro[i]<=e) or ((not b) and ro[i]>=e)
            if crossed:
                fs=start; fill=ro[i]
            else:
                hits=np.flatnonzero((l[start:min(start+m,len(t))]<=e)&(h[start:min(start+m,len(t))]>=e))
                if not hits.size: continue
                fs=start+int(hits[0]); fill=e
            ph=h[fs:end];pl=l[fs:end]
            is_taker=bool(crossed)
            for sb in SL_BUFFER_ATR:
                sl=s-(sb*a0 if b else -sb*a0)
                if (b and not fill>sl) or ((not b) and not fill<sl):continue
                risk=abs(fill-sl)
                if risk<=MIN_RISK_EPS*max(1.,abs(fill),abs(sl)): continue
                for r in RS:
                    tp=fill+r*risk if b else fill-r*risk
                    th=(ph>=tp) if b else (pl<=tp); sh=(pl<=sl) if b else (ph>=sl)
                    ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 10**9;is_=si[0] if si.size else 10**9
                    k=f"P{pct:g}|SB{sb:g}|R{r:g}|{side}";q=out.setdefault(k,{"fills":0,"taker":0,"maker":0,"win":0,"loss":0,"collision_15m":0,"resolved_1m":0,"collision_1m_loss":0,"data_gap":0,"unresolved_eod":0})
                    q["fills"]+=1; q["taker" if is_taker else "maker"]+=1
                    # Taker fills at strategy-TF OPEN, so the full 15m fill bar is post-entry.
                    # Maker fills intrabar. With 15m OHLC only, a TP touch on the fill bar
                    # may have occurred before entry. Never credit that as a win.
                    if not is_taker:
                        fill_tp=bool(th[0]); fill_sl=bool(sh[0])
                        if fill_tp or fill_sl:
                            # Exact maker chronology: no fill-bar TP/SL may count before the entry touch.
                            # Resolve every fill-bar exit candidate on official 1m data starting at entry.
                            q["collision_15m"]+=int(fill_tp and fill_sl)
                            rr=_resolve_1m(symbol,int(t[fs]),tp,sl,b,fill,float(h[fs]),float(l[fs]))
                            q["resolved_1m"]+=int(rr in ("win","loss")); q["collision_1m_loss"]+=int(rr=="loss" and fill_tp and fill_sl)
                            if rr=="win":q["win"]+=1;continue
                            if rr=="loss":q["loss"]+=1;continue
                            if rr=="data_gap":q["data_gap"]+=1;continue
                            if rr=="data_error":raise RuntimeError(f"1m chronology mismatch {symbol} {int(t[fs])}")
                            th=th.copy(); sh=sh.copy(); th[0]=False; sh[0]=False
                            ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 10**9;is_=si[0] if si.size else 10**9
                        th=th.copy(); sh=sh.copy(); th[0]=False; sh[0]=False
                        ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 10**9;is_=si[0] if si.size else 10**9
                    if it==is_ and it<10**9:q["collision_15m"]+=1; rr=_resolve_1m(symbol,int(t[fs+it]),tp,sl,b,None,float(h[fs+it]),float(l[fs+it])); q["resolved_1m"]+=int(rr in ("win","loss")); q["collision_1m_loss"]+=int(rr=="loss"); q["win"]+=int(rr=="win"); q["loss"]+=int(rr=="loss"); (_ for _ in ()).throw(RuntimeError(f"1m collision unresolved {symbol} {int(t[fs+it])}")) if rr not in ("win","loss") else None
                    elif it<is_:q["win"]+=1
                    elif is_<it:q["loss"]+=1
                    else:q["unresolved_eod"]+=1
    return out

ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--out",default="psar_open_spider_grid.json");ap.add_argument("--tf",choices=("1h","4h"),default="4h");ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);a=ap.parse_args()
if a.shards<1 or a.shard<0 or a.shard>=a.shards:raise RuntimeError(f"invalid shard selection {a.shard}/{a.shards}")
m={"1h":4,"4h":16}[a.tf]
all_files=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert all_files
seen_ranges={}
for p in all_files:
    data=load(p); sym=_symbol(p); t0=data[0]
    lo,hi=int(t0[0]),int(t0[-1])
    for old_lo,old_hi,old_p in seen_ranges.get(sym,[]):
        if max(lo,old_lo)<=min(hi,old_hi):
            raise RuntimeError(f"overlapping symbol/time-range input {sym}: {old_p} [{old_lo},{old_hi}] vs {p} [{lo},{hi}]")
    seen_ranges.setdefault(sym,[]).append((lo,hi,p))
files=[p for j,p in enumerate(all_files) if j%a.shards==a.shard]
agg={};errors=[]
for z,p in enumerate(files,1):
    try:
        data=load(p); sym=_symbol(p)
        rr={}
        for aa,bb in contiguous_segments(data[0]):
            if bb-aa < m*(PSAR_BURNIN_BARS+1):continue
            part=tuple(x[aa:bb] for x in data)
            if len(resample(*part,m)[0]) <= PSAR_BURNIN_BARS:continue
            seg=evaluate(*part,m,sym)
            for k,v in seg.items():
                q=rr.setdefault(k,{kk:0 for kk in v})
                for kk,vv in v.items():q[kk]+=vv
    except Exception as e:
        _ONE_MIN_CACHE.clear()
        raise RuntimeError(f"{p}: {e}") from e
    _ONE_MIN_CACHE.clear()
    for k,v in rr.items():
        q=agg.setdefault(k,{kk:0 for kk in v})
        for kk,vv in v.items():q[kk]+=vv
    if z%20==0:print("progress",z,len(files),flush=True)
for k,q in agg.items():
    if q["taker"]+q["maker"]!=q["fills"]:
        raise RuntimeError(f"accounting invariant failed order types {k}: {q}")
    if q["win"]+q["loss"]+q["data_gap"]+q["unresolved_eod"]!=q["fills"]:
        raise RuntimeError(f"accounting invariant failed outcomes {k}: {q}")
    if q["resolved_1m"]>q["fills"] or q["collision_1m_loss"]>q["resolved_1m"]:
        raise RuntimeError(f"accounting invariant failed 1m counters {k}: {q}")
    resolved=q["win"]+q["loss"]
    q["win_pct_amb_loss"]=round(100*q["win"]/resolved,3) if resolved else None
    # expectancy in R with ambiguous conservatively loss; unresolved only means dataset ended before TP/SL
    r=float(k.split("|R")[1].split("|")[0])
    q["gross_expectancy_R_amb_loss"]=round((q["win"]*r-q["loss"])/resolved,5) if resolved else None
res={"definition":{"workflow_commit_sha":os.environ.get("GITHUB_SHA","local"),"engine_blob_sha":os.environ.get("PSAR_ENGINE_BLOB_SHA","local"),"source_data_run":"36095439671","input_files_total":len(all_files),"shard_index":a.shard,"shard_count":a.shards,"tf":a.tf,"order_live":"same strategy-TF bar open","psar":"projected at open using closed history only; legacy initialization forced bullish; first 100 strategy bars excluded as burn-in","atr":"SMA14 of True Range through prior closed strategy-TF bar","entry_atr":ENTRY_ATR,"entry_pct":ENTRY_PCT,"sl_buffer_atr":SL_BUFFER_ATR,"tp_R":RS,"horizon_bars":None,"exit_tracking":"from fill until TP/SL or dataset end","statistics_scope":"independent-signal gross edge scan; overlapping positions allowed; no equity curve or MDD","gap_policy":"15m gaps split a symbol into independent contiguous segments; positions never cross gaps; unresolved_eod includes segment/gap end","costs":"fees, slippage and funding excluded","ambiguous":"15m TP+SL collision -> official Binance 1m; same 1m TP+SL -> loss; maker 1m starts only after entry touch; actual risk=abs(fill-SL)","one_min_gap_policy":"official Binance 1m timestamp gap/duplicate affecting a required chronology window -> DATA_GAP excluded from win/loss; counted explicitly; other 1m download/schema failures hard-fail"},"files":len(files),"errors":errors,"summary":agg}
open(a.out,"w").write(json.dumps(res,indent=2));print(json.dumps(res["definition"],indent=2))
