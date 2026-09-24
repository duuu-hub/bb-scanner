from __future__ import annotations

import io, math, time, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests

OUT_ROOT=Path("research_output/btc_eth_regime_stage7_walkforward")
SYMBOLS=("BTCUSDT","ETHUSDT")
BINANCE_BASE="https://data.binance.vision/data/spot/monthly/klines"
START_MONTH=pd.Timestamp("2017-08-01",tz="UTC")
END_MONTH=pd.Timestamp("2026-08-01",tz="UTC")
WINDOW=30
FIXED_ER=0.193654
RT_COST=0.25

PERIODS=(
 ("BACKWARD_2017_2019",pd.Timestamp("2017-09-15",tz="UTC"),pd.Timestamp("2019-08-10",tz="UTC")),
 ("OLD_2019_2023",pd.Timestamp("2019-08-10",tz="UTC"),pd.Timestamp("2024-01-01",tz="UTC")),
 ("VALIDATION_2024_2025H1",pd.Timestamp("2024-01-01",tz="UTC"),pd.Timestamp("2025-07-01",tz="UTC")),
 ("HOLDOUT_2025H2_2026AUG",pd.Timestamp("2025-07-01",tz="UTC"),pd.Timestamp("2026-09-01",tz="UTC")),
 ("FULL",pd.Timestamp("2017-09-15",tz="UTC"),pd.Timestamp("2026-09-01",tz="UTC")),
)

def months(a,b):
    x=a
    while x<=b:
        yield x
        x=x+pd.offsets.MonthBegin(1)

def dt(v):
    z=pd.to_numeric(v,errors="coerce")
    ms=np.where(z>1e14,z/1000.0,z)
    return pd.to_datetime(ms,unit="ms",utc=True,errors="coerce")

def load(symbol):
    cols=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    ses=requests.Session(); ses.headers.update({"User-Agent":"bb-stage7/1"})
    fs=[]
    for m in months(START_MONTH,END_MONTH):
        ym=m.strftime("%Y-%m"); name=f"{symbol}-1d-{ym}.zip"
        r=ses.get(f"{BINANCE_BASE}/{symbol}/1d/{name}",timeout=30)
        if r.status_code==404: continue
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            mem=[q for q in z.namelist() if q.endswith(".csv")]
            if mem:
                with z.open(mem[0]) as fh: fs.append(pd.read_csv(fh,header=None,names=cols))
        time.sleep(.003)
    x=pd.concat(fs,ignore_index=True)
    x["datetime_utc"]=dt(x["open_time"])
    for c in ("open","close"): x[c]=pd.to_numeric(x[c],errors="coerce")
    return x.dropna(subset=["datetime_utc","open","close"]).drop_duplicates("datetime_utc").sort_values("datetime_utc")[["datetime_utc","open","close"]].reset_index(drop=True)

def panel(data):
    ps={}
    for s in SYMBOLS:
        d=data[s].copy(); c=d["close"]
        d[f"{s}_ret30"]=c/c.shift(WINDOW)-1
        path=c.diff().abs().rolling(WINDOW,min_periods=15).sum()
        d[f"{s}_er30"]=(c-c.shift(WINDOW)).abs()/path.replace(0,np.nan)
        d=d.rename(columns={"open":f"{s}_open","close":f"{s}_close"})
        ps[s]=d
    x=ps["BTCUSDT"].merge(ps["ETHUSDT"],on="datetime_utc")
    x["agree_up"]=(x["BTCUSDT_ret30"]>0)&(x["ETHUSDT_ret30"]>0)
    x["market_er"]=(x["BTCUSDT_er30"]+x["ETHUSDT_er30"])/2
    x["er_wf_2y"]=x["market_er"].shift(1).rolling(730,min_periods=365).median()
    x["er_wf_3y"]=x["market_er"].shift(1).rolling(1095,min_periods=365).median()
    x["p_fixed"]=(x["agree_up"]&(x["market_er"]>=FIXED_ER)).astype(float)
    x["p_wf2"]=(x["agree_up"]&(x["market_er"]>=x["er_wf_2y"])).astype(float)
    x["p_wf3"]=(x["agree_up"]&(x["market_er"]>=x["er_wf_3y"])).astype(float)
    x["p_noer"]=x["agree_up"].astype(float)
    x["p_buyhold"]=1.0
    return x

