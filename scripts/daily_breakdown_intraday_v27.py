"""Preregistered 24h capacity/latency diagnosis; prior rejection is retained."""
from __future__ import annotations
import argparse, json, shutil, sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import daily_breakdown_account_v26 as prior

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "research/daily-breakdown-intraday-v27"
CONTEXT, REGISTRY = STUDY / "FROZEN_CONTEXT.json", STUDY / "REGISTRY.json"
v25, engine, account = prior.v25, prior.engine, prior.account
BAR, DAY, digest, write_json = prior.BAR, prior.DAY, prior.digest, prior.write_json
STATUS = "V27_OBSERVED_HISTORY_INTRADAY_DIAGNOSTIC_V25_REJECTION_RETAINED"
PLAN_COMMIT = "1400c093121dd1d56c3ec8c094812714978a0523"
DELAYS, CAPS, HOLD = (0, 1), (None, 1, 2), 96
COLUMNS = v25.COLUMNS + ["delay_minutes", "nominal_entry_time", "nominal_entry_price", "frozen_sl"]

def configurations():
    return [p for p in v25.configurations() if p["side"] == -1]

def policies():
    return [dict(**p, hold=HOLD, exit_type="R20", delay_bars=d,
                 policy=f'{p["key"]}__H96__R20__D{d*15}')
            for p in configurations() for d in DELAYS]

def registry(path=REGISTRY):
    r = json.loads(path.read_text())
    if (r["policies"] != policies() or r["daily_caps"] != list(CAPS) or r["scenarios"] != 384
            or r["source_context_sha256"] != digest(CONTEXT)
            or r["plan_commit"] != PLAN_COMMIT or not r["v25_rejection_retained"]
            or r["plan_sha256"] != digest(STUDY/"PLAN.md")
            or r["prior_v25_context_sha256"] != digest(v25.CONTEXT)):
        raise ValueError("altered preregistered family/context")
    return r

def delayed_seed(seed, raw, delay, end):
    if delay not in DELAYS:
        raise ValueError("unregistered delay")
    t, o, h, l, c = raw
    j0, j = seed["entry_index"], seed["entry_index"] + delay
    if j >= len(t) or t[j] >= end:
        return None, "DELAY_OUTSIDE_SPLIT"
    if not np.array_equal(t[j0:j+1], seed["entry_time"] + np.arange(delay+1)*BAR):
        return None, "DELAY_PATH_GAP"
    side, stop, entry = seed["side"], float(seed["sl"]), float(o[j])
    if not np.isfinite(entry) or entry <= 0:
        return None, "INVALID_DELAY_ENTRY"
    if delay:
        touched = (l[j0:j] <= stop).any() if side == 1 else (h[j0:j] >= stop).any()
        if touched or side*(entry-stop) <= 0:
            return None, "DELAY_STOP_INVALIDATED"
    risk = side*(entry-stop)
    if not np.isfinite(risk) or risk <= 0:
        return None, "DELAY_STOP_INVALIDATED"
    if risk/entry > .25:
        return None, "DELAY_RISK_ABOVE_25PCT"
    target = entry + side*2*risk
    if not np.isfinite(target) or target <= 0:
        return None, "NONPOSITIVE_TP"
    return dict(seed, entry_time=int(t[j]), entry_index=int(j), entry=entry, sl=stop,
        tp=float(target), risk_pct=float(risk/entry), max_hold_bars=HOLD,
        delay_minutes=delay*15, nominal_entry_time=seed["entry_time"],
        nominal_entry_price=seed["entry"], frozen_sl=stop), None

