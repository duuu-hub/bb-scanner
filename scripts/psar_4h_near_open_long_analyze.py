import argparse,glob,json
import numpy as np,pandas as pd

DIST_BUCKETS=((0.0,0.5),(0.5,1.0),(1.0,2.0),(2.0,3.0))
COSTS=(0.20,0.40)
CUT=pd.Timestamp("2025-01-01",tz="UTC")

def metric(z,cost):
    z=z[z.outcome.isin(["win","loss"]) & z.pnl_pct.notna()].copy()
    if z.empty:return {"n":0,"win_pct":None,"pf":None,"ev_pct":None,"median_hold_h":None}
    net=z.pnl_pct.to_numpy(float)-cost
    gp=net[net>0].sum();gl=-net[net<0].sum()
    hold=(z.exit_ts.to_numpy(np.int64)-z.fill_ts.to_numpy(np.int64))/3_600_000.0
    return {"n":int(len(z)),"win_pct":float((z.outcome=="win").mean()*100),
            "pf":float(gp/gl) if gl>0 else None,"ev_pct":float(net.mean()),
            "median_hold_h":float(np.median(hold))}

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
    ap=argparse.ArgumentParser();ap.add_argument("--root",default="near");ap.add_argument("--btc",default="btc");ap.add_argument("--out",default="near_long_analysis.json");a=ap.parse_args()
    ef=glob.glob(a.root+"/**/events_*.csv.gz",recursive=True);assert len(ef)==8,(len(ef),ef)
    events=pd.concat([pd.read_csv(f) for f in ef],ignore_index=True)
    events["signal_dt"]=pd.to_datetime(events.signal_ts,unit="ms",utc=True)
    events["exit_dt"]=pd.to_datetime(events.exit_ts,unit="ms",utc=True)
    events["year"]=events.signal_dt.dt.year.astype(int)
    events["day"]=events.signal_dt.dt.floor("1D")
    events=events.merge(btc_features(a.btc),on="day",how="left",validate="many_to_one")
    events["bear200"]=(events.prev_close<events.sma200)&(events.sma50<events.sma200)
    events["btc_lt200"]=events.prev_close<events.sma200
    events["mom30neg"]=events.ret30<0
    gates={
      "ALL":np.ones(len(events),dtype=bool),
      "BEAR200":events.bear200.to_numpy(bool),
      "BTC_LT200":events.btc_lt200.to_numpy(bool),
      "BEAR200_MOM30NEG":(events.bear200&events.mom30neg).to_numpy(bool),
    }
    rows=[]
    for r in sorted(events.r.unique()):
      for lo,hi in DIST_BUCKETS:
        base=(events.r.eq(r)&(events.dist_pct>lo)&(events.dist_pct<=hi))
        for gn,gm in gates.items():
          z=events[base & gm].copy()
          tr=z[(z.signal_dt<CUT)&(z.exit_dt<CUT)].copy()
          va=z[z.signal_dt>=CUT].copy()
          rec={"r":float(r),"dist":f"{lo:g}-{hi:g}","gate":gn,
               "train_20bp":metric(tr,.20),"train_40bp":metric(tr,.40),
               "valid_20bp":metric(va,.20),"valid_40bp":metric(va,.40),
               "train_years":{str(int(y)):{"20bp":metric(g,.20),"40bp":metric(g,.40)} for y,g in tr.groupby("year")},
               "valid_years":{str(int(y)):{"20bp":metric(g,.20),"40bp":metric(g,.40)} for y,g in va.groupby("year")}}
          rows.append(rec)
    # Primary ranking is fixed symmetric BEAR200 on TRAIN only.
    prim=[q for q in rows if q["gate"]=="BEAR200"]
    ranked=sorted(prim,key=lambda q:((-999 if q["train_40bp"]["pf"] is None else q["train_40bp"]["pf"]),q["train_40bp"]["n"]),reverse=True)
    robust=[q for q in prim if q["train_40bp"]["n"]>=500 and (q["train_20bp"]["pf"] or 0)>1 and (q["train_40bp"]["pf"] or 0)>1]
    out={"definition":{"side":"LONG","entry":"4H OPEN","distance":"(fill-PSAR_ref)/fill","sl":"PSAR_ref","R":[2,3,4,5,6,8],
                       "primary_regime":"BTC prior daily close < SMA200 AND SMA50 < SMA200; all based on prior completed daily bars",
                       "train":"signal<2025-01-01 AND exit<2025-01-01","validation":"signal>=2025-01-01","costs_pct":COSTS,
                       "selection":"fixed BEAR200; rank on Train only, validation reported after"},
         "events":len(events),"robust_train_count":len(robust),"train_top":ranked[:40],"robust_train":robust,"all":rows}
    json.dump(out,open(a.out,"w"),indent=2)
    print("NEAR_LONG_ANALYZE_PASS","events",len(events),"robust",len(robust),flush=True)
    for q in ranked[:24]:
      t2=q["train_20bp"];t4=q["train_40bp"];v2=q["valid_20bp"];v4=q["valid_40bp"]
      print("TOP",q["r"],q["dist"],q["gate"],
            "trainN",t2["n"],"WR",None if t2["win_pct"] is None else round(t2["win_pct"],2),
            "PF20",None if t2["pf"] is None else round(t2["pf"],3),"PF40",None if t4["pf"] is None else round(t4["pf"],3),
            "validPF20",None if v2["pf"] is None else round(v2["pf"],3),"validPF40",None if v4["pf"] is None else round(v4["pf"],3),
            "validN",v2["n"],flush=True)
if __name__=="__main__":main()
