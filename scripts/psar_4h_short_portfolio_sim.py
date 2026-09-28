import argparse,glob,json
import numpy as np,pandas as pd

VARIANTS=("P4T26_DD8","P9T27_DD9")
CAPS=(4,6,8,10)
TOTAL_EXPOSURES=(1.0,1.5,2.0)
COST_BPS=(0,20,40)
RANKINGS=("DD72_HIGH","LOW_STOP_RISK","SYMBOL")

def qstats(x):
    x=np.asarray(x,float)
    if len(x)==0:return {"mean":0.0,"p50":0.0,"p90":0.0,"p95":0.0,"p99":0.0,"max":0.0}
    return {
      "mean":float(np.mean(x)),"p50":float(np.quantile(x,.5)),
      "p90":float(np.quantile(x,.9)),"p95":float(np.quantile(x,.95)),
      "p99":float(np.quantile(x,.99)),"max":float(np.max(x))
    }

def sort_group(g,ranking):
    if ranking=="DD72_HIGH":
        return g.sort_values(["dd72_pct","stop_pct","symbol"],ascending=[False,True,True],kind="mergesort")
    if ranking=="LOW_STOP_RISK":
        return g.sort_values(["stop_pct","dd72_pct","symbol"],ascending=[True,False,True],kind="mergesort")
    return g.sort_values(["symbol"],kind="mergesort")

def select_all_caps(x,ranking):
    # Acceptance depends only on fill/exit chronology, symbol occupancy, ranking and cap.
    # It does NOT depend on position size or trading costs, so compute it once for all 9 size/cost replays.
    states={cap:{"open":{},"accepted":[],"skip_symbol":0,"skip_cap":0} for cap in CAPS}
    for ts,g in x.groupby("fill_ts",sort=True):
        ts=int(ts); sg=sort_group(g,ranking)
        rows=list(sg[["symbol","exit_ts"]].itertuples())
        idxs=list(sg.index)
        for cap,st in states.items():
            op=st["open"]
            for sym,et in list(op.items()):
                if et<=ts: del op[sym]
            for idx,r in zip(idxs,rows):
                sym=str(r.symbol); et=int(r.exit_ts)
                if sym in op:
                    st["skip_symbol"]+=1;continue
                if len(op)>=cap:
                    st["skip_cap"]+=1;continue
                st["accepted"].append(idx);op[sym]=et
    return states

