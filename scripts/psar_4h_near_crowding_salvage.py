import argparse,glob,json
import numpy as np,pandas as pd

CUT=pd.Timestamp("2025-01-01",tz="UTC")
COSTS=(0.20,0.40)

def met(z,cost):
    z=z[z.outcome.isin(["win","loss"]) & z.pnl_pct.notna()].copy()
    if z.empty:return {"n":0,"win_pct":None,"pf":None,"ev":None,"sum":0.0}
    net=z.pnl_pct.to_numpy(float)-cost
    gp=net[net>0].sum();gl=-net[net<0].sum()
    return {"n":int(len(z)),"win_pct":float((z.outcome=="win").mean()*100),
            "pf":float(gp/gl) if gl>0 else None,"ev":float(net.mean()),"sum":float(net.sum())}

def eval_mask(d,m):
    out={"train":{},"valid":{},"train_years":{},"valid_years":{}}
    tr=m&(d.signal_dt<CUT)&(d.exit_dt<CUT);va=m&(d.signal_dt>=CUT)
    for cost in COSTS:
        tag=f"{int(cost*100)}bp";out["train"][tag]=met(d[tr],cost);out["valid"][tag]=met(d[va],cost)
    for y in (2021,2022,2023,2024):
        out["train_years"][str(y)]=met(d[tr&(d.year==y)],.20)
    for y in (2025,2026):
        out["valid_years"][str(y)]=met(d[va&(d.year==y)],.20)
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--root",default="near");ap.add_argument("--out",default="crowd_salvage.json");a=ap.parse_args()
    sf=glob.glob(a.root+"/**/setups_*.csv.gz",recursive=True);ef=glob.glob(a.root+"/**/events_*.csv.gz",recursive=True)
    assert len(sf)==8,(len(sf),sf);assert len(ef)==8,(len(ef),ef)
    S=pd.concat([pd.read_csv(f,usecols=["symbol","signal_ts","dist_pct","atr_pct"]) for f in sf],ignore_index=True)
    E=pd.concat([pd.read_csv(f) for f in ef],ignore_index=True)
    E=E[E.r.eq(4.0)&E.outcome.isin(["win","loss"])&E.pnl_pct.notna()].copy()
    E["signal_dt"]=pd.to_datetime(E.signal_ts,unit="ms",utc=True);E["exit_dt"]=pd.to_datetime(E.exit_ts,unit="ms",utc=True);E["year"]=E.signal_dt.dt.year.astype(int)

    allc=S.groupby("signal_ts").size().rename("crowd_all").reset_index()
    near=S[(S.dist_pct>2)&(S.dist_pct<=3)].groupby("signal_ts").size().rename("crowd_2_3").reset_index()
    c=allc.merge(near,on="signal_ts",how="left");c["crowd_2_3"]=c.crowd_2_3.fillna(0)
    c=c.sort_values("signal_ts")
    # six 4H bars/day * ~180 days = 1080. Shift to exclude the current bar.
    c["med180_all"]=c.crowd_all.shift(1).rolling(1080,min_periods=180).median()
    c["med180_near"]=c.crowd_2_3.shift(1).rolling(1080,min_periods=180).median()
    c["ratio_all"]=c.crowd_all/c.med180_all.replace(0,np.nan)
    c["ratio_near"]=c.crowd_2_3/c.med180_near.replace(0,np.nan)
    E=E.merge(c,on="signal_ts",how="left",validate="many_to_one")
    base=(E.dist_pct>2)&(E.dist_pct<=3)

    rules=[("BASE",base)]
    # fixed interpretable absolute bins
    for lo,hi in [(0,10),(10,20),(20,30),(30,40),(40,60),(60,100),(100,10**9)]:
        rules.append((f"crowd_all_{lo}_{hi}",base&(E.crowd_all>lo)&(E.crowd_all<=hi)))
    for lo,hi in [(0,2),(2,5),(5,10),(10,20),(20,40),(40,10**9)]:
        rules.append((f"crowd23_{lo}_{hi}",base&(E.crowd_2_3>lo)&(E.crowd_2_3<=hi)))
    # broad normalized ranges fixed a priori
    for lo,hi in [(0,.7),(.7,1.0),(1.0,1.3),(1.3,1.7),(1.7,2.5),(2.5,99)]:
        rules.append((f"ratio_all_{lo}_{hi}",base&(E.ratio_all>lo)&(E.ratio_all<=hi)))
        rules.append((f"ratio_near_{lo}_{hi}",base&(E.ratio_near>lo)&(E.ratio_near<=hi)))

    rows=[]
    base_train=eval_mask(E,base)["train"]["20bp"]["n"]
    for name,m in rules:
        ev=eval_mask(E,m)
        t20=ev["train"]["20bp"];t40=ev["train"]["40bp"];v20=ev["valid"]["20bp"];v40=ev["valid"]["40bp"]
        cov=t20["n"]/base_train if base_train else 0
        minyr=min([ev["train_years"][str(y)]["pf"] if ev["train_years"][str(y)]["pf"] is not None and ev["train_years"][str(y)]["n"]>=300 else -999 for y in (2021,2022,2023,2024)])
        rows.append({"rule":name,"coverage_train":cov,"metrics":ev,"score_train":[t40["pf"] if t40["pf"] is not None else -999,minyr,t20["sum"]]})
    ranked=sorted(rows,key=lambda x:tuple(x["score_train"]),reverse=True)
    robust=[x for x in rows if x["coverage_train"]>=.05 and x["metrics"]["train"]["20bp"]["n"]>=1000
            and (x["metrics"]["train"]["20bp"]["pf"] or 0)>1 and (x["metrics"]["train"]["40bp"]["pf"] or 0)>1
            and all((x["metrics"]["train_years"][str(y)]["pf"] or 0)>1 for y in (2021,2022,2023,2024))]
    out={"definition":{"variant":"actual-distance 2-3%, R4, SL=PSAR","selection":"Train 2021-24 only; validation 2025-26 untouched","costs":COSTS,
                      "crowd_all":"all bearish PSAR setups at same 4H OPEN","crowd_2_3":"actual-distance 2-3% bearish setups at same OPEN",
                      "ratio":"current count / shifted trailing 180d median"},"rows_total":len(E),"robust_count":len(robust),
         "robust":robust,"top_train":ranked[:30]}
    json.dump(out,open(a.out,"w"),indent=2)
    print("NEAR_CROWD_SALVAGE_PASS","events",len(E),"robust",len(robust),flush=True)
    for x in ranked[:20]:
        e=x["metrics"];print("TOP",x["rule"],"cov",round(x["coverage_train"],3),
          "trainN",e["train"]["20bp"]["n"],"PF20",None if e["train"]["20bp"]["pf"] is None else round(e["train"]["20bp"]["pf"],3),
          "PF40",None if e["train"]["40bp"]["pf"] is None else round(e["train"]["40bp"]["pf"],3),
          "yrs",*[None if e["train_years"][str(y)]["pf"] is None else round(e["train_years"][str(y)]["pf"],3) for y in (2021,2022,2023,2024)],
          "validN",e["valid"]["20bp"]["n"],"vPF20",None if e["valid"]["20bp"]["pf"] is None else round(e["valid"]["20bp"]["pf"],3),
          "vPF40",None if e["valid"]["40bp"]["pf"] is None else round(e["valid"]["40bp"]["pf"],3),flush=True)

if __name__=="__main__":main()