def policy_rows(symbol, chosen, raw, f, btc, start, end):
    rows, counts, bad = [], Counter(), []
    grouped = {}
    for p in chosen:
        grouped.setdefault(p["key"], []).append(p)
    for group in grouped.values():
        seeds, excluded = v25.intents(symbol, group[0], raw, f, btc, start, end)
        counts.update({group[0]["key"]+"/"+k:v for k,v in excluded.items()})
        for p in group:
            for seed in seeds:
                tr, failure = delayed_seed(seed, raw, p["delay_bars"], end)
                if failure:
                    counts[p["policy"]+"/"+failure] += 1
                    bad.append(dict(symbol=symbol, policy=p["policy"],
                                    entry_time=seed["entry_time"], status=failure))
                    continue
                result = engine.canonical.resolve(tr, raw, f, "TP2", end)
                counts[p["policy"]+"/"+result["status"]] += 1
                if result["status"] != "RESOLVED":
                    bad.append(dict(symbol=symbol, policy=p["policy"],
                                    entry_time=tr["entry_time"], status=result["status"]))
                    continue
                row = {k:v for k,v in tr.items() if k != "entry_index"}
                row.update(result, variant=p["policy"], policy=p["policy"], exit_type="R20")
                row["hold_min"] = (row["exit_time"]-row["entry_time"])/60000
                px = row["exit"]*(1-row["side"]*.001) if row["reason"] in {"SL","SPLIT_END"} else row["exit"]
                ratio = px/row["entry"]
                net = row["side"]*(ratio-1)-.002*(1+ratio)-.0002*row["hold_min"]/1440
                row["net40_fraction"] = float(net)
                row["net40_R"] = float(net/account.stop_loss_fraction(row,.002))
                rows.append(row)
    return rows, counts, bad

@contextmanager
def minute_scope(cache, out):
    old_dir = engine.chronology.MINUTE_CACHE_DIR
    old_loader = engine.chronology.chronology.one_min
    old_slice, old_raw = engine.minute_audit.SLICE_DIR, engine.minute_audit.MINUTE_RAW_DIR
    maps = [engine.chronology.chronology.CACHE, engine.official.INPUTS,
            engine.minute_audit.MINUTE_INPUTS, engine.minute_audit.MINUTE_SLICES]
    saved = [dict(m) for m in maps]
    try:
        engine.chronology.MINUTE_CACHE_DIR = cache
        engine.chronology.chronology.one_min = engine.audited_minutes
        engine.minute_audit.SLICE_DIR = out/"minute_evidence"
        engine.minute_audit.MINUTE_RAW_DIR = out/"minute_original_archives"
        engine.minute_audit.SLICE_DIR.mkdir(exist_ok=True)
        engine.minute_audit.MINUTE_RAW_DIR.mkdir(exist_ok=True)
        for m in maps: m.clear()
        yield
    finally:
        engine.chronology.MINUTE_CACHE_DIR = old_dir
        engine.chronology.chronology.one_min = old_loader
        engine.minute_audit.SLICE_DIR, engine.minute_audit.MINUTE_RAW_DIR = old_slice, old_raw
        for m,s in zip(maps,saved): m.clear(); m.update(s)

def scan(data, btc_path, out, cache, stage, source_check, contract=REGISTRY):
    r = registry(contract)
    paths = sorted(data.rglob("*.csv.gz"))
    source, context, hashes = engine.verify_source(source_check, paths, btc_path, CONTEXT)
    start, end = engine.source_helpers.interval(stage)
    br, q, buy = v25.SOURCE_LOAD(btc_path, end)
    if not len(br[0]): raise ValueError("empty BTC")
    bf = v25.features(br,q,buy)
    out.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source_check,out/"source_check.json")
    shutil.copyfile(CONTEXT,out/"frozen_context.json")
    shutil.copyfile(contract,out/"registry.json")
    rows, counts, bad, coverage = [], Counter(), [], []
    def save(complete):
        lp = out/"independent_candidates.csv.gz"
        pd.DataFrame(rows,columns=COLUMNS).to_csv(lp,index=False,compression=dict(method="gzip",mtime=0))
        pd.DataFrame(bad,columns=["symbol","policy","entry_time","status"]).to_csv(out/"exclusions.csv",index=False)
        write_json(out/"minute_inputs.json",list(engine.minute_audit.MINUTE_INPUTS.values()))
        write_json(out/"minute_slices.json",list(engine.minute_audit.MINUTE_SLICES.values()))
        write_json(out/"scan_meta.json",dict(complete=complete,stage=stage,shard=source["shards"][0],
            policies=r["policies"],ledger_rows=len(rows),ledger_sha256=digest(lp),market_hashes=hashes,
            source_context_sha256=digest(CONTEXT),contract_sha256=digest(contract),
            btc_sha256=digest(btc_path),counts=dict(counts),exclusions=len(bad),coverage=coverage,
            status=STATUS,minute_months=len(engine.minute_audit.MINUTE_INPUTS),
            minute_official_checksums_verified=sum(bool(x.get("checksum_verified"))
                for x in engine.minute_audit.MINUTE_INPUTS.values())))
    with minute_scope(cache,out):
        try:
            for n,path in enumerate(paths,1):
                symbol=path.name[:-7];raw,q,buy=v25.SOURCE_LOAD(path,end)
                if not len(raw[0]) or raw[0][0]>=end-30*DAY:
                    coverage.append(dict(symbol=symbol,status="NO_PERIOD_ELIGIBLE_HISTORY",outcomes=0))
                    continue
                f=v25.features(raw,q,buy);btc=v25.align_context(raw[0],br,bf)
                found,excluded,mismatch=policy_rows(symbol,r["policies"],raw,f,btc,start,end)
                for row in found:row["split"]=stage
                rows.extend(found);bad.extend(mismatch)
                counts.update({stage+"/"+k:v for k,v in excluded.items()})
                coverage.append(dict(symbol=symbol,status="SCANNED",outcomes=len(found)))
                engine.chronology.chronology.CACHE.clear()
                if n%16==0 or n==len(paths):
                    save(False);print("V27_SCAN",stage,n,len(paths),len(rows),flush=True)
        except BaseException:save(False);raise
        save(True)
    print("V27_SCAN_COMPLETE",stage,len(rows),len(bad),flush=True)

