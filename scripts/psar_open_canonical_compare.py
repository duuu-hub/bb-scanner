import argparse,glob,json,io,urllib.request,zipfile,os,time
from datetime import datetime,timezone
import pandas as pd,numpy as np
_ONE_MIN_CACHE={}
def _symbol(p):
    b=os.path.basename(p)
    if b.endswith(".csv.gz"): b=b[:-7]
    return b.split("_")[0].split("-")[0].upper()
def _one_min(symbol,ts):
    ym=datetime.fromtimestamp(ts/1000,tz=timezone.utc).strftime("%Y-%m"); key=(symbol,ym)
    if key in _ONE_MIN_CACHE:return _ONE_MIN_CACHE[key]
    u=f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/{symbol}-1m-{ym}.zip"
    last=None
    for attempt in range(4):
        try:
            raw=urllib.request.urlopen(u,timeout=60).read()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:d=pd.read_csv(z.open(z.namelist()[0]),header=None)
            v=(d.iloc[:,0].to_numpy(np.int64),d.iloc[:,2].to_numpy(float),d.iloc[:,3].to_numpy(float))
            if len(v[0])==0: raise RuntimeError("empty 1m archive")
            if not np.all(np.isfinite(v[1])) or not np.all(np.isfinite(v[2])):raise RuntimeError("non-finite 1m high/low")
            if np.any(v[1]<=0) or np.any(v[2]<=0) or np.any(v[1]<v[2]):raise RuntimeError("invalid 1m high/low")
            if len(v[0])>1 and np.any(np.diff(v[0])!=60000):
                bad=np.flatnonzero(np.diff(v[0])!=60000)[:5]
                raise RuntimeError(f"1m timestamp gap/duplicate at rows {bad.tolist()}")
            _ONE_MIN_CACHE[key]=v;return v
        except Exception as e:
            last=e
            if attempt<3: time.sleep(2**attempt)
    raise RuntimeError(f"1m download failed after retries {symbol} {ym}: {last}")
def _resolve_1m(symbol,ts,tp,sl,long,entry=None):
    d=_one_min(symbol,ts)
    t,h,l=d;a=np.searchsorted(t,ts);z=np.searchsorted(t,ts+900000)
    if z-a!=15 or a>=len(t) or t[a]!=ts or t[z-1]!=ts+840000:
        raise RuntimeError(f"incomplete 1m window {symbol} {ts}: count={z-a}")
    entered=entry is None;entry_seen=entered
    for j in range(a,z):
        if not entered:
            if not (l[j]<=entry<=h[j]):continue
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
    if len(t)>1 and np.any(np.diff(t)!=900000):
        bad=np.flatnonzero(np.diff(t)!=900000)[:5]
        raise RuntimeError(f"15m timestamp gap/duplicate at rows {bad.tolist()}")
    vals=tuple(d[x].to_numpy(float) for x in ["open","high","low","close"])
    o,h,l,c=vals
    if not all(np.all(np.isfinite(x)) for x in vals):raise RuntimeError("non-finite OHLC")
    if any(np.any(x<=0) for x in vals):raise RuntimeError("non-positive OHLC")
    if np.any(h<np.maximum.reduce([o,l,c])) or np.any(l>np.minimum.reduce([o,h,c])):
        raise RuntimeError("invalid OHLC geometry")
    return (t,)+vals

def resample(t,o,h,l,c,m=16):
    bucket=t//(900000*m); cut=np.r_[0,np.flatnonzero(bucket[1:]!=bucket[:-1])+1,len(t)]
    st=cut[:-1];en=cut[1:];good=(en-st)==m
    st=st[good];en=en[good]
    if len(st):
        ok=np.array([np.all(np.diff(t[a:b])==900000) for a,b in zip(st,en)],dtype=bool)
        st=st[ok];en=en[ok]
    return t[st],o[st],np.maximum.reduceat(h,st),np.minimum.reduceat(l,st),c[en-1]

