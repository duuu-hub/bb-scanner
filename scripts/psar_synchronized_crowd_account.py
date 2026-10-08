#!/usr/bin/env python3
"""PSAR synchronized oversold reversal: frozen-trade portfolio research.
Existing canonical 4H PSAR market-OPEN signal and 15m stop ledger are kept unchanged.
Crowd is count of simultaneous valid PSAR LONG candidates known at that 4H OPEN.
No intrabar TP/SL collision: original ledgers use one stop price-level only.
"""
import argparse,glob,json,inspect,sys,hashlib,datetime
from pathlib import Path
from collections import defaultdict
import pandas as pd,numpy as np
sys.path.insert(0,"scripts")
import psar_fade_risk_portfolio_v1 as base

POLICIES=("P3_SL6_TIME","P3_SL6_FLIP","P3p5_SL6_TIME","P3p5_SL6_FLIP","P3p5_SL8_FLIP")
CROWD_AT_LEAST=(0,2,4,6,8,12,16)
RISKS=(.0025,.005)
SLOTS=(2,4,8)
HALTS=("FULL","CAPS_ONLY")
ORIGINAL_RULE=dict(base.PORT_RULE)
SOURCE_SHA_EXPECTED="7937e6bca49556e9abc1b47985e758c6cd435127"
SOURCE_RUN=37741872209
DATA_RUN=36095439671
def simulate(z, gate, risk, slots, policy, split):
    original=dict(base.PORT_RULE)
    try:
        base.PORT_RULE.update(original)
        base.PORT_RULE["max_slots"]=slots
        if gate=="CAPS_ONLY":
            base.PORT_RULE["daily_realized_loss_halt"]=1e9
            base.PORT_RULE["drawdown_pause"]=1e9
            base.PORT_RULE["loss_streak_pause"]=10**9
        return base.stats(z,split,risk,policy)
    finally:
        base.PORT_RULE.clear();base.PORT_RULE.update(original)
def assert_inputs(data,metas):
    assert len(metas)==4 and {m["shard"] for m in metas}==set(range(4))
    assert len({m["code_sha"] for m in metas})==1
    assert metas[0]["code_sha"]==SOURCE_SHA_EXPECTED,(metas[0]["code_sha"],SOURCE_SHA_EXPECTED)
    assert len({m["engine_sha"] for m in metas})==1
    assert sum(m["files"] for m in metas)==metas[0]["all_files"]
    assert len(data)==sum(m["signals"] for m in metas)
    assert not data.duplicated(["policy","symbol","entry_ts"]).any()
    assert data["policy"].isin(POLICIES).all()
    assert data["split"].isin(["TRAIN","SEEN_VALIDATION"]).all()
    assert (data["exit_ts"]>=data["entry_ts"]).all()
    # trade has at most one intrabar stop; no re-entry after fill, no future info for crowd.
    assert not data[["symbol","entry_ts","dist_atr","net40_pct"]].isna().any().any()
    return metas[0]["engine_sha"]
def crowd_features(data):
    d=data.copy()
    # One symbol may generate a signal for more than one policy, so count per
    # policy and 4H open only; never count exits, subsequent outcomes, or scores.
    d["crowd"]=d.groupby(["policy","entry_ts"])["symbol"].transform("nunique").astype(int)
    assert (d["crowd"]>=1).all()
    counts=d.groupby(["policy","entry_ts"]).agg(symbols=("symbol","nunique"),rows=("symbol","size"))
    assert (counts.symbols==counts.rows).all()
    return d
def annual_at_open(d):
    y=d.copy();y["year"]=pd.to_datetime(y.entry_ts,unit="ms",utc=True).dt.year
    return {str(int(yr)):{"candidate_rows":int(len(q)),"market_events":int(q.entry_ts.nunique()),
        "mean_net40_pct_individual":round(float(q.net40_pct.mean()),4)}
        for yr,q in y.groupby("year")}
def analysis(d):
    all_results=[]
    for policy in POLICIES:
        for crowd in CROWD_AT_LEAST:
            for slots in SLOTS:
                for risk in RISKS:
                    for gate in HALTS:
                        row={"policy":policy,"crowd_min":crowd,"slots":slots,"risk":risk,"halt":gate}
                        for split in ("TRAIN","SEEN_VALIDATION"):
                            subset=d[(d.policy==policy)&(d.split==split)&(d.crowd>=crowd)]
                            r=simulate(subset,gate,risk,slots,policy,split)
                            r["eligible_market_events"]=int(subset.entry_ts.nunique())
                            r["eligible_signal_rows"]=int(len(subset))
                            row[split]=r
                        all_results.append(row)
    candidates=[]
    for r in all_results:
        a=r["TRAIN"];b=r["SEEN_VALIDATION"]
        # Evaluate TRAIN only for filtering/ranking; seen used as falsification.
        if a["accepted"]<24 or a["eligible_market_events"]<7 or b["eligible_market_events"]<7:
            continue
        z=d[(d.policy==r["policy"])&(d.split=="TRAIN")&(d.crowd>=r["crowd_min"])]
        yrs=annual_at_open(z)
        if len(yrs)<3:continue
        # pre-registered robust proxy: train account return minus 0.5x
        # realized-only MDD; cap high concurrency and compare 40bp results.
        score=a["return_pct"]-.5*a["realized_only_mdd_pct"]
        candidates.append({"config":{k:r[k] for k in ("policy","crowd_min","slots","risk","halt")},
            "train_score":round(score,3),"train":a,"seen":b,
            "train_years_signal_breakdown":yrs})
    candidates.sort(key=lambda x:(x["train_score"],x["train"]["accepted"]),reverse=True)
    no_halting=[]
    for r in all_results:
        if r["slots"]==4 and r["risk"]==.005 and r["halt"]=="FULL" and r["crowd_min"] in (0,2,4,8):
            no_halting.append({"policy":r["policy"],"crowd_min":r["crowd_min"],
                "train":{k:r["TRAIN"][k] for k in ("return_pct","realized_only_mdd_pct","accepted","eligible_market_events","eligible_signal_rows")},
                "seen":{k:r["SEEN_VALIDATION"][k] for k in ("return_pct","realized_only_mdd_pct","accepted","eligible_market_events","eligible_signal_rows")}})
    return all_results,candidates,no_halting
