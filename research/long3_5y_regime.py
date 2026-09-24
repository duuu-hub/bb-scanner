#!/usr/bin/env python3
from pathlib import Path
import json, math
import numpy as np, pandas as pd

ROOT=Path("canonical_um")
OUT=Path("artifacts"); OUT.mkdir(exist_ok=True)
TF={"15M":"15min","30M":"30min","1H":"1h","4H":"4h","12H":"12h","1D":"1D","1W":"7D"}
CFG={"L1":{"tp":10.0,"sl":5.0,"hold":48},"L2":{"tp":10.0,"sl":2.5,"hold":4},"L3":{"tp":10.0,"sl":4.0,"hold":48}}
FEE=.12

def resample(df,rule):
    x=df.set_index("dt").resample(rule,origin="epoch",label="left",closed="left").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
    # live BB: 19 prior completed TF closes + current 15m boundary close
    return x

def signals(sym,df):
    df=df.sort_values("open_time").drop_duplicates("open_time").copy()
    df["dt"]=pd.to_datetime(df.open_time,unit="ms",utc=True)
    df=df.set_index("dt")
    base=df[["open","high","low","close"]].astype(float)
    idx=base.index
    above={}
    for name,rule in TF.items():
        r=base.resample(rule,origin="epoch",label="left",closed="left").agg({"close":"last"}).dropna()
        prev19=r["close"].rolling(19).agg(list) if False else None
        # compute sum/sumsq of 19 completed bars, shifted one TF
        c=r["close"]; s=c.rolling(19).sum().shift(1); ss=(c*c).rolling(19).sum().shift(1)
        # map each 15m boundary to its containing TF bucket
        buckets=idx.floor(rule) if name!="1W" else idx.floor("7D")
        sm=pd.Series(s.reindex(buckets).to_numpy(),index=idx)
        sqm=pd.Series(ss.reindex(buckets).to_numpy(),index=idx)
        # Match the frozen scanner: evaluate every 15m boundary at the\n        # new candle OPEN, using 19 fully completed TF closes + live price.\n        p=base["open"]\n        mean=(sm+p)/20.0
        var=((sqm+p*p)/20.0)-mean*mean
        upper=mean+2*np.sqrt(var.clip(lower=0))
        above[name]=p>upper
    A=pd.DataFrame(above,index=idx)
    exact=A.sum(axis=1)
    # Frozen momentum bases are 15m boundary opens 1h/4h earlier.\n    p=base["open"]\n    ret1=p.pct_change(4)*100; ret4=p.pct_change(16)*100\n    raw={
      "L1":(exact>=6)&(ret1>=10),
      "L2":(exact>=6)&(ret4>=30),
      "L3":(exact==6)&(~A["4H"])
    }
    rows=[]
    for st,m in raw.items():
        trig=m & ~m.shift(1,fill_value=False)
        for t in idx[trig]:
            rows.append((sym,t,st,float(p.loc[t])))
    return rows,base

def trade(row,base):
    sym,t,st,entry=row; cfg=CFG[st]
    pos=base.index.get_indexer([t])[0]
    path=base.iloc[pos+1:pos+1+cfg["hold"]]
    if path.empty:return None
    tp=entry*(1+cfg["tp"]/100); sl=entry*(1-cfg["sl"]/100)
    out="TIME"; ex=float(path.iloc[-1].close); xt=path.index[-1]
    for tt,b in path.iterrows():
        ht=b.high>=tp; hs=b.low<=sl
        if hs: out="SL";ex=sl;xt=tt;break
        if ht: out="TP";ex=tp;xt=tt;break
    net=(ex/entry-1)*100-FEE
    return [sym,t,st,entry,xt,out,net]

def regime(btc):
    d=btc.resample("1D").close.last().dropna()
    ma=d.rolling(200).mean(); slope=ma-ma.shift(20)
    rg=pd.Series("SIDEWAYS",index=d.index)
    rg[(d>ma)&(slope>0)]="BULL"; rg[(d<ma)&(slope<0)]="BEAR"
    return rg

def main():
    files=sorted(ROOT.glob("*.parquet"))
    print("symbols",len(files),flush=True)
    btc=None; alltr=[]
    for i,p in enumerate(files,1):
        df=pd.read_parquet(p)
        if p.stem=="BTCUSDT":
            z=df.copy();z["dt"]=pd.to_datetime(z.open_time,unit="ms",utc=True);btc=z.set_index("dt")[["open","high","low","close"]].astype(float)
        try:
            sig,base=signals(p.stem,df)
            for s in sig:
                tr=trade(s,base)
                if tr: alltr.append(tr)
            if i%10==0: print(i,p.stem,"trades",len(alltr),flush=True)
        except Exception as e: print("ERR",p.stem,e,flush=True)
    tr=pd.DataFrame(alltr,columns=["symbol","signal_time","strategy","entry","exit_time","outcome","net_pct"])
    if btc is None: raise SystemExit("BTCUSDT missing")
    rg=regime(btc)
    days=tr.signal_time.dt.floor("D")
    tr["regime"]=rg.reindex(days).to_numpy()
    tr["year"]=tr.signal_time.dt.year
    tr.to_csv(OUT/"long3_5y_trades.csv",index=False)
    def summary(g):
        gp=g.net_pct[g.net_pct>0].sum();gl=-g.net_pct[g.net_pct<0].sum()
        return pd.Series({"n":len(g),"win_rate":(g.net_pct>0).mean()*100,"avg_net_pct":g.net_pct.mean(),"sum_net_pct":g.net_pct.sum(),"PF":gp/gl if gl else math.inf})
    res=pd.concat({"overall":summary(tr)},axis=1).T
    byreg=tr.groupby("regime",dropna=False).apply(summary,include_groups=False)
    byyr=tr.groupby("year").apply(summary,include_groups=False)
    byrs=tr.groupby(["regime","strategy"],dropna=False).apply(summary,include_groups=False)
    res.to_csv(OUT/"overall.csv");byreg.to_csv(OUT/"by_regime.csv");byyr.to_csv(OUT/"by_year.csv");byrs.to_csv(OUT/"by_regime_strategy.csv")
    print("OVERALL\n",res.to_string());print("BY_REGIME\n",byreg.to_string());print("BY_YEAR\n",byyr.to_string());print("BY_REGIME_STRATEGY\n",byrs.to_string())


if __name__ == "__main__":
    main()