def replay(acc,cap,total_exposure,cost_bp):
    frac=total_exposure/cap
    acc=acc.sort_values(["fill_ts","symbol"],kind="mergesort")
    equity=1.0;peak=1.0;max_dd=0.0
    open_pos={}
    open_counts=[];exposure_samples=[];stoprisk_samples=[]
    realized_wins=0;realized_losses=0
    start_ts=None;end_ts=None;unresolved=0

    def snap():
        nonlocal peak,max_dd
        n=len(open_pos)
        notion=sum(p["notional"] for p in open_pos.values())
        sr=sum(p["notional"]*p["stop_pct"]/100.0 for p in open_pos.values())
        denom=max(equity,1e-12)
        open_counts.append(n);exposure_samples.append(notion/denom*100.0);stoprisk_samples.append(sr/denom*100.0)
        peak=max(peak,equity)
        if peak>0:max_dd=max(max_dd,(peak-equity)/peak*100.0)

    def close_through(ts):
        nonlocal equity,end_ts,realized_wins,realized_losses
        due={}
        for k,p in list(open_pos.items()):
            if p["exit_ts"]<=ts:
                due.setdefault(p["exit_ts"],[]).append((k,p))
        for et in sorted(due):
            delta=0.0
            for k,p in due[et]:
                open_pos.pop(k,None)
                rp=p["pnl_pct"]
                if rp is None or not np.isfinite(rp):
                    net=0.0
                else:
                    net=(float(rp)-cost_bp/100.0)/100.0
                    if rp>0:realized_wins+=1
                    elif rp<0:realized_losses+=1
                delta += p["notional"]*net
            equity += delta
            end_ts=max(end_ts or int(et),int(et));snap()

    for ts,g in acc.groupby("fill_ts",sort=True):
        ts=int(ts)
        if start_ts is None:start_ts=ts
        close_through(ts)
        base_equity=equity
        for r in g.itertuples(index=False):
            notional=base_equity*frac
            rp=None if pd.isna(r.pnl_pct) else float(r.pnl_pct)
            if rp is None:unresolved+=1
            open_pos[str(r.symbol)]={
              "exit_ts":int(r.exit_ts),"notional":float(notional),
              "pnl_pct":rp,"stop_pct":float(r.stop_pct)
            }
        snap()
    close_through(10**19)
    if open_pos:
        open_pos.clear();snap()
    years=((end_ts-start_ts)/1000/86400/365.25) if start_ts and end_ts and end_ts>start_ts else None
    cagr=((equity**(1.0/years)-1.0)*100.0) if equity>0 and years and years>0 else None
    return {
      "cap":cap,"total_exposure_pct":total_exposure*100.0,
      "per_position_pct":frac*100.0,"cost_bp":cost_bp,
      "accepted":int(len(acc)),"unresolved_flat_at_eod":int(unresolved),
      "final_equity_multiple":float(equity),"total_return_pct":float((equity-1.0)*100.0),
      "cagr_pct":None if cagr is None else float(cagr),
      "realized_equity_mdd_pct":float(max_dd),
      "open_positions_event_sample":qstats(open_counts),
      "gross_exposure_pct_event_sample":qstats(exposure_samples),
      "all_stops_risk_pct_event_sample":qstats(stoprisk_samples),
      "realized_win_count":int(realized_wins),"realized_loss_count":int(realized_losses),
      "period_years":years
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",default="source")
    ap.add_argument("--out",default="psar_4h_short_portfolio_analysis.json")
    a=ap.parse_args()
    efs=glob.glob(a.source+"/**/events_*.csv.gz",recursive=True)
    mfs=glob.glob(a.source+"/**/meta_*.json",recursive=True)
    assert len(efs)==8,(len(efs),efs)
    metas=[]
    for p in mfs:
        try: metas.append(json.load(open(p)))
        except: pass
    good=[m for m in metas if m.get("definition",{}).get("source_fixed_engine_blob")=="834d5e34ea4253951d42d5a083ad494ce2d498e4"]
    assert len(good)>=8,len(good)
    d=pd.concat([pd.read_csv(x) for x in efs],ignore_index=True)
    assert "BTCUSDT" not in set(d.symbol)
    d=d[d.variant.isin(VARIANTS) & d.outcome.isin(["win","loss","unresolved_eod"]) & d.exit_ts.notna()].copy()
    d["fill_ts"]=d.fill_ts.astype(np.int64);d["exit_ts"]=d.exit_ts.astype(np.int64)

    out={"definition":{
      "source_sizing_run":"36437560302",
      "source_fixed_engine_blob":"834d5e34ea4253951d42d5a083ad494ce2d498e4",
      "stop_policy":"FIXED entry-time PSAR stop; no ratchet","btc_excluded":True,
      "variants":VARIANTS,
      "capacity_rule":"one open position per symbol; exits at/before fill timestamp free capacity first",
      "position_size":"entry notional = realized equity at timestamp * (total exposure / position cap)",
      "rankings":{
        "DD72_HIGH":"less-oversold (higher dd72) first",
        "LOW_STOP_RISK":"smaller initial stop distance first",
        "SYMBOL":"deterministic lexical baseline"
      },
      "optimization":"acceptance state computed once per variant/ranking for all caps in one pass; exposure/cost replay uses accepted ledger only",
      "mdd":"realized-equity MDD only; no intra-trade mark-to-market drawdown",
      "unresolved":"occupy capacity through EOD and close flat for PnL",
      "cost_model":"flat roundtrip bps deducted at exit"
    },"source_rows":int(len(d)),"results":[]}

    for v in VARIANTS:
        x=d[d.variant==v].copy().sort_values(["fill_ts","symbol"],kind="mergesort")
        print("VARIANT",v,"rows",len(x),flush=True)
        for ranking in RANKINGS:
            states=select_all_caps(x,ranking)
            print("SELECT",v,ranking,{c:len(states[c]["accepted"]) for c in CAPS},flush=True)
            for cap in CAPS:
                st=states[cap]
                acc=x.loc[st["accepted"]].copy()
                for expo in TOTAL_EXPOSURES:
                    for bp in COST_BPS:
                        z=replay(acc,cap,expo,bp)
                        z.update({
                          "variant":v,"ranking":ranking,
                          "skipped_same_symbol":int(st["skip_symbol"]),
                          "skipped_cap":int(st["skip_cap"])
                        })
                        out["results"].append(z)

    r=pd.DataFrame(out["results"])
    r20=r[r.cost_bp==20].copy()
    out["leaders_20bp"]={}
    for v in VARIANTS:
        z=r20[r20.variant==v].sort_values(["final_equity_multiple","realized_equity_mdd_pct"],ascending=[False,True])
        out["leaders_20bp"][v]=z.head(20).to_dict("records")
    json.dump(out,open(a.out,"w"),indent=2)
    print("PORTFOLIO_FAST_PASS",len(d),len(out["results"]),flush=True)
    for v in VARIANTS:
        z=r20[(r20.variant==v)&(r20.ranking=="DD72_HIGH")].sort_values("final_equity_multiple",ascending=False)
        for rr in z.head(12).itertuples():
            print("TOP20BP",v,"cap",rr.cap,"expo",rr.total_exposure_pct,"per",round(rr.per_position_pct,3),
                  "mult",round(rr.final_equity_multiple,4),"MDD",round(rr.realized_equity_mdd_pct,3),
                  "CAGR",None if pd.isna(rr.cagr_pct) else round(rr.cagr_pct,3),
                  "taken",rr.accepted,"skipcap",rr.skipped_cap,
                  "stopRiskP99",round(rr.all_stops_risk_pct_event_sample["p99"],3),flush=True)
if __name__=="__main__":main()
