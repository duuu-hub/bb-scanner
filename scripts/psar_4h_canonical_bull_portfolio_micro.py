import argparse,glob,json,heapq
from collections import defaultdict
import pandas as pd,numpy as np

VARIANT="P12_SB0.1_R4"; LO=30; HI=50; SAME_MAX=10
BASE=(0.10,0.25,0.50,1.0,2.0,3.0)
CAPS=(50.,100.,150.,200.)
SYMCAPS=(10.,20.,30.)
COSTS=(0.20,0.40)

def load(ledger,sizing,data):
    fs=glob.glob(ledger+"/**/events_*.csv.gz",recursive=True);assert len(fs)==8
    cols=["variant","symbol","signal_ts","fill_ts","exit_ts","outcome","pnl_pct","stop_pct"]
    ds=[]
    for f in fs:
        d=pd.read_csv(f,usecols=cols)
        d=d[d.variant.eq(VARIANT)&d.outcome.isin(["win","loss"])&d.exit_ts.notna()&d.pnl_pct.notna()]
        ds.append(d)
    D=pd.concat(ds,ignore_index=True)
    for c in ("signal_ts","fill_ts","exit_ts","pnl_pct","stop_pct"):D[c]=pd.to_numeric(D[c],errors="coerce")
    D["signal_dt"]=pd.to_datetime(D.signal_ts,unit="ms",utc=True)
    D["exit_dt"]=pd.to_datetime(D.exit_ts,unit="ms",utc=True)
    ss=glob.glob(sizing+"/**/setups_*.csv.gz",recursive=True);assert len(ss)==8
    cs=[]
    for f in ss:
        d=pd.read_csv(f,usecols=["variant","signal_ts","setup"])
        cs.append(d[d.variant.eq("P4T26_BASE")][["signal_ts","setup"]])
    C=pd.concat(cs,ignore_index=True).groupby("signal_ts",as_index=False).agg(setups=("setup","sum"))
    D=D.merge(C,on="signal_ts",how="inner",validate="many_to_one")
    D=D[D.setups.between(LO,HI)].copy()
    bf=glob.glob(data+"/**/BTCUSDT.csv.gz",recursive=True);assert len(bf)==1,bf
    b=pd.read_csv(bf[0],usecols=["open_time","close"]).sort_values("open_time")
    b["dt"]=pd.to_datetime(pd.to_numeric(b.open_time,errors="raise"),unit="ms",utc=True)
    b["close"]=pd.to_numeric(b.close,errors="raise")
    q=b.set_index("dt")["close"].resample("1D").last().dropna().to_frame("close")
    q["prev_close"]=q.close.shift(1);q["sma50"]=q.close.rolling(50,min_periods=50).mean().shift(1);q["sma200"]=q.close.rolling(200,min_periods=200).mean().shift(1);q["ret30"]=q.close.shift(1)/q.close.shift(31)-1
    q=q.reset_index().rename(columns={"dt":"day"})
    D["day"]=D.signal_dt.dt.floor("1D")
    D=D.merge(q,on="day",how="left",validate="many_to_one")
    D=D[(D.prev_close>=D.sma200)&(D.sma50>=D.sma200)&(D.ret30>=0)].copy()
    # symbol ids
    syms={s:i for i,s in enumerate(sorted(D.symbol.unique()))};D["sid"]=D.symbol.map(syms).astype(int)
    cut=pd.Timestamp("2025-01-01",tz="UTC")
    tr=D[(D.signal_dt<cut)&(D.exit_dt<cut)].copy()
    va=D[D.signal_dt>=cut].copy()
    return tr,va

def prep(x):
    x=x.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort").reset_index(drop=True)
    return (x.fill_ts.to_numpy(np.int64),x.exit_ts.to_numpy(np.int64),x.sid.to_numpy(np.int32),
            x.pnl_pct.to_numpy(float),x.stop_pct.to_numpy(float),x.outcome.eq("win").to_numpy(np.int8))

