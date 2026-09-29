import argparse,glob,json,heapq,math
import numpy as np,pandas as pd

BASE=(0.5,1.0,2.0,3.0,4.0,5.0,7.5,10.0)
CAPS=(50.0,100.0,150.0,200.0,300.0)
COSTS=(0.20,0.40,0.60)

def load(root):
    fs=glob.glob(root+"/**/events_*.csv.gz",recursive=True)
    ms=glob.glob(root+"/**/meta_*.json",recursive=True)
    assert len(fs)==8,(len(fs),fs);assert len(ms)==8,(len(ms),ms)
    metas=[json.load(open(x)) for x in ms]
    base=metas[0]["definition"]
    assert all(m["definition"]==base for m in metas),"meta definition mismatch"
    assert base["tf"]=="4h" and base["side"]=="SHORT"
    assert base["tp"]=="actual_fill - R*abs(actual_fill-SL)"
    D=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
    D["year"]=pd.to_datetime(D.signal_ts,unit="ms",utc=True).dt.year.astype(int)
    return D,base,metas

def max_streak(vals):
    cur=best=0
    for v in vals:
        if v<0:cur+=1;best=max(best,cur)
        else:cur=0
    return int(best)

def sim(x,base_pct,cap_pct,cost):
    x=x.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort")
    groups=[(int(ts),g.copy()) for ts,g in x.groupby("fill_ts",sort=True)]
    eq=1.0;peak=1.0;mdd=0.0;gross=0.0;openp={};heap=[];seq=0
    start=groups[0][0] if groups else None;end=start
    accepted=skip_sym=0
    expo_hist=[];conc_hist=[];risk_hist=[];closed=[]
    def snap():
        nonlocal peak,mdd
        q=max(eq,1e-12)
        expo_hist.append(gross/q*100.0);conc_hist.append(len(openp))
        risk_hist.append(sum(p["notional"]*p["stop_pct"]/100.0 for p in openp.values())/q*100.0)
        peak=max(peak,eq)
        if peak>0:mdd=max(mdd,(peak-eq)/peak*100.0)
    def close_until(ts):
        nonlocal eq,gross,end
        while heap and heap[0][0]<=ts:
            et=heap[0][0];batch=[]
            while heap and heap[0][0]==et:
                _,_,sym=heapq.heappop(heap)
                p=openp.get(sym)
                if p is not None and p["exit_ts"]==et:batch.append((sym,p))
            delta=0.0
            for sym,p in sorted(batch,key=lambda z:z[0]):
                openp.pop(sym,None);gross-=p["notional"]
                if p["pnl_pct"] is None or not np.isfinite(p["pnl_pct"]):
                    netpct=0.0
                else:netpct=float(p["pnl_pct"])-cost
                pnl=p["notional"]*netpct/100.0
                delta+=pnl
                closed.append((et,sym,pnl,netpct,p["outcome"]))
            eq+=delta;end=max(end or et,et);snap()
    for ts,g in groups:
        close_until(ts)
        if eq<=0:break
        elig=[]
        for r in g.itertuples(index=False):
            sym=str(r.symbol)
            if sym in openp:
                skip_sym+=1;continue
            elig.append(r)
        if not elig:
            snap();continue
        baseeq=eq; intended=baseeq*base_pct/100.0
        room=max(0.0,baseeq*cap_pct/100.0-gross)
        scale=min(1.0,room/(intended*len(elig))) if intended>0 and len(elig)>0 else 0.0
        if scale<=0:
            snap();continue
        notion=intended*scale
        for r in elig:
            sym=str(r.symbol);seq+=1
            p={"exit_ts":int(r.exit_ts),"notional":float(notion),
               "pnl_pct":None if pd.isna(r.pnl_pct) else float(r.pnl_pct),
               "stop_pct":float(r.stop_pct),"outcome":str(r.outcome)}
            openp[sym]=p;gross+=notion;heapq.heappush(heap,(p["exit_ts"],seq,sym));accepted+=1
        snap()
    close_until(10**30)
    years=((end-start)/1000/86400/365.25) if start and end and end>start else None
    cagr=((eq**(1/years)-1)*100) if years and eq>0 else None
    net=np.array([z[3] for z in closed if np.isfinite(z[3])],float)
    gp=net[net>0].sum();gl=-net[net<0].sum()
    pf=float(gp/gl) if gl>0 else None
    rawwin=sum(1 for z in closed if z[4]=="win"); resolved=sum(1 for z in closed if z[4] in ("win","loss"))
    ordered=[z[3] for z in sorted(closed,key=lambda q:(q[0],q[1]))]
    loss_clusters={}
    for et,_,_,netpct,_ in closed:
        if netpct<0:loss_clusters[et]=loss_clusters.get(et,0)+1
    q=lambda a,p:float(np.quantile(a,p)) if a else 0.0
    return {
      "equity_multiple":float(eq),"return_pct":float((eq-1)*100),
      "cagr_pct":None if cagr is None else float(cagr),"mdd_pct":float(mdd),
      "pf_net_pct":pf,"win_pct_raw":100*rawwin/resolved if resolved else None,
      "net_positive_trade_pct":100*float((net>0).mean()) if len(net) else None,
      "expectancy_net_pct":float(net.mean()) if len(net) else None,
      "executed":int(accepted),"closed_resolved":int(len(net)),"skip_same_symbol":int(skip_sym),
      "max_losing_streak":max_streak(ordered),"max_same_exit_loss_cluster":max(loss_clusters.values()) if loss_clusters else 0,
      "avg_concurrent":float(np.mean(conc_hist)) if conc_hist else 0.0,"max_concurrent":int(max(conc_hist) if conc_hist else 0),
      "avg_exposure_pct":float(np.mean(expo_hist)) if expo_hist else 0.0,
      "p95_exposure_pct":q(expo_hist,.95),"max_exposure_pct":float(max(expo_hist) if expo_hist else 0.0),
      "avg_cap_utilization_pct":float(np.mean(expo_hist)/cap_pct*100) if expo_hist and cap_pct else 0.0,
      "p99_stoprisk_pct":q(risk_hist,.99),"max_stoprisk_pct":float(max(risk_hist) if risk_hist else 0.0),
      "period_years":years,"ruined":bool(eq<=0)
    }