def smoke():
    assert CROWD_AT_LEAST[0]==0 and 8 in CROWD_AT_LEAST
    assert ORIGINAL_RULE["max_slots"]==4
    df=pd.DataFrame([{"policy":"A","entry_ts":100,"symbol":"X"},{"policy":"A","entry_ts":100,"symbol":"Y"},
      {"policy":"A","entry_ts":200,"symbol":"Z"},{"policy":"B","entry_ts":100,"symbol":"X"}])
    z=crowd_features(df)
    assert z.crowd.tolist()==[2,2,1,1]
    assert SOURCE_RUN==37741872209
    print("CROWD_SMOKE_PASS identical timestamp, independent policy, no exit/outcome reference",flush=True)
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--data",default="ledgers");ap.add_argument("--out",default="psar-crowd-account-report.json");ap.add_argument("--audit",action="store_true")
    a=ap.parse_args();smoke()
    if a.audit:return
    fs=sorted(glob.glob(a.data+"/**/portfolio-ledger-*.csv.gz",recursive=True))
    ms=sorted(glob.glob(a.data+"/**/portfolio-meta-*.json",recursive=True))
    assert len(fs)==len(ms)==4,(fs,ms)
    metas=[json.load(open(p)) for p in ms]
    df=pd.concat([pd.read_csv(p) for p in fs],ignore_index=True)
    engine=assert_inputs(df,metas)
    d=crowd_features(df)
    # Exact previous run baseline must match, otherwise no admissible comparison.
    baseline={"P3_SL6_TIME":(-9.412,-6.917),"P3p5_SL6_TIME":(-1.326,-9.093),
             "P3p5_SL8_FLIP":(-3.23,-.872)}
    for pol,values in baseline.items():
        for split,expect in zip(("TRAIN","SEEN_VALIDATION"),values):
            sample=d[(d.policy==pol)&(d.split==split)]
            z=simulate(sample,"FULL",.005,4,pol,split)
            assert abs(z["return_pct"]-expect)<.002,(pol,split,z,expect)
    print("BASELINE_REPRO_PASS",len(baseline)*2,"comparisons",flush=True)
    rows,candidates,comparisons=analysis(d)
    doc={"experiment":"PSAR_SYNCHRONIZED_OVERSOLD_CROWD_V1","class":"EXPLORATORY posthoc",
         "source_run":SOURCE_RUN,"source_ledger_commit":metas[0]["code_sha"],
         "canonical_psar_sha256":engine,"data_run":DATA_RUN,"trades":len(d),
         "grid":{"policies":POLICIES,"crowd_min":CROWD_AT_LEAST,"slots":SLOTS,
                 "risk_fraction":RISKS,"halts":HALTS},
         "definition":"Count concurrently eligible distinct alt symbols at SAME 4H candle OPEN for identical frozen 12th-bar PSAR countertrend LONG policy; observable at entry time. No exits/outcomes used as feature.",
         "costs":"Prior ledger 40bp roundtrip all-in, funding excluded","splits":"TRAIN 2021-24, already seen 2025-26",
         "results":rows,"ranked_train_only":candidates[:30],
         "predefined_comparisons":comparisons,"limitations":["Trade signals clustered: report independent timestamps, not just N",
          "Many viable configurations have <10 independent training events and do not support deployable edge",
          "Selection posthoc after user saw original signals and crowd observation, not pristine OOS",
          "Realized-only MDD underestimates peak intratrade drawdown; stop-market gap risks remain",
          "No funding, no exchange orderbook, no point-in-time delisted universe",
          "Large positive outlier/event concentration can dominate average, examine per year",
          "Simulated slots alphabetical among same timestamp, not an actionable price ranking",
          "Active demo/main unchanged; production admission requires canonical audits and future forward"]}
    Path(a.out).write_text(json.dumps(doc,indent=2))
    print("CROWD_REPORT_PASS",len(d),"rows",len(rows),"configs","ranked",len(candidates),flush=True)
    for r in comparisons:
        if r["policy"] in ("P3_SL6_TIME","P3p5_SL6_TIME") and r["crowd_min"] in (0,4,8):
            print("COMPARE",r["policy"],"C",r["crowd_min"],"TRAIN",r["train"],"SEEN",r["seen"],flush=True)
    for r in candidates[:15]:
        print("TOP_TRAIN",r["config"],"train_score",r["train_score"],
            "train",r["train"]["return_pct"],"train_mdd",r["train"]["realized_only_mdd_pct"],
            "train_events",r["train"]["eligible_market_events"],
            "seen",r["seen"]["return_pct"],"seen_mdd",r["seen"]["realized_only_mdd_pct"],
            "seen_events",r["seen"]["eligible_market_events"],
            "years",r["train_years_signal_breakdown"],flush=True)
if __name__=="__main__":main()