def sim(arr,bp,cap,symcap,cost):
    fill,exit_,sid,pnl,stop,win=arr;n=len(fill)
    eq=1.;peak=1.;mdd=0.;gross=0.;seq=0;accepted=0
    heap=[];pos={};symids=defaultdict(set);closed=[]
    expo=[];risk=[];conc=[]
    start=int(fill[0]) if n else None;end=start
    def snap():
        nonlocal peak,mdd
        q=max(eq,1e-12)
        expo.append(gross/q*100);conc.append(len(pos))
        risk.append(sum(p[2]*p[4]/100 for p in pos.values())/q*100)
        peak=max(peak,eq)
        if peak>0:mdd=max(mdd,(peak-eq)/peak*100)
    def close_until(ts):
        nonlocal eq,gross,end
        while heap and heap[0][0]<=ts:
            et=heap[0][0];batch=[]
            while heap and heap[0][0]==et:
                _,pid=heapq.heappop(heap)
                p=pos.pop(pid,None)
                if p is not None:batch.append((pid,p))
            delta=0.
            for pid,p in batch:
                symids[p[0]].discard(pid);gross-=p[2]
                net=p[3]-cost;delta+=p[2]*net/100;closed.append((net,p[5]))
            eq+=delta;end=max(end or et,et);snap()
    i=0
    while i<n:
        ts=int(fill[i]);close_until(ts)
        if eq<=0:break
        baseeq=eq;intended=baseeq*bp/100
        j=i
        while j<n and fill[j]==fill[i]:j+=1
        for k in range(i,j):
            s=int(sid[k]);ids=symids[s]
            if len(ids)>=SAME_MAX:continue
            cur=sum(pos[ii][2] for ii in ids if ii in pos)
            roomg=max(0.,baseeq*cap/100-gross);rooms=max(0.,baseeq*symcap/100-cur)
            notion=min(intended,roomg,rooms)
            if notion<intended*.2:continue
            seq+=1;pos[seq]=(s,int(exit_[k]),notion,float(pnl[k]),float(stop[k]),int(win[k]))
            ids.add(seq);gross+=notion;heapq.heappush(heap,(int(exit_[k]),seq));accepted+=1
        snap();i=j
    close_until(10**30)
    yrs=((end-start)/1000/86400/365.25) if start and end and end>start else None
    cagr=(eq**(1/yrs)-1)*100 if yrs and eq>0 else None
    net=np.array([z[0] for z in closed],float)
    gp=net[net>0].sum();gl=-net[net<0].sum();pf=gp/gl if gl>0 else None
    return {"ret":(eq-1)*100,"cagr":cagr,"mdd":mdd,"pf":pf,"n":accepted,
            "avg_exp":float(np.mean(expo)) if expo else 0,"p95_exp":float(np.quantile(expo,.95)) if expo else 0,
            "p99_stoprisk":float(np.quantile(risk,.99)) if risk else 0,"max_conc":max(conc) if conc else 0}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--ledger");ap.add_argument("--sizing");ap.add_argument("--data");ap.add_argument("--out");a=ap.parse_args()
    tr,va=load(a.ledger,a.sizing,a.data);ta,vaa=prep(tr),prep(va)
    rows=[]
    for bp in BASE:
      for cap in CAPS:
        for sc in SYMCAPS:
          if sc>cap:continue
          r={"base_pct":bp,"cap_pct":cap,"symcap_pct":sc}
          for cost in COSTS:
            tag=str(int(cost*100))+"bp";r["train_"+tag]=sim(ta,bp,cap,sc,cost);r["valid_"+tag]=sim(vaa,bp,cap,sc,cost)
          rows.append(r)
          t=r["train_20bp"];v=r["valid_20bp"]
          print("ROW",bp,cap,sc,"TR",round(t["ret"],2),round(t["cagr"],2),round(t["mdd"],2),"VA",round(v["ret"],2),round(v["cagr"],2),round(v["mdd"],2),flush=True)
    good=[r for r in rows if r["train_20bp"]["ret"]>0 and r["valid_20bp"]["ret"]>0]
    stress=[r for r in good if r["train_40bp"]["ret"]>0 and r["valid_40bp"]["ret"]>0]
    rank=sorted(good,key=lambda r:((r["train_20bp"]["cagr"] or -999)/(1+r["train_20bp"]["mdd"]),r["train_20bp"]["cagr"] or -999),reverse=True)
    out={"definition":{"variant":VARIANT,"crowding":[LO,HI],"same_max":SAME_MAX,"gate":"BTC>=SMA200 & SMA50>=SMA200 & ret30>=0","base":BASE,"caps":CAPS,"symcaps":SYMCAPS,"costs":COSTS},
         "train_n":len(tr),"valid_n":len(va),"positive20":len(good),"positive40":len(stress),"top":rank[:30]}
    json.dump(out,open(a.out,"w"),indent=2)
    print("MICRO_PASS",len(tr),len(va),"good20",len(good),"good40",len(stress),flush=True)
    for r in rank[:10]:
      t=r["train_20bp"];v=r["valid_20bp"];s=r["valid_40bp"]
      print("TOP",r["base_pct"],r["cap_pct"],r["symcap_pct"],"TR",round(t["ret"],1),round(t["cagr"],2),round(t["mdd"],1),"VA",round(v["ret"],1),round(v["cagr"],2),round(v["mdd"],1),"VA40",round(s["ret"],1),"PF",round(v["pf"],3) if v["pf"] else None,"N",v["n"],flush=True)
if __name__=="__main__":main()
