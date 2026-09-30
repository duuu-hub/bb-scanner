import argparse,glob,json
import numpy as np,pandas as pd

CUT=pd.Timestamp("2025-01-01",tz="UTC")
FEATURES=("atr_pct","trend_age","ret24_pct","ret72_pct","bounce24_pct","bounce72_pct","dd24_pct","dd72_pct")
COSTS=(0.20,0.40)

def met(z,cost):
    z=z[z.outcome.isin(["win","loss"]) & z.pnl_pct.notna()].copy()
    if z.empty:return {"n":0,"win_pct":None,"pf":None,"ev":None,"sum":0.0}
    net=z.pnl_pct.to_numpy(float)-cost
    gp=net[net>0].sum();gl=-net[net<0].sum()
    return {"n":int(len(z)),"win_pct":float((z.outcome=="win").mean()*100),
            "pf":float(gp/gl) if gl>0 else None,"ev":float(net.mean()),"sum":float(net.sum())}

def eval_mask(d,m):
    tr=m&(d.signal_dt<CUT)&(d.exit_dt<CUT);va=m&(d.signal_dt>=CUT)
    out={"train":{},"valid":{},"train_years":{},"valid_years":{}}
    for c in COSTS:
        tag=f"{int(c*100)}bp";out["train"][tag]=met(d[tr],c);out["valid"][tag]=met(d[va],c)
    for y in (2021,2022,2023,2024):out["train_years"][str(y)]=met(d[tr&(d.year==y)],.20)
    for y in (2025,2026):out["valid_years"][str(y)]=met(d[va&(d.year==y)],.20)
    return out

def mask_rule(d,r):
    if r["kind"]=="single":
        x=d[r["f"]]
        return (x>=r["q"]) if r["op"]==">=" else (x<=r["q"])
    if r["kind"]=="range":
        x=d[r["f"]];return (x>=r["lo"])&(x<r["hi"])
    if r["kind"]=="and":return mask_rule(d,r["a"]) & mask_rule(d,r["b"])
    raise ValueError(r)

