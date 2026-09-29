import argparse,glob,json,heapq
import pandas as pd,numpy as np
SAME=(1,2,3,5,10,999)
BUCKETS=("LE10","11_20","21_40","41_PLUS","GT10","GT20","ALL")
COSTS=(0.20,0.40)

def load_events(root):
    fs=glob.glob(root+"/**/events_*.csv.gz",recursive=True); assert len(fs)==8,(len(fs),fs)
    D=pd.concat([pd.read_csv(x,usecols=["variant","symbol","signal_ts","fill_ts","exit_ts","outcome","pnl_pct","stop_pct"]) for x in fs],ignore_index=True)
    for c in ("signal_ts","fill_ts","exit_ts","pnl_pct","stop_pct"):D[c]=pd.to_numeric(D[c],errors="coerce")
    D=D[D.outcome.isin(["win","loss"])&D.exit_ts.notna()&D.pnl_pct.notna()].copy()
    D["signal_dt"]=pd.to_datetime(D.signal_ts,unit="ms",utc=True);D["exit_dt"]=pd.to_datetime(D.exit_ts,unit="ms",utc=True)
    return D

def load_counts(root):
    fs=glob.glob(root+"/**/setups_*.csv.gz",recursive=True); assert len(fs)==8,(len(fs),fs)
    S=pd.concat([pd.read_csv(x,usecols=["variant","signal_ts","setup"]) for x in fs],ignore_index=True)
    S=S[S.variant.eq("P4T26_BASE")].copy()
    C=S.groupby("signal_ts",as_index=False).agg(setups=("setup","sum"))
    return C

def bucket_mask(x,b):
    s=x.setups
    if b=="LE10":return s<=10
    if b=="11_20":return (s>=11)&(s<=20)
    if b=="21_40":return (s>=21)&(s<=40)
    if b=="41_PLUS":return s>=41
    if b=="GT10":return s>10
    if b=="GT20":return s>20
    if b=="ALL":return s>=0
    raise ValueError(b)

def selected(x,same_max):
    x=x.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort")
    heap=[];active={};seq=0;idx=[]
    for r in x.itertuples():
        ts=int(r.fill_ts)
        while heap and heap[0][0]<=ts:
            _,_,sym=heapq.heappop(heap);active[sym]=max(0,active.get(sym,0)-1)
        sym=str(r.symbol)
        if active.get(sym,0)>=same_max:continue
        idx.append(r.Index);active[sym]=active.get(sym,0)+1;seq+=1
        heapq.heappush(heap,(int(r.exit_ts),seq,sym))
    return x.loc[idx].copy()

def metrics(z,cost):
    net=z.pnl_pct.to_numpy(float)-cost
    gp=net[net>0].sum();gl=-net[net<0].sum()
    return {"n":int(len(z)),"win_pct":float((z.outcome=="win").mean()*100) if len(z) else None,
            "pf":float(gp/gl) if gl>0 else None,"expectancy_pct":float(net.mean()) if len(net) else None,
            "sum_net_pct":float(net.sum()) if len(net) else 0.0}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--ledger",default="ledger");ap.add_argument("--sizing",default="sizing");ap.add_argument("--out",default="diag.json");a=ap.parse_args()
    D=load_events(a.ledger);C=load_counts(a.sizing)
    D=D.merge(C,on="signal_ts",how="inner",validate="many_to_one")
    cut=pd.Timestamp("2025-01-01",tz="UTC")
    tr=D[(D.signal_dt<cut)&(D.exit_dt<cut)].copy()
    va=D[D.signal_dt>=cut].copy()
    out={"definition":{"train":"signal<2025 and exit<2025","valid":"signal>=2025","crowding":"same 4H OPEN P4T26_BASE bearish setup count across symbols; setup count known at decision time","same_max":SAME,"costs_pct":COSTS},"variants":{}}
    for v in sorted(D.variant.unique()):
        out["variants"][v]={}
        for b in BUCKETS:
            tv=tr[(tr.variant==v)&bucket_mask(tr,b)].copy();vv=va[(va.variant==v)&bucket_mask(va,b)].copy()
            rec={"train_raw":len(tv),"valid_raw":len(vv),"same":{}}
            for sm in SAME:
                tz=selected(tv,sm);vz=selected(vv,sm)
                rr={}
                for cost in COSTS:
                    tag=f"{int(cost*100)}bp";rr["train_"+tag]=metrics(tz,cost);rr["valid_"+tag]=metrics(vz,cost)
                rec["same"][str(sm)]=rr
            out["variants"][v][b]=rec
            best=[]
            for sm,rr in rec["same"].items():
                t=rr["train_20bp"];z=rr["valid_20bp"];s=rr["valid_40bp"]
                if t["pf"] and z["pf"]:
                    best.append((min(t["pf"],z["pf"]),int(sm),t,z,s))
            best=sorted(best,reverse=True)[:2]
            for _,sm,t,z,s in best:
                print("BEST",v,b,"same",sm,"trainPF",round(t["pf"],3),"validPF",round(z["pf"],3),"valid40",round(s["pf"],3),"trainN",t["n"],"validN",z["n"],flush=True)
    json.dump(out,open(a.out,"w"),indent=2)
    print("CROWD_STACK_DIAG_PASS",len(D),flush=True)
if __name__=="__main__":main()
