import argparse,glob,json,math,heapq
import numpy as np,pandas as pd

VARIANTS=("P4T26_DD8","P9T27_DD9")
MAPS={
 "STATIC":(1.0,1.0,1.0,1.0),
 "CROWD_A":(0.25,0.50,1.00,0.75),
 "CROWD_B":(0.00,0.50,1.00,0.75),
 "CROWD_C":(0.00,0.00,1.00,0.75),
 "SWEET_ONLY":(0.00,0.00,1.00,0.00),
}
BASE_PCTS=(0.5,1.0,2.0,3.0,4.0,5.0)
EXPOSURE_CAPS=(100.0,150.0,200.0)

def wt(n,w):
    return w[0] if n<=10 else w[1] if n<=20 else w[2] if n<=40 else w[3]

def load(root):
    sfs=glob.glob(root+"/**/setups_*.csv.gz",recursive=True)
    efs=glob.glob(root+"/**/events_*.csv.gz",recursive=True)
    assert len(sfs)==8 and len(efs)==8,(len(sfs),len(efs))
    S=pd.concat([pd.read_csv(x,usecols=["variant","signal_ts","setup"]) for x in sfs],ignore_index=True)
    keep=["variant","symbol","signal_ts","fill_ts","exit_ts","outcome","pnl_pct","stop_pct"]
    E=pd.concat([pd.read_csv(x,usecols=keep) for x in efs],ignore_index=True)
    S=S[S.variant.isin(VARIANTS)]
    E=E[E.variant.isin(VARIANTS)&E.outcome.isin(["win","loss","unresolved_eod"])&E.exit_ts.notna()].copy()
    C=S.groupby(["variant","signal_ts"],as_index=False)["setup"].sum().rename(columns={"setup":"setups"})
    E=E.merge(C,on=["variant","signal_ts"],how="left",validate="many_to_one")
    E["year"]=pd.to_datetime(E.signal_ts,unit="ms",utc=True).dt.year.astype(int)
    assert not E.symbol.astype(str).eq("BTCUSDT").any()
    return E

def make_groups(x):
    x=x.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort")
    out=[]
    for ts,g in x.groupby("fill_ts",sort=True):
        rows=[]
        for r in g.itertuples(index=False):
            rows.append((str(r.symbol),int(r.setups),int(r.exit_ts),
                         None if pd.isna(r.pnl_pct) else float(r.pnl_pct),
                         float(r.stop_pct)))
        out.append((int(ts),rows))
    return out

