from __future__ import annotations
import gzip, math
from pathlib import Path
import numpy as np, pandas as pd
DATA=Path("data5y"); OUT=Path("research_output/asl1_5y_oos"); OUT.mkdir(parents=True,exist_ok=True)
COST=.0025; TP=.10; SL=.06; HOLDS=[24,30]

def load(p):
    x=pd.read_csv(p,usecols=["open_time","open","high","low","close"])
    x=x.rename(columns={"open_time":"ts"}); x["dt"]=pd.to_datetime(x.ts,unit="ms",utc=True)
    for c in ["open","high","low","close"]: x[c]=pd.to_numeric(x[c],errors="coerce")
    return x.dropna().sort_values("ts").drop_duplicates("ts").reset_index(drop=True)

def tfclose(x,m):
    z=x.set_index("dt").resample(f"{m}min",label="left",closed="left").agg(close=("close","last")).dropna().reset_index()
    z["cts"]=(z.dt+pd.Timedelta(minutes=m)).astype("int64")//1_000_000
    return z

def dyn(live,ts,z,n=20):
    c=z.close.to_numpy(float); ct=z.cts.to_numpy(np.int64); idx=np.searchsorted(ct,ts,side="right")
    ps=np.r_[0.,np.cumsum(c)]; ps2=np.r_[0.,np.cumsum(c*c)]
    b=np.full(len(ts),np.nan); lo=b.copy()
    ok=idx>=n-1; j=idx[ok]; a=j-(n-1); lv=live[ok]
    mean=(ps[j]-ps[a]+lv)/n; var=np.maximum(0,(ps2[j]-ps2[a]+lv*lv)/n-mean*mean); sd=np.sqrt(var)
    b[ok]=mean; lo[ok]=mean-2*sd
    return b,lo

def feat(x):
    live=x.open.to_numpy(float); ts=x.ts.to_numpy(np.int64)
    z15=x[["dt","close"]].copy(); z15["cts"]=x.ts+900000
    b15,l15=dyn(live,ts,z15); b1,_=dyn(live,ts,tfclose(x,60)); b4,_=dyn(live,ts,tfclose(x,240))
    x=x.copy(); x["lower15"]=l15; x["basis1h"]=b1; x["basis4h"]=b4
    x["ret4h"]=x.open.pct_change(16)*100
    rv=x.open.pct_change().rolling(16,min_periods=12).std(ddof=1)*math.sqrt(16)*100
    x["rvpre"]=rv.shift(1); x["rvmed"]=x["rvpre"].shift(1).rolling(96,min_periods=48).median()
    return x

def signals(x,btcflat):
    below=x.open<x.lower15
    cross=below & ~below.shift(1,fill_value=False)
    m=cross & (x.open<x.basis1h) & (x.open<x.basis4h) & (x.ret4h<=-2) & (x.rvpre>x.rvmed)
    return np.flatnonzero((m & x.ts.map(btcflat).fillna(False).astype(bool)).to_numpy())

def sim(x,idx,hh,sym,side):
    out=[]; hb=hh*4
    for i in idx:
        ei=i+1
        if ei>=len(x): continue
        e=float(x.open.iloc[ei]); last=min(ei+hb-1,len(x)-1); px=float(x.close.iloc[last]); reason="TIME"; ex=last
        tp=e*(1-TP) if side=="SHORT" else e*(1+TP); sl=e*(1+SL) if side=="SHORT" else e*(1-SL)
        for j in range(ei,last+1):
            hi=float(x.high.iloc[j]); lo=float(x.low.iloc[j])
            if hi>=sl: px=sl; reason="SL"; ex=j; break
            if lo<=tp: px=tp; reason="TP"; ex=j; break
        r=1-px/e-COST
        out.append((sym,x.dt.iloc[ei],x.dt.iloc[ex],hh,r,reason))
    return out

files=list(DATA.rglob("*.csv.gz")); print("FILES",len(files),flush=True)
btcfile=next((p for p in files if p.name=="BTCUSDT.csv.gz"),None)
if btcfile is None: raise RuntimeError("BTCUSDT missing")
btc=feat(load(btcfile)); btcflat=pd.Series((btc.ret4h.abs()<=.5).to_numpy(),index=btc.ts.to_numpy())
rows=[]
for k,p in enumerate(files,1):
    try:
        x=feat(load(p)); idx=signals(x,btcflat)
        for hh in HOLDS:
            for side in ["LONG","SHORT"]: rows.extend(sim(x,idx,hh,p.name[:-7],side))
        print(f"[{k}/{len(files)}] {p.name} sig={len(idx)}",flush=True)
    except Exception as e: print("ERR",p,e,flush=True)
t=pd.DataFrame(rows,columns=["symbol","entry_dt","exit_dt","hold_h","side","net_ret","reason"]); t.to_csv(OUT/"trades.csv",index=False)
def stat(g):
    r=g.net_ret.astype(float); gp=r[r>0].sum(); gl=-r[r<0].sum(); eq=(1+r).cumprod(); dd=eq/eq.cummax()-1
    return pd.Series({"trades":len(g),"win_rate_pct":(r>0).mean()*100,"avg_net_pct":r.mean()*100,"PF":gp/gl if gl>0 else np.inf,"sum_net_pct":r.sum()*100,"trade_seq_MDD_pct":dd.min()*100})
allstats=t.groupby(["side","hold_h"]).apply(stat,include_groups=False).reset_index(); allstats.to_csv(OUT/"overall.csv",index=False)
t["year"]=pd.to_datetime(t.entry_dt,utc=True).dt.year
yr=t.groupby(["side","hold_h","year"]).apply(stat,include_groups=False).reset_index(); yr.to_csv(OUT/"yearly.csv",index=False)
ss=t.groupby(["side","hold_h","symbol"]).apply(stat,include_groups=False).reset_index().sort_values(["side","hold_h","sum_net_pct"],ascending=[True,True,False]); ss.to_csv(OUT/"symbols.csv",index=False)
print("
=== OVERALL ===
"+allstats.to_string(index=False)); print("
=== YEARLY ===
"+yr.to_string(index=False))
print("
=== TOP/BOTTOM SYMBOLS ===")
for h in HOLDS:
 q=ss[ss.hold_h==h]; print("
HOLD",h); print(pd.concat([q.head(10),q.tail(10)]).to_string(index=False))
