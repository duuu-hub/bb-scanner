import argparse,glob,json,heapq,os
from collections import defaultdict
import pandas as pd,numpy as np

VARIANT="P12_SB0.1_R4"
CROWD_LO=30;CROWD_HI=50;SAME_MAX=10
COSTS=(0.20,0.40)

def load_events(root):
    fs=glob.glob(root+"/**/events_*.csv.gz",recursive=True);assert len(fs)==8,(len(fs),fs)
    cols=["variant","symbol","signal_ts","fill_ts","exit_ts","outcome","pnl_pct","stop_pct"]
    ds=[]
    for f in fs:
        d=pd.read_csv(f,usecols=cols)
        d=d[d.variant.eq(VARIANT)&d.outcome.isin(["win","loss"])&d.exit_ts.notna()&d.pnl_pct.notna()]
        ds.append(d)
    D=pd.concat(ds,ignore_index=True)
    for c in ("signal_ts","fill_ts","exit_ts","pnl_pct","stop_pct"):D[c]=pd.to_numeric(D[c],errors="coerce")
    D["signal_dt"]=pd.to_datetime(D.signal_ts,unit="ms",utc=True)
    D["exit_dt"]=pd.to_datetime(D.exit_ts,unit="ms",utc=True)
    D["year"]=D.signal_dt.dt.year.astype(int)
    return D

def crowd_counts(root):
    fs=glob.glob(root+"/**/setups_*.csv.gz",recursive=True);assert len(fs)==8,(len(fs),fs)
    ds=[]
    for f in fs:
        d=pd.read_csv(f,usecols=["variant","signal_ts","setup"])
        ds.append(d[d.variant.eq("P4T26_BASE")][["signal_ts","setup"]])
    S=pd.concat(ds,ignore_index=True)
    return S.groupby("signal_ts",as_index=False).agg(setups=("setup","sum"))

def btc_features(root):
    fs=glob.glob(root+"/**/BTCUSDT.csv.gz",recursive=True)
    assert len(fs)==1,(len(fs),fs)
    d=pd.read_csv(fs[0],usecols=["open_time","close"]).sort_values("open_time")
    d["dt"]=pd.to_datetime(pd.to_numeric(d.open_time,errors="raise"),unit="ms",utc=True)
    d["close"]=pd.to_numeric(d.close,errors="raise")
    # Binance daily bar = UTC calendar day. Only prior COMPLETED daily bars may be used.
    daily=d.set_index("dt")["close"].resample("1D").last().dropna().to_frame("close")
    daily["prev_close"]=daily.close.shift(1)
    daily["sma50"]=daily.close.rolling(50,min_periods=50).mean().shift(1)
    daily["sma100"]=daily.close.rolling(100,min_periods=100).mean().shift(1)
    daily["sma200"]=daily.close.rolling(200,min_periods=200).mean().shift(1)
    daily["ret30"]=daily.close.shift(1)/daily.close.shift(31)-1
    daily["ret90"]=daily.close.shift(1)/daily.close.shift(91)-1
    daily["sma200_slope30"]=daily["sma200"]/daily["sma200"].shift(30)-1
    daily=daily.reset_index().rename(columns={"dt":"day"})
    return daily

def select_stack(df):
    df=df.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort")
    h=[];active=defaultdict(int);keep=[];seq=0
    for r in df.itertuples():
        t=int(r.fill_ts)
        while h and h[0][0]<=t:
            _,_,s=heapq.heappop(h);active[s]-=1
        s=str(r.symbol)
        if active[s]>=SAME_MAX:continue
        keep.append(r.Index);active[s]+=1;seq+=1;heapq.heappush(h,(int(r.exit_ts),seq,s))
    return df.loc[keep].copy()