def validate_frame(f, stage, chosen):
    if not len(f):return
    start,end=engine.source_helpers.interval(stage)
    info={p["policy"]:p for p in chosen}
    if not f.variant.isin(info).all() or not f.split.eq(stage).all():
        raise ValueError("policy/period leakage")
    if f.duplicated(["symbol","variant","entry_time"]).any():
        raise ValueError("duplicate intents")
    if (not f.entry_time.between(start,end-1).all() or not (f.exit_time<=end).all()
            or not (f.exit_time>f.entry_time).all() or (f.exit_time-f.entry_time>HOLD*BAR).any()
            or not f.max_hold_bars.eq(HOLD).all() or not f.side.eq(-1).all()
            or not f.status.eq("RESOLVED").all()):
        raise ValueError("holding/chronology/side contract")
    if (not np.allclose(f.sl,f.frozen_sl,rtol=0,atol=0)
            or not np.allclose(f.tp,f.entry-2*(f.sl-f.entry),rtol=1e-12,atol=1e-12)
            or not ((f.sl-f.entry)/f.entry).between(0,.25,inclusive="right").all()):
        raise ValueError("stop/target contract")
    for p in chosen:
        part=f[f.variant==p["policy"]]
        if not part.delay_minutes.eq(p["delay_bars"]*15).all():
            raise ValueError("delay label mismatch")
        if not (part.entry_time-part.nominal_entry_time==p["delay_bars"]*BAR).all():
            raise ValueError("delay timestamp mismatch")

def read_parts(parts, contract=REGISTRY):
    r=registry(contract);ctx=json.loads(CONTEXT.read_text())
    paths=[p for folder in parts for p in folder.rglob("independent_candidates.csv.gz")]
    if len(paths)!=16:raise ValueError("eight DEV plus eight GATE shards required")
    seen,sources,frames,counts=set(),{"DEV":{},"GATE":{}},[],Counter()
    for lp in sorted(paths):
        m=json.loads((lp.parent/"scan_meta.json").read_text());stage,shard=m["stage"],m["shard"]
        if stage not in sources or shard not in range(8) or (stage,shard) in seen:
            raise ValueError("duplicate/unregistered scan")
        if (not m["complete"] or m["status"]!=STATUS or m["policies"]!=r["policies"]
                or m["ledger_sha256"]!=digest(lp) or m["contract_sha256"]!=digest(contract)
                or m["source_context_sha256"]!=digest(CONTEXT) or m["btc_sha256"]!=ctx["btc_sha256"]):
            raise ValueError("altered/incomplete scan")
        seen.add((stage,shard))
        for symbol,sha in m["market_hashes"].items():
            if symbol in sources[stage] or ctx["expected_market_sha256"].get(symbol)!=sha:
                raise ValueError("source catalogue mismatch")
            sources[stage][symbol]=sha
        f=pd.read_csv(lp);validate_frame(f,stage,r["policies"])
        if len(f)!=m["ledger_rows"]:raise ValueError("scan row mismatch")
        frames.append(f);counts.update(m["counts"])
    if any(x!=ctx["expected_market_sha256"] for x in sources.values()):
        raise ValueError("incomplete source universe")
    f=pd.concat(frames,ignore_index=True)
    if f.duplicated(["symbol","variant","entry_time","split"]).any():
        raise ValueError("duplicate across shards")
    return f,counts

