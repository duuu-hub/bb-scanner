import argparse,glob,json,heapq
from collections import defaultdict
import pandas as pd,numpy as np

VARIANT="P12_SB0.1_R4"; LO=30; HI=50; SAME_MAX=10
BASE=(3.0,4.0,5.0,6.0,7.5,10.0,12.5)
CAPS=(10.,15.,20.,25.,30.,40.,50.)
SYMCAP=20.0
COSTS=(0.20,0.40,0.60)

def load(ledger,sizing,data):
    fs=glob.glob(ledger+"/**/events_*.csv.gz",recursive=True);assert len(fs)==8,(len(fs),fs)
    cols=["variant","symbol","signal_ts","fill_ts","exit_ts","outcome","pnl_pct","stop_pct"]
    ds=[]
    for f in fs:
        d=pd.read_csv(f,usecols=cols)
        d=d[d.variant.eq(VARIANT)&d.outcome.isin(["win","loss"])&d.exit_ts.notna()&d.pnl_pct.notna()]
        ds.append(d)
    D=pd.concat(ds,ignore_index=True)
    for c in ("signal_ts","fill_ts","exit_ts","pnl_pct","stop_pct"):D[c]=pd.to_numeric(D[c],errors="coerce")
    D["signal_dt"]=pd.to_datetime(D.signal_ts,unit="ms",utc=True);D["exit_dt"]=pd.to_datetime(D.exit_ts,unit="ms",utc=True)

    ss=glob.glob(sizing+"/**/setups_*.csv.gz",recursive=True);assert len(ss)==8,(len(ss),ss)
    cs=[]
    for f in ss:
        d=pd.read_csv(f,usecols=["variant","signal_ts","setup"])
        cs.append(d[d.variant.eq("P4T26_BASE")][["signal_ts","setup"]])
    C=pd.concat(cs,ignore_index=True).groupby("signal_ts",as_index=False).agg(setups=("setup","sum"))
    D=D.merge(C,on="signal_ts",how="inner",validate="many_to_one")
    D=D[D.setups.between(LO,HI)].copy()

    bf=glob.glob(data+"/**/BTCUSDT.csv.gz",recursive=True);assert len(bf)==1,bf
    b=pd.read_csv(bf[0],usecols=["open_time","close"]).sort_values("open_time")
    b["dt"]=pd.to_datetime(pd.to_numeric(b.open_time,errors="raise"),unit="ms",utc=True);b["close"]=pd.to_numeric(b.close,errors="raise")
    data_start=b.dt.min();data_end=b.dt.max()+pd.Timedelta(minutes=15)
    q=b.set_index("dt")["close"].resample("1D").last().dropna().to_frame("close")
    q["prev_close"]=q.close.shift(1);q["sma50"]=q.close.rolling(50,min_periods=50).mean().shift(1);q["sma200"]=q.close.rolling(200,min_periods=200).mean().shift(1);q["ret30"]=q.close.shift(1)/q.close.shift(31)-1
    q=q.reset_index().rename(columns={"dt":"day"})
    D["day"]=D.signal_dt.dt.floor("1D");D=D.merge(q,on="day",how="left",validate="many_to_one")
    D=D[(D.prev_close>=D.sma200)&(D.sma50>=D.sma200)&(D.ret30>=0)].copy()
    syms={s:i for i,s in enumerate(sorted(D.symbol.unique()))};D["sid"]=D.symbol.map(syms).astype(int)
    cut=pd.Timestamp("2025-01-01",tz="UTC")
    tr=D[(D.signal_dt<cut)&(D.exit_dt<cut)].copy();va=D[D.signal_dt>=cut].copy()
    periods={"train":(data_start,min(cut,data_end)),"valid":(max(cut,data_start),data_end)}
    return tr,va,periods

def prep(x):
    x=x.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort").reset_index(drop=True)
    return (x.fill_ts.to_numpy(np.int64),x.exit_ts.to_numpy(np.int64),x.sid.to_numpy(np.int32),x.pnl_pct.to_numpy(float),x.stop_pct.to_numpy(float))