def text_rule(r):
    if r["kind"]=="single":return f'{r["f"]}{r["op"]}{r["q"]:.5g}'
    if r["kind"]=="range":return f'{r["f"]}[{r["lo"]:.5g},{r["hi"]:.5g})'
    return text_rule(r["a"])+" & "+text_rule(r["b"])

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--near",default="near");ap.add_argument("--features",default="features");ap.add_argument("--out",default="near_feature_salvage.json");a=ap.parse_args()
    ef=glob.glob(a.near+"/**/events_*.csv.gz",recursive=True);ff=glob.glob(a.features+"/**/features_*.csv.gz",recursive=True)
    assert len(ef)==8,(len(ef),ef);assert len(ff)==8,(len(ff),ff)
    E=pd.concat([pd.read_csv(f) for f in ef],ignore_index=True)
    F=pd.concat([pd.read_csv(f) for f in ff],ignore_index=True)
    E=E[E.r.eq(4.0)&E.outcome.isin(["win","loss"])&E.pnl_pct.notna()&(E.dist_pct>2)&(E.dist_pct<=3)].copy()
    d=E.merge(F[["symbol","signal_ts",*FEATURES]],on=["symbol","signal_ts"],how="inner",validate="many_to_one")
    d["signal_dt"]=pd.to_datetime(d.signal_ts,unit="ms",utc=True);d["exit_dt"]=pd.to_datetime(d.exit_ts,unit="ms",utc=True);d["year"]=d.signal_dt.dt.year.astype(int)
    base=np.ones(len(d),bool);base_ev=eval_mask(d,base);base_n=base_ev["train"]["20bp"]["n"]
    tr=d[(d.signal_dt<CUT)&(d.exit_dt<CUT)].copy()

    rules=[]
    # Train-only broad quantile thresholds: 20/40/60/80%, deliberately coarse.
    for f in FEATURES:
        x=tr[f].replace([np.inf,-np.inf],np.nan).dropna()
        if len(x)<1000:continue
        for q in np.unique(np.quantile(x,[.2,.4,.6,.8])):
            rules.append({"kind":"single","f":f,"op":"<=","q":float(q)})
            rules.append({"kind":"single","f":f,"op":">=","q":float(q)})
    # A-priori interpretable trend-age bins.
    for lo,hi in [(1,3),(3,6),(6,12),(12,24),(24,48),(48,9999)]:
        rules.append({"kind":"range","f":"trend_age","lo":lo,"hi":hi})

    singles=[]
    for r in rules:
        m=mask_rule(d,r).fillna(False).to_numpy() if hasattr(mask_rule(d,r),"fillna") else np.asarray(mask_rule(d,r),bool)
        ev=eval_mask(d,m);t=ev["train"]["20bp"];t4=ev["train"]["40bp"]
        cov=t["n"]/base_n if base_n else 0
        yrs=ev["train_years"]
        minyr=min([yrs[str(y)]["pf"] if yrs[str(y)]["pf"] is not None and yrs[str(y)]["n"]>=200 else -999 for y in (2021,2022,2023,2024)])
        singles.append({"rule":r,"rule_text":text_rule(r),"coverage":cov,"metrics":ev,
                        "_pre":[minyr,t4["pf"] if t4["pf"] is not None else -999,t["sum"]]})
    # Pair seeds chosen only from Train, diverse by feature.
    pre=sorted(singles,key=lambda x:tuple(x["_pre"]),reverse=True)
    seeds=[];counts={}
    for x in pre:
        f=x["rule"]["f"];counts[f]=counts.get(f,0)
        if counts[f]>=2 or x["metrics"]["train"]["20bp"]["n"]<3000:continue
        seeds.append(x);counts[f]+=1
        if len(seeds)>=16:break
    pairs=[]
    for i in range(len(seeds)):
        for j in range(i+1,len(seeds)):
            if seeds[i]["rule"]["f"]==seeds[j]["rule"]["f"]:continue
            r={"kind":"and","a":seeds[i]["rule"],"b":seeds[j]["rule"]}
            mm=mask_rule(d,r);m=mm.fillna(False).to_numpy() if hasattr(mm,"fillna") else np.asarray(mm,bool)
            ev=eval_mask(d,m);t=ev["train"]["20bp"];t4=ev["train"]["40bp"];cov=t["n"]/base_n if base_n else 0
            pairs.append({"rule":r,"rule_text":text_rule(r),"coverage":cov,"metrics":ev})

    candidates=singles+pairs
    robust=[]
    for x in candidates:
        e=x["metrics"];t=e["train"]["20bp"];t4=e["train"]["40bp"]
        if x["coverage"]<.05 or t["n"]<3000 or (t["pf"] or 0)<=1 or (t4["pf"] or 0)<=1:continue
        ok=True
        for y in (2021,2022,2023,2024):
            q=e["train_years"][str(y)]
            if q["n"]<200 or (q["pf"] or 0)<=1:ok=False;break
        if ok:robust.append(x)
    def score(x):
        e=x["metrics"];yrs=e["train_years"]
        return (e["train"]["40bp"]["pf"],min(yrs[str(y)]["pf"] for y in (2021,2022,2023,2024)),e["train"]["20bp"]["sum"])
    robust=sorted(robust,key=score,reverse=True)
    # Also report best Train candidates even if one year fails.
    ranked=sorted(candidates,key=lambda x:(
        x["metrics"]["train"]["40bp"]["pf"] if x["metrics"]["train"]["40bp"]["pf"] is not None else -999,
        x["metrics"]["train"]["20bp"]["sum"]),reverse=True)
    for x in candidates:x.pop("_pre",None)
    payload={"definition":{"base":"actual PSAR distance 2-3%, R4, SL=frozen PSAR","features":FEATURES,
                           "thresholds":"Train-only 20/40/60/80 quantiles + fixed trend-age bins","pairs":"Train-selected diverse single-feature seeds only",
                           "robust":"coverage>=5%, Train n>=3000, overall PF20/PF40>1, each 2021-24 PF20>1 with n>=200",
                           "validation":"2025-26 never used for threshold or pair selection"},
             "joined_rows":len(d),"base":base_ev,"robust_count":len(robust),"robust":robust[:50],"top_train":ranked[:30],
             "pair_seed_rules":[x["rule_text"] for x in seeds]}
    json.dump(payload,open(a.out,"w"),indent=2)
    print("NEAR_FEATURE_SALVAGE_PASS","rows",len(d),"robust",len(robust),flush=True)
    print("BASE","train",base_ev["train"]["20bp"],base_ev["train"]["40bp"],"valid",base_ev["valid"]["20bp"],base_ev["valid"]["40bp"],flush=True)
    for x in robust[:20]:
        e=x["metrics"];print("ROBUST",x["rule_text"],"cov",round(x["coverage"],3),
          "trainN",e["train"]["20bp"]["n"],"PF20",round(e["train"]["20bp"]["pf"],3),"PF40",round(e["train"]["40bp"]["pf"],3),
          "yrs",*[round(e["train_years"][str(y)]["pf"],3) for y in (2021,2022,2023,2024)],
          "validN",e["valid"]["20bp"]["n"],"vPF20",None if e["valid"]["20bp"]["pf"] is None else round(e["valid"]["20bp"]["pf"],3),
          "vPF40",None if e["valid"]["40bp"]["pf"] is None else round(e["valid"]["40bp"]["pf"],3),flush=True)
    if not robust:
        for x in ranked[:15]:
            e=x["metrics"];print("TOP",x["rule_text"],"cov",round(x["coverage"],3),
              "trainN",e["train"]["20bp"]["n"],"PF20",None if e["train"]["20bp"]["pf"] is None else round(e["train"]["20bp"]["pf"],3),
              "PF40",None if e["train"]["40bp"]["pf"] is None else round(e["train"]["40bp"]["pf"],3),
              "yrs",*[None if e["train_years"][str(y)]["pf"] is None else round(e["train_years"][str(y)]["pf"],3) for y in (2021,2022,2023,2024)],
              "vPF20",None if e["valid"]["20bp"]["pf"] is None else round(e["valid"]["20bp"]["pf"],3),
              "vPF40",None if e["valid"]["40bp"]["pf"] is None else round(e["valid"]["40bp"]["pf"],3),flush=True)

if __name__=="__main__":main()
