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

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--root",default="near");ap.add_argument("--out",default="near_analysis.json");a=ap.parse_args()
    sf=glob.glob(a.root+"/**/setups_*.csv.gz",recursive=True);ef=glob.glob(a.root+"/**/events_*.csv.gz",recursive=True)
    assert len(sf)==8,(len(sf),sf);assert len(ef)==8,(len(ef),ef)
    setups=pd.concat([pd.read_csv(f,usecols=["symbol","signal_ts","dist_pct","atr_pct"]) for f in sf],ignore_index=True)
    events=pd.concat([pd.read_csv(f) for f in ef],ignore_index=True)
    # Cross-sectional volatility ranks use only information available at this exact 4H OPEN.
    setups["vol_rank_all"]=setups.groupby("signal_ts")["atr_pct"].rank(pct=True,method="average")
    setups["n_bear_ts"]=setups.groupby("signal_ts")["symbol"].transform("size")
    near=setups[(setups.dist_pct>0)&(setups.dist_pct<=3)].copy()
    near["vol_rank_near"]=near.groupby("signal_ts")["atr_pct"].rank(pct=True,method="average")
    near["n_near_ts"]=near.groupby("signal_ts")["symbol"].transform("size")
    f=near[["symbol","signal_ts","vol_rank_all","vol_rank_near","n_bear_ts","n_near_ts"]]
    events=events.merge(f,on=["symbol","signal_ts"],how="left",validate="many_to_one")
    events["signal_dt"]=pd.to_datetime(events.signal_ts,unit="ms",utc=True)
    events["exit_dt"]=pd.to_datetime(events.exit_ts,unit="ms",utc=True)
    events["year"]=events.signal_dt.dt.year.astype(int)

    filters={
      "ALL":lambda x:np.ones(len(x),dtype=bool),
      "ATR_GE_2":lambda x:x.atr_pct>=2,
      "ATR_GE_3":lambda x:x.atr_pct>=3,
      "ATR_GE_4":lambda x:x.atr_pct>=4,
      "ATR_GE_5":lambda x:x.atr_pct>=5,
      "ALLBEAR_TOP30":lambda x:x.vol_rank_all>=.70,
      "ALLBEAR_TOP20":lambda x:x.vol_rank_all>=.80,
      "ALLBEAR_TOP10":lambda x:x.vol_rank_all>=.90,
      "NEAR_TOP30":lambda x:x.vol_rank_near>=.70,
      "NEAR_TOP20":lambda x:x.vol_rank_near>=.80,
      "NEAR_TOP10":lambda x:x.vol_rank_near>=.90,
    }
    rows=[]
    for r in sorted(events.r.unique()):
      re=events[events.r.eq(r)]
      train=re[(re.signal_dt<CUT)&(re.exit_dt<CUT)].copy()
      valid=re[re.signal_dt>=CUT].copy()
      for lo,hi in DIST_BUCKETS:
        td=train[(train.dist_pct>lo)&(train.dist_pct<=hi)]
        vd=valid[(valid.dist_pct>lo)&(valid.dist_pct<=hi)]
        for name,fn in filters.items():
          tz=td[fn(td)];vz=vd[fn(vd)]
          rec={"r":float(r),"dist":f"{lo:g}-{hi:g}","vol_filter":name,
               "train_20bp":metric(tz,.20),"train_40bp":metric(tz,.40),
               "valid_20bp":metric(vz,.20),"valid_40bp":metric(vz,.40)}
          # Year metrics are descriptive; candidate ranking below uses TRAIN aggregate only.
          rec["train_years"]={str(int(y)):metric(g,.20) for y,g in tz.groupby("year")}
          rec["valid_years"]={str(int(y)):metric(g,.20) for y,g in vz.groupby("year")}
          rows.append(rec)
    ranked=sorted(rows,key=lambda q:(
      -999 if q["train_40bp"]["pf"] is None else q["train_40bp"]["pf"],
      q["train_40bp"]["n"]),reverse=True)
    robust=[q for q in rows if q["train_20bp"]["n"]>=500 and (q["train_20bp"]["pf"] or 0)>1 and (q["train_40bp"]["pf"] or 0)>1]
    out={"definition":{"entry":"4H OPEN immediate short only","distance":"(PSAR_ref-fill)/fill","sl":"PSAR_ref","r":[2,4],
                       "volatility":"prior closed 4H ATR14 / current OPEN; ranks cross-sectional at same 4H OPEN",
                       "train":"signal<2025-01-01 AND exit<2025-01-01","validation":"signal>=2025-01-01",
                       "costs_pct":COSTS,"selection":"rank/choose on Train only; validation reported afterward"},
         "setups":len(setups),"near_setups":len(near),"events":len(events),"robust_train_count":len(robust),
         "train_top":ranked[:40],"robust_train":sorted(robust,key=lambda q:q["train_40bp"]["pf"],reverse=True),"all":rows}
    json.dump(out,open(a.out,"w"),indent=2)
    print("NEAR_ANALYZE_PASS","setups",len(setups),"near",len(near),"events",len(events),"robust",len(robust),flush=True)
    for q in ranked[:24]:
      t2=q["train_20bp"];t4=q["train_40bp"];v2=q["valid_20bp"];v4=q["valid_40bp"]
      print("TOP",q["r"],q["dist"],q["vol_filter"],
            "trainN",t2["n"],"WR",None if t2["win_pct"] is None else round(t2["win_pct"],2),
            "PF20",None if t2["pf"] is None else round(t2["pf"],3),"PF40",None if t4["pf"] is None else round(t4["pf"],3),
            "EV40",None if t4["ev_pct"] is None else round(t4["ev_pct"],4),
            "validPF20",None if v2["pf"] is None else round(v2["pf"],3),"validPF40",None if v4["pf"] is None else round(v4["pf"],3),
            "validN",v2["n"],flush=True)

if __name__=="__main__":main()