def next_open_ret(x,pcol,delay=0,cost=RT_COST):
    p=x[pcol].shift(delay).fillna(0.0).shift(1).fillna(0.0)
    r=((x["BTCUSDT_close"]/x["BTCUSDT_open"]-1)+(x["ETHUSDT_close"]/x["ETHUSDT_open"]-1))*50.0
    turn=p.diff().abs().fillna(p.abs())
    return p*r-turn*(cost/2.0),p

def perf(v):
    a=np.asarray(v,dtype=float); a=a[np.isfinite(a)]
    if not len(a): return {}
    w=np.prod(1+a/100); yrs=len(a)/365.25
    curve=np.cumprod(1+a/100); full=np.r_[1.,curve]; peak=np.maximum.accumulate(full); dd=(full/peak-1)*100
    sd=np.std(a); downside=np.std(np.minimum(a,0))
    return {
      "days":len(a),"return_pct":(w-1)*100,
      "cagr_pct":(w**(1/yrs)-1)*100 if w>0 else math.nan,
      "sharpe":np.mean(a)/sd*math.sqrt(365.25) if sd>0 else math.nan,
      "sortino":np.mean(a)/downside*math.sqrt(365.25) if downside>0 else math.nan,
      "mdd_pct":float(dd.min()),
    }

def summarize(x,name,r,p):
    rows=[]
    for per,a,b in PERIODS:
        m=(x["datetime_utc"]>=a)&(x["datetime_utc"]<b)
        q=perf(r[m].to_numpy())
        rows.append({"strategy":name,"period":per,**q,
                     "active_day_pct":float((p[m]!=0).mean()*100),
                     "avg_er_threshold":float(x.loc[m,{"WF2": "er_wf_2y","WF3":"er_wf_3y"}.get(name,"market_er")].mean()) if name in ("WF2","WF3") else math.nan})
    return rows

def rolling(x,r,p,days):
    rows=[]; cur=max(x["datetime_utc"].min()+pd.Timedelta(days=400),pd.Timestamp("2018-09-01",tz="UTC")); end=x["datetime_utc"].max()
    while cur+pd.Timedelta(days=days)<=end:
        stop=cur+pd.Timedelta(days=days); m=(x["datetime_utc"]>=cur)&(x["datetime_utc"]<stop)
        rows.append({"start":cur,"end":stop,"window_days":days,**perf(r[m].to_numpy()),"active_day_pct":float((p[m]!=0).mean()*100)})
        cur+=pd.Timedelta(days=30)
    return pd.DataFrame(rows)

def block_bootstrap(a,block,reps=2000,seed=123):
    a=np.asarray(a,dtype=float); a=a[np.isfinite(a)]; n=len(a)
    rng=np.random.default_rng(seed)
    cagr=[]; mdds=[]; positive=0
    starts=np.arange(0,max(1,n-block+1))
    for _ in range(reps):
        out=[]
        while len(out)<n:
            s=int(rng.choice(starts)); out.extend(a[s:s+block].tolist())
        z=np.asarray(out[:n]); w=np.prod(1+z/100); yrs=n/365.25
        if w>1: positive+=1
        cagr.append((w**(1/yrs)-1)*100 if w>0 else -100)
        curve=np.cumprod(1+z/100); full=np.r_[1.,curve]; dd=(full/np.maximum.accumulate(full)-1)*100
        mdds.append(float(dd.min()))
    return {"block_days":block,"reps":reps,"positive_final_pct":positive/reps*100,
            "cagr_p05":float(np.quantile(cagr,.05)),"cagr_median":float(np.median(cagr)),"cagr_p95":float(np.quantile(cagr,.95)),
            "mdd_p05_worse":float(np.quantile(mdds,.05)),"mdd_median":float(np.median(mdds))}

