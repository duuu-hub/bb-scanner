import argparse,glob,json,heapq
import numpy as np,pandas as pd

VARIANT="P4T26_DD8"
COND="P4|T26"
FEATURES=["bounce72_pct","trend_age"]
STRICT_BOUNCE_MAX=5.742642032729082
STRICT_TREND_MIN=9.0
BASE_PCTS=(0.5,1.0,2.0,3.0,4.0,5.0)
CAPS=(50.0,100.0,150.0,200.0,300.0)
COSTS=(0.20,0.40)

def load(features_dir,sizing_dir):
    ffs=glob.glob(features_dir+"/**/features_*.csv.gz",recursive=True)
    sfs=glob.glob(sizing_dir+"/**/setups_*.csv.gz",recursive=True)
    efs=glob.glob(sizing_dir+"/**/events_*.csv.gz",recursive=True)
    assert len(ffs)==8,(len(ffs),ffs);assert len(sfs)==8,(len(sfs),sfs);assert len(efs)==8,(len(efs),efs)
    F=pd.concat([pd.read_csv(x,usecols=["condition","symbol","signal_ts","fill_ts","bounce72_pct","trend_age"]) for x in ffs],ignore_index=True)
    S=pd.concat([pd.read_csv(x,usecols=["variant","signal_ts","setup"]) for x in sfs],ignore_index=True)
    cols=["variant","symbol","signal_ts","fill_ts","exit_ts","outcome","pnl_pct","stop_pct"]
    E=pd.concat([pd.read_csv(x,usecols=cols) for x in efs],ignore_index=True)
    C=S[S.variant.eq(VARIANT)].groupby("signal_ts",as_index=False).agg(setups=("setup","sum"))
    E=E[E.variant.eq(VARIANT)&E.outcome.isin(["win","loss"])&E.pnl_pct.notna()&E.exit_ts.notna()].copy()
    E=E.merge(C,on="signal_ts",how="left",validate="many_to_one")
    E=E[(E.setups>=21)&(E.setups<=40)].copy()
    F=F[F.condition.eq(COND)].copy()
    E=E.merge(F[["symbol","signal_ts","fill_ts","bounce72_pct","trend_age"]],
              on=["symbol","signal_ts","fill_ts"],how="inner",validate="many_to_one")
    E["year"]=pd.to_datetime(E.signal_ts,unit="ms",utc=True).dt.year.astype(int)
    assert not E.symbol.astype(str).eq("BTCUSDT").any()
    return E

def groups(x):
    x=x.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort")
    out=[]
    for ts,g in x.groupby("fill_ts",sort=True):
        rows=[(str(r.symbol),int(r.exit_ts),float(r.pnl_pct),float(r.stop_pct)) for r in g.itertuples(index=False)]
        out.append((int(ts),rows))
    return out