def prepare_market(data,btc,source_check,parts,out,contract=REGISTRY):
    f,_=read_parts(parts,contract)
    hashes=prior.verify_full_market(source_check,sorted(data.rglob("*.csv.gz")),btc,CONTEXT)
    used=set(f.symbol)
    if not used.issubset(hashes):raise ValueError("missing account source")
    out.mkdir(parents=True,exist_ok=True)
    for path in data.rglob("*.csv.gz"):
        if path.name[:-7] in used:shutil.copyfile(path,out/path.name)
    write_json(out/"market_manifest.json",dict(status="VERIFIED",contract_sha256=digest(contract),
        source_context_sha256=digest(CONTEXT),account_market_sha256={s:hashes[s] for s in sorted(used)},
        original_inventory=len(hashes),diagnostic_status=STATUS))
    print("V27_MARKET_VERIFIED",len(hashes),len(used),flush=True)

def audit_account(result,tr,day,curve,start,end,cap):
    audit=prior.audit_account(result,tr,day,curve,start,end)
    if len(tr):assert tr.hold_min.between(0,1440,inclusive="right").all()
    if cap is not None:assert day.entries.le(cap).all()
    assert int(day.entries.sum())==len(tr)
    return dict(audit,actual_max_hold_minutes=1440,daily_cap=cap,quota_verified=True)

def replay_probe(data,parts,out,probe_index,contract=REGISTRY):
    r=registry(contract)
    if probe_index not in range(16):raise ValueError("unregistered probe")
    f,counts=read_parts(parts,contract);p=r["policies"][probe_index];ledger=f[f.variant==p["policy"]]
    m=json.loads((data/"market_manifest.json").read_text());ctx=json.loads(CONTEXT.read_text())
    if m["contract_sha256"]!=digest(contract) or m["source_context_sha256"]!=digest(CONTEXT):
        raise ValueError("altered account manifest")
    files={}
    for path in data.rglob("*.csv.gz"):
        symbol=path.name[:-7]
        if symbol in files:raise ValueError("duplicate market file")
        files[symbol]=path
    if set(files)!=set(m["account_market_sha256"]):raise ValueError("incomplete marking universe")
    for s in sorted(set(ledger.symbol)):
        sha=m["account_market_sha256"].get(s)
        if s not in files or digest(files[s])!=sha or ctx["expected_market_sha256"].get(s)!=sha:
            raise ValueError("altered market bytes")
    market=account.Market({s:v25.SOURCE_LOAD(files[s],engine.base.GATE_END)[0] for s in sorted(set(ledger.symbol))})
    out.mkdir(parents=True,exist_ok=True);rows,periods,clusters,audits=[],[],[],[]
    for split in ("DEV","GATE"):
        start,end=engine.source_helpers.interval(split);sel=ledger[ledger.split==split]
        exclusions={k.rsplit("/",1)[-1]:v for k,v in counts.items() if k.startswith(split+"/"+p["policy"]+"/")}
        clusters.append(dict(split=split,policy=p["policy"],scope="INDEPENDENT_CANONICAL_OUTCOMES",
            exclusions=exclusions,**prior.cluster_stats(sel)))
        for cap in CAPS:
            for cost in (20,40):
                for guarded in (True,False):
                    cap_label="UNLIMITED" if cap is None else str(cap)
                    name=f'{split}__{p["policy"]}__CAP{cap_label}__{cost}bp__{"guarded" if guarded else "unguarded_diagnostic"}'
                    result,tr,day,curve=account.simulate(sel,market,start,end,cost,guarded,
                        all_kst_days=True,max_entries_per_kst_day=cap)
                    audit=audit_account(result,tr,day,curve,start,end,cap)
                    result.update(split=split,policy=p["policy"],entry_key=p["key"],probe_index=probe_index,
                        delay_minutes=p["delay_bars"]*15,daily_cap=cap_label,independent_n=len(sel),diagnostic_status=STATUS)
                    folder=out/"details"/name;folder.mkdir(parents=True,exist_ok=True)
                    tr.to_csv(folder/"trades.csv.gz",index=False,compression=dict(method="gzip",mtime=0))
                    day.to_csv(folder/"daily.csv",index=False)
                    curve.to_csv(folder/"curve.csv.gz",index=False,compression=dict(method="gzip",mtime=0))
                    audits.append(dict(scenario=name,**audit,sha256={n:digest(folder/n)
                        for n in ("trades.csv.gz","daily.csv","curve.csv.gz")}))
                    rows.append(result)
                    if cost==40 and guarded:
                        clusters.append(dict(split=split,policy=p["policy"],daily_cap=cap_label,
                            scope="EXECUTABLE_ACCOUNT_REALIZED_PNL",**prior.cluster_stats(tr,"net_pnl","net_pnl")))
                    dates=pd.to_datetime(day.day)
                    for freq in ("Y","Q"):
                        for period,g in day.groupby(dates.dt.to_period(freq)):
                            periods.append(dict(scenario=name,split=split,policy=p["policy"],entry_key=p["key"],
                                delay_minutes=p["delay_bars"]*15,daily_cap=cap_label,cost_bps=cost,guarded=guarded,
                                period=str(period),days=len(g),partial_days=int(g.partial_day.sum()),
                                net_return_pct=float(100*(np.prod(1+g.return_pct/100)-1))))
                    print("V27_ACCOUNT",name,result["trades"],result["net_return_pct"],flush=True)
    write_json(out/"summary.json",rows)
    pd.DataFrame([{k:v for k,v in row.items() if not isinstance(v,(dict,list))} for row in rows]).to_csv(out/"summary.csv",index=False)
    pd.DataFrame(periods).to_csv(out/"year_quarter.csv",index=False)
    write_json(out/"cluster_diagnostics.json",clusters);write_json(out/"audit.json",audits)
    write_json(out/"result_manifest.json",dict(status=STATUS,probe_index=probe_index,policy=p["policy"],
        contract_sha256=digest(contract),scenarios=24,all_checks_passed=True,v25_rejection_retained=True))

