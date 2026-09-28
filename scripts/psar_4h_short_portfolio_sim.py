import argparse,glob,json,math,os
import numpy as np,pandas as pd

VARIANTS=("P4T26_DD8","P9T27_DD9")
CAPS=(4,6,8,10)
TOTAL_EXPOSURES=(1.0,1.5,2.0)
COST_BPS=(0,20,40)
RANKINGS=("DD72_HIGH","LOW_STOP_RISK","SYMBOL")

def qstats(x):
    x=np.asarray(x,float)
    if len(x)==0:return {"mean":0.0,"p50":0.0,"p90":0.0,"p95":0.0,"p99":0.0,"max":0.0}
    return {k:float(v) for k,v in {
      "mean":np.mean(x),"p50":np.quantile(x,.5),"p90":np.quantile(x,.9),
      "p95":np.quantile(x,.95),"p99":np.quantile(x,.99),"max":np.max(x)}.items()}

def sort_group(g,ranking):
    if ranking=="DD72_HIGH":
        return g.sort_values(["dd72_pct","stop_pct","symbol"],ascending=[False,True,True])
    if ranking=="LOW_STOP_RISK":
        return g.sort_values(["stop_pct","dd72_pct","symbol"],ascending=[True,False,True])
    return g.sort_values(["symbol"])