def sim(gs,base_pct,cap_pct,cost):
    eq=1.0;peak=1.0;mdd=0.0;gross=0.0;openp={};heap=[];seq=0
    accepted=skip_symbol=skip_cap=0
    nh=[];exp=[];srisk=[]
    start=gs[0][0] if gs else None;end=start
    def snap():
        nonlocal peak,mdd
        q=max(eq,1e-12)
        nh.append(len(openp));exp.append(gross/q*100.0)
        srisk.append(sum(p[2]*p[3]/100.0 for p in openp.values())/q*100.0)
        peak=max(peak,eq)
        if peak>0:mdd=max(mdd,(peak-eq)/peak*100.0)
    def close_until(ts):
        nonlocal eq,gross,end
        while heap and heap[0][0]<=ts:
            et=heap[0][0];batch=[]
            while heap and heap[0][0]==et:
                _,_,sym=heapq.heappop(heap)
                p=openp.get(sym)
                if p is not None and p[0]==et:batch.append((sym,p))
            delta=0.0
            for sym,p in batch:
                openp.pop(sym,None);gross-=p[2]
                delta+=p[2]*((p[1]-cost)/100.0)
            eq+=delta;end=max(end or et,et);snap()
    for ts,rows in gs:
        close_until(ts)
        base_eq=eq
        for sym,exit_ts,pnl,stop_pct in rows:
            if sym in openp:
                skip_symbol+=1;continue
            intended=base_eq*base_pct/100.0
            room=eq*cap_pct/100.0-gross
            if room<intended*.20:
                skip_cap+=1;continue
            notion=min(intended,room)
            seq+=1;openp[sym]=(exit_ts,pnl,notion,stop_pct);gross+=notion
            heapq.heappush(heap,(exit_ts,seq,sym));accepted+=1
        snap()
    close_until(10**30)
    yrs=((end-start)/1000/86400/365.25) if start and end and end>start else None
    cagr=((eq**(1/yrs)-1)*100) if yrs and eq>0 else None
    q=lambda a,p:float(np.quantile(a,p)) if a else 0.0
    return {
      "accepted":accepted,"skip_same_symbol":skip_symbol,"skip_cap":skip_cap,
      "equity_multiple":float(eq),"return_pct":float((eq-1)*100),
      "cagr_pct":None if cagr is None else float(cagr),"mdd_pct":float(mdd),
      "open_p99":q(nh,.99),"open_max":int(max(nh) if nh else 0),
      "exposure_p99_pct":q(exp,.99),"exposure_max_pct":float(max(exp) if exp else 0),
      "stoprisk_p99_pct":q(srisk,.99),"stoprisk_max_pct":float(max(srisk) if srisk else 0),
      "period_years":yrs
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--features",default="features");ap.add_argument("--sizing",default="sizing");ap.add_argument("--out",default="exposure.json");a=ap.parse_args()
    D=load(a.features,a.sizing)
    scopes={
      "CROWD_ONLY":D,
      "STRICT":D[(D.bounce72_pct<=STRICT_BOUNCE_MAX)&(D.trend_age>=STRICT_TREND_MIN)].copy(),
    }
    out={"definition":{
      "strategy":"P4/T26 + DD8, BTC excluded, FIXED SL",
      "crowding":"21-40 setup signals at same 4H OPEN",
      "strict_filter":{"bounce72_pct_max":STRICT_BOUNCE_MAX,"trend_age_min":STRICT_TREND_MIN},
      "train":"2021-2024","validation":"2025-2026",
      "base_position_pct":BASE_PCTS,"gross_exposure_caps_pct":CAPS,
      "costs_pct":COSTS,
      "same_symbol":"one open position per symbol",
      "order_sizing":"notional per accepted trade = equity at fill timestamp * base_position_pct; gross exposure cap enforced",
      "risk":"realized-equity MDD plus aggregate initial-stop risk (sum notional*stop_pct/equity); stop-risk is not MTM VaR",
      "partial_cap_rule":"cap-boundary partial order accepted only if >=20% of intended size"
    },"scopes":{}}
    for name,x in scopes.items():
        tr=x[x.year<=2024];va=x[x.year>=2025]
        tg=groups(tr);vg=groups(va)
        rows=[]
        for bp in BASE_PCTS:
          for cap in CAPS:
            rec={"base_pct":bp,"cap_pct":cap}
            for cost in COSTS:
              rec[f"train_{int(cost*100)}bp"]=sim(tg,bp,cap,cost)
              rec[f"valid_{int(cost*100)}bp"]=sim(vg,bp,cap,cost)
            rows.append(rec)
            r=rec["valid_20bp"]
            print(name,"base",bp,"cap",cap,"validRet",round(r["return_pct"],2),
                  "validMDD",round(r["mdd_pct"],2),"p99StopRisk",round(r["stoprisk_p99_pct"],2),
                  "n",r["accepted"],"capSkip",r["skip_cap"],flush=True)
        out["scopes"][name]={"train_signals":int(len(tr)),"valid_signals":int(len(va)),"rows":rows}
    json.dump(out,open(a.out,"w"),indent=2)
    print("EXPOSURE_SWEEP_PASS",len(D),flush=True)
if __name__=="__main__":main()
