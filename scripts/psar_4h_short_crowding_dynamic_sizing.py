import argparse,glob,json,math
import numpy as np,pandas as pd

VARIANTS=("P4T26_DD8","P9T27_DD9")
MAPS={
 "STATIC":(1.0,1.0,1.0,1.0),
 "CROWD_A":(0.25,0.50,1.00,0.75),
 "CROWD_B":(0.00,0.50,1.00,0.75),
 "CROWD_C":(0.00,0.00,1.00,0.75),
 "SWEET_ONLY":(0.00,0.00,1.00,0.00),
}
BASE_PCTS=(0.5,1.0,2.0,3.0,4.0,5.0)
EXPOSURE_CAPS=(100.0,150.0,200.0)

def bucket_weight(n,w):
    if n<=10:return w[0]
    if n<=20:return w[1]
    if n<=40:return w[2]
    return w[3]

def prepare(root):
    sfs=glob.glob(root+"/**/setups_*.csv.gz",recursive=True)
    efs=glob.glob(root+"/**/events_*.csv.gz",recursive=True)
    assert len(sfs)==8 and len(efs)==8,(len(sfs),len(efs))
    S=pd.concat([pd.read_csv(x) for x in sfs],ignore_index=True)
    E=pd.concat([pd.read_csv(x) for x in efs],ignore_index=True)
    assert "BTCUSDT" not in set(E.symbol)
    S=S[S.variant.isin(VARIANTS)]
    E=E[E.variant.isin(VARIANTS) & E.outcome.isin(["win","loss","unresolved_eod"]) & E.exit_ts.notna()].copy()
    C=S.groupby(["variant","signal_ts"],as_index=False).agg(setups=("setup","sum"))
    E=E.merge(C,on=["variant","signal_ts"],how="left",validate="many_to_one")
    E["year"]=pd.to_datetime(E.signal_ts,unit="ms",utc=True).dt.year.astype(int)
    E["fill_ts"]=E.fill_ts.astype(np.int64);E["exit_ts"]=E.exit_ts.astype(np.int64)
    return E

