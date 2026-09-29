import argparse,glob,json,heapq
from collections import defaultdict
import pandas as pd,numpy as np

VARIANT="P12_SB0.1_R4"
CROWD_LO=30; CROWD_HI=50
BASE=(0.1,0.25,0.5,1.0,2.0,3.0,5.0)
CAPS=(25.0,50.0,100.0,150.0,200.0,300.0)
SAME=(3,5,10,20,999)
SYMCAPS=(10.0,20.0,30.0,50.0,100.0)
COSTS=(0.20,0.40)

def load_events(root):
    fs=glob.glob(root+"/**/events_*.csv.gz",recursive=True); assert len(fs)==8
    cols=["variant","symbol","signal_ts","fill_ts","exit_ts","outcome","pnl_pct","stop_pct"]
    D=pd.concat([pd.read_csv(f,usecols=cols) for f in fs],ignore_index=True)
    for c in ("signal_ts","fill_ts","exit_ts","pnl_pct","stop_pct"):D[c]=pd.to_numeric(D[c],errors="coerce")
    D=D[D.variant.eq(VARIANT)&D.outcome.isin(["win","loss"])&D.exit_ts.notna()&D.pnl_pct.notna()].copy()
    D["signal_dt"]=pd.to_datetime(D.signal_ts,unit="ms",utc=True);D["exit_dt"]=pd.to_datetime(D.exit_ts,unit="ms",utc=True)
    return D

def crowd_counts(root):
    fs=glob.glob(root+"/**/setups_*.csv.gz",recursive=True);assert len(fs)==8
    S=pd.concat([pd.read_csv(f,usecols=["variant","signal_ts","setup"]) for f in fs],ignore_index=True)
    S=S[S.variant.eq("P4T26_BASE")]
    return S.groupby("signal_ts",as_index=False).agg(setups=("setup","sum"))

def btc_features(root):
    fs=glob.glob(root+"/**/BTCUSDT.csv.gz",recursive=True);assert len(fs)==1,(len(fs),fs)
    d=pd.read_csv(fs[0],usecols=["open_time","close"]).sort_values("open_time")
    d["dt"]=pd.to_datetime(pd.to_numeric(d.open_time),unit="ms",utc=True);d["close"]=pd.to_numeric(d.close)
    q=d.set_index("dt")["close"].resample("1D").last().dropna().to_frame("close")
    q["prev_close"]=q.close.shift(1)
    q["sma50"]=q.close.rolling(50,min_periods=50).mean().shift(1)
    q["sma200"]=q.close.rolling(200,min_periods=200).mean().shift(1)
    q["ret30"]=q.close.shift(1)/q.close.shift(31)-1
    return q.reset_index().rename(columns={"dt":"day"})

def prepare(ledger,sizing,data):
    D=load_events(ledger).merge(crowd_counts(sizing),on="signal_ts",how="inner",validate="many_to_one")
    D=D[D.setups.between(CROWD_LO,CROWD_HI)].copy()
    D["day"]=D.signal_dt.dt.floor("1D")
    D=D.merge(btc_features(data),on="day",how="left",validate="many_to_one")
    D=D[(D.prev_close>=D.sma200)&(D.sma50>=D.sma200)&(D.ret30>=0)].copy()
    cut=pd.Timestamp("2025-01-01",tz="UTC")
    tr=D[(D.signal_dt<cut)&(D.exit_dt<cut)].copy()
    va=D[D.signal_dt>=cut].copy()
    return tr,va