def evaluate(t,o,h,l,c,m,horizon=None,symbol=None):
    rt,ro,rh,rl,rc=resample(t,o,h,l,c,m); sar,bull=psar_open_projection(rh,rl); n=len(rt)
    # ATR available at bar i open = ATR14 through bar i-1 only
    prev=np.r_[np.nan,rc[:-1]]
    tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prev),np.abs(rl-prev)))
    atr_closed=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()
    atr_open=np.r_[np.nan,atr_closed[:-1]]
    pos=np.searchsorted(t,rt); out={}
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
                    k=f"E{em:g}|SB{sb:g}|R{r:g}|{side}";q=out.setdefault(k,{"fills":0,"taker":0,"maker":0,"win":0,"loss":0,"collision_15m":0,"resolved_1m":0,"collision_1m_loss":0,"maker_fillbar_amb":0,"unresolved_eod":0})
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
                            rr=_resolve_1m(symbol,int(t[fs]),tp,sl,b,fill)
                            q["resolved_1m"]+=int(rr in ("win","loss")); q["collision_1m_loss"]+=int(rr=="loss" and fill_tp and fill_sl)
                            if rr=="win":q["win"]+=1;continue
                            if rr=="loss":q["loss"]+=1;continue
                            if rr=="data_error":raise RuntimeError(f"1m chronology mismatch {symbol} {int(t[fs])}")
                            # entry was established but no post-entry exit occurred in the fill bar:
                            # continue tracking from the next 15m bar instead of inventing a loss.
                            th=th.copy(); sh=sh.copy(); th[0]=False; sh[0]=False
                            ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 10**9;is_=si[0] if si.size else 10**9
                        th=th.copy(); sh=sh.copy(); th[0]=False; sh[0]=False
                        ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 10**9;is_=si[0] if si.size else 10**9
                    if it==is_ and it<10**9:q["collision_15m"]+=1; rr=_resolve_1m(symbol,int(t[fs+it]),tp,sl,b); q["resolved_1m"]+=int(rr in ("win","loss")); q["collision_1m_loss"]+=int(rr=="loss"); q["win"]+=int(rr=="win"); q["loss"]+=int(rr=="loss"); (_ for _ in ()).throw(RuntimeError(f"1m collision unresolved {symbol} {int(t[fs+it])}")) if rr not in ("win","loss") else None
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
                    k=f"P{pct:g}|SB{sb:g}|R{r:g}|{side}";q=out.setdefault(k,{"fills":0,"taker":0,"maker":0,"win":0,"loss":0,"collision_15m":0,"resolved_1m":0,"collision_1m_loss":0,"maker_fillbar_amb":0,"unresolved_eod":0})
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
                            rr=_resolve_1m(symbol,int(t[fs]),tp,sl,b,fill)
                            q["resolved_1m"]+=int(rr in ("win","loss")); q["collision_1m_loss"]+=int(rr=="loss" and fill_tp and fill_sl)
                            if rr=="win":q["win"]+=1;continue
                            if rr=="loss":q["loss"]+=1;continue
                            if rr=="data_error":raise RuntimeError(f"1m chronology mismatch {symbol} {int(t[fs])}")
                            th=th.copy(); sh=sh.copy(); th[0]=False; sh[0]=False
                            ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 10**9;is_=si[0] if si.size else 10**9
                        th=th.copy(); sh=sh.copy(); th[0]=False; sh[0]=False
                        ti=np.flatnonzero(th);si=np.flatnonzero(sh);it=ti[0] if ti.size else 10**9;is_=si[0] if si.size else 10**9
                    if it==is_ and it<10**9:q["collision_15m"]+=1; rr=_resolve_1m(symbol,int(t[fs+it]),tp,sl,b); q["resolved_1m"]+=int(rr in ("win","loss")); q["collision_1m_loss"]+=int(rr=="loss"); q["win"]+=int(rr=="win"); q["loss"]+=int(rr=="loss"); (_ for _ in ()).throw(RuntimeError(f"1m collision unresolved {symbol} {int(t[fs+it])}")) if rr not in ("win","loss") else None
                    elif it<is_:q["win"]+=1
                    elif is_<it:q["loss"]+=1
                    else:q["unresolved_eod"]+=1
    return out

ap=argparse.ArgumentParser();ap.add_argument("--data",default="data");ap.add_argument("--out",default="psar_open_spider_grid.json");ap.add_argument("--tf",choices=("1h","4h"),default="4h");ap.add_argument("--horizon",type=int,default=None);ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);a=ap.parse_args();m={"1h":4,"4h":16}[a.tf]
files=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert files;files=[p for j,p in enumerate(files) if j%a.shards==a.shard]
agg={};errors=[]
for z,p in enumerate(files,1):
    try: rr=evaluate(*load(p),m,a.horizon,_symbol(p))
    except Exception as e:errors.append([p,str(e)]);continue
    for k,v in rr.items():
        q=agg.setdefault(k,{kk:0 for kk in v})
        for kk,vv in v.items():q[kk]+=vv
    if z%20==0:print("progress",z,len(files),flush=True)
for k,q in agg.items():
    resolved=q["win"]+q["loss"]
    q["win_pct_amb_loss"]=round(100*q["win"]/resolved,3) if resolved else None
    # expectancy in R with ambiguous conservatively loss; unresolved only means dataset ended before TP/SL
    r=float(k.split("|R")[1].split("|")[0])
    q["gross_expectancy_R_amb_loss"]=round((q["win"]*r-q["loss"])/resolved,5) if resolved else None
if errors: raise RuntimeError("input/evaluation errors: "+json.dumps(errors[:10]))
res={"definition":{"tf":a.tf,"order_live":"same strategy-TF bar open","psar":"projected at open using closed history only; legacy initialization forced bullish; first 100 strategy bars excluded as burn-in","atr":"SMA14 of True Range through prior closed strategy-TF bar","entry_atr":ENTRY_ATR,"entry_pct":ENTRY_PCT,"sl_buffer_atr":SL_BUFFER_ATR,"tp_R":RS,"horizon_bars":None,"exit_tracking":"from fill until TP/SL or dataset end","statistics_scope":"independent-signal gross edge scan; overlapping positions allowed; no equity curve or MDD","costs":"fees, slippage and funding excluded","ambiguous":"15m TP+SL collision -> official Binance 1m; same 1m TP+SL -> loss; 1m download/integrity failure -> run fails; maker 1m starts only after entry touch; actual risk=abs(fill-SL)"},"files":len(files),"errors":errors,"summary":agg}
open(a.out,"w").write(json.dumps(res,indent=2));print(json.dumps(res["definition"],indent=2))