def simulate(x,weights,base_pct,expo_cap_pct,cost_bp):
    x=x.sort_values(["fill_ts","signal_ts","symbol"],kind="mergesort")
    equity=1.0;peak=1.0;mdd=0.0
    open_pos={}
    accepted=skip_symbol=skip_exposure=0
    n_hist=[];expo_hist=[];stoprisk_hist=[]
    start=None;end=None;wins=losses=0

    def snap():
        nonlocal peak,mdd
        notion=sum(p["notional"] for p in open_pos.values())
        sr=sum(p["notional"]*p["stop_pct"]/100.0 for p in open_pos.values())
        eq=max(equity,1e-12)
        n_hist.append(len(open_pos));expo_hist.append(notion/eq*100.0);stoprisk_hist.append(sr/eq*100.0)
        peak=max(peak,equity)
        if peak>0:mdd=max(mdd,(peak-equity)/peak*100.0)

    def close_through(ts):
        nonlocal equity,end,wins,losses
        due={}
        for s,p in list(open_pos.items()):
            if p["exit_ts"]<=ts:due.setdefault(p["exit_ts"],[]).append((s,p))
        for et in sorted(due):
            delta=0.0
            for s,p in due[et]:
                open_pos.pop(s,None)
                rp=p["pnl_pct"]
                if rp is None or not np.isfinite(rp):net=0.0
                else:
                    net=(float(rp)-cost_bp/100.0)/100.0
                    if rp>0:wins+=1
                    elif rp<0:losses+=1
                delta+=p["notional"]*net
            equity+=delta;end=max(end or int(et),int(et));snap()

    for ts,g in x.groupby("fill_ts",sort=True):
        ts=int(ts);start=ts if start is None else start
        close_through(ts)
        for r in g.itertuples(index=False):
            sym=str(r.symbol)
            if sym in open_pos:
                skip_symbol+=1;continue
            wt=bucket_weight(int(r.setups),weights)
            if wt<=0:continue
            intended=equity*(base_pct/100.0)*wt
            current=sum(p["notional"] for p in open_pos.values())
            max_notional=equity*(expo_cap_pct/100.0)
            room=max_notional-current
            if room<=1e-12 or intended<=1e-12:
                skip_exposure+=1;continue
            notional=min(intended,room)
            # ignore microscopic partial fills at cap boundary
            if notional < intended*0.20:
                skip_exposure+=1;continue
            rp=None if pd.isna(r.pnl_pct) else float(r.pnl_pct)
            open_pos[sym]={"exit_ts":int(r.exit_ts),"notional":float(notional),
                           "pnl_pct":rp,"stop_pct":float(r.stop_pct)}
            accepted+=1
        snap()
    close_through(10**19)
    if open_pos:open_pos.clear();snap()
    yrs=((end-start)/1000/86400/365.25) if start and end and end>start else None
    cagr=((equity**(1.0/yrs)-1.0)*100.0) if equity>0 and yrs else None
    def q(a,p):
        return float(np.quantile(a,p)) if a else 0.0
    return {
      "accepted":accepted,"skip_same_symbol":skip_symbol,"skip_exposure":skip_exposure,
      "equity_multiple":float(equity),"return_pct":float((equity-1)*100),
      "cagr_pct":None if cagr is None else float(cagr),"mdd_pct":float(mdd),
      "open_mean":float(np.mean(n_hist)) if n_hist else 0.0,"open_p95":q(n_hist,.95),"open_p99":q(n_hist,.99),"open_max":max(n_hist) if n_hist else 0,
      "exposure_p95":q(expo_hist,.95),"exposure_p99":q(expo_hist,.99),"exposure_max":max(expo_hist) if expo_hist else 0,
      "stoprisk_p99":q(stoprisk_hist,.99),"wins":wins,"losses":losses,"period_years":yrs
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--source",default="source");ap.add_argument("--out",default="crowd_sizing.json");a=ap.parse_args()
    D=prepare(a.source)
    out={"definition":{
      "source_sizing_run":"36437560302","btc_excluded":True,"stop_policy":"FIXED",
      "train":"2021-2024","validation":"2025-2026",
      "maps":MAPS,"base_position_pct":BASE_PCTS,"gross_exposure_caps_pct":EXPOSURE_CAPS,
      "primary_cost_bp":20,"stress_cost_bp":40,
      "sizing_rule":"per-fill notional = current realized equity * base_pct * crowding weight; filled gross exposure capped globally",
      "crowding":"setup count at 4H signal OPEN; known before maker orders are sent",
      "same_symbol":"one open position per symbol",
      "partial_cap_rule":"allow cap-boundary partial size only if >=20% of intended order",
      "mdd":"realized-equity MDD; no intra-trade mark-to-market"
    },"variants":{}}
    for v in VARIANTS:
        x=D[D.variant==v].copy();tr=x[x.year<=2024].copy();va=x[x.year>=2025].copy()
        rows=[]
        for mapn,w in MAPS.items():
          for bpct in BASE_PCTS:
            for cap in EXPOSURE_CAPS:
              t20=simulate(tr,w,bpct,cap,20);v20=simulate(va,w,bpct,cap,20)
              t40=simulate(tr,w,bpct,cap,40);v40=simulate(va,w,bpct,cap,40)
              # Training selection favors log growth but penalizes realized drawdown and 40bp fragility.
              score=(math.log(max(t20["equity_multiple"],1e-12))
                     -0.012*t20["mdd_pct"]
                     +0.35*math.log(max(t40["equity_multiple"],1e-12)))
              rows.append({"map":mapn,"weights":w,"base_pct":bpct,"exposure_cap_pct":cap,
                           "train20":t20,"valid20":v20,"train40":t40,"valid40":v40,
                           "train_score":score})
        rows.sort(key=lambda r:r["train_score"],reverse=True)
        out["variants"][v]={"train_selected":rows[0],"train_top15":rows[:15],"all_results":rows}
        print("VAR",v,"SELECT",rows[0]["map"],rows[0]["base_pct"],rows[0]["exposure_cap_pct"],flush=True)
        for r in rows[:12]:
            print("TOP",r["map"],"base",r["base_pct"],"cap",r["exposure_cap_pct"],
                  "tr20",round(r["train20"]["equity_multiple"],4),"trMDD",round(r["train20"]["mdd_pct"],2),
                  "va20",round(r["valid20"]["equity_multiple"],4),"vaMDD",round(r["valid20"]["mdd_pct"],2),
                  "va40",round(r["valid40"]["equity_multiple"],4),
                  "n",r["valid20"]["accepted"],"p99exp",round(r["valid20"]["exposure_p99"],1),
                  flush=True)
    json.dump(out,open(a.out,"w"),indent=2)
    print("DYNAMIC_SIZING_PASS",len(D),flush=True)
if __name__=="__main__":main()
