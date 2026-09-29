import argparse,glob,json,heapq
import pandas as pd,numpy as np
SAME=(1,2,3,5,10,999)
COSTS=(0.0,0.10,0.20,0.40)

def load(root):
    fs=glob.glob(root+"/**/events_*.csv.gz",recursive=True); assert len(fs)==8,(len(fs),fs)
    D=pd.concat([pd.read_csv(x,usecols=["variant","symbol","signal_ts","fill_ts","exit_ts","outcome","pnl_pct","stop_pct"]) for x in fs],ignore_index=True)
    for c in ("signal_ts","fill_ts","exit_ts","pnl_pct","stop_pct"):D[c]=pd.to_numeric(D[c],errors="coerce")
    D=D[D.outcome.isin(["win","loss"])&D.exit_ts.notna()&D.pnl_pct.notna()].copy()
    D["signal_dt"]=pd.to_datetime(D.signal_ts,unit="ms",utc=True);D["exit_dt"]=pd.to_datetime(D.exit_ts,unit="ms",utc=True)
    cut=pd.Timestamp("2025-01-01",tz="UTC")
    return D[(D.signal_dt<cut)&(D.exit_dt<cut)].copy(),D[D.signal_dt>=cut].copy()

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
    ap=argparse.ArgumentParser();ap.add_argument("--root",default="ledger");ap.add_argument("--out",default="diag.json");a=ap.parse_args()
    tr,va=load(a.root);out={}
    for v in sorted(tr.variant.unique()):
        out[v]={}
        for sm in SAME:
            tz=selected(tr[tr.variant.eq(v)],sm);vz=selected(va[va.variant.eq(v)],sm)
            rec={"same_max":sm,"train_raw":int((tr.variant==v).sum()),"valid_raw":int((va.variant==v).sum())}
            for cost in COSTS:
                tag=f"{int(cost*100)}bp";rec["train_"+tag]=metrics(tz,cost);rec["valid_"+tag]=metrics(vz,cost)
            out[v][str(sm)]=rec
            t=rec["train_20bp"];z=rec["valid_20bp"];s=rec["valid_40bp"]
            print("STACK",v,"same",sm,"trainN",t["n"],"trainPF",round(t["pf"],3),"validN",z["n"],"validPF",round(z["pf"],3),"valid40",round(s["pf"],3),flush=True)
    json.dump(out,open(a.out,"w"),indent=2);print("STACK_DIAG_PASS",flush=True)
if __name__=="__main__":main()