def sim(groups,weights,base_pct,cap_pct,cost_bp):
    eq=1.0; peak=1.0; mdd=0.0
    openp={}; heap=[]; seq=0; gross=0.0
    acc=ss=se=0; nh=[]; eh=[]; rh=[]; wins=losses=0
    start=groups[0][0] if groups else None; end=start

    def snap():
        nonlocal peak,mdd
        q=max(eq,1e-12)
        nh.append(len(openp)); eh.append(gross/q*100.0)
        rh.append(sum(p[2]*p[3]/100.0 for p in openp.values())/q*100.0)
        peak=max(peak,eq)
        mdd=max(mdd,(peak-eq)/peak*100.0 if peak>0 else 0.0)

    def close_until(ts):
        nonlocal eq,gross,end,wins,losses
        while heap and heap[0][0]<=ts:
            et=heap[0][0]; batch=[]
            while heap and heap[0][0]==et:
                _,_,sym=heapq.heappop(heap)
                p=openp.get(sym)
                if p is not None and p[0]==et:
                    batch.append((sym,p))
            delta=0.0
            for sym,p in batch:
                openp.pop(sym,None); gross-=p[2]
                rp=p[1]
                if rp is not None and np.isfinite(rp):
                    delta += p[2]*((rp-cost_bp/100.0)/100.0)
                    if rp>0:wins+=1
                    elif rp<0:losses+=1
            eq+=delta; end=max(end or et,et); snap()

    for ts,rows in groups:
        close_until(ts)
        for sym,setups,exit_ts,pnl,stop in rows:
            if sym in openp: ss+=1; continue
            w=wt(setups,weights)
            if w<=0: continue
            intended=eq*(base_pct/100.0)*w
            room=eq*(cap_pct/100.0)-gross
            if room<=1e-12 or intended<=1e-12: se+=1; continue
            notion=min(intended,room)
            if notion<intended*0.20: se+=1; continue
            seq+=1; openp[sym]=(exit_ts,pnl,notion,stop); gross+=notion
            heapq.heappush(heap,(exit_ts,seq,sym)); acc+=1
        snap()
    close_until(10**30)
    yrs=((end-start)/1000/86400/365.25) if start and end and end>start else None
    cagr=((eq**(1/yrs)-1)*100) if yrs and eq>0 else None
    q=lambda a,p: float(np.quantile(a,p)) if a else 0.0
    return {"accepted":acc,"skip_same_symbol":ss,"skip_exposure":se,
      "equity_multiple":float(eq),"return_pct":float((eq-1)*100),
      "cagr_pct":None if cagr is None else float(cagr),"mdd_pct":float(mdd),
      "open_p95":q(nh,.95),"open_p99":q(nh,.99),"open_max":max(nh) if nh else 0,
      "exposure_p95":q(eh,.95),"exposure_p99":q(eh,.99),"exposure_max":max(eh) if eh else 0,
      "stoprisk_p99":q(rh,.99),"wins":wins,"losses":losses}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--source",default="source");ap.add_argument("--out",default="out.json");a=ap.parse_args()
    D=load(a.source); out={"definition":{"source_sizing_run":"36437560302","btc_excluded":True,
      "stop_policy":"FIXED","train":"2021-2024","validation":"2025-2026",
      "maps":MAPS,"base_position_pct":BASE_PCTS,"gross_exposure_caps_pct":EXPOSURE_CAPS,
      "primary_cost_bp":20,"stress_cost_bp":40,"engine":"heap-optimized exact same portfolio rules",
      "mdd":"realized-equity MDD; no intra-trade mark-to-market"},"variants":{}}
    for v in VARIANTS:
        x=D[D.variant==v]
        tg=make_groups(x[x.year<=2024]); vg=make_groups(x[x.year>=2025])
        rows=[]
        for mn,w in MAPS.items():
            for bp in BASE_PCTS:
                for cap in EXPOSURE_CAPS:
                    t20=sim(tg,w,bp,cap,20); v20=sim(vg,w,bp,cap,20)
                    t40=sim(tg,w,bp,cap,40); v40=sim(vg,w,bp,cap,40)
                    score=math.log(max(t20["equity_multiple"],1e-12))-0.012*t20["mdd_pct"]+0.35*math.log(max(t40["equity_multiple"],1e-12))
                    rows.append({"map":mn,"weights":w,"base_pct":bp,"exposure_cap_pct":cap,
                                 "train20":t20,"valid20":v20,"train40":t40,"valid40":v40,"train_score":score})
        rows.sort(key=lambda z:z["train_score"],reverse=True)
        out["variants"][v]={"train_selected":rows[0],"train_top15":rows[:15],"all_results":rows}
        print("VAR",v,"SELECT",rows[0]["map"],rows[0]["base_pct"],rows[0]["exposure_cap_pct"],flush=True)
        for r in rows[:10]:
            print("TOP",r["map"],r["base_pct"],r["exposure_cap_pct"],
                  "tr20",round(r["train20"]["equity_multiple"],4),"trMDD",round(r["train20"]["mdd_pct"],2),
                  "va20",round(r["valid20"]["equity_multiple"],4),"vaMDD",round(r["valid20"]["mdd_pct"],2),
                  "va40",round(r["valid40"]["equity_multiple"],4),"n",r["valid20"]["accepted"],flush=True)
    json.dump(out,open(a.out,"w"),indent=2)
    print("FAST_DYNAMIC_SIZING_PASS",len(D),flush=True)
if __name__=="__main__": main()
