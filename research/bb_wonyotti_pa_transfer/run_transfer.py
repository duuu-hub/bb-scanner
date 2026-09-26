#!/usr/bin/env python3
from __future__ import annotations
import io,json,math,urllib.parse,urllib.request,zipfile
from pathlib import Path
import numpy as np,pandas as pd
import research.aoa_price_action_v1.price_action_v1 as v1
import research.aoa_price_action_v4.price_action_v4 as v4
import research.aoa_price_action_v41.price_action_v41 as v41

ROOT=Path(__file__).resolve().parents[2]; OUT=ROOT/"research"/"bb_wonyotti_pa_transfer"/"output"; OUT.mkdir(parents=True,exist_ok=True)
BB_ARTIFACT="https://api.github.com/repos/duuu-hub/bb-scanner/actions/artifacts/10781288564/zip"
TH=0.8354464189135131; MIN_HOLD=8; CONFIRM=1

def get_json(url):
    req=urllib.request.Request(url,headers={"User-Agent":"bb-scanner-research"})
    with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)

def candles(symbol,start,end):
    q=urllib.parse.urlencode({"category":"USDT-FUTURES","symbol":symbol,"interval":"15m","startTime":str(int(start.timestamp()*1000)),"endTime":str(int(end.timestamp()*1000)),"limit":"100"})
    j=get_json("https://api.bitget.com/api/v3/market/history-candles?"+q)
    if j.get("code")!="00000": raise RuntimeError((symbol,j))
    a=j.get("data",[])
    d=pd.DataFrame(a,columns=["timestamp_ms","open","high","low","close","base_volume","quote_volume"])
    for c in d.columns:d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d.drop_duplicates("timestamp_ms").sort_values("timestamp_ms").reset_index(drop=True)
    d["bar_start"]=pd.to_datetime(d.timestamp_ms,unit="ms",utc=True); d["bar_end_s"]=(d.timestamp_ms//1000+900).astype("int64")
    return v1.add_price_action_features(d)

def train_frozen():
    c=v1.load_pa_candles(); rows,dec=v41.build_competing_rows(c)
    tr=dec[dec.year==2019].copy(); va=dec[dec.year==2020].copy()
    model,m,_=v41.fit_competing(tr,va); return v4.compile_fast(model),m

def score_event(ev,fast):
    ts=pd.to_datetime(int(ev.signal_ts),unit="ms",utc=True)
    d=candles(ev.symbol,ts-pd.Timedelta(days=8),ts+pd.Timedelta(hours=26))
    before=d[d.bar_end_s<=int(ev.signal_ts/1000)]
    after=d[d.bar_end_s>int(ev.signal_ts/1000)]
    if len(before)<672 or len(after)<8:return {"pa_valid":False}
    prev=before.iloc[-1]; side=-1; entry=float(ev.signal_price); ets=int(ev.signal_ts/1000)
    ectx=v1.entry_context(prev,side); state={"prev_close":entry,"path_bps":0.0,"mfe_bps":0.0,"mae_bps":0.0}
    streak=0; confirm=None; scores=[]
    for n,(_,r) in enumerate(after.iterrows(),1):
        f=v1.hazard_state(r,side,entry,ets,ectx); f.update(v4.update_path_state(state,r,side,entry))
        p=v4.fast_prob(fast,f); scores.append(p)
        trig=n>=MIN_HOLD and p>=TH; streak=streak+1 if trig else 0
        if streak>=CONFIRM: confirm=r; break
    out={"pa_valid":True,"pa_max_score_24h":float(max(scores)) if scores else np.nan,"pa_confirm":confirm is not None}
    if confirm is None:return out
    cp=float(confirm.close); ct=confirm.bar_end_s
    out.update({"pa_confirm_ts":int(ct*1000),"pa_confirm_delay_h":float((ct-ets)/3600),"pa_confirm_price":cp})
    fut=d[d.bar_end_s>ct].copy()
    for h in [1,4,24]:
        z=fut[fut.bar_end_s<=ct+h*3600]
        out[f"pa_short_ret_{h}h"]=np.nan if z.empty else float((1-z.iloc[-1].close/cp)*100)
    # same frozen BB payoff shape: TP +9.88 / SL -5.12; if both hit same 15m bar => conservative SL
    tp=cp*0.90; sl=cp*1.05; result=np.nan; bars=0
    for _,r in fut.iterrows():
        bars+=1; hit_tp=r.low<=tp; hit_sl=r.high>=sl
        if hit_sl: result=-5.12; break
        if hit_tp: result=9.88; break
        if bars>=96: break
    out["pa_tpsl_24h"]=result; return out

def metrics(x):
    x=pd.to_numeric(pd.Series(x),errors="coerce").dropna()
    if x.empty:return {"n":0,"avg":None,"sum":None,"wr":None,"pf":None}
    gp=x[x>0].sum(); gl=-x[x<0].sum()
    return {"n":len(x),"avg":float(x.mean()),"sum":float(x.sum()),"wr":float((x>0).mean()*100),"pf":None if gl==0 else float(gp/gl)}

def main():
    # exact frozen replay artifact is public; GitHub Actions can fetch it without mutating source data
    req=urllib.request.Request(BB_ARTIFACT,headers={"Authorization":"Bearer "+__import__("os").environ["GITHUB_TOKEN"],"User-Agent":"bb-scanner-research"})
    with urllib.request.urlopen(req) as r:b=r.read()
    z=zipfile.ZipFile(io.BytesIO(b)); ev=pd.read_csv(z.open("prewindow_events_enriched.csv"))
    fast,fitmeta=train_frozen(); rows=[]
    for _,e in ev.iterrows():
        r=e.to_dict()
        try:r.update(score_event(e,fast))
        except Exception as ex:r.update({"pa_valid":False,"pa_error":repr(ex)})
        rows.append(r)
    d=pd.DataFrame(rows); d.to_csv(OUT/"events_scored.csv",index=False)
    cand=d[d.candidate_short==True].copy(); hybrid=cand[cand.pa_confirm==True].copy()
    summary={
      "frozen":{"bb_rule":"2-of-4","pa_threshold":TH,"min_hold_bars":MIN_HOLD,"confirm_bars":CONFIRM,"pa_fit_2020":fitmeta},
      "counts":{"parent":len(d),"bb_candidate":len(cand),"pa_valid":int(d.pa_valid.fillna(False).sum()),"bb_pa_confirmed":len(hybrid)},
      "bb_candidate_d2":metrics(cand.short_net_d2),
      "bb_candidate_d3":metrics(cand.short_net_d3),
      "hybrid_original_d2":metrics(hybrid.short_net_d2),
      "hybrid_original_d3":metrics(hybrid.short_net_d3),
      "hybrid_from_confirmation_tpsl24h":metrics(hybrid.pa_tpsl_24h),
      "hybrid_from_confirmation_1h":metrics(hybrid.pa_short_ret_1h),
      "hybrid_from_confirmation_4h":metrics(hybrid.pa_short_ret_4h),
      "hybrid_from_confirmation_24h":metrics(hybrid.pa_short_ret_24h),
      "warning":"Transfer test: BTC/AoA PA model trained on 2019 behavior, applied frozen to 2024-26 alt events. No alt PnL tuning. Same-bar TP+SL is conservatively SL."
    }
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2,default=str)); print(json.dumps(summary,indent=2,default=str))
if __name__=="__main__":main()

# workflow trigger: frozen transfer v1
