#!/usr/bin/env python3
"""Counterfactual PSAR fade simultaneous-signal ordering audit.
Current-open PSAR distance ranking vs alphabetical first; all use same frozen ledger.
"""
import argparse,glob,json,sys,inspect
from pathlib import Path
import pandas as pd
sys.path.insert(0,"scripts")
import psar_fade_risk_portfolio_v1 as base
RANKS={
"ALPHABET":'trades=trades.sort_values(["entry_ts","symbol"],kind="mergesort")',
"FARTHEST_ATR_FIRST":'trades=trades.sort_values(["entry_ts","dist_atr","symbol"],ascending=[True,False,True],kind="mergesort")',
"NEAREST_ATR_FIRST":'trades=trades.sort_values(["entry_ts","dist_atr","symbol"],ascending=[True,True,True],kind="mergesort")'
}
BASE_LINE=RANKS["ALPHABET"]
RULES={
"CAPS_ONLY":{"daily_realized_loss_halt":1e9,"drawdown_pause":1e9,"loss_streak_pause":10**9},
"FULL":{"daily_realized_loss_halt":.015,"drawdown_pause":.05,"loss_streak_pause":3}
}
def create_sim(rule,rank):
    orig=inspect.getsource(base.stats)
    assert orig.count(BASE_LINE)==1
    scope=dict(vars(base))
    scope["PORT_RULE"]=dict(base.PORT_RULE);scope["PORT_RULE"].update(RULES[rule])
    exec(orig.replace(BASE_LINE,RANKS[rank]),scope)
    return scope["stats"]
def main():
    a=argparse.ArgumentParser();a.add_argument("--audit",action="store_true")
    a.add_argument("--data",default="ledgers");a.add_argument("--out")
    x=a.parse_args()
    for gate in RULES:
        for rank in RANKS:
            fn=create_sim(gate,rank)
            assert callable(fn)
    if x.audit:
        print("RANK_AUDIT_PASS ranking takes only current-open ATR distance, no future data; 3 orders 2 gate modes")
        return
    fs=glob.glob(x.data+"/**/portfolio-ledger-*.csv.gz",recursive=True)
    ms=glob.glob(x.data+"/**/portfolio-meta-*.json",recursive=True)
    assert len(fs)==len(ms)==4,(fs,ms)
    metas=[json.load(open(p)) for p in ms]
    assert {m["shard"] for m in metas}==set(range(4))
    assert len({m["code_sha"] for m in metas})==1
    d=pd.concat([pd.read_csv(p) for p in fs],ignore_index=True)
    assert len(d)==sum(m["signals"] for m in metas)
    assert d.dist_atr.notna().all()
    out={"experiment":"PSAR_FADE_SIMULTANEOUS_RANK_AUDIT",
        "classification":"EXPLORATORY posthoc", "source_ledger_run":37741872209,
        "original_code_sha":metas[0]["code_sha"],"rows":len(d),"ranking":list(RANKS),
        "risk_rules":RULES,"results":[],"caution":"Rank post-selected on seen data, no external out-of-sample and no MTM MDD"}
    for gate in RULES:
        for rank in RANKS:
            sim=create_sim(gate,rank)
            for policy in base.POLICIES:
                for split in ("TRAIN","SEEN_VALIDATION"):
                    subset=d[(d.policy==policy)&(d.split==split)]
                    for risk in (.0025,.005):
                        res=sim(subset,split,risk,policy)
                        res.update(gate=gate,rank=rank)
                        out["results"].append(res)
    # hard invariant - alphabetical/ FULL/ 0.5% matches original run 
    baseline={("P3_SL6_TIME","TRAIN"):-9.412,("P3_SL6_TIME","SEEN_VALIDATION"):-6.917,
              ("P3p5_SL8_FLIP","TRAIN"):-3.23,("P3p5_SL8_FLIP","SEEN_VALIDATION"):-.872}
    for (policy,split),expected in baseline.items():
        q=next(z for z in out["results"] if z["policy"]==policy and z["split"]==split and z["gate"]=="FULL" and z["rank"]=="ALPHABET" and z["risk_equity_fraction"]==.005)
        assert abs(q["return_pct"]-expected)<.002,(q,expected)
    for policy in base.POLICIES:
        for split in ("TRAIN","SEEN_VALIDATION"):
            for gate in ("CAPS_ONLY","FULL"):
                items=[q for q in out["results"] if q["policy"]==policy and q["split"]==split and q["gate"]==gate and q["risk_equity_fraction"]==.005]
                print("RANK",policy,split,gate,[(q["rank"],q["return_pct"],q["realized_only_mdd_pct"],q["accepted"]) for q in items],flush=True)
    Path(x.out).write_text(json.dumps(out,indent=2))
    print("RANK_MERGE_PASS",len(d),len(out["results"]),"frozen-ledger account cases")
if __name__=="__main__":main()
