#!/usr/bin/env python3
"""Pre-registered exploratory risk-gate decomposition using frozen PSAR fade portfolio ledgers.
No new features or signals; all models use exactly the same 5 variants and 40bp cost.
"""
import argparse,glob,json,sys
from pathlib import Path
import pandas as pd
sys.path.insert(0,"scripts")
import psar_fade_risk_portfolio_v1 as base
ORIGINAL=dict(base.PORT_RULE)
GATES={
"CAPS_ONLY":{"daily_realized_loss_halt":1e9,"drawdown_pause":1e9,"loss_streak_pause":10**9},
"DAILY_ONLY":{"daily_realized_loss_halt":.015,"drawdown_pause":1e9,"loss_streak_pause":10**9},
"STREAK_ONLY":{"daily_realized_loss_halt":1e9,"drawdown_pause":1e9,"loss_streak_pause":3},
"WEEK_DD_ONLY":{"daily_realized_loss_halt":1e9,"drawdown_pause":.05,"loss_streak_pause":10**9},
"DAILY_STREAK":{"daily_realized_loss_halt":.015,"drawdown_pause":1e9,"loss_streak_pause":3},
"FULL":{"daily_realized_loss_halt":.015,"drawdown_pause":.05,"loss_streak_pause":3}
}
def main():
    a=argparse.ArgumentParser();a.add_argument("--audit",action="store_true")
    a.add_argument("--data",default="ledgers");a.add_argument("--out")
    x=a.parse_args()
    assert len(GATES)==6 and GATES["FULL"]["drawdown_pause"]==ORIGINAL["drawdown_pause"]
    assert len(base.POLICIES)==5
    assert all(k in ORIGINAL for cfg in GATES.values() for k in cfg)
    if x.audit:
        print("HALT_AUDIT_PASS registered gating configs; original full config unchanged; original ledger source run 37741872209")
        return
    paths=sorted(glob.glob(x.data+"/**/portfolio-ledger-*.csv.gz",recursive=True))
    metas=sorted(glob.glob(x.data+"/**/portfolio-meta-*.json",recursive=True))
    assert len(paths)==len(metas)==4,(paths,metas)
    data=pd.concat([pd.read_csv(p) for p in paths],ignore_index=True)
    m=[json.load(open(p)) for p in metas]
    assert {v["shard"] for v in m}==set(range(4))
    assert len({v["code_sha"] for v in m})==1
    assert len(data)==sum(v["signals"] for v in m)
    assert not data.duplicated(["policy","symbol","entry_ts"]).any()
    output={"experiment":"PSAR_FADE_HALT_ABLATION","class":"EXPLORATORY post-hoc",
       "data_run":36095439671,"source_portfolio_run":37741872209,
       "source_ledger_commit":m[0]["code_sha"],"source_raw_trades":len(data),
       "gates":GATES,"base_rule":ORIGINAL,"results":[]}
    for gate,settings in GATES.items():
        base.PORT_RULE.clear();base.PORT_RULE.update(ORIGINAL);base.PORT_RULE.update(settings)
        for policy in base.POLICIES:
            for split in ("TRAIN","SEEN_VALIDATION"):
                part=data[(data.policy==policy)&(data.split==split)]
                for risk in (.0025,.005):
                    rec=base.stats(part,split,risk,policy)
                    rec["gate"]=gate
                    output["results"].append(rec)
    base.PORT_RULE.clear();base.PORT_RULE.update(ORIGINAL)
    for policy in base.POLICIES:
        for split in ("TRAIN","SEEN_VALIDATION"):
            for gate in GATES:
                p=next(z for z in output["results"] if z["policy"]==policy and z["split"]==split and z["gate"]==gate and z["risk_equity_fraction"]==.005)
                print("SENSITIVITY",policy,split,gate,"account_return",p["return_pct"],"MDD_realized",p["realized_only_mdd_pct"],"accepted",p["accepted"],"blocked",p["excluded"],flush=True)
    Path(x.out).write_text(json.dumps(output,indent=2))
    print("HALT_MERGE_PASS",len(data),"source trade rows",len(output["results"]),"portfolio cases")
if __name__=="__main__":main()