def material_screen(rows,periods,clusters):
    table=pd.DataFrame(rows);years=pd.DataFrame(periods);flags=[]
    for cfg in configurations():
        for cap in ("UNLIMITED","1","2"):
            part=table[(table.entry_key==cfg["key"])&(table.daily_cap.astype(str)==cap)&table.guarded]
            failures=[]
            if cap=="UNLIMITED":failures.append("CONTROL_ONLY")
            if len(part)!=8:failures.append("INCOMPLETE_ROWS")
            for row in part.to_dict("records"):
                if row["trades"]<80:failures.append("N_LT_80")
                if row["mdd_15m_pct"]>=15 or pd.notna(row["halt_time"]):failures.append("DD_OR_HALT")
                pf=row["pf"]
                if row["cost_bps"]==20:
                    if row["cagr_pct"]<20:failures.append("CAGR20_LT_20")
                    if pd.isna(pf) or pf<1.15:failures.append("PF20_LT_1_15")
                elif row["net_return_pct"]<=0 or pd.isna(pf) or pf<1.05:
                    failures.append("STRESSED_RETURN_OR_PF")
                required=("2022","2023") if row["split"]=="DEV" else ("2024",)
                yr=years[(years.policy==row["policy"])&(years.split==row["split"])
                    &(years.daily_cap.astype(str)==cap)&years.guarded&(years.cost_bps==row["cost_bps"])]
                for year in required:
                    selected=yr[yr.period.astype(str)==year]
                    if len(selected)!=1 or selected.net_return_pct.iloc[0]<=0:
                        failures.append("NONPOSITIVE_OR_MISSING_YEAR_"+year)
                if row["cost_bps"]==40:
                    matching=[c for c in clusters if c["scope"]=="EXECUTABLE_ACCOUNT_REALIZED_PNL"
                        and c["policy"]==row["policy"] and c["split"]==row["split"] and str(c["daily_cap"])==cap]
                    if len(matching)!=1:failures.append("MISSING_CLUSTER_AUDIT");continue
                    c=matching[0];remaining=c["after_remove_best5_dates_mean"];share=c["top5_positive_date_share"]
                    if remaining is None or remaining<=0:failures.append("BEST5_REMOVAL_NONPOSITIVE")
                    if share is None or share>=.5:failures.append("BEST5_SHARE_GE_50PCT")
            stressed=part[part.cost_bps==40]
            goal=cap in ("1","2") and len(stressed)==4 and stressed.daily_mean_pct.ge(.7).all()
            flags.append(dict(entry_key=cfg["key"],daily_cap=cap,further_audit_flag=not failures,
                daily_mean_goal_met=bool(goal),failed_conditions=sorted(set(failures))))
    return flags

