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
            touched=(h[j]>=entry) if long else (l[j]<=entry)
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

def _book(q,outcome,fill,tp,sl,risk):
    if outcome=="win":
        q["win"]+=1
        q["gross_profit_R"]+=abs(tp-fill)/risk
    elif outcome=="loss":
        q["loss"]+=1
        q["gross_loss_R"]+=1.0
    else:
        raise RuntimeError(f"unexpected resolved outcome {outcome}")

ENTRY_ATR=(0.0,.10,.20,.30,.40,.50,.60,.70,.80,.90,1.0,1.25,1.5,1.75,2.0,2.25,2.5,2.75,3.0,3.5,4.0,4.5,5.0,5.5,6.0)
ENTRY_PCT=(0.0,.1,.2,.3,.4,.5,.75,1.0,1.5,2.0,3.0,4.0,5.0,6.0,7.0,8.0,9.0,10.0)
SL_BUFFER_ATR=(.10,.20,.30,.50,.75,1.0,1.5,2.0,3.0)
TP_ATR=(.25,.5,.75,1.,1.25,1.5,2.,2.5,3.,3.5,4.,4.5,5.,5.5,6.,7.,8.,10.)
TP_PCT=(.5,.75,1.,1.5,2.,3.,4.,5.,6.,7.,8.,9.,10.,12.,15.,20.,25.,30.)
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

def _approach_fill(o,h,l,start,stop,entry,psar,long):
    """Stop-style approach entry; 15m OPEN gap fills at OPEN before PSAR only."""
    for j in range(start,stop):
        at_open=(o[j]>=entry) if long else (o[j]<=entry)
        touched=(h[j]>=entry) if long else (l[j]<=entry)
        if not (at_open or touched):continue
        fill=float(o[j]) if at_open else float(entry)
        if (long and not fill<psar) or ((not long) and not fill>psar):
            return None
        return j,fill,bool(at_open)
    return None


import csv,gzip

# Death-test shortlist chosen BEFORE this run from the legacy aggregate scan.
# No holdout/result-driven retuning is permitted inside this run.
CANDIDATES=(
    (.1,3.0,20.0),
    (.3,3.0,20.0),
    (.5,3.0,20.0),
    (5.0,3.0,3.0),
    (6.0,3.0,3.0),
    (7.0,3.0,3.0),
    (6.0,1.5,2.0),
    (7.0,1.5,2.0),
    (8.0,1.5,2.0),
)
LEDGER_KEYS={
    "P0.1|SB3|TP20|SHORT",
    "P6|SB3|TP3|SHORT",
    "P7|SB1.5|TP2|SHORT",
}
COST_BPS=(20,40)
_LEDGER=[]

def _new_q():
    q={"fills":0,"taker":0,"maker":0,"win":0,"loss":0,
       "collision_15m":0,"resolved_1m":0,"collision_1m_loss":0,
       "data_gap":0,"entry_mismatch":0,"exit_mismatch":0,"unresolved_eod":0,
       "gross_profit_R":0.0,"gross_loss_R":0.0}
    for bps in COST_BPS:
        q[f"cost_R_{bps}"]=0.0
        q[f"net_sum_R_{bps}"]=0.0
        q[f"net_profit_R_{bps}"]=0.0
        q[f"net_loss_R_{bps}"]=0.0
    return q

def _book_cost(q,outcome,fill,tp,sl,risk):
    _book(q,outcome,fill,tp,sl,risk)
    exit_px=tp if outcome=="win" else sl
    gross_R=abs(tp-fill)/risk if outcome=="win" else -1.0
    details={"gross_R":gross_R,"exit_price":exit_px}
    for bps in COST_BPS:
        # "20bp/40bp round trip" = total all-in cost rate applied to average
        # entry/exit notional, not 20/40bp per leg.
        cost_R=(bps/10000.0)*((abs(fill)+abs(exit_px))/2.0)/risk
        net_R=gross_R-cost_R
        q[f"cost_R_{bps}"]+=cost_R
        q[f"net_sum_R_{bps}"]+=net_R
        if net_R>=0:q[f"net_profit_R_{bps}"]+=net_R
        else:q[f"net_loss_R_{bps}"]+=-net_R
        details[f"cost_R_{bps}"]=cost_R
        details[f"net_R_{bps}"]=net_R
    return details

def _ledger(key,symbol,entry_bar_ts,exit_bar_ts,fill,tp,sl,risk,outcome,is_open_fill,details=None):
    if key not in LEDGER_KEYS:return
    row={"key":key,"symbol":symbol,"entry_bar_ts":int(entry_bar_ts),
         "exit_bar_ts":None if exit_bar_ts is None else int(exit_bar_ts),
         "entry_price":float(fill),"tp":float(tp),"sl":float(sl),"risk_price":float(risk),
         "outcome":outcome,"entry_was_15m_open_gap":bool(is_open_fill)}
    if details: row.update(details)
    _LEDGER.append(row)

