#!/usr/bin/env python3
from __future__ import annotations
import io,json,math,os,time,urllib.parse,urllib.request,zipfile
from pathlib import Path
import numpy as np,pandas as pd
import sys
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import research.aoa_price_action_v1.price_action_v1 as v1
import research.aoa_price_action_v4.price_action_v4 as v4
import research.aoa_price_action_v41.price_action_v41 as v41

OUT=ROOT/"research"/"bb_wonyotti_pa_transfer"/"output"; OUT.mkdir(parents=True,exist_ok=True)
BB_ARTIFACT="https://api.github.com/repos/duuu-hub/bb-scanner/actions/artifacts/10781288564/zip"
TH=0.8354464189135131; MIN_HOLD=8; CONFIRM=1

def get_json(url):
    req=urllib.request.Request(url,headers={"User-Agent":"bb-scanner-research"})
    with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)

def candles(symbol,start,end):
    # Bitget history endpoint is paginated. Fetch <=90 15m bars per request so
    # the 8-day PA lookback is actually present instead of silently truncating.
    start_ms=int(start.timestamp()*1000); end_ms=int(end.timestamp()*1000)
    step_ms=90*15*60*1000; a=[]; cur=start_ms
    while cur<=end_ms:
        chunk_end=min(end_ms,cur+step_ms-1)
        q=urllib.parse.urlencode({"category":"USDT-FUTURES","symbol":symbol,"interval":"15m","startTime":str(cur),"endTime":str(chunk_end),"limit":"100"})
        j=get_json("https://api.bitget.com/api/v3/market/history-candles?"+q)
        if j.get("code")!="00000": raise RuntimeError((symbol,j))
        a.extend(j.get("data",[])); cur=chunk_end+1
        time.sleep(0.03)
    d=pd.DataFrame(a,columns=["timestamp_ms","open","high","low","close","base_volume","quote_volume"])
    for c in d.columns:d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d.drop_duplicates("timestamp_ms").sort_values("timestamp_ms").reset_index(drop=True)
    d["bar_start"]=pd.to_datetime(d.timestamp_ms,unit="ms",utc=True); d["bar_end_s"]=(d.timestamp_ms//1000+900).astype("int64")
    return v1.add_price_action_features(d)

def train_frozen():
    c=v1.load_pa_candles(); rows,dec=v41.build_competing_rows(c)
    tr=dec[dec.year==2019].copy(); va=dec[dec.year==2020].copy()
    model,m,_=v41.fit_competing(tr,va)
    fast=v4.compile_fast(model)
    # Frozen 2019 training feature envelope for transfer diagnostics only.
    q={}
    for k in v4.POLICY_FEATURES:
        s=pd.to_numeric(tr[k],errors="coerce").dropna()
        q[k]=(float(s.quantile(.001)),float(s.quantile(.999))) if len(s) else (np.nan,np.nan)
    return fast,m,q

def score_event(ev,fast,train_q):
    ts=pd.to_datetime(int(ev.signal_ts),unit="ms",utc=True)
    d=candles(ev.symbol,ts-pd.Timedelta(days=8),ts+pd.Timedelta(hours=26))
    before=d[d.bar_end_s<=int(ev.signal_ts/1000)]
    after=d[d.bar_end_s>int(ev.signal_ts/1000)]
    if len(before)<672 or len(after)<9:return {"pa_valid":False,"pa_error":f"coverage before={len(before)} after={len(after)}"}
    prev=before.iloc[-1]; side=-1; entry=float(ev.signal_price); ets=int(ev.signal_ts/1000)
    ectx=v1.entry_context(prev,side); state={"prev_close":entry,"path_bps":0.0,"mfe_bps":0.0,"mae_bps":0.0}
    streak=0; confirm=None; scores=[]; ood_counts=[]; max_abs_z=[]
    for n,(_,r) in enumerate(after.iterrows(),1):
        f=v1.hazard_state(r,side,entry,ets,ectx); f.update(v4.update_path_state(state,r,side,entry))
        p=v4.fast_prob(fast,f); scores.append(p)
        xv=np.asarray([f[k] for k in v4.POLICY_FEATURES],float)
        bad=~np.isfinite(xv); xv[bad]=fast["med"][bad]
        z=(xv-fast["mean"])/np.where(fast["scale"]==0,1.0,fast["scale"])
        max_abs_z.append(float(np.max(np.abs(z))))
        ood_counts.append(int(sum((np.isfinite(xv[j]) and (xv[j]<train_q[k][0] or xv[j]>train_q[k][1])) for j,k in enumerate(v4.POLICY_FEATURES))))
        trig=n>=MIN_HOLD and p>=TH; streak=streak+1 if trig else 0
        if streak>=CONFIRM: confirm=r; break
    out={"pa_valid":True,"pa_max_score_24h":float(max(scores)) if scores else np.nan,"pa_first_score":float(scores[0]) if scores else np.nan,
         "pa_score_at_minhold":float(scores[MIN_HOLD-1]) if len(scores)>=MIN_HOLD else np.nan,
         "pa_max_abs_z":float(max(max_abs_z)) if max_abs_z else np.nan,
         "pa_max_ood_features":int(max(ood_counts)) if ood_counts else 0,
         "pa_confirm":confirm is not None}
    if confirm is None:return out
    ct=int(confirm.bar_end_s)
    # Frozen PA semantics: confirmation is known only after that bar closes;
    # execution is the NEXT 15m bar open, never the confirmation close.
    nxt=d[d.bar_end_s>ct].head(1)
    if nxt.empty:
        out.update({"pa_confirm":False,"pa_error":"no next bar for entry"}); return out
    er=nxt.iloc[0]; cp=float(er.open); entry_end=int(er.bar_end_s); entry_start=entry_end-900
    out.update({"pa_confirm_ts":int(ct*1000),"pa_confirm_delay_h":float((ct-ets)/3600),
                "pa_entry_ts":int(entry_start*1000),"pa_entry_price":cp})
    fut=d[d.bar_end_s>=entry_end].copy()
    for h in [1,4,24]:
        z=fut[fut.bar_end_s<=entry_start+h*3600]
        out[f"pa_short_ret_{h}h"]=np.nan if z.empty else float((1-z.iloc[-1].close/cp)*100)
    # same frozen BB payoff shape: TP +9.88 / SL -5.12; if both hit same 15m bar => conservative SL
    tp=cp*0.90; sl=cp*1.05; result=np.nan; bars=0; last_close=np.nan
    for _,r in fut.iterrows():
        bars+=1; last_close=float(r.close); hit_tp=r.low<=tp; hit_sl=r.high>=sl
        if hit_sl: result=-5.12; break
        if hit_tp: result=9.88; break
        if bars>=96: break
    unresolved=result is np.nan or (isinstance(result,float) and np.isnan(result))
    if unresolved and not np.isnan(last_close):
        result=float((1-last_close/cp)*100-0.12)
    out["pa_tpsl_24h"]=result; out["pa_tpsl_unresolved_time_exit"]=bool(unresolved); return out

def metrics(x):
    x=pd.to_numeric(pd.Series(x),errors="coerce").dropna()
    if x.empty:return {"n":0,"avg":None,"sum":None,"wr":None,"pf":None}
    gp=x[x>0].sum(); gl=-x[x<0].sum()
    return {"n":len(x),"avg":float(x.mean()),"sum":float(x.sum()),"wr":float((x>0).mean()*100),"pf":None if gl==0 else float(gp/gl)}

def main():
    # exact frozen replay artifact is public; GitHub Actions can fetch it without mutating source data
    # GitHub artifact endpoint redirects to blob storage. Do not forward the
    # Authorization header to the signed cross-host URL (urllib did, causing 401).
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None
    req=urllib.request.Request(BB_ARTIFACT,headers={"Authorization":"Bearer "+os.environ["GITHUB_TOKEN"],"User-Agent":"bb-scanner-research"})
    try:
        urllib.request.build_opener(NoRedirect).open(req)
        raise RuntimeError("artifact endpoint unexpectedly did not redirect")
    except urllib.error.HTTPError as e:
        if e.code not in (301,302,303,307,308): raise
        loc=e.headers["Location"]
    with urllib.request.urlopen(urllib.request.Request(loc,headers={"User-Agent":"bb-scanner-research"}),timeout=60) as r:b=r.read()
    z=zipfile.ZipFile(io.BytesIO(b)); ev=pd.read_csv(z.open("prewindow_events_enriched.csv"))
    fast,fitmeta,train_q=train_frozen(); rows=[]
    for _,e in ev.iterrows():
        r=e.to_dict()
        try:r.update(score_event(e,fast,train_q))
        except Exception as ex:r.update({"pa_valid":False,"pa_error":repr(ex)})
        rows.append(r)
    d=pd.DataFrame(rows); d.to_csv(OUT/"events_scored.csv",index=False)
    if len(d)!=24: raise RuntimeError(f"expected 24 parent events, got {len(d)}")
    if d.duplicated(["symbol","signal_ts"]).any(): raise RuntimeError("duplicate parent events")
    if int(d.pa_valid.fillna(False).sum())==0: raise RuntimeError("zero PA-valid events")
    cand=d[d.candidate_short==True].copy(); pa=d[d.pa_confirm==True].copy(); hybrid=cand[cand.pa_confirm==True].copy()
    summary={
      "frozen":{"bb_rule":"2-of-4","pa_threshold":TH,"min_hold_bars":MIN_HOLD,"confirm_bars":CONFIRM,"pa_fit_2020":fitmeta},
      "counts":{"parent":len(d),"bb_candidate":len(cand),"pa_valid":int(d.pa_valid.fillna(False).sum()),"pa_only_confirmed":len(pa),"bb_pa_confirmed":len(hybrid)},
      "pa_only_from_confirmation_tpsl24h":metrics(pa.pa_tpsl_24h),
      "pa_only_original_d2":metrics(pa.short_net_d2),
      "bb_candidate_d2":metrics(cand.short_net_d2),
      "bb_candidate_d3":metrics(cand.short_net_d3),
      "hybrid_original_d2":metrics(hybrid.short_net_d2),
      "hybrid_original_d3":metrics(hybrid.short_net_d3),
      "hybrid_from_confirmation_tpsl24h":metrics(hybrid.pa_tpsl_24h),
      "hybrid_from_confirmation_1h":metrics(hybrid.pa_short_ret_1h),
      "hybrid_from_confirmation_4h":metrics(hybrid.pa_short_ret_4h),
      "hybrid_from_confirmation_24h":metrics(hybrid.pa_short_ret_24h),
      "transfer_diagnostics":{"score_minhold_min":float(d.pa_score_at_minhold.min()),"score_minhold_median":float(d.pa_score_at_minhold.median()),"score_minhold_max":float(d.pa_score_at_minhold.max()),"max_abs_z":float(d.pa_max_abs_z.max()),"max_ood_features":int(d.pa_max_ood_features.max())},
      "warning":"Transfer test only. If scores saturate or features are far outside the frozen 2019 training envelope, quarantine the transfer result; do not interpret it as a valid PA filter."
    }
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2,default=str)); print(json.dumps(summary,indent=2,default=str))
if __name__=="__main__":main()

# workflow trigger: frozen transfer v1