def pareto(rows):
    out=[]
    for r in rows:
        a=r["train20"]; dominated=False
        for s in rows:
            b=s["train20"]
            if (b["cagr_pct"] is not None and a["cagr_pct"] is not None and
                b["cagr_pct"]>=a["cagr_pct"] and b["mdd_pct"]<=a["mdd_pct"] and
                (b["cagr_pct"]>a["cagr_pct"] or b["mdd_pct"]<a["mdd_pct"])):
                dominated=True;break
        if not dominated:out.append(r)
    return sorted(out,key=lambda r:r["train20"]["mdd_pct"])

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--root",default="ledger");ap.add_argument("--out",default="portfolio.json");a=ap.parse_args()
    D,definition,metas=load(a.root)
    variants=sorted(D.variant.unique())
    out={"definition":{
      "ledger_definition":definition,"base_pct":BASE,"gross_caps_pct":CAPS,"costs_pct":COSTS,
      "same_symbol":"one open position per symbol",
      "max_concurrent":"unlimited and identical across candidates; gross exposure cap is binding risk budget",
      "cap_handling":"all eligible fills sharing a fill timestamp are proportionally scaled to available gross-cap room; no arbitrary symbol ranking",
      "mdd":"realized-equity MDD; p99/max aggregate initial-stop risk reported separately because intra-trade MTM path is not reconstructed",
      "selection":"compare train 2021-2024, untouched validation 2025-2026, and full period; use Pareto return/MDD frontier rather than a single cosmetic metric"
    },"variants":{}}
    for v in variants:
        x=D[D.variant==v].copy()
        scopes={"train":x[x.year<=2024],"valid":x[x.year>=2025],"full":x}
        rows=[]
        for bp in BASE:
          for cap in CAPS:
            rec={"base_pct":bp,"cap_pct":cap}
            for cost in COSTS:
              tag=f"{int(cost*100)}bp"
              for sn,z in scopes.items():
                rec[f"{sn}{tag}"]=sim(z,bp,cap,cost)
            rows.append(rec)
        robust=[r for r in rows if
                r["train20bp"]["equity_multiple"]>1 and r["valid20bp"]["equity_multiple"]>1 and
                r["train40bp"]["equity_multiple"]>1 and r["valid40bp"]["equity_multiple"]>1]
        pf=pareto([{"base_pct":r["base_pct"],"cap_pct":r["cap_pct"],"train20":r["train20bp"],
                    "valid20":r["valid20bp"],"full20":r["full20bp"],
                    "train40":r["train40bp"],"valid40":r["valid40bp"],"full40":r["full40bp"],
                    "full60":r["full60bp"]} for r in robust])
        out["variants"][v]={"signal_rows":int(len(x)),"all_results":rows,"robust_pareto":pf}
        top=sorted(robust,key=lambda r:(r["full20bp"]["cagr_pct"] or -1),reverse=True)[:8]
        print("VAR",v,"rows",len(x),"ROBUST",len(robust),flush=True)
        for r in top:
            a=r["full20bp"];b=r["valid20bp"];s=r["full40bp"]
            print("TOP",v,"bp",r["base_pct"],"cap",r["cap_pct"],
                  "fullCAGR",round(a["cagr_pct"],2),"fullRet",round(a["return_pct"],1),"MDD",round(a["mdd_pct"],1),
                  "validRet",round(b["return_pct"],1),"40CAGR",round(s["cagr_pct"],2),
                  "PF",None if a["pf_net_pct"] is None else round(a["pf_net_pct"],3),
                  "WR",None if a["win_pct_raw"] is None else round(a["win_pct_raw"],2),
                  "N",a["executed"],"avgExp",round(a["avg_exposure_pct"],1),"p99Risk",round(a["p99_stoprisk_pct"],1),flush=True)
    # cross-variant robust Pareto on full candidate/config rows
    cross=[]
    for v,vv in out["variants"].items():
        for r in vv["all_results"]:
            if (r["train20bp"]["equity_multiple"]>1 and r["valid20bp"]["equity_multiple"]>1 and
                r["train40bp"]["equity_multiple"]>1 and r["valid40bp"]["equity_multiple"]>1):
                cross.append({"variant":v,"base_pct":r["base_pct"],"cap_pct":r["cap_pct"],
                              "train20":r["train20bp"],"valid20":r["valid20bp"],"full20":r["full20bp"],
                              "full40":r["full40bp"],"full60":r["full60bp"]})
    out["cross_variant_robust_pareto"]=pareto(cross)
    json.dump(out,open(a.out,"w"),indent=2)
    print("PORTFOLIO_OPT_PASS",len(D),"variants",len(variants),"cross_robust",len(cross),flush=True)
if __name__=="__main__":main()
