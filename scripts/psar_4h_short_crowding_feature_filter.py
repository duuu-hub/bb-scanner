import argparse,glob,json,math,itertools,heapq
import numpy as np,pandas as pd

FEATURES=["gap_pct","atr_pct","trend_age","ret24_pct","ret72_pct","bounce24_pct","bounce72_pct","dd24_pct","dd72_pct"]
VARIANTS={"P4T26_DD8":("P4|T26",-8.0),"P9T27_DD9":("P9|T27",-9.0)}
COST20=.20; COST40=.40

def pf(x):
    x=np.asarray(x,float); gp=x[x>0].sum(); gl=-x[x<0].sum()
    return float(gp/gl) if gl>0 else None

def load(features_dir,sizing_dir):
    ffs=glob.glob(features_dir+"/**/features_*.csv.gz",recursive=True)
    sfs=glob.glob(sizing_dir+"/**/setups_*.csv.gz",recursive=True)
    efs=glob.glob(sizing_dir+"/**/events_*.csv.gz",recursive=True)
    assert len(ffs)==8,(len(ffs),ffs); assert len(sfs)==8,(len(sfs),sfs); assert len(efs)==8,(len(efs),efs)
    F=pd.concat([pd.read_csv(x) for x in ffs],ignore_index=True)
    S=pd.concat([pd.read_csv(x) for x in sfs],ignore_index=True)
    E=pd.concat([pd.read_csv(x) for x in efs],ignore_index=True)
    C=S.groupby(["variant","signal_ts"],as_index=False).agg(setups=("setup","sum"))
    rows=[]
    for v,(cond,ddmin) in VARIANTS.items():
        f=F[(F.condition==cond)&(F.dd72_pct>=ddmin)].copy()
        e=E[(E.variant==v)&E.outcome.isin(["win","loss"])&E.pnl_pct.notna()&E.exit_ts.notna()].copy()
        e=e.drop(columns=[c for c in FEATURES if c in e.columns],errors="ignore")
        j=e.merge(C[C.variant==v][["signal_ts","setups"]],on="signal_ts",how="left",validate="many_to_one")
        j=j[(j.setups>=21)&(j.setups<=40)].copy()
        j=j.merge(f[["symbol","signal_ts","fill_ts",*FEATURES]],
                  on=["symbol","signal_ts","fill_ts"],how="inner",validate="many_to_one")
        j["variant"]=v
        j["year"]=pd.to_datetime(j.signal_ts,unit="ms",utc=True).dt.year.astype(int)
        j["net20"]=j.pnl_pct-COST20; j["net40"]=j.pnl_pct-COST40
        rows.append(j)
    D=pd.concat(rows,ignore_index=True)
    assert not D.symbol.astype(str).eq("BTCUSDT").any()
    return D

def metrics(g):
    ev=g.groupby("signal_ts").agg(net20=("net20","sum"),avg20=("net20","mean"))
    yp=[]
    for y,z in g.groupby("year"):
        p=pf(z.net20)
        if p is not None: yp.append((int(y),p))
    return {
      "n":int(len(g)),"events":int(g.signal_ts.nunique()),
      "win_pct":float((g.pnl_pct>0).mean()*100) if len(g) else None,
      "net20_pf":pf(g.net20) if len(g) else None,
      "net40_pf":pf(g.net40) if len(g) else None,
      "mean_net20_pct":float(g.net20.mean()) if len(g) else None,
      "event_pf":pf(ev.net20) if len(ev) else None,
      "positive_event_pct":float((ev.avg20>0).mean()*100) if len(ev) else None,
      "median_year_pf":float(np.median([p for _,p in yp])) if yp else None,
      "min_year_pf":float(min([p for _,p in yp])) if yp else None,
      "positive_pf_years":int(sum(p>1 for _,p in yp)),
      "year_pf":{str(y):float(p) for y,p in yp},
    }