def main():
    OUT_ROOT.mkdir(parents=True,exist_ok=True)
    x=panel({s:load(s) for s in SYMBOLS})
    configs={"FIXED":"p_fixed","WF2":"p_wf2","WF3":"p_wf3","NO_ER":"p_noer","BUYHOLD":"p_buyhold"}
    allrows=[]; series={}
    for name,col in configs.items():
        r,p=next_open_ret(x,col)
        series[name]=(r,p); allrows.extend(summarize(x,name,r,p))
    pd.DataFrame(allrows).to_csv(OUT_ROOT/"strategy_summary.csv",index=False)

    # Timing sensitivity of the post-hoc LONG candidate: no retuning.
    drows=[]
    for d in (0,1,2,7,14,30):
        r,p=next_open_ret(x,"p_fixed",delay=d)
        for per,a,b in PERIODS:
            if per=="FULL" or per.startswith("VALIDATION") or per.startswith("HOLDOUT"):
                m=(x["datetime_utc"]>=a)&(x["datetime_utc"]<b)
                drows.append({"delay_days":d,"period":per,**perf(r[m].to_numpy())})
    pd.DataFrame(drows).to_csv(OUT_ROOT/"delay_stress.csv",index=False)

    # Rolling stability comparison: fixed candidate versus causal walk-forward ER threshold.
    rollrows=[]
    for name in ("FIXED","WF2","WF3","NO_ER"):
        r,p=series[name]
        for days in (365,730):
            q=rolling(x,r,p,days); q["strategy"]=name; rollrows.append(q)
    rolls=pd.concat(rollrows,ignore_index=True); rolls.to_csv(OUT_ROOT/"rolling.csv",index=False)
    rs=[]
    for (name,days),g in rolls.groupby(["strategy","window_days"]):
        rs.append({"strategy":name,"window_days":int(days),"windows":len(g),
                   "positive_pct":float((g["return_pct"]>0).mean()*100),
                   "median_return_pct":float(g["return_pct"].median()),
                   "worst_return_pct":float(g["return_pct"].min()),
                   "median_sharpe":float(g["sharpe"].median()),
                   "worst_sharpe":float(g["sharpe"].min()),
                   "worst_mdd_pct":float(g["mdd_pct"].min())})
    pd.DataFrame(rs).to_csv(OUT_ROOT/"rolling_summary.csv",index=False)

    # Moving block bootstrap on full fixed candidate and the recent 2024+ slice.
    boots=[]
    r,_=series["FIXED"]
    fullm=(x["datetime_utc"]>=pd.Timestamp("2017-09-15",tz="UTC"))&(x["datetime_utc"]<pd.Timestamp("2026-09-01",tz="UTC"))
    recentm=(x["datetime_utc"]>=pd.Timestamp("2024-01-01",tz="UTC"))&(x["datetime_utc"]<pd.Timestamp("2026-09-01",tz="UTC"))
    for label,m in (("FULL",fullm),("RECENT_2024_PLUS",recentm)):
        for block in (30,90):
            boots.append({"sample":label,**block_bootstrap(r[m].to_numpy(),block)})
    pd.DataFrame(boots).to_csv(OUT_ROOT/"block_bootstrap.csv",index=False)

    print("=== STRATEGY SUMMARY ===")
    print(pd.DataFrame(allrows).to_string(index=False))
    print("\n=== DELAY STRESS ===")
    print(pd.DataFrame(drows).to_string(index=False))
    print("\n=== ROLLING SUMMARY ===")
    print(pd.DataFrame(rs).to_string(index=False))
    print("\n=== BLOCK BOOTSTRAP ===")
    print(pd.DataFrame(boots).to_string(index=False))

if __name__=="__main__":
    main()