def evaluate(t,o,h,l,c,m,symbol=None):
    rt,ro,rh,rl,rc=resample(t,o,h,l,c,m); sar,bull=psar_open_projection(rh,rl); n=len(rt)
    prev=np.r_[np.nan,rc[:-1]]
    tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prev),np.abs(rl-prev)))
    atr_closed=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()
    atr_open=np.r_[np.nan,atr_closed[:-1]]
    pos=np.searchsorted(t,rt)
    if len(pos) and (np.any(pos>=len(t)) or np.any(t[pos]!=rt)):
        raise RuntimeError("strategy-bar to 15m timestamp mapping mismatch")
    out={};last_regime=None;entered_pct=set()
    by_pct={}
    for pct,sb,tpct in CANDIDATES:by_pct.setdefault(float(pct),[]).append((float(sb),float(tpct)))
    for i in range(max(15,PSAR_BURNIN_BARS),n):
        s=sar[i];a0=atr_open[i]
        if not np.isfinite(s) or not np.isfinite(a0) or a0<=0:continue
        regime_bull=bool(bull[i]);b=not regime_bull
        if last_regime is None or regime_bull!=last_regime:
            entered_pct.clear();last_regime=regime_bull
        # Death test is SHORT-only because every legacy PF>=1 candidate was SHORT.
        if b:continue
        # Strict pre-break: price must still be above PSAR at 1H OPEN.
        if ro[i]<=s:continue
        start=pos[i]
        for pct,settings in by_pct.items():
            if pct in entered_pct:continue
            e=s*(1+pct/100.0)
            approach=_approach_fill(o,h,l,start,min(start+m,len(t)),e,s,False)
            if approach is None:continue
            fs,fill,is_open_fill=approach
            entered_pct.add(pct)
            for sb,tpct in settings:
                sl=fill+sb*a0
                tp=s*(1-tpct/100.0)
                if not (tp<s<fill<sl):continue
                risk=abs(fill-sl)
                if risk<=MIN_RISK_EPS*max(1.,abs(fill),abs(sl)):continue
                key=f"P{pct:g}|SB{sb:g}|TP{tpct:g}|SHORT"
                q=out.setdefault(key,_new_q());q["fills"]+=1;q["taker"]+=1
                scan_start=fs
                if not is_open_fill:
                    fill_tp=l[fs]<=tp;fill_sl=h[fs]>=sl
                    if fill_tp or fill_sl:
                        q["collision_15m"]+=int(fill_tp and fill_sl)
                        rr=_resolve_1m(symbol,int(t[fs]),tp,sl,False,fill,float(h[fs]),float(l[fs]))
                        q["resolved_1m"]+=int(rr in ("win","loss"))
                        q["collision_1m_loss"]+=int(rr=="loss" and fill_tp and fill_sl)
                        if rr in ("win","loss"):
                            d=_book_cost(q,rr,fill,tp,sl,risk)
                            _ledger(key,symbol,t[fs],t[fs],fill,tp,sl,risk,rr,is_open_fill,d);continue
                        if rr=="data_gap":
                            q["data_gap"]+=1
                            _ledger(key,symbol,t[fs],t[fs],fill,tp,sl,risk,rr,is_open_fill);continue
                        if rr=="entry_mismatch":
                            q["fills"]-=1;q["taker"]-=1;q["entry_mismatch"]+=1
                            continue
                        if rr!="continue":raise RuntimeError(f"unexpected 1m trigger result {symbol} {int(t[fs])}: {rr}")
                    scan_start=fs+1
                off,hit_tp,hit_sl=_first_exit(h,l,scan_start,tp,sl,False)
                if off<0:
                    q["unresolved_eod"]+=1
                    _ledger(key,symbol,t[fs],None,fill,tp,sl,risk,"unresolved_eod",is_open_fill)
                    continue
                exit_i=scan_start+int(off)
                if hit_tp and hit_sl:
                    q["collision_15m"]+=1
                    rr=_resolve_1m(symbol,int(t[exit_i]),tp,sl,False,None,float(h[exit_i]),float(l[exit_i]))
                    q["resolved_1m"]+=int(rr in ("win","loss"))
                    q["collision_1m_loss"]+=int(rr=="loss")
                    if rr in ("win","loss"):
                        d=_book_cost(q,rr,fill,tp,sl,risk)
                        _ledger(key,symbol,t[fs],t[exit_i],fill,tp,sl,risk,rr,is_open_fill,d)
                    elif rr=="data_gap":
                        q["data_gap"]+=1
                        _ledger(key,symbol,t[fs],t[exit_i],fill,tp,sl,risk,rr,is_open_fill)
                    elif rr=="exit_mismatch":
                        q["exit_mismatch"]+=1
                        _ledger(key,symbol,t[fs],t[exit_i],fill,tp,sl,risk,rr,is_open_fill)
                    else:raise RuntimeError(f"1m collision unresolved {symbol} {int(t[exit_i])}: {rr}")
                elif hit_tp:
                    d=_book_cost(q,"win",fill,tp,sl,risk)
                    _ledger(key,symbol,t[fs],t[exit_i],fill,tp,sl,risk,"win",is_open_fill,d)
                else:
                    d=_book_cost(q,"loss",fill,tp,sl,risk)
                    _ledger(key,symbol,t[fs],t[exit_i],fill,tp,sl,risk,"loss",is_open_fill,d)
    return out