def collect(parts,out,contract=REGISTRY):
    r=registry(contract);paths=sorted(parts.rglob("result_manifest.json"))
    if len(paths)!=16:raise ValueError("sixteen complete account probes required")
    seen,rows,periods,clusters,audits=set(),[],[],[],[]
    for path in paths:
        m=json.loads(path.read_text());i=m["probe_index"]
        if (i in seen or i not in range(16) or m["policy"]!=r["policies"][i]["policy"]
                or m["contract_sha256"]!=digest(contract) or m["status"]!=STATUS
                or m["scenarios"]!=24 or not m["all_checks_passed"] or not m["v25_rejection_retained"]):
            raise ValueError("altered/missing account probe")
        seen.add(i);original=json.loads((path.parent/"summary.json").read_text())
        if len(original)!=24:raise ValueError("missing scenarios")
        rows.extend(original);periods.extend(pd.read_csv(path.parent/"year_quarter.csv").to_dict("records"))
        clusters.extend(json.loads((path.parent/"cluster_diagnostics.json").read_text()))
        for a in json.loads((path.parent/"audit.json").read_text()):
            if not a["all_checks_passed"] or not a["quota_verified"]:raise ValueError("failed account audit")
            for name,sha in a["sha256"].items():
                if digest(path.parent/"details"/a["scenario"]/name)!=sha:raise ValueError("altered account evidence")
            audits.append(a)
    table=pd.DataFrame(rows)
    expected={(p["policy"],s,c,g,cap) for p in r["policies"] for s in ("DEV","GATE")
        for c in (20,40) for g in (True,False) for cap in ("UNLIMITED","1","2")}
    actual=set(map(tuple,table[["policy","split","cost_bps","guarded","daily_cap"]].astype({"daily_cap":str}).to_numpy()))
    if len(table)!=384 or actual!=expected or len(audits)!=384:raise ValueError("incomplete/duplicate account matrix")
    flags=material_screen(rows,periods,clusters)
    out.mkdir(parents=True,exist_ok=True)
    table.to_csv(out/"summary.csv",index=False);pd.DataFrame(periods).to_csv(out/"year_quarter.csv",index=False)
    for name,value in (("summary",rows),("cluster_diagnostics",clusters),("audit",audits),("material_screen",flags)):
        write_json(out/(name+".json"),value)
    write_json(out/"decision.json",dict(status=STATUS,scenarios=384,v25_rejection_retained=True,
        further_audit_flags=sum(f["further_audit_flag"] for f in flags),qualified_candidates=0,
        account_daily_target_claim=any(f["daily_mean_goal_met"] for f in flags),
        note="Exploratory selected historical family. Flags require independent subsequent audit/freeze; no automatic promotion."))
    print("V27_384_SCENARIOS_HASH_AND_ARITHMETIC_PASS",flush=True)

def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest="command",required=True)
    s=sub.add_parser("scan")
    for k in ("data","btc","out","cache","source-check"):s.add_argument("--"+k,type=Path,required=True)
    s.add_argument("--stage",choices=["DEV","GATE"],required=True)
    m=sub.add_parser("prepare-market")
    for k in ("data","btc","out","source-check"):m.add_argument("--"+k,type=Path,required=True)
    m.add_argument("--parts",type=Path,nargs="+",required=True)
    a=sub.add_parser("accounts")
    for k in ("data","out"):a.add_argument("--"+k,type=Path,required=True)
    a.add_argument("--parts",type=Path,nargs="+",required=True);a.add_argument("--probe-index",type=int,required=True)
    c=sub.add_parser("collect");c.add_argument("--parts",type=Path,required=True);c.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    if args.command=="scan":scan(args.data,args.btc,args.out,args.cache,args.stage,args.source_check)
    elif args.command=="prepare-market":prepare_market(args.data,args.btc,args.source_check,args.parts,args.out)
    elif args.command=="accounts":replay_probe(args.data,args.parts,args.out,args.probe_index)
    else:collect(args.parts,args.out)

if __name__=="__main__":main()
