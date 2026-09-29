import argparse,glob,json,heapq,math
from collections import defaultdict
import pandas as pd,numpy as np

VARIANT="P12_SB0.1_R4"; CROW_LO=30; CROW_HI=50; SAME_MAX=10
BASE=(0.10,0.25,0.50,0.75,1.0,1.5,2.0,3.0,4.0,5.0)
CAPS=(25.,50.,75.,100.,150.,200.,300.)
SYMCAPS=(5.,10.,15.,20.,30.,50.)
COSTS=(0.20,0.40)
TRAIN_START=pd.Timestamp("2021-09-17 16:00:00",tz="UTC")
TRAIN_END=pd.Timestamp("2025-01-01",tz="UTC")
VALID_START=pd.Timestamp("2025-01-01",tz="UTC")
VALID_END=pd.Timestamp("2026-08-31 20:00:00",tz="UTC")

def load_events(root):
    fs=glob.glob(root+"/**/events_*.csv.gz",recursive=True);assert len(fs)==8
    cols=["variant","symbol","signal_ts","fill_ts","exit_ts","outcome","pnl_pct","stop_pct"]
    D=pd.concat([pd.read_csv(f,usecols=cols) for f in fs],ignore_index=True)
    for c in ("signal_ts","fill_ts","exit_ts","pnl_pct","stop_pct"):D[c]=pd.to_numeric(D[c],errors="coerce")
    D=D[D.variant.eq(VARIANT)&D.outcome.isin(["win","loss"])&D.exit_ts.notna()&D.pnl_pct.notna()].copy()
    D["signal_dt"]=pd.to_datetime(D.signal_ts,unit="ms",utc=True);D["exit_dt"]=pd.to_datetime(D.exit_ts,unit="ms",utc=True)
    return D
def counts(root):
    fs=glob.glob(root+"/**/setups_*.csv.gz",recursive=True);assert len(fs)==8
    S=pd.concat([pd.read_csv(f,usecols=["variant","signal_ts","setup"]) for f in fs],ignore_index=True)
    S=S[S.variant.eq("P4T26_BASE")]
    return S.groupby("signal_ts",as_index=False).agg(setups=("setup","sum"))
def btc(root):
    fs=glob.glob(root+"/**/BTCUSDT.csv.gz",recursive=True);assert len(fs)==1
    d=pd.read_csv(fs[0],usecols=["open_time","close"]).sort_values("open_time")
    d["dt"]=pd.to_datetime(pd.to_numeric(d.open_time,errors="raise"),unit="ms",utc=True)
    d["close"]=pd.to_numeric(d.close,errors="raise")
    q=d.set_index("dt")["close"].resample("1D").last().dropna().to_frame("close")
    q["prev_close"]=q.close.shift(1);q["sma50"]=q.close.rolling(50,min_periods=50).mean().shift(1);q["sma200"]=q.close.rolling(200,min_periods=200).mean().shift(1);q["ret30"]=q.close.shift(1)/q.close.shift(31)-1
    return q.reset_index().rename(columns={"dt":"day"})
def maxstreak(a):
    cur=best=0
    for x in a:
      if x<0:cur+=1;best=max(best,cur)
      else:cur=0
    return best
def cal_cagr(eq,start,end):
    yrs=(end-start).total_seconds()/86400/365.25
    return (eq**(1/yrs)-1)*100 if eq>0 and yrs>0 else None
