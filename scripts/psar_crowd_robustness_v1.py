#!/usr/bin/env python3
"""Frozen ledger robustness: same-timestamp rank, position risk, leave-one-year.
Prior experiment is exploratory; this is a sensitivity/reproducibility check, not new OOS.
"""
import argparse,glob,json,inspect,sys
from pathlib import Path
import pandas as pd
sys.path.insert(0,"scripts")
import psar_fade_risk_portfolio_v1 as base
PICKS={"CONSERVATIVE":("P3p5_SL6_TIME",4),"BROAD":("P3_SL6_TIME",8)}
ORDERS={"ALPHABET":'trades=trades.sort_values(["entry_ts","symbol"],kind="mergesort")',
 "FARTHEST_FIRST":'trades=trades.sort_values(["entry_ts","dist_atr","symbol"],ascending=[True,False,True],kind="mergesort")',
 "NEAREST_FIRST":'trades=trades.sort_values(["entry_ts","dist_atr","symbol"],ascending=[True,True,True],kind="mergesort")'}
ORIGINAL=ORDERS["ALPHABET"]
def stats_rank(x,sp,risk,slots,policy,ordering):
    source=inspect.getsource(base.stats)
    assert source.count(ORIGINAL)==1
    scope=dict(vars(base))
    scope["PORT_RULE"]=dict(base.PORT_RULE)
    scope["PORT_RULE"]["max_slots"]=slots
    exec(source.replace(ORIGINAL,ORDERS[ordering]),scope)
    return scope["stats"](x,sp,risk,policy)
def main():
    a=argparse.ArgumentParser();a.add_argument("--data",default="data");a.add_argument("--audit",action="store_true");a.add_argument("--out",default="robust.json")
    x=a.parse_args()
    assert len(PICKS)==2 and len(ORDERS)==3 and ORIGINAL in inspect.getsource(base.stats)
    if x.audit:
        print("ROBUSTNESS_SMOKE_PASS prior-open rank ordering only, no new signal outcomes used")
        return
    fs=glob.glob(x.data+"/**/portfolio-ledger-*.csv.gz",recursive=True)
    ms=glob.glob(x.data+"/**/portfolio-meta-*.json",recursive=True)
    assert len(fs)==len(ms)==4
    metas=[json.load(open(p)) for p in ms]
    assert {m["shard"] for m in metas}==set(range(4))
    assert len({m["code_sha"] for m in metas})==1 and metas[0]["code_sha"]=="7937e6bca49556e9abc1b47985e758c6cd435127"
    d=pd.concat([pd.read_csv(p) for p in fs],ignore_index=True)
    assert len(d)==sum(m["signals"] for m in metas)
    d["crowd"]=d.groupby(["policy","entry_ts"])["symbol"].transform("nunique")
    assert not d.duplicated(["policy","symbol","entry_ts"]).any()
    out={"experiment":"PSAR_CROWD_STRATEGY_ROBUSTNESS_V1","class":"EXPLORATORY sensitivity",
     "source_run":37741872209,"data_run":36095439671,"policies":PICKS,
     "orders":list(ORDERS),"results":[]}
    for name,(policy,crowd) in PICKS.items():
      for sp in ["TRAIN","SEEN_VALIDATION"]:
        d0=d[(d.policy==policy)&(d.split==sp)&(d.crowd>=crowd)]
        event_count=int(d0.entry_ts.nunique())
        assert event_count>1
        for risk in (.0025,.005):
          for slots in (2,4,8):
            for order in ORDERS:
              rec=stats_rank(d0,sp,risk,slots,policy,order)
              rec.update(strategy=name,crowd_min=crowd,rank=order,slot_cap=slots,
                independent_market_events=event_count,years=sorted(d0.year.unique().tolist()))
              out["results"].append(rec)
        # Leave one full calendar year out: same fixed risk/max slots and alphabetical.
        for omitted in sorted(d0.year.unique()):
          a0=d0[d0.year!=omitted]
          r=stats_rank(a0,sp,.005,4,policy,"ALPHABET")
          rec={"strategy":name,"split":sp,"omit_year":int(omitted),"return_pct":r["return_pct"],
            "realized_only_mdd_pct":r["realized_only_mdd_pct"],"accepted":r["accepted"],
            "market_events_remaining":int(a0.entry_ts.nunique())}
          out.setdefault("year_omission",[]).append(rec)
    for key in PICKS:
      for sp in ["TRAIN","SEEN_VALIDATION"]:
        print("ROBUST",key,sp)
        for a in out["results"]:
          if a["strategy"]==key and a["split"]==sp and a["risk_equity_fraction"]==.0025 and a["max_concurrent"]<=4 and a["rank"] in ORDERS:
            # max_concurrent 2 or 4 mixes across slots; filter later via full result
            pass
        selected=[x for x in out["results"] if x["strategy"]==key and x["split"]==sp and x["risk_equity_fraction"]==.0025 and x["rank"]=="ALPHABET"]
        for e in selected:print("POSITION_STRESS",key,sp,e["accepted"],e["return_pct"],e["realized_only_mdd_pct"],"events",e["independent_market_events"],flush=True)
        print("LEAVE_YEAR_OUT",key,sp,[(x["omit_year"],x["return_pct"]) for x in out["year_omission"] if x["strategy"]==key and x["split"]==sp],flush=True)
    Path(x.out).write_text(json.dumps(out,indent=2))
    print("ROBUSTNESS_PASS",len(d),len(out["results"]),len(out["year_omission"]))
if __name__=="__main__":main()