ap=argparse.ArgumentParser()
ap.add_argument("--data",default="data");ap.add_argument("--out",default="death.json")
ap.add_argument("--ledger-out",default="death_ledger.csv.gz")
ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1)
ap.add_argument("--max-files",type=int,default=0)
a=ap.parse_args()
if a.shards<1 or a.shard<0 or a.shard>=a.shards:raise RuntimeError(f"invalid shard selection {a.shard}/{a.shards}")
m=4
all_files=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert all_files
seen_ranges={}
for p in all_files:
    data=load(p);sym=_symbol(p);t0=data[0];lo,hi=int(t0[0]),int(t0[-1])
    for old_lo,old_hi,old_p in seen_ranges.get(sym,[]):
        if max(lo,old_lo)<=min(hi,old_hi):
            raise RuntimeError(f"overlapping symbol/time-range input {sym}: {old_p} [{old_lo},{old_hi}] vs {p} [{lo},{hi}]")
    seen_ranges.setdefault(sym,[]).append((lo,hi,p))
files=[p for j,p in enumerate(all_files) if j%a.shards==a.shard]
if a.max_files>0:files=files[:a.max_files]
agg={};_run_started=time.time()
print(f"DEATH_START shard={a.shard}/{a.shards} files={len(files)} total_inputs={len(all_files)} candidates={len(CANDIDATES)}",flush=True)
for z,p in enumerate(files,1):
    st=time.time()
    try:
        data=load(p);sym=_symbol(p);rr={}
        for aa,bb in contiguous_segments(data[0]):
            if bb-aa<m*(PSAR_BURNIN_BARS+1):continue
            part=tuple(x[aa:bb] for x in data)
            if len(resample(*part,m)[0])<=PSAR_BURNIN_BARS:continue
            seg=evaluate(*part,m,sym)
            for k,v in seg.items():
                q=rr.setdefault(k,{kk:0 for kk in v})
                for kk,vv in v.items():q[kk]+=vv
    except Exception as e:
        _ONE_MIN_CACHE.clear();raise RuntimeError(f"{p}: {e}") from e
    _ONE_MIN_CACHE.clear()
    for k,v in rr.items():
        q=agg.setdefault(k,{kk:0 for kk in v})
        for kk,vv in v.items():q[kk]+=vv
    elapsed=time.time()-_run_started
    print(f"DEATH_PROGRESS shard={a.shard}/{a.shards} file={z}/{len(files)} symbol={sym} file_sec={time.time()-st:.1f} elapsed_min={elapsed/60:.1f} ledger={len(_LEDGER)}",flush=True)
for k,q in agg.items():
    if q["taker"]+q["maker"]!=q["fills"]:raise RuntimeError(f"order accounting {k}: {q}")
    if q["win"]+q["loss"]+q["data_gap"]+q["exit_mismatch"]+q["unresolved_eod"]!=q["fills"]:
        raise RuntimeError(f"outcome accounting {k}: {q}")
    resolved=q["win"]+q["loss"]
    q["win_pct"]=round(100*q["win"]/resolved,5) if resolved else None
    q["gross_pf_actual_R"]=q["gross_profit_R"]/q["gross_loss_R"] if q["gross_loss_R"] else None
    q["gross_expectancy_actual_R"]=(q["gross_profit_R"]-q["gross_loss_R"])/resolved if resolved else None
    for bps in COST_BPS:
        q[f"net_pf_{bps}bp"]=q[f"net_profit_R_{bps}"]/q[f"net_loss_R_{bps}"] if q[f"net_loss_R_{bps}"] else None
        q[f"net_expectancy_R_{bps}bp"]=q[f"net_sum_R_{bps}"]/resolved if resolved else None
definition={
    "workflow_commit_sha":os.environ.get("GITHUB_SHA","local"),
    "source_engine_blob_sha":os.environ.get("PSAR_SOURCE_ENGINE_BLOB","unknown"),
    "source_data_run":"36095439671","input_files_total":len(all_files),
    "shard_index":a.shard,"shard_count":a.shards,
    "scope":"PSAR 1H strict pre-break SHORT death test; shortlist frozen before run",
    "candidates":[f"P{p:g}|SB{s:g}|TP{t:g}|SHORT" for p,s,t in CANDIDATES],
    "ledger_keys":sorted(LEDGER_KEYS),
    "execution":"all approach entries taker; 15m OPEN gap filled at actual OPEN only if still pre-break; same-1m ambiguity LOSS",
    "costs":"20bp and 40bp are total round-trip all-in rates applied to average entry/exit notional; funding excluded",
    "ledger_time_precision":"15m entry/exit bar timestamp; authoritative Binance 1m is used only where chronology requires it",
}
res={"definition":definition,"files":len(files),"summary":agg}
open(a.out,"w").write(json.dumps(res,indent=2))
pd.DataFrame(_LEDGER).to_csv(a.ledger_out,index=False,compression="gzip")
print(json.dumps(definition,indent=2));print(f"LEDGER_ROWS {len(_LEDGER)}",flush=True)
