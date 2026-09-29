import argparse,glob,json,os
import numpy as np,pandas as pd

# Reuse canonical helpers without executing its CLI. This keeps PSAR/ATR/resample/1m
# chronology semantics tied to the validated canonical engine.
src=open("scripts/psar_4h_canonical_compare.py").read()
prefix=src.split("ap=argparse.ArgumentParser()",1)[0].replace("@njit(cache=True)","@njit(cache=False)")
ns={}
exec(compile(prefix,"canonical_prefix","exec"),ns)
load=ns["load"]; contiguous_segments=ns["contiguous_segments"]; resample=ns["resample"]
psar_open_projection=ns["psar_open_projection"]; _first_exit=ns["_first_exit"]; _resolve_1m=ns["_resolve_1m"]
_symbol=ns["_symbol"]; _ONE_MIN_CACHE=ns["_ONE_MIN_CACHE"]; PSAR_BURNIN_BARS=ns["PSAR_BURNIN_BARS"]

CANDIDATES={
 "A":{"em":2.25,"sb":0.0,"r":2.0},
 "B":{"em":2.0,"sb":0.0,"r":2.5},
 "C":{"em":1.75,"sb":0.0,"r":3.0},
 "D":{"em":1.5,"sb":0.0,"r":3.5},
}

def eval_candidate(t,o,h,l,c,m,symbol,label,p):
    rt,ro,rh,rl,rc=resample(t,o,h,l,c,m); sar,bull=psar_open_projection(rh,rl); n=len(rt)
    prev=np.r_[np.nan,rc[:-1]]
    tr=np.maximum(rh-rl,np.maximum(np.abs(rh-prev),np.abs(rl-prev)))
    atr_closed=pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()
    atr_open=np.r_[np.nan,atr_closed[:-1]]
    pos=np.searchsorted(t,rt); rows=[]
    for i in range(max(15,PSAR_BURNIN_BARS),n):
        if bool(bull[i]): continue
        s=sar[i]; a0=atr_open[i]
        if not np.isfinite(s) or not np.isfinite(a0) or a0<=0: continue
        e=s-p["em"]*a0
        crossed=ro[i]>=e
        if crossed: fs=pos[i]; fill=ro[i]
        else:
            z0=pos[i]; hits=np.flatnonzero((l[z0:min(z0+m,len(t))]<=e)&(h[z0:min(z0+m,len(t))]>=e))
            if not hits.size: continue
            fs=z0+int(hits[0]); fill=e
        sl=s
        if not fill<sl: continue
        risk=sl-fill
        tp=fill-p["r"]*risk
        scan_start=fs
        outcome=None; exit_i=None; reason=None
        if not crossed:
            fill_tp=l[fs]<=tp; fill_sl=h[fs]>=sl
            if fill_tp or fill_sl:
                rr=_resolve_1m(symbol,int(t[fs]),tp,sl,False,fill,float(h[fs]),float(l[fs]))
                if rr in ("win","loss"): outcome=rr; exit_i=fs; reason="maker_fillbar_1m"
                elif rr in ("data_gap","entry_mismatch"): continue
                elif rr!="continue": raise RuntimeError(f"unexpected maker result {rr}")
            scan_start=fs+1
        if outcome is None:
            off,hit_tp,hit_sl=_first_exit(h,l,scan_start,tp,sl,False)
            if off<0: continue
            exit_i=scan_start+int(off)
            if hit_tp and hit_sl:
                rr=_resolve_1m(symbol,int(t[exit_i]),tp,sl,False,None,float(h[exit_i]),float(l[exit_i]))
                if rr in ("data_gap","exit_mismatch"): continue
                if rr not in ("win","loss"): raise RuntimeError(f"unexpected exit result {rr}")
                outcome=rr; reason="collision_1m"
            else:
                outcome="win" if hit_tp else "loss"; reason="15m"
        rows.append({"candidate":label,"symbol":symbol,"entry_ts":int(t[fs]),"exit_ts":int(t[exit_i]),
                     "fill":float(fill),"sl":float(sl),"tp":float(tp),"risk_px":float(risk),
                     "stop_pct":float(risk/fill),"r_target":p["r"],"outcome":outcome,
                     "result_R":p["r"] if outcome=="win" else -1.0,
                     "order_type":"taker" if crossed else "maker","exit_resolution":reason})
    return rows

ap=argparse.ArgumentParser(); ap.add_argument("--data",default="data"); ap.add_argument("--shard",type=int,default=0); ap.add_argument("--shards",type=int,default=8); ap.add_argument("--out",required=True)
a=ap.parse_args(); m=96
all_files=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True)); assert all_files
files=[p for j,p in enumerate(all_files) if j%a.shards==a.shard]
rows=[]
for z,p in enumerate(files,1):
    d=load(p); sym=_symbol(p)
    for aa,bb in contiguous_segments(d[0]):
        if bb-aa<m*(PSAR_BURNIN_BARS+1): continue
        part=tuple(x[aa:bb] for x in d)
        if len(resample(*part,m)[0])<=PSAR_BURNIN_BARS: continue
        for label,pv in CANDIDATES.items(): rows.extend(eval_candidate(*part,m,sym,label,pv))
    _ONE_MIN_CACHE.clear()
    print("PROGRESS",a.shard,z,len(files),sym,"events",len(rows),flush=True)
rows.sort(key=lambda x:(x["entry_ts"],x["symbol"],x["candidate"]))
json.dump({"definition":{"tf":"1d","candidates":CANDIDATES,"canonical_engine_blob":os.popen("git hash-object scripts/psar_4h_canonical_compare.py").read().strip(),
"source_data_run":"36095439671","note":"candidate-only event ledger; canonical PSAR/ATR/fill/1m chronology reused; collision exit timestamp stored at parent 15m bar open"},
"shard":a.shard,"events":rows},open(a.out,"w"))
print("LEDGER_PASS",a.shard,len(rows))
