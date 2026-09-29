import argparse,glob,json,heapq,math
import numpy as np,pandas as pd

BASE=(0.25,0.5,1.0,2.0,3.0,5.0,7.5,10.0)
CAPS=(25.0,50.0,100.0,150.0,200.0,300.0)
SAME=(1,2,3,5,10,999)
SYMCAPS=(10.0,20.0,30.0,50.0,100.0,999.0)
COSTS=(0.20,0.40)

def load(root):
    fs=glob.glob(root+"/**/events_*.csv.gz",recursive=True)
    ms=glob.glob(root+"/**/meta_*.json",recursive=True)
    assert len(fs)==8,(len(fs),fs); assert len(ms)==8,(len(ms),ms)
    meta=[json.load(open(x)) for x in ms]
    base=meta[0]["definition"]
    assert all(m["definition"]==base for m in meta),"meta mismatch"
    assert base["tp"]=="actual_fill - R*abs(actual_fill-SL)",base
    D=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
    for c in ("signal_ts","fill_ts","exit_ts","pnl_pct","stop_pct"):
        D[c]=pd.to_numeric(D[c],errors="coerce")
    D=D[D.outcome.isin(["win","loss"])&D.exit_ts.notna()&D.pnl_pct.notna()].copy()
    D["signal_dt"]=pd.to_datetime(D.signal_ts,unit="ms",utc=True)
    D["exit_dt"]=pd.to_datetime(D.exit_ts,unit="ms",utc=True)
    return D,base

def split(D):
    cut=pd.Timestamp("2025-01-01",tz="UTC")
    train=D[(D.signal_dt<cut)&(D.exit_dt<cut)].copy()
    valid=D[D.signal_dt>=cut].copy()
    return train,valid

def max_streak(vals):
    cur=best=0
    for v in vals:
        if v<0: cur+=1;best=max(best,cur)
        else: cur=0
    return best