def sim(x,base_pct,cap_pct,same_max,symcap_pct,cost):
    x=x.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort")
    eq=1.0;peak=1.0;mdd=0.0;gross=0.0;seq=0
    heap=[];pos={};symids=defaultdict(set);closed=[]
    exp=[];conc=[];risk=[];symexp=[]
    start=int(x.fill_ts.min()) if len(x) else None;end=start
    accepted=skip_same=skip_cap=skip_symcap=0
    def snap():
        nonlocal peak,mdd
        q=max(eq,1e-12);exp.append(gross/q*100);conc.append(len(pos))
        risk.append(sum(p["notional"]*p["stop_pct"]/100 for p in pos.values())/q*100)
        mx=0
        for s,ids in symids.items():
            mx=max(mx,sum(pos[i]["notional"] for i in ids if i in pos)/q*100)
        symexp.append(mx);peak=max(peak,eq)
        if peak>0:mdd=max(mdd,(peak-eq)/peak*100)
    def close_until(ts):
        nonlocal eq,gross,end
        while heap and heap[0][0]<=ts:
            et=heap[0][0];batch=[]
            while heap and heap[0][0]==et:
                _,pid=heapq.heappop(heap)
                if pid in pos:batch.append((pid,pos[pid]))
            d=0.0
            for pid,p in batch:
                pos.pop(pid,None);symids[p["symbol"]].discard(pid);gross-=p["notional"]
                net=p["pnl_pct"]-cost;pnl=p["notional"]*net/100;d+=pnl;closed.append((et,pid,net,p["outcome"]))
            eq+=d;end=max(end or et,et);snap()
    for ts,g in x.groupby("fill_ts",sort=True):
        ts=int(ts);close_until(ts)
        if eq<=0:break
        baseeq=eq; intended=baseeq*base_pct/100
        for r in g.itertuples(index=False):
            s=str(r.symbol);ids=symids[s]
            if len(ids)>=same_max:skip_same+=1;continue
            sexp=sum(pos[i]["notional"] for i in ids if i in pos)
            roomg=max(0,baseeq*cap_pct/100-gross); rooms=max(0,baseeq*symcap_pct/100-sexp)
            notion=min(intended,roomg,rooms)
            if notion<intended*.2:
                if roomg<=rooms:skip_cap+=1
                else:skip_symcap+=1
                continue
            seq+=1;p={"exit_ts":int(r.exit_ts),"notional":notion,"pnl_pct":float(r.pnl_pct),"stop_pct":float(r.stop_pct),"outcome":str(r.outcome),"symbol":s}
            pos[seq]=p;symids[s].add(seq);gross+=notion;heapq.heappush(heap,(p["exit_ts"],seq));accepted+=1
        snap()
    close_until(10**30)
    yrs=((end-start)/1000/86400/365.25) if start and end and end>start else None
    cagr=(eq**(1/yrs)-1)*100 if yrs and eq>0 else None
    net=np.array([z[2] for z in closed],float);gp=net[net>0].sum();gl=-net[net<0].sum();pf=gp/gl if gl>0 else None
    rawwins=sum(1 for z in closed if z[3]=="win")
    q=lambda a,p:float(np.quantile(a,p)) if a else 0
    return {"return_pct":(eq-1)*100,"cagr_pct":cagr,"mdd_pct":mdd,"pf_net":pf,"win_pct":100*rawwins/len(closed) if closed else None,
            "executed":accepted,"skip_same":skip_same,"skip_cap":skip_cap,"skip_symcap":skip_symcap,
            "avg_concurrent":float(np.mean(conc)) if conc else 0,"max_concurrent":max(conc) if conc else 0,
            "avg_exposure_pct":float(np.mean(exp)) if exp else 0,"p95_exposure_pct":q(exp,.95),"max_exposure_pct":max(exp) if exp else 0,
            "p99_stoprisk_pct":q(risk,.99),"max_stoprisk_pct":max(risk) if risk else 0,
            "p95_max_symbol_exposure_pct":q(symexp,.95),"max_symbol_exposure_pct":max(symexp) if symexp else 0,"years":yrs}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--ledger",default="ledger");ap.add_argument("--sizing",default="sizing");ap.add_argument("--data",default="data");ap.add_argument("--out",default="final_account.json");a=ap.parse_args()
    tr,va=prepare(a.ledger,a.sizing,a.data)
    rows=[]
    for sm in SAME:
      for sc in SYMCAPS:
       for bp in BASE:
        for cap in CAPS:
         if sc>cap:continue
         r={"same_max":sm,"symcap":sc,"base_pct":bp,"cap_pct":cap}
         for cost in COSTS:
            tag=f"{int(cost*100)}bp";r["train_"+tag]=sim(tr,bp,cap,sm,sc,cost);r["valid_"+tag]=sim(va,bp,cap,sm,sc,cost)
         rows.append(r)
    good=[r for r in rows if r["train_20bp"]["return_pct"]>0 and r["valid_20bp"]["return_pct"]>0]
    stress=[r for r in good if r["train_40bp"]["return_pct"]>0 and r["valid_40bp"]["return_pct"]>0]
    # rank only on train; prefer meaningful CAGR per drawdown, with train CAGR as tiebreaker
    ranked=sorted(good,key=lambda r:((r["train_20bp"]["cagr_pct"] or -999)/(1+r["train_20bp"]["mdd_pct"]),r["train_20bp"]["cagr_pct"] or -999),reverse=True)
    money=sorted(good,key=lambda r:r["train_20bp"]["cagr_pct"] or -999,reverse=True)
    out={"definition":{"variant":VARIANT,"crowding":[CROWD_LO,CROWD_HI],"regime":"BTC prev daily close>=SMA200 AND SMA50>=SMA200 AND prior 30D return>=0","train":"signal<2025 and exit<2025","valid":"signal>=2025","costs":COSTS},
         "train_rows":len(tr),"valid_rows":len(va),"positive20":len(good),"positive40":len(stress),"top_return_risk":ranked[:30],"top_train_cagr":money[:30],"all":rows}
    json.dump(out,open(a.out,"w"),indent=2)
    print("FINAL_ACCOUNT_PASS","train",len(tr),"valid",len(va),"good20",len(good),"good40",len(stress),flush=True)
    for r in ranked[:12]:
        t=r["train_20bp"];v=r["valid_20bp"];v4=r["valid_40bp"]
        print("TOP RR","same",r["same_max"],"symcap",r["symcap"],"base",r["base_pct"],"cap",r["cap_pct"],
              "trainCAGR",round(t["cagr_pct"],2),"ret",round(t["return_pct"],1),"MDD",round(t["mdd_pct"],1),
              "validCAGR",round(v["cagr_pct"],2),"ret",round(v["return_pct"],1),"MDD",round(v["mdd_pct"],1),
              "valid40ret",round(v4["return_pct"],1),"PF",round(v["pf_net"],3) if v["pf_net"] else None,
              "N",v["executed"],"avgExp",round(v["avg_exposure_pct"],1),"p99Risk",round(v["p99_stoprisk_pct"],1),flush=True)
    print("TOP MONEY",flush=True)
    for r in money[:8]:
        t=r["train_20bp"];v=r["valid_20bp"]
        print("MONEY","same",r["same_max"],"symcap",r["symcap"],"base",r["base_pct"],"cap",r["cap_pct"],
              "trainCAGR",round(t["cagr_pct"],2),"MDD",round(t["mdd_pct"],1),"validCAGR",round(v["cagr_pct"],2),"validMDD",round(v["mdd_pct"],1),flush=True)
if __name__=="__main__":main()