def sim(arr,bp,cap,cost,pstart,pend):
    fill,exit_,sid,pnl,stop=arr;n=len(fill)
    eq=1.;peak=1.;mdd=0.;gross=0.;seq=0;accepted=0
    heap=[];pos={};symids=defaultdict(set)
    expo_area=0.;conc_area=0.;last_ts=int(pstart.timestamp()*1000)
    risk_hist=[];max_exp=0.;max_conc=0

    def accrue(ts):
        nonlocal expo_area,conc_area,last_ts,max_exp,max_conc
        ts=max(ts,last_ts);dt=ts-last_ts
        q=max(eq,1e-12);ex=gross/q*100
        expo_area+=ex*dt;conc_area+=len(pos)*dt;last_ts=ts
        max_exp=max(max_exp,ex);max_conc=max(max_conc,len(pos))
    def snap():
        nonlocal peak,mdd
        q=max(eq,1e-12)
        risk_hist.append(sum(p[2]*p[4]/100 for p in pos.values())/q*100)
        peak=max(peak,eq)
        if peak>0:mdd=max(mdd,(peak-eq)/peak*100)
    def close_until(ts):
        nonlocal eq,gross
        while heap and heap[0][0]<=ts:
            et=heap[0][0];accrue(et);batch=[]
            while heap and heap[0][0]==et:
                _,pid=heapq.heappop(heap)
                p=pos.pop(pid,None)
                if p is not None:batch.append((pid,p))
            delta=0.
            for pid,p in batch:
                symids[p[0]].discard(pid);gross-=p[2]
                net=p[3]-cost;delta+=p[2]*net/100
            eq+=delta;snap()

    i=0
    while i<n:
        ts=int(fill[i]);close_until(ts);accrue(ts)
        if eq<=0:break
        baseeq=eq;intended=baseeq*bp/100;j=i
        while j<n and fill[j]==fill[i]:j+=1
        for k in range(i,j):
            s=int(sid[k]);ids=symids[s]
            if len(ids)>=SAME_MAX:continue
            cur=sum(pos[ii][2] for ii in ids if ii in pos)
            roomg=max(0.,baseeq*cap/100-gross);rooms=max(0.,baseeq*SYMCAP/100-cur)
            notion=min(intended,roomg,rooms)
            if notion<intended*.2:continue
            seq+=1;pos[seq]=(s,int(exit_[k]),notion,float(pnl[k]),float(stop[k]))
            ids.add(seq);gross+=notion;heapq.heappush(heap,(int(exit_[k]),seq));accepted+=1
        snap();i=j
    close_until(int(pend.timestamp()*1000));accrue(int(pend.timestamp()*1000))
    total_ms=max(1,int((pend-pstart).total_seconds()*1000))
    years=max(1e-9,(pend-pstart).total_seconds()/86400/365.25)
    cagr=(eq**(1/years)-1)*100 if eq>0 else None
    return {"ret":(eq-1)*100,"cagr_calendar":cagr,"mdd":mdd,"n":accepted,
            "avg_exp_time":expo_area/total_ms,"avg_conc_time":conc_area/total_ms,
            "max_exp":max_exp,"max_conc":max_conc,
            "p99_stoprisk":float(np.quantile(risk_hist,.99)) if risk_hist else 0.,
            "max_stoprisk":max(risk_hist) if risk_hist else 0.}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--ledger");ap.add_argument("--sizing");ap.add_argument("--data");ap.add_argument("--out");a=ap.parse_args()
    tr,va,periods=load(a.ledger,a.sizing,a.data);ta,vaa=prep(tr),prep(va)
    rows=[]
    for bp in BASE:
      for cap in CAPS:
        r={"base_pct":bp,"cap_pct":cap,"symcap_pct":SYMCAP}
        for cost in COSTS:
          tag=str(int(cost*100))+"bp"
          r["train_"+tag]=sim(ta,bp,cap,cost,*periods["train"]);r["valid_"+tag]=sim(vaa,bp,cap,cost,*periods["valid"])
        rows.append(r)
        t=r["train_20bp"];v=r["valid_20bp"];s=r["train_40bp"]
        print("ROW","bp",bp,"cap",cap,"TRret",round(t["ret"],2),"TRcagr",round(t["cagr_calendar"],2),"TRmdd",round(t["mdd"],2),"VAret",round(v["ret"],2),"VAcagr",round(v["cagr_calendar"],2),"VAmdd",round(v["mdd"],2),"TR40",round(s["ret"],2),flush=True)
    good=[r for r in rows if all(r[k]["ret"]>0 for k in ("train_20bp","valid_20bp","train_40bp","valid_40bp"))]
    rank=sorted(good,key=lambda r:((r["train_20bp"]["cagr_calendar"] or -999)/(1+r["train_20bp"]["mdd"]),r["train_20bp"]["cagr_calendar"] or -999),reverse=True)
    out={"definition":{"variant":VARIANT,"crowding":[LO,HI],"same_max":SAME_MAX,"symcap":SYMCAP,"gate":"BTC>=SMA200 & SMA50>=SMA200 & ret30>=0","base":BASE,"caps":CAPS,"costs":COSTS,
                       "cagr":"calendar-period CAGR including inactive time","mdd":"realized-equity MDD"},"periods":{k:[str(v[0]),str(v[1])] for k,v in periods.items()},"train_n":len(tr),"valid_n":len(va),"rows":rows,"robust":rank}
    json.dump(out,open(a.out,"w"),indent=2)
    print("REFINE_PASS","train",len(tr),"valid",len(va),"robust",len(rank),flush=True)
    for r in rank[:15]:
      t=r["train_20bp"];v=r["valid_20bp"];t4=r["train_40bp"];v4=r["valid_40bp"];v6=r["valid_60bp"]
      print("TOP","bp",r["base_pct"],"cap",r["cap_pct"],"TR",round(t["ret"],1),round(t["cagr_calendar"],2),round(t["mdd"],1),"VA",round(v["ret"],1),round(v["cagr_calendar"],2),round(v["mdd"],1),"TR40",round(t4["ret"],1),"VA40",round(v4["ret"],1),"VA60",round(v6["ret"],1),"avgExp",round(v["avg_exp_time"],1),"p99Risk",round(v["p99_stoprisk"],1),flush=True)
if __name__=="__main__":main()