def portfolio(g,base_pct=.5,cap_pct=100.0,cost=.20):
    # Same-symbol block, all qualifying signals accepted subject only to global gross-exposure cap.
    g=g.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort")
    eq=1.0; peak=1.0;mdd=0.0;gross=0.0;openp={};heap=[];seq=0;acc=skip_sym=skip_cap=0
    start=None;end=None
    def close(ts):
        nonlocal eq,peak,mdd,gross,end
        while heap and heap[0][0]<=ts:
            et=heap[0][0];batch=[]
            while heap and heap[0][0]==et:
                _,_,sym=heapq.heappop(heap)
                p=openp.get(sym)
                if p is not None and p[0]==et: batch.append((sym,p))
            delta=0.0
            for sym,p in batch:
                openp.pop(sym,None);gross-=p[2]
                delta+=p[2]*((p[1]-cost)/100.0)
            eq+=delta;end=max(end or et,et);peak=max(peak,eq)
            if peak>0:mdd=max(mdd,(peak-eq)/peak*100.0)
    for ts,z in g.groupby("fill_ts",sort=True):
        ts=int(ts);start=ts if start is None else start;close(ts)
        for r in z.itertuples(index=False):
            sym=str(r.symbol)
            if sym in openp:skip_sym+=1;continue
            intended=eq*base_pct/100.0
            room=eq*cap_pct/100.0-gross
            if room<intended*.20:skip_cap+=1;continue
            notion=min(intended,room);seq+=1
            openp[sym]=(int(r.exit_ts),float(r.pnl_pct),notion);gross+=notion
            heapq.heappush(heap,(int(r.exit_ts),seq,sym));acc+=1
    close(10**30)
    yrs=((end-start)/1000/86400/365.25) if start and end and end>start else None
    cagr=((eq**(1/yrs)-1)*100) if yrs and eq>0 else None
    return {"accepted":acc,"skip_symbol":skip_sym,"skip_cap":skip_cap,
            "equity_multiple":float(eq),"return_pct":float((eq-1)*100),
            "mdd_pct":float(mdd),"cagr_pct":None if cagr is None else float(cagr)}

def make_single_rules(tr):
    rules=[]
    for f in FEATURES:
        vals=tr[f].replace([np.inf,-np.inf],np.nan).dropna().to_numpy(float)
        if len(vals)<100:continue
        for q in [.1,.2,.3,.4,.5,.6,.7,.8,.9]:
            th=float(np.quantile(vals,q))
            rules.append((f"{f}_GE_q{int(q*100)}",[(f,"GE",th)]))
            rules.append((f"{f}_LE_q{int(q*100)}",[(f,"LE",th)]))
    return rules

def apply_rule(x,clauses):
    m=np.ones(len(x),dtype=bool)
    for f,op,th in clauses:
        a=x[f].to_numpy(float)
        m &= (a>=th) if op=="GE" else (a<=th)
    return x[m]