def sim(x,base_pct,cap_pct,same_max,symcap_pct,cost):
    x=x.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort")
    eq=1.0;peak=1.0;mdd=0.0;gross=0.0
    heap=[];positions={};sympos={};seq=0
    accepted=skip_same=skip_gross=skip_symcap=0
    closed=[];expo=[];symexpo=[];conc=[];risk=[]
    start=int(x.fill_ts.min()) if len(x) else None; end=start

    def snapshot():
        nonlocal peak,mdd
        q=max(eq,1e-12)
        expo.append(gross/q*100)
        conc.append(len(positions))
        risk.append(sum(p["notional"]*p["stop_pct"]/100 for p in positions.values())/q*100)
        mx=0.0
        for sym,ids in sympos.items():
            se=sum(positions[i]["notional"] for i in ids if i in positions)
            mx=max(mx,se/q*100)
        symexpo.append(mx)
        peak=max(peak,eq)
        if peak>0:mdd=max(mdd,(peak-eq)/peak*100)

    def close_until(ts):
        nonlocal eq,gross,end
        while heap and heap[0][0]<=ts:
            et=heap[0][0]; batch=[]
            while heap and heap[0][0]==et:
                _,pid=heapq.heappop(heap)
                p=positions.get(pid)
                if p is not None: batch.append((pid,p))
            delta=0.0
            for pid,p in batch:
                positions.pop(pid,None); gross-=p["notional"]
                ids=sympos.get(p["symbol"],set()); ids.discard(pid)
                if not ids: sympos.pop(p["symbol"],None)
                netpct=p["pnl_pct"]-cost
                pnl=p["notional"]*netpct/100
                delta+=pnl; closed.append((et,pid,pnl,netpct,p["outcome"]))
            eq+=delta; end=max(end or et,et); snapshot()

    for ts,g in x.groupby("fill_ts",sort=True):
        ts=int(ts); close_until(ts)
        if eq<=0:break
        baseeq=eq; intended=baseeq*base_pct/100
        for r in g.itertuples(index=False):
            sym=str(r.symbol)
            ids=sympos.get(sym,set())
            if len(ids)>=same_max:
                skip_same+=1; continue
            current_sym=sum(positions[i]["notional"] for i in ids if i in positions)
            room_g=max(0.0,baseeq*cap_pct/100-gross)
            room_s=max(0.0,baseeq*symcap_pct/100-current_sym)
            notion=min(intended,room_g,room_s)
            if notion<intended*0.20:
                if room_g<room_s: skip_gross+=1
                else: skip_symcap+=1
                continue
            seq+=1
            p={"exit_ts":int(r.exit_ts),"notional":float(notion),"pnl_pct":float(r.pnl_pct),
               "stop_pct":float(r.stop_pct),"outcome":str(r.outcome),"symbol":sym}
            positions[seq]=p; sympos.setdefault(sym,set()).add(seq); gross+=notion
            heapq.heappush(heap,(p["exit_ts"],seq)); accepted+=1
        snapshot()
    close_until(10**30)
    yrs=((end-start)/1000/86400/365.25) if start and end and end>start else None
    cagr=((eq**(1/yrs)-1)*100) if yrs and eq>0 else None
    net=np.array([z[3] for z in closed],float)
    gp=net[net>0].sum();gl=-net[net<0].sum();pf=float(gp/gl) if gl>0 else None
    rawwin=sum(1 for z in closed if z[4]=="win"); nres=len(closed)
    ordnet=[z[3] for z in sorted(closed,key=lambda q:(q[0],q[1]))]
    q=lambda a,p:float(np.quantile(a,p)) if a else 0.0
    return {"return_pct":(eq-1)*100,"cagr_pct":cagr,"mdd_pct":mdd,"pf_net":pf,
            "win_pct":100*rawwin/nres if nres else None,"expectancy_net_pct":float(net.mean()) if len(net) else None,
            "executed":accepted,"skip_same":skip_same,"skip_gross":skip_gross,"skip_symcap":skip_symcap,
            "max_losing_streak":max_streak(ordnet),"avg_concurrent":float(np.mean(conc)) if conc else 0,
            "max_concurrent":max(conc) if conc else 0,"avg_exposure_pct":float(np.mean(expo)) if expo else 0,
            "p95_exposure_pct":q(expo,.95),"max_exposure_pct":max(expo) if expo else 0,
            "p99_stoprisk_pct":q(risk,.99),"max_stoprisk_pct":max(risk) if risk else 0,
            "p95_max_symbol_exposure_pct":q(symexpo,.95),"max_symbol_exposure_pct":max(symexpo) if symexpo else 0,
            "period_years":yrs,"ruined":eq<=0}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--root",default="ledger");ap.add_argument("--out",default="salvage.json");a=ap.parse_args()
    D,definition=load(a.root); tr,va=split(D)
    variants=sorted(D.variant.unique())
    out={"definition":{"ledger":definition,"train":"signal<2025-01-01 AND exit<2025-01-01","validation":"signal>=2025-01-01",
                       "base_pct":BASE,"gross_caps":CAPS,"same_symbol_max":SAME,"symbol_caps":SYMCAPS,"costs":COSTS,
                       "selection_rule":"train first; validation untouched; require positive train/valid at 20bp, inspect 40bp stress; report Pareto not cosmetic PF"},
         "variants":{}}
    for v in variants:
        tv=tr[tr.variant.eq(v)]; vv=va[va.variant.eq(v)]
        rows=[]
        for sm in SAME:
          for sc in SYMCAPS:
            if sc>300 and sm<999: pass
            for bp in BASE:
              for cap in CAPS:
                if sc>cap and sc<999: continue
                rec={"same_max":sm,"symcap_pct":sc,"base_pct":bp,"cap_pct":cap}
                for cost in COSTS:
                    tag=f"{int(cost*100)}bp"
                    rec["train_"+tag]=sim(tv,bp,cap,sm,sc,cost)
                    rec["valid_"+tag]=sim(vv,bp,cap,sm,sc,cost)
                rows.append(rec)
        good=[r for r in rows if r["train_20bp"]["return_pct"]>0 and r["valid_20bp"]["return_pct"]>0]
        stress=[r for r in good if r["train_40bp"]["return_pct"]>0 and r["valid_40bp"]["return_pct"]>0]
        # score only training; validation never used to rank
        ranked=sorted(good,key=lambda r:(
            r["train_20bp"]["cagr_pct"]/(1+r["train_20bp"]["mdd_pct"]) if r["train_20bp"]["cagr_pct"] is not None else -999,
            r["train_20bp"]["cagr_pct"] or -999),reverse=True)
        money=sorted(good,key=lambda r:(r["train_20bp"]["cagr_pct"] or -999),reverse=True)
        out["variants"][v]={"train_rows":len(tv),"valid_rows":len(vv),"configs":len(rows),
                            "positive20_count":len(good),"positive40_count":len(stress),
                            "top_train_return_risk":ranked[:25],"top_train_cagr":money[:25]}
        print("VAR",v,"train",len(tv),"valid",len(vv),"positive20",len(good),"positive40",len(stress),flush=True)
        for r in ranked[:5]:
            t=r["train_20bp"];z=r["valid_20bp"];s=r["valid_40bp"]
            print("TOP",v,"same",r["same_max"],"symcap",r["symcap_pct"],"base",r["base_pct"],"cap",r["cap_pct"],
                  "trainCAGR",round(t["cagr_pct"],2),"MDD",round(t["mdd_pct"],2),"ret",round(t["return_pct"],1),
                  "validCAGR",round(z["cagr_pct"],2),"MDD",round(z["mdd_pct"],2),"ret",round(z["return_pct"],1),
                  "valid40",round(s["return_pct"],1),"PF",None if z["pf_net"] is None else round(z["pf_net"],3),
                  "N",z["executed"],"avgExp",round(z["avg_exposure_pct"],1),"p99Risk",round(z["p99_stoprisk_pct"],1),flush=True)
    json.dump(out,open(a.out,"w"),indent=2)
    print("STACK_SALVAGE_PASS",len(D),flush=True)
if __name__=="__main__": main()
