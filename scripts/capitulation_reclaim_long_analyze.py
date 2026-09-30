import argparse,glob,json
import numpy as np,pandas as pd

CUT=pd.Timestamp("2025-01-01",tz="UTC")
COSTS=(0.20,0.40)

def pf(x):
    x=pd.Series(x,dtype=float).dropna()
    pos=float(x[x>0].sum());neg=float(-x[x<0].sum())
    return (pos/neg) if neg>0 else (float("inf") if pos>0 else np.nan)

def metrics(g,cost):
    z=g[g.outcome.isin(["win","loss"]) & g.pnl_pct.notna()].copy()
    if z.empty:return {"n":0,"win_pct":None,"pf":None,"ev_pct":None,"median_hold_h":None}
    net=z.pnl_pct.to_numpy(float)-cost
    hold=(z.exit_ts.to_numpy(np.int64)-z.entry_ts.to_numpy(np.int64))/3_600_000.0
    return {"n":int(len(z)),"win_pct":float((net>0).mean()*100),"pf":float(pf(net)),
            "ev_pct":float(net.mean()),"median_hold_h":float(np.median(hold))}

def btc_features(root):
    fs=glob.glob(root+"/**/BTCUSDT.csv.gz",recursive=True);assert len(fs)==1,(len(fs),fs)
    d=pd.read_csv(fs[0],usecols=["open_time","close"]).sort_values("open_time")
    d["dt"]=pd.to_datetime(pd.to_numeric(d.open_time,errors="raise"),unit="ms",utc=True)
    d["close"]=pd.to_numeric(d.close,errors="raise")
    q=d.set_index("dt")["close"].resample("1D").last().dropna().to_frame("close")
    q["prev_close"]=q.close.shift(1)
    q["sma50"]=q.close.rolling(50,min_periods=50).mean().shift(1)
    q["sma200"]=q.close.rolling(200,min_periods=200).mean().shift(1)
    q["ret30"]=q.close.shift(1)/q.close.shift(31)-1
    return q.reset_index().rename(columns={"dt":"day"})

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--root",default="cap");ap.add_argument("--btc",default="btc");ap.add_argument("--out",default="cap_analysis.json");a=ap.parse_args()
    ef=glob.glob(a.root+"/**/events_*.csv.gz",recursive=True);assert len(ef)==8,(len(ef),ef)
    e=pd.concat([pd.read_csv(f) for f in ef],ignore_index=True)
    e["signal_dt"]=pd.to_datetime(e.signal_ts,unit="ms",utc=True)
    e["exit_dt"]=pd.to_datetime(e.exit_ts,unit="ms",utc=True)
    e["year"]=e.signal_dt.dt.year.astype(int);e["day"]=e.signal_dt.dt.floor("1D")
    e=e.merge(btc_features(a.btc),on="day",how="left",validate="many_to_one")
    e["bear200"]=(e.prev_close<e.sma200)&(e.sma50<e.sma200)
    e["btc_lt200"]=e.prev_close<e.sma200
    e["bear_mom30"]=(e.bear200)&(e.ret30<0)
    gates={"BEAR200":e.bear200,"ALL":pd.Series(True,index=e.index),
           "BTC_LT200":e.btc_lt200,"BEAR200_MOM30NEG":e.bear_mom30}
    rows=[]
    for (cfg,r),g in e.groupby(["config","r"],sort=False):
        for gn,mask in gates.items():
            z=g[mask.loc[g.index]].copy()
            tr=z[(z.signal_dt<CUT)&(z.exit_dt<CUT)]
            va=z[z.signal_dt>=CUT]
            rec={"config":cfg,"r":float(r),"gate":gn,
                 "train20":metrics(tr,.20),"train40":metrics(tr,.40),
                 "valid20":metrics(va,.20),"valid40":metrics(va,.40),
                 "train_years":{str(int(y)):metrics(x,.40) for y,x in tr.groupby("year")},
                 "valid_years":{str(int(y)):metrics(x,.40) for y,x in va.groupby("year")}}
            rows.append(rec)
    primary=[q for q in rows if q["gate"]=="BEAR200"]
    def pos_years(q):
        return sum(1 for v in q["train_years"].values() if v["n"]>=20 and (v["pf"] or 0)>1)
    ranked=sorted(primary,key=lambda q:(
        pos_years(q),
        -999 if q["train40"]["pf"] is None else q["train40"]["pf"],
        q["train40"]["n"]),reverse=True)
    robust=[q for q in primary if q["train40"]["n"]>=200 and (q["train20"]["pf"] or 0)>1 and
            (q["train40"]["pf"] or 0)>1 and pos_years(q)>=2]
    out={"definition":{"primary_regime":"BTC prior completed daily close < SMA200 AND SMA50 < SMA200",
                       "train":"signal<2025-01-01 AND exit<2025-01-01","validation":"signal>=2025-01-01",
                       "costs_pct":COSTS,"selection":"BEAR200 Train only; Holdout not used for ranking"},
         "events":len(e),"robust_train_count":len(robust),
         "top_train":ranked[:60],"robust_train":sorted(robust,key=lambda q:(pos_years(q),q["train40"]["pf"]),reverse=True),
         "all":rows}
    json.dump(out,open(a.out,"w"),indent=2)
    print("CAP_RECLAIM_ANALYZE_PASS","events",len(e),"robust",len(robust),flush=True)
    for q in ranked[:30]:
        t2=q["train20"];t4=q["train40"];v2=q["valid20"];v4=q["valid40"]
        print("TOP",q["config"],"R",q["r"],"years+",pos_years(q),
              "trainN",t4["n"],"WR",None if t4["win_pct"] is None else round(t4["win_pct"],2),
              "PF20",None if t2["pf"] is None else round(t2["pf"],3),
              "PF40",None if t4["pf"] is None else round(t4["pf"],3),
              "validN",v4["n"],
              "validPF20",None if v2["pf"] is None else round(v2["pf"],3),
              "validPF40",None if v4["pf"] is None else round(v4["pf"],3),flush=True)

if __name__=="__main__":main()