def simulate(d,cap,total_exposure,cost_bp,ranking):
    frac=total_exposure/cap
    d=d.copy()
    d=d[d.outcome.isin(["win","loss","unresolved_eod"]) & d.exit_ts.notna()].copy()
    d["exit_ts"]=d.exit_ts.astype(np.int64)
    d["fill_ts"]=d.fill_ts.astype(np.int64)
    d=d.sort_values(["fill_ts","symbol"]).reset_index(drop=True)

    equity=1.0
    peak=1.0
    max_dd=0.0
    open_pos={}
    exit_heap=[]  # list of (exit_ts, unique_id)
    uid=0
    realized_points=[]
    open_counts=[]
    exposure_samples=[]
    stoprisk_samples=[]
    accepted=0;skip_symbol=0;skip_cap=0;unresolved=0
    gross_wins=0;gross_losses=0
    start_ts=None;end_ts=None

    def snapshot(ts):
        nonlocal max_dd,peak
        n=len(open_pos)
        notion=sum(p["notional"] for p in open_pos.values())
        sr=sum(p["notional"]*p["stop_pct"]/100.0 for p in open_pos.values())
        denom=max(equity,1e-12)
        open_counts.append(n)
        exposure_samples.append(notion/denom*100.0)
        stoprisk_samples.append(sr/denom*100.0)
        peak=max(peak,equity)
        if peak>0:max_dd=max(max_dd,(peak-equity)/peak*100.0)
        realized_points.append((int(ts),float(equity),n))

    def close_through(ts):
        nonlocal equity,peak,max_dd,end_ts,gross_wins,gross_losses
        due=sorted([(p["exit_ts"],k) for k,p in open_pos.items() if p["exit_ts"]<=ts])
        for et,k in due:
            p=open_pos.pop(k,None)
            if p is None:continue
            rp=p["pnl_pct"]
            if rp is None or not np.isfinite(rp):
                net=0.0
            else:
                net=(float(rp)-cost_bp/100.0)/100.0
                if rp>0:gross_wins+=1
                elif rp<0:gross_losses+=1
            equity += p["notional"]*net
            end_ts=max(end_ts or et,et)
            snapshot(et)

    for ts,g in d.groupby("fill_ts",sort=True):
        ts=int(ts)
        if start_ts is None:start_ts=ts
        close_through(ts)
        g=sort_group(g,ranking)
        for r in g.itertuples(index=False):
            if r.symbol in open_pos:
                skip_symbol+=1;continue
            if len(open_pos)>=cap:
                skip_cap+=1;continue
            notional=equity*frac
            p={
              "exit_ts":int(r.exit_ts),"notional":float(notional),
              "pnl_pct":None if pd.isna(r.pnl_pct) else float(r.pnl_pct),
              "stop_pct":float(r.stop_pct),"symbol":r.symbol
            }
            open_pos[r.symbol]=p
            accepted+=1
            if pd.isna(r.pnl_pct):unresolved+=1
        snapshot(ts)

    close_through(10**19)
    # any remaining pathological positions close flat
    for k,p in list(open_pos.items()):
        open_pos.pop(k)
        snapshot(p["exit_ts"])

    dur_years=((end_ts-start_ts)/1000/86400/365.25) if (start_ts and end_ts and end_ts>start_ts) else None
    if equity>0 and dur_years and dur_years>0:
        cagr=(equity**(1.0/dur_years)-1.0)*100.0
    else:cagr=None
    return {
      "cap":cap,"total_exposure_pct":total_exposure*100.0,
      "per_position_pct":frac*100.0,"cost_bp":cost_bp,"ranking":ranking,
      "accepted":accepted,"skipped_same_symbol":skip_symbol,"skipped_cap":skip_cap,
      "unresolved_flat_at_eod":unresolved,
      "final_equity_multiple":float(equity),
      "total_return_pct":float((equity-1.0)*100.0),
      "cagr_pct":None if cagr is None else float(cagr),
      "realized_equity_mdd_pct":float(max_dd),
      "open_positions_event_sample":qstats(open_counts),
      "gross_exposure_pct_event_sample":qstats(exposure_samples),
      "all_stops_risk_pct_event_sample":qstats(stoprisk_samples),
      "realized_win_count":gross_wins,"realized_loss_count":gross_losses,
      "period_years":dur_years,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",default="source")
    ap.add_argument("--out",default="psar_4h_short_portfolio_analysis.json")
    a=ap.parse_args()
    efs=glob.glob(a.source+"/**/events_*.csv.gz",recursive=True)
    mfs=glob.glob(a.source+"/**/meta_*.json",recursive=True)
    assert len(efs)==8,(len(efs),efs)
    assert len(mfs)>=8,len(mfs)
    metas=[json.load(open(x)) for x in mfs if "sizing-analysis" not in x]
    good=[m for m in metas if m.get("definition",{}).get("source_fixed_engine_blob")=="834d5e34ea4253951d42d5a083ad494ce2d498e4"]
    assert len(good)>=8,len(good)
    d=pd.concat([pd.read_csv(x) for x in efs],ignore_index=True)
    assert "BTCUSDT" not in set(d.symbol)
    out={"definition":{
      "source_sizing_run":"36437560302",
      "source_fixed_engine_blob":"834d5e34ea4253951d42d5a083ad494ce2d498e4",
      "stop_policy":"FIXED entry-time PSAR stop; no ratchet",
      "btc_excluded":True,
      "variants":VARIANTS,
      "capacity_rule":"one open position per symbol; close positions with exit_ts <= new fill_ts before admitting new fills",
      "position_size":"entry notional = current realized equity * (total exposure / position cap)",
      "rankings":{
        "DD72_HIGH":"for fills at same timestamp, less-oversold (higher dd72) first; validated pre-entry feature",
        "LOW_STOP_RISK":"for fills at same timestamp, smaller initial stop distance first",
        "SYMBOL":"neutral deterministic lexical tie-break baseline"
      },
      "mdd":"realized-equity MDD only; does not include intra-trade mark-to-market drawdown",
      "unresolved":"positions unresolved at dataset end occupy capacity through EOD and are closed flat for account PnL",
      "cost_model":"flat roundtrip bps deducted at exit"
    },"source_rows":int(len(d)),"results":[]}
    for v in VARIANTS:
        x=d[d.variant==v].copy()
        assert len(x)>0,v
        for ranking in RANKINGS:
          for cap in CAPS:
            for expo in TOTAL_EXPOSURES:
              for bp in COST_BPS:
                out["results"].append({"variant":v,**simulate(x,cap,expo,bp,ranking)})
    # compact leaderboards under 20bp, plus risk-constrained views
    r=pd.DataFrame(out["results"])
    r20=r[r.cost_bp==20].copy()
    leaders={}
    for v in VARIANTS:
      z=r20[r20.variant==v].copy()
      z=z.sort_values(["final_equity_multiple","realized_equity_mdd_pct"],ascending=[False,True])
      leaders[v]=z.head(20).to_dict("records")
    out["leaders_20bp"]=leaders
    json.dump(out,open(a.out,"w"),indent=2)
    print("PORTFOLIO_PASS",len(d),len(out["results"]))
    for v in VARIANTS:
      z=r20[(r20.variant==v)&(r20.ranking=="DD72_HIGH")].sort_values("final_equity_multiple",ascending=False)
      print("VAR",v)
      for rr in z.head(12).itertuples():
        print("TOP20BP","cap",rr.cap,"expo",rr.total_exposure_pct,"per",round(rr.per_position_pct,3),
              "mult",round(rr.final_equity_multiple,4),"MDD",round(rr.realized_equity_mdd_pct,3),
              "CAGR",None if pd.isna(rr.cagr_pct) else round(rr.cagr_pct,3),
              "taken",rr.accepted,"skipcap",rr.skipped_cap,
              "stopRiskP99",round(rr.all_stops_risk_pct_event_sample["p99"],3))
if __name__=="__main__":main()
