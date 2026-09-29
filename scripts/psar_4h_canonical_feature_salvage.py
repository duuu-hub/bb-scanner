import argparse,glob,json,zipfile,os,math
import numpy as np,pandas as pd

VARS=("P6_SB0_R6","P8_SB0_R5","P10_SB0.1_R4")
FEATURES=("gap_pct","atr_pct","trend_age","ret24_pct","ret72_pct","bounce24_pct","bounce72_pct","dd24_pct","dd72_pct","crowd_ratio180")
CUT=pd.Timestamp("2025-01-01",tz="UTC")
COSTS=(0.20,0.40)

def metrics(d,mask):
    z=d.loc[mask]
    out={"n":int(len(z))}
    if len(z)==0:
        out.update({"win_pct":None,"pf20":None,"pf40":None,"ev20":None,"ev40":None,"sum20":0.0,"sum40":0.0});return out
    out["win_pct"]=float((z.outcome=="win").mean()*100)
    p=z.pnl_pct.to_numpy(float)
    for cost,tag in ((.20,"20"),(.40,"40")):
        net=p-cost;gp=net[net>0].sum();gl=-net[net<0].sum()
        out["pf"+tag]=float(gp/gl) if gl>0 else None
        out["ev"+tag]=float(net.mean());out["sum"+tag]=float(net.sum())
    return out

def evaluate_rule(d,mask):
    allm=metrics(d,mask)
    years={}
    for y in (2021,2022,2023,2024,2025,2026):
        years[str(y)]=metrics(d,mask&(d.year==y))
    return allm,years

def rule_mask(d,r):
    if r["kind"]=="single":
        x=d[r["feature"]].to_numpy(float)
        return x>=r["threshold"] if r["op"]==">=" else x<=r["threshold"]
    if r["kind"]=="range":
        x=d[r["feature"]].to_numpy(float);return (x>=r["lo"])&(x<r["hi"])
    if r["kind"]=="and":
        return rule_mask(d,r["a"]) & rule_mask(d,r["b"])
    raise ValueError(r)

def key_rule(r):
    if r["kind"]=="single":return f'{r["feature"]}{r["op"]}{r["threshold"]:.6g}'
    if r["kind"]=="range":return f'{r["feature"]}[{r["lo"]:.6g},{r["hi"]:.6g})'
    return key_rule(r["a"])+" & "+key_rule(r["b"])

def train_ok(ev,coverage):
    a=ev["train_all"]; ys=ev["train_years"]
    if a["n"]<10000 or coverage<0.05:return False
    if a["pf20"] is None or a["pf40"] is None or a["pf20"]<=1 or a["pf40"]<=1:return False
    for y in ("2021","2022","2023","2024"):
        q=ys[y]
        if q["n"]<500 or q["pf20"] is None or q["pf20"]<=1:return False
    return True