def score(train,baseline_n):
    m=metrics(train)
    cov=len(train)/baseline_n if baseline_n else 0
    if len(train)<300 or m["events"]<80 or cov<.15:return -1e99
    vals=[m["net20_pf"],m["net40_pf"],m["event_pf"],m["median_year_pf"]]
    if any(v is None or v<=0 for v in vals):return -1e99
    # Train-only robustness score: reward cost-stressed PF, event PF, year robustness;
    # mild penalty for throwing away too much data.
    return (1.2*math.log(m["net20_pf"])+.8*math.log(m["net40_pf"])
            +.45*math.log(m["event_pf"])+.35*math.log(m["median_year_pf"])
            +.20*math.log(max(cov,.01)))

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--features",default="features");ap.add_argument("--sizing",default="sizing");ap.add_argument("--out",default="filter.json");a=ap.parse_args()
    D=load(a.features,a.sizing)
    out={"definition":{"source_feature_run":"36431639340","source_sizing_run":"36437560302",
      "btc_excluded":True,"crowding":"21-40 setup signals at same 4H OPEN","train":"2021-2024","validation":"2025-2026 untouched",
      "costs":"20bp primary / 40bp stress","features":FEATURES,
      "single_thresholds":"train deciles, both directions",
      "pair_search":"top 12 train-only single rules, all non-duplicate-feature pairs",
      "eligibility":"train >=300 trades, >=80 events, >=15% crowding-baseline coverage",
      "portfolio":"0.5% equity per accepted trade, 100% gross cap, same-symbol overlap blocked, realized-equity MDD"},"variants":{}}
    for v in VARIANTS:
        x=D[D.variant==v].copy();tr=x[x.year<=2024].copy();va=x[x.year>=2025].copy()
        base={"train":metrics(tr),"valid":metrics(va),
              "train_port20":portfolio(tr,.5,100,.20),"valid_port20":portfolio(va,.5,100,.20),
              "train_port40":portfolio(tr,.5,100,.40),"valid_port40":portfolio(va,.5,100,.40)}
        singles=[]
        for name,cl in make_single_rules(tr):
            gt=apply_rule(tr,cl); sc=score(gt,len(tr))
            if sc<=-1e90:continue
            singles.append({"name":name,"clauses":cl,"score":sc,"coverage":len(gt)/len(tr),
                            "train":metrics(gt),"valid":metrics(apply_rule(va,cl))})
        singles.sort(key=lambda r:r["score"],reverse=True)
        top=[]
        seen_features={}
        # keep diverse thresholds but cap repeated feature directions to avoid pair explosion
        for r in singles:
            f,op,_=r["clauses"][0];k=f+"_"+op
            if seen_features.get(k,0)>=2:continue
            top.append(r);seen_features[k]=seen_features.get(k,0)+1
            if len(top)>=12:break
        pairs=[]
        for a1,b1 in itertools.combinations(top,2):
            if a1["clauses"][0][0]==b1["clauses"][0][0]:continue
            cl=a1["clauses"]+b1["clauses"];gt=apply_rule(tr,cl);sc=score(gt,len(tr))
            if sc<=-1e90:continue
            pairs.append({"name":a1["name"]+"__"+b1["name"],"clauses":cl,"score":sc,
                          "coverage":len(gt)/len(tr),"train":metrics(gt),"valid":metrics(apply_rule(va,cl))})
        pairs.sort(key=lambda r:r["score"],reverse=True)
        candidates=singles[:20]+pairs[:30]
        candidates.sort(key=lambda r:r["score"],reverse=True)
        finalists=[]
        for r in candidates[:15]:
            gt=apply_rule(tr,r["clauses"]);gv=apply_rule(va,r["clauses"])
            z=dict(r)
            z["train_port20"]=portfolio(gt,.5,100,.20);z["valid_port20"]=portfolio(gv,.5,100,.20)
            z["train_port40"]=portfolio(gt,.5,100,.40);z["valid_port40"]=portfolio(gv,.5,100,.40)
            finalists.append(z)
        out["variants"][v]={"baseline":base,"train_top_single":singles[:20],"train_top_pair":pairs[:20],"finalists":finalists}
        print("VAR",v,"BASE",
              "trPF",round(base["train"]["net20_pf"],4),"vaPF",round(base["valid"]["net20_pf"],4),
              "trEq",round(base["train_port20"]["equity_multiple"],4),"vaEq",round(base["valid_port20"]["equity_multiple"],4),flush=True)
        for r in finalists[:10]:
            print("FINAL",r["name"],"cov",round(r["coverage"],3),
              "trPF",round(r["train"]["net20_pf"],4),"tr40",round(r["train"]["net40_pf"],4),
              "vaPF",round(r["valid"]["net20_pf"],4) if r["valid"]["net20_pf"] else None,
              "va40",round(r["valid"]["net40_pf"],4) if r["valid"]["net40_pf"] else None,
              "trEq",round(r["train_port20"]["equity_multiple"],4),"trMDD",round(r["train_port20"]["mdd_pct"],2),
              "vaEq",round(r["valid_port20"]["equity_multiple"],4),"vaMDD",round(r["valid_port20"]["mdd_pct"],2),
              flush=True)
    json.dump(out,open(a.out,"w"),indent=2)
    print("CROWD_FEATURE_FILTER_PASS",len(D),flush=True)
if __name__=="__main__":main()