def sim(x,bp,cap,symcap,cost,cal_start,cal_end):
    x=x.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort")
    eq=1.;peak=1.;mdd=0.;gross=0.;seq=0
    heap=[];pos={};symids=defaultdict(set)
    closed=[];expo=[];risk=[];conc=[];symexpo=[];accepted=skip_same=skip_cap=skip_symcap=0
    def snap():
      nonlocal peak,mdd
      q=max(eq,1e-12);expo.append(gross/q*100);conc.append(len(pos))
      risk.append(sum(p["n"]*p["stop"]/100 for p in pos.values())/q*100)
      mx=0
      for s,ids in symids.items():mx=max(mx,sum(pos[i]["n"] for i in ids if i in pos)/q*100)
      symexpo.append(mx);peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak*100 if peak else 0)
    def close(ts):
      nonlocal eq,gross
      while heap and heap[0][0]<=ts:
        et=heap[0][0];batch=[]
        while heap and heap[0][0]==et:
          _,pid=heapq.heappop(heap)
          if pid in pos:batch.append((pid,pos[pid]))
        delta=0
        for pid,p in batch:
          pos.pop(pid,None);symids[p["s"]].discard(pid);gross-=p["n"]
          net=p["pnl"]-cost;pl=p["n"]*net/100;delta+=pl;closed.append((et,pid,net,p["outcome"]))
        eq+=delta;snap()
    for ts,g in x.groupby("fill_ts",sort=True):
      ts=int(ts);close(ts)
      if eq<=0:break
      baseeq=eq;intended=baseeq*bp/100
      eligible=[]
      # first apply same-symbol count. If multiple same-symbol fills share timestamp,
      # keep earliest signal rows up to remaining capacity (not symbol ordering).
      bysym=defaultdict(list)
      for r in g.itertuples(index=False):bysym[str(r.symbol)].append(r)
      for s,rows in bysym.items():
        room_n=max(0,SAME_MAX-len(symids[s]))
        if room_n<=0:
          skip_same+=len(rows);continue
        rows=sorted(rows,key=lambda r:(int(r.signal_ts),float(r.stop_pct)))
        eligible.extend(rows[:room_n]);skip_same+=max(0,len(rows)-room_n)
      if not eligible:
        snap();continue

      gross_room=max(0,baseeq*cap/100-gross)
      # per-symbol room and intended desired notionals
      desired=[];total_desired=0.
      for r in eligible:
        s=str(r.symbol);cur=sum(pos[i]["n"] for i in symids[s] if i in pos)
        sym_room=max(0,baseeq*symcap/100-cur)
        d=min(intended,sym_room)
        if d<=0:
          skip_symcap+=1;continue
        desired.append([r,d]);total_desired+=d
      if not desired:
        snap();continue
      scale=min(1.0,gross_room/total_desired) if total_desired>0 else 0.
      if scale<=0:
        skip_cap+=len(desired);snap();continue
      for r,d in desired:
        n=d*scale
        # no arbitrary 20% cutoff: pro-rata keeps all economically eligible signals.
        if n<=0:continue
        s=str(r.symbol);seq+=1
        pos[seq]={"exit":int(r.exit_ts),"n":n,"pnl":float(r.pnl_pct),"stop":float(r.stop_pct),"outcome":str(r.outcome),"s":s}
        symids[s].add(seq);gross+=n;heapq.heappush(heap,(int(r.exit_ts),seq));accepted+=1
      if scale<1:skip_cap+=len(desired)
      snap()
    close(10**30)
    net=np.array([z[2] for z in closed]);gp=net[net>0].sum();gl=-net[net<0].sum();pf=gp/gl if gl>0 else None
    q=lambda a,p:float(np.quantile(a,p)) if a else 0.
    return {"ret":(eq-1)*100,"calendar_cagr":cal_cagr(eq,cal_start,cal_end),"mdd":mdd,"pf":pf,"n":accepted,"loss_streak":maxstreak(net),
            "avg_exp":float(np.mean(expo)) if expo else 0,"p95_exp":q(expo,.95),"max_exp":max(expo) if expo else 0,
            "p99_stoprisk":q(risk,.99),"max_stoprisk":max(risk) if risk else 0,"avg_conc":float(np.mean(conc)) if conc else 0,
            "max_conc":max(conc) if conc else 0,"p95_symexp":q(symexpo,.95),"max_symexp":max(symexpo) if symexpo else 0,
            "skip_same":skip_same,"cap_scaled_groups":skip_cap,"skip_symcap":skip_symcap}
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--ledger");ap.add_argument("--sizing");ap.add_argument("--data");ap.add_argument("--out");a=ap.parse_args()
    D=load_events(a.ledger).merge(counts(a.sizing),on="signal_ts",how="inner",validate="many_to_one")
    D=D[D.setups.between(CROW_LO,CROW_HI)].copy();D["day"]=D.signal_dt.dt.floor("1D")
    D=D.merge(btc(a.data),on="day",how="left",validate="many_to_one")
    D=D[(D.prev_close>=D.sma200)&(D.sma50>=D.sma200)&(D.ret30>=0)].copy()
    cut=pd.Timestamp("2025-01-01",tz="UTC")
    tr=D[(D.signal_dt<cut)&(D.exit_dt<cut)].copy();va=D[D.signal_dt>=cut].copy()
    rows=[]
    for bp in BASE:
      for cap in CAPS:
        for sc in SYMCAPS:
          if sc>cap:continue
          r={"base_pct":bp,"cap_pct":cap,"symcap_pct":sc}
          for cost in COSTS:
            tag=str(int(cost*100))+"bp"
            r["train_"+tag]=sim(tr,bp,cap,sc,cost,TRAIN_START,TRAIN_END)
            r["valid_"+tag]=sim(va,bp,cap,sc,cost,VALID_START,VALID_END)
          rows.append(r)
    good=[r for r in rows if r["train_20bp"]["ret"]>0 and r["valid_20bp"]["ret"]>0]
    stress=[r for r in good if r["train_40bp"]["ret"]>0 and r["valid_40bp"]["ret"]>0]
    ranked=sorted(good,key=lambda r:((r["train_20bp"]["calendar_cagr"] or -999)/(1+r["train_20bp"]["mdd"]),r["train_20bp"]["calendar_cagr"] or -999),reverse=True)
    money=sorted(good,key=lambda r:r["train_20bp"]["calendar_cagr"] or -999,reverse=True)
    out={"definition":{"variant":VARIANT,"crowding":[CROW_LO,CROW_HI],"same_max":SAME_MAX,"gate":"BTC>=SMA200 & SMA50>=SMA200 & ret30>=0","allocation":"pro-rata across all eligible same-fill-time signals; no symbol-order selection","base":BASE,"caps":CAPS,"symcaps":SYMCAPS,"costs":COSTS},"train_n":len(tr),"valid_n":len(va),"positive20":len(good),"positive40":len(stress),"top_return_risk":ranked[:50],"top_cagr":money[:50]}
    json.dump(out,open(a.out,"w"),indent=2)
    print("BULL_PRORATA_PASS","train",len(tr),"valid",len(va),"positive20",len(good),"positive40",len(stress))
    for r in ranked[:15]:
      t=r["train_20bp"];v=r["valid_20bp"];s=r["valid_40bp"]
      print("TOP PR","bp",r["base_pct"],"cap",r["cap_pct"],"symcap",r["symcap_pct"],"trainRet",round(t["ret"],1),"calCAGR",round(t["calendar_cagr"],2),"MDD",round(t["mdd"],1),"validRet",round(v["ret"],1),"calCAGR",round(v["calendar_cagr"],2),"MDD",round(v["mdd"],1),"valid40",round(s["ret"],1),"N",v["n"],"avgExp",round(v["avg_exp"],1),"p99Risk",round(v["p99_stoprisk"],1))
if __name__=="__main__":main()