def score_train(ev):
    a=ev["train_all"];ys=ev["train_years"]
    minpf=min(ys[y]["pf20"] for y in ("2021","2022","2023","2024"))
    # money first among all-year-positive robust rules; then stress / min-year robustness.
    return (a["sum20"],a["sum40"],minpf,a["pf20"])

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--features",default="features");ap.add_argument("--ledger",default="ledger");ap.add_argument("--out",default="salvage.json");a=ap.parse_args()
    ffs=sorted(glob.glob(a.features+"/**/features_*.csv.gz",recursive=True));efs=sorted(glob.glob(a.ledger+"/**/events_*.csv.gz",recursive=True))
    assert len(ffs)==8,(len(ffs),ffs);assert len(efs)==8,(len(efs),efs)

    # Breadth normalization is computed only from contemporaneously available bearish setup counts.
    sigparts=[]
    for f in ffs:
        z=pd.read_csv(f,usecols=["signal_ts"]);sigparts.append(z)
    crowd=pd.concat(sigparts,ignore_index=True).groupby("signal_ts").size().rename("crowd").reset_index().sort_values("signal_ts")
    crowd["crowd_med180"]=crowd.crowd.shift(1).rolling(1080,min_periods=180).median()
    crowd["crowd_ratio180"]=crowd.crowd/crowd.crowd_med180
    cmap=crowd[["signal_ts","crowd_ratio180"]]

    out={"definition":{
        "variants":VARS,"train":"signal<2025-01-01 and exit<2025-01-01","validation":"signal>=2025-01-01 untouched",
        "costs_pct":COSTS,"features":FEATURES,
        "crowd_ratio180":"current bearish setup count / trailing 180-day median setup count, median shifted one 4H bar",
        "selection":"train only; require PF20>1 in each 2021/22/23/24, overall train PF20>1 and PF40>1, coverage>=5%, n>=10000; rank by train sum net return proxy then stress robustness",
        "note":"independent resolved signals only at this stage; account stacking/exposure simulation is a separate finalist stage"
    },"variants":{}}

    # Load one variant at a time to keep memory bounded.
    for v in VARS:
        parts=[]
        for ff,ef in zip(ffs,efs):
            F=pd.read_csv(ff)
            E=pd.read_csv(ef,usecols=["variant","symbol","signal_ts","exit_ts","outcome","pnl_pct"])
            E=E[E.variant.eq(v)&E.outcome.isin(["win","loss"])&E.exit_ts.notna()&E.pnl_pct.notna()].copy()
            M=E.merge(F,on=["symbol","signal_ts"],how="inner",validate="many_to_one")
            parts.append(M)
        d=pd.concat(parts,ignore_index=True)
        d=d.merge(cmap,on="signal_ts",how="left",validate="many_to_one")
        d["signal_dt"]=pd.to_datetime(d.signal_ts,unit="ms",utc=True);d["exit_dt"]=pd.to_datetime(d.exit_ts,unit="ms",utc=True)
        d["year"]=d.signal_dt.dt.year.astype(int)
        train=(d.signal_dt<CUT)&(d.exit_dt<CUT);valid=d.signal_dt>=CUT
        base_train=metrics(d,train);base_valid=metrics(d,valid)

        # Candidate singles from TRAIN quantiles only.
        rules=[]
        td=d.loc[train]
        for f in FEATURES:
            x=td[f].replace([np.inf,-np.inf],np.nan).dropna()
            if len(x)<1000:continue
            qs=np.unique(np.quantile(x,np.arange(.1,1.0,.1)))
            for q in qs:
                rules.append({"kind":"single","feature":f,"op":"<=","threshold":float(q)})
                rules.append({"kind":"single","feature":f,"op":">=","threshold":float(q)})
        # Stable, interpretable breadth ranges; fixed a priori grid, not validation-tuned.
        for lo in (.6,.8,1.0,1.2):
            for hi in (1.2,1.5,2.0,2.5):
                if hi>lo:rules.append({"kind":"range","feature":"crowd_ratio180","lo":lo,"hi":hi})

        singles=[]
        for r in rules:
            rm=rule_mask(d,r);tm=train&rm;vm=valid&rm
            tr_all,tr_year=evaluate_rule(d,tm);va_all,va_year=evaluate_rule(d,vm)
            cov=tr_all["n"]/max(base_train["n"],1)
            ev={"rule":r,"rule_text":key_rule(r),"coverage_train":cov,"train_all":tr_all,"train_years":tr_year,"valid_all":va_all,"valid_years":va_year}
            # broad preselection for pair generation, still TRAIN-only.
            minpf=min([tr_year[str(y)]["pf20"] if tr_year[str(y)]["pf20"] is not None and tr_year[str(y)]["n"]>=500 else -999 for y in (2021,2022,2023,2024)])
            ev["_pre_score"]=(minpf,tr_all["sum20"],tr_all["pf40"] if tr_all["pf40"] is not None else -999)
            singles.append(ev)
        pre=sorted(singles,key=lambda x:x["_pre_score"],reverse=True)
        # Keep diverse train-selected singles for pairs.
        chosen=[];perfeat={}
        for x in pre:
            f=x["rule"]["feature"];perfeat[f]=perfeat.get(f,0)
            if perfeat[f]>=3:continue
            if x["train_all"]["n"]<20000:continue
            chosen.append(x);perfeat[f]+=1
            if len(chosen)>=24:break

        pairs=[]
        for i in range(len(chosen)):
            for j in range(i+1,len(chosen)):
                a0=chosen[i]["rule"];b0=chosen[j]["rule"]
                if a0.get("feature")==b0.get("feature"):continue
                r={"kind":"and","a":a0,"b":b0};rm=rule_mask(d,r);tm=train&rm;vm=valid&rm
                tr_all,tr_year=evaluate_rule(d,tm);va_all,va_year=evaluate_rule(d,vm)
                cov=tr_all["n"]/max(base_train["n"],1)
                ev={"rule":r,"rule_text":key_rule(r),"coverage_train":cov,"train_all":tr_all,"train_years":tr_year,"valid_all":va_all,"valid_years":va_year}
                pairs.append(ev)

        candidates=singles+pairs
        robust=[x for x in candidates if train_ok(x,x["coverage_train"])]
        robust=sorted(robust,key=score_train,reverse=True)
        # Pareto-like alternate: best minimum train-year PF among meaningful coverage.
        alt=sorted([x for x in candidates if x["train_all"]["n"]>=10000 and x["coverage_train"]>=.05],
                   key=lambda x:(min([x["train_years"][str(y)]["pf20"] if x["train_years"][str(y)]["pf20"] is not None and x["train_years"][str(y)]["n"]>=500 else -999 for y in (2021,2022,2023,2024)]),
                                 x["train_all"]["sum20"]),reverse=True)
        for x in candidates:
            x.pop("_pre_score",None)
        out["variants"][v]={"rows":len(d),"base_train":base_train,"base_valid":base_valid,
                            "robust_count":len(robust),"top_robust":robust[:40],"top_min_year":alt[:20],
                            "pair_seed_rules":[x["rule_text"] for x in chosen]}
        print("\\nVARIANT",v,"rows",len(d),"baseTrainPF",round(base_train["pf20"],3),"baseValidPF",round(base_valid["pf20"],3),"robust",len(robust),flush=True)
        for x in robust[:12]:
            ty=x["train_years"];vy=x["valid_years"]
            print("TOP",x["rule_text"],"cov",round(x["coverage_train"],3),
                  "trainN",x["train_all"]["n"],"PF20",round(x["train_all"]["pf20"],3),"PF40",round(x["train_all"]["pf40"],3),
                  "yrs",*[round(ty[str(y)]["pf20"],3) for y in (2021,2022,2023,2024)],
                  "validN",x["valid_all"]["n"],"validPF20",round(x["valid_all"]["pf20"],3),"validPF40",round(x["valid_all"]["pf40"],3),
                  "vyrs",*[round(vy[str(y)]["pf20"],3) for y in (2025,2026)],flush=True)
    json.dump(out,open(a.out,"w"),indent=2)
    print("FEATURE_SALVAGE_PASS",flush=True)
if __name__=="__main__":main()