def stats(z,cost):
    net=z.pnl_pct.to_numpy(float)-cost
    gp=net[net>0].sum();gl=-net[net<0].sum()
    return {"n":int(len(z)),"win_pct":float(z.outcome.eq("win").mean()*100) if len(z) else None,
            "pf":float(gp/gl) if gl>0 else None,"ev_pct":float(net.mean()) if len(net) else None,
            "sum_net_pct":float(net.sum()) if len(net) else 0.0}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--ledger",default="ledger");ap.add_argument("--sizing",default="sizing");ap.add_argument("--data",default="data");ap.add_argument("--out",default="btc_regime.json");a=ap.parse_args()
    D=load_events(a.ledger).merge(crowd_counts(a.sizing),on="signal_ts",how="inner",validate="many_to_one")
    D=D[D.setups.between(CROWD_LO,CROWD_HI)].copy()
    D["day"]=D.signal_dt.dt.floor("1D")
    B=btc_features(a.data)
    D=D.merge(B,on="day",how="left",validate="many_to_one")
    cut=pd.Timestamp("2025-01-01",tz="UTC")
    train=D[(D.signal_dt<cut)&(D.exit_dt<cut)].copy()
    valid=D[D.signal_dt>=cut].copy()
    gates={
      "ALL":lambda x:np.ones(len(x),dtype=bool),
      "BTC_LT_SMA200":lambda x:x.prev_close<x.sma200,
      "SMA50_LT_SMA200":lambda x:x.sma50<x.sma200,
      "BTC_LT_SMA200_AND_50LT200":lambda x:(x.prev_close<x.sma200)&(x.sma50<x.sma200),
      "RET30_LT0":lambda x:x.ret30<0,
      "RET90_LT0":lambda x:x.ret90<0,
      "SMA200_SLOPE30_LT0":lambda x:x.sma200_slope30<0,
      "BTC_LT_SMA100":lambda x:x.prev_close<x.sma100,
      "BTC_GTE_SMA200":lambda x:x.prev_close>=x.sma200,
      "SMA50_GTE_SMA200":lambda x:x.sma50>=x.sma200,
      "BTC_GTE_SMA200_AND_50GTE200":lambda x:(x.prev_close>=x.sma200)&(x.sma50>=x.sma200),
      "RET30_GTE0":lambda x:x.ret30>=0,
      "RET90_GTE0":lambda x:x.ret90>=0,
      "SMA200_SLOPE30_GTE0":lambda x:x.sma200_slope30>=0,
      "BTC_GTE_SMA100":lambda x:x.prev_close>=x.sma100,
    }
    out={"definition":{"variant":VARIANT,"crowding":[CROWD_LO,CROWD_HI],"same_symbol_max":SAME_MAX,
                       "btc_daily_features":"prior completed UTC daily bars only","train":"signal<2025-01-01 and exit<2025-01-01","validation":"signal>=2025-01-01","costs_pct":COSTS},"gates":{}}
    for name,fn in gates.items():
        tz=select_stack(train[fn(train)]);vz=select_stack(valid[fn(valid)])
        rec={"train":{},"valid":{},"train_years":{},"valid_years":{}}
        for cost in COSTS:
            tag=f"{int(cost*100)}bp";rec["train"][tag]=stats(tz,cost);rec["valid"][tag]=stats(vz,cost)
        for y,g in tz.groupby("year"):rec["train_years"][str(int(y))]={"20bp":stats(g,.20),"40bp":stats(g,.40)}
        for y,g in vz.groupby("year"):rec["valid_years"][str(int(y))]={"20bp":stats(g,.20),"40bp":stats(g,.40)}
        out["gates"][name]=rec
        t=rec["train"]["20bp"];v=rec["valid"]["20bp"];v4=rec["valid"]["40bp"]
        yrs=" ".join(f"{y}:{q['20bp']['pf']:.3f}" for y,q in rec["train_years"].items() if q["20bp"]["pf"] is not None)
        print("GATE",name,"TRAIN n",t["n"],"PF20",None if t["pf"] is None else round(t["pf"],3),"PF40",round(rec["train"]["40bp"]["pf"],3) if rec["train"]["40bp"]["pf"] else None,
              "VALID n",v["n"],"PF20",None if v["pf"] is None else round(v["pf"],3),"PF40",None if v4["pf"] is None else round(v4["pf"],3),"YEARS",yrs,flush=True)
    json.dump(out,open(a.out,"w"),indent=2)
    print("BTC_REGIME_DIAG_PASS",len(D),flush=True)
if __name__=="__main__":main()
