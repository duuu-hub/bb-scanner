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

def sim(x,idx,hh,sym,side,btcfeat):
    out=[]; hb=hh*4
    for i in idx:
        ei=i+1
        if ei>=len(x): continue
        e=float(x.open.iloc[ei]); last=min(ei+hb-1,len(x)-1); px=float(x.close.iloc[last]); reason="TIME"; ex=last
        tp=e*(1-TP) if side=="SHORT" else e*(1+TP); sl=e*(1+SL) if side=="SHORT" else e*(1-SL)
        for j in range(ei,last+1):
            hi=float(x.high.iloc[j]); lo=float(x.low.iloc[j])
            if side=="SHORT":
                if hi>=sl: px=sl; reason="SL"; ex=j; break
                if lo<=tp: px=tp; reason="TP"; ex=j; break
            else:
                if lo<=sl: px=sl; reason="SL"; ex=j; break
                if hi>=tp: px=tp; reason="TP"; ex=j; break
        r=(1-px/e-COST) if side=="SHORT" else (px/e-1-COST)
        # persist entry-state features so regime filters can be tested without lookahead
        bf=btcfeat.get(int(x.ts.iloc[i]),{})
        out.append((sym,x.dt.iloc[ei],x.dt.iloc[ex],hh,side,r,reason,float(x.ret4h.iloc[i]),float(x.rvpre.iloc[i]),float(x.rvmed.iloc[i]),bf.get("ret4h",np.nan),bf.get("ret24h",np.nan),bf.get("open",np.nan),bf.get("ma30",np.nan),bf.get("ma40",np.nan),bf.get("ma50",np.nan),bf.get("ma60",np.nan),bf.get("ma80",np.nan),bf.get("ma100",np.nan),bf.get("ma200",np.nan),bf.get("btc_rv24",np.nan)))
    return out

files=list(DATA.rglob("*.csv.gz")); print("FILES",len(files),flush=True)
btcfile=next((p for p in files if p.name=="BTCUSDT.csv.gz"),None)
if btcfile is None: raise RuntimeError("BTCUSDT missing")
btc=feat(load(btcfile)); btcflat=pd.Series((btc.ret4h.abs()<=.5).to_numpy(),index=btc.ts.to_numpy())
# entry-known BTC regime features
btc["ret24h"]=btc.open.pct_change(96)*100
btc["ma200"]=btc.open.rolling(96*200,min_periods=96*120).mean()
btc["ma30"]=btc.open.rolling(96*30,min_periods=96*20).mean()
    btc["ma40"]=btc.open.rolling(96*40,min_periods=96*25).mean()
    btc["ma50"]=btc.open.rolling(96*50,min_periods=96*30).mean()
    btc["ma60"]=btc.open.rolling(96*60,min_periods=96*35).mean()
    btc["ma80"]=btc.open.rolling(96*80,min_periods=96*50).mean()
    btc["ma100"]=btc.open.rolling(96*100,min_periods=96*60).mean()
btc["btc_rv24"]=btc.open.pct_change().rolling(96,min_periods=48).std(ddof=1)*math.sqrt(96)*100
btcfeat=btc.set_index("ts")[["ret4h","ret24h","open","ma30","ma40","ma50","ma60","ma80","ma100","ma200","btc_rv24"]].to_dict("index")
rows=[]
for k,p in enumerate(files,1):
    try:
        x=feat(load(p)); idx=signals(x,btcflat)
        for hh in HOLDS:
            for side in ["LONG","SHORT"]: rows.extend(sim(x,idx,hh,p.name[:-7],side,btcfeat))
        print(f"[{k}/{len(files)}] {p.name} sig={len(idx)}",flush=True)
    except Exception as e: print("ERR",p,e,flush=True)
t=pd.DataFrame(rows,columns=["symbol","entry_dt","exit_dt","hold_h","side","net_ret","reason","asset_ret4h","asset_rvpre","asset_rvmed","btc_ret4h","btc_ret24h","btc_open","btc_ma30","btc_ma40","btc_ma50","btc_ma60","btc_ma80","btc_ma100","btc_ma200","btc_rv24"]); t.to_csv(OUT/"trades.csv",index=False)
def stat(g):
    r=g.net_ret.astype(float); gp=r[r>0].sum(); gl=-r[r<0].sum(); eq=(1+r).cumprod(); dd=eq/eq.cummax()-1
    return pd.Series({"trades":len(g),"win_rate_pct":(r>0).mean()*100,"avg_net_pct":r.mean()*100,"PF":gp/gl if gl>0 else np.inf,"sum_net_pct":r.sum()*100,"trade_seq_MDD_pct":dd.min()*100})
allstats=t.groupby(["side","hold_h"]).apply(stat,include_groups=False).reset_index(); allstats.to_csv(OUT/"overall.csv",index=False)
t["year"]=pd.to_datetime(t.entry_dt,utc=True).dt.year
yr=t.groupby(["side","hold_h","year"]).apply(stat,include_groups=False).reset_index(); yr.to_csv(OUT/"yearly.csv",index=False)
ss=t.groupby(["side","hold_h","symbol"]).apply(stat,include_groups=False).reset_index().sort_values(["side","hold_h","sum_net_pct"],ascending=[True,True,False]); ss.to_csv(OUT/"symbols.csv",index=False)
print("=== OVERALL ===")
print(allstats.to_string(index=False))
print("=== YEARLY ===")
print(yr.to_string(index=False))
print("=== TOP/BOTTOM SYMBOLS ===")
for h in HOLDS:
    q=ss[ss.hold_h==h]
    print("HOLD",h)
    print(pd.concat([q.head(10),q.tail(10)]).to_string(index=False))
