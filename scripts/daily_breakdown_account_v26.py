"""Exploratory account diagnostic. V25 rejection and source engine are unchanged."""
from __future__ import annotations
import argparse, hashlib, json, shutil, sys
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import daily_channel_breakout_v25 as v25

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / "research/daily-breakdown-account-v26"
REGISTRY, FROZEN = STUDY / "PROBES.json", STUDY / "FROZEN_V25_INPUTS.json"
DIAGNOSTIC = "DIAGNOSTIC_ONLY_V25_REJECTION_RETAINED"
CONTEXT_SHA = "dab527bef32197364070e1d1cd17b5f9260cbf17f099e9698070ce5843c87faa"
engine, account = v25.ENGINE, v25.prior.account
digest, BAR, DAY = v25.prior.digest, v25.BAR, v25.DAY

def probes():
    return [p for p in v25.policies() if p["side"] == -1 and p["hold"] == 672 and p["exit_type"] == "R20"]

def registry(path=REGISTRY):
    r = json.loads(path.read_text())
    if (r["policies"] != probes() or not r["diagnostic_only"] or not r["v25_rejection_retained"]
            or r["source_context_sha256"] != CONTEXT_SHA or digest(v25.CONTEXT) != CONTEXT_SHA):
        raise ValueError("altered diagnostic cohort/context")
    return r

def git_blob_sha(path):
    b = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(b)).encode() + b"\0" + b).hexdigest()

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")

def validate_frame(f, stage, policies):
    start, end = engine.source_helpers.interval(stage)
    if not len(f): return
    if not set(f.variant).issubset({p["policy"] for p in policies}) or not f.split.eq(stage).all():
        raise ValueError("policy/stage leakage")
    if f.duplicated(["symbol", "variant", "entry_time"]).any(): raise ValueError("duplicate diagnostic intents")
    if not f.entry_time.between(start, end-1).all() or not (f.exit_time <= end).all() or not (f.exit_time > f.entry_time).all():
        raise ValueError("split/holding chronology leakage")
    if not f.max_hold_bars.eq(672).all() or (f.exit_time-f.entry_time > 672*BAR).any():
        raise ValueError("holding contract changed")
    if not f.side.eq(-1).all() or not f.status.eq("RESOLVED").all(): raise ValueError("unresolved/wrong-side outcome")
    if not np.allclose(f.tp, f.entry-2*abs(f.entry-f.sl), rtol=1e-12, atol=1e-12):
        raise ValueError("actual-risk target changed")

def prepare_dev(parts, out, contract=REGISTRY, frozen_path=FROZEN):
    r, frozen = registry(contract), json.loads(frozen_path.read_text())
    paths = sorted(parts.rglob("independent_candidates.csv.gz"))
    if len(paths) != 8: raise ValueError("missing/extra original DEV shard")
    expected = {m["shard"]:m for m in frozen["expected_shards"]}
    context = json.loads(v25.CONTEXT.read_text())
    names = {p["policy"] for p in r["policies"]}
    seen, sources, total = set(), {}, 0
    out.mkdir(parents=True, exist_ok=True)
    for path in paths:
        mp = path.parent/"scan_meta.json"; m = json.loads(mp.read_text()); shard = m["shard"]
        if shard not in expected or shard in seen: raise ValueError("duplicate/unregistered original shard")
        e = expected[shard]
        if (not m["complete"] or m["stage"] != "DEV" or m["policies"] != v25.policies()
                or digest(path) != e["ledger_sha256"] or m["ledger_sha256"] != e["ledger_sha256"]
                or git_blob_sha(mp) != e["scan_meta_git_blob_sha"] or m["ledger_rows"] != e["ledger_rows"]
                or m["source_context_sha256"] != CONTEXT_SHA or m["btc_sha256"] != context["btc_sha256"]):
            raise ValueError("altered/incomplete original evidence")
        seen.add(shard)
        for s, sha in m["market_hashes"].items():
            if s in sources or context["expected_market_sha256"].get(s) != sha: raise ValueError("original source mismatch")
            sources[s] = sha
        f = pd.read_csv(path)
        if len(f) != e["ledger_rows"]: raise ValueError("original row count mismatch")
        total += len(f); f = f[f.variant.isin(names)].copy(); validate_frame(f,"DEV",r["policies"])
        target = out/f"dev-{shard}"; target.mkdir(exist_ok=True); lp = target/"independent_candidates.csv.gz"
        f.to_csv(lp,index=False,compression=dict(method="gzip",mtime=0))
        bad = pd.read_csv(path.parent/"exclusions.csv"); bad = bad[bad.policy.isin(names)]
        bad.to_csv(target/"exclusions.csv",index=False)
        counts = {k:v for k,v in m["counts"].items() if any("/"+n+"/" in k for n in names)}
        write_json(target/"scan_meta.json",dict(complete=True,stage="DEV",shard=shard,
            ledger_sha256=digest(lp),ledger_rows=len(f),contract_sha256=digest(contract),
            original_ledger_sha256=e["ledger_sha256"],original_meta_git_blob_sha=e["scan_meta_git_blob_sha"],
            source_context_sha256=CONTEXT_SHA,market_hashes=m["market_hashes"],counts=counts,
            exclusions=len(bad),policies=r["policies"],status=DIAGNOSTIC))
    if seen != set(range(8)) or sources != context["expected_market_sha256"] or total != frozen["total_parameterized_outcomes"]:
        raise ValueError("incomplete original global universe")
    shutil.copyfile(contract,out/"diagnostic_registry.json")
    write_json(out/"original_dev_integrity.json",dict(status="PASS",original_rows=total,
        original_run_id=37559595896,shards=8,source_files=len(sources),diagnostic_status=DIAGNOSTIC))
    print("V26_ORIGINAL_DEV_HASH_AND_COHORT_PASS",total,flush=True)

def scan_gate(data,btc_path,out,cache,source_check,contract=REGISTRY):
    r = registry(contract); paths = sorted(data.rglob("*.csv.gz"))
    source,_,hashes = engine.verify_source(source_check,paths,btc_path,v25.CONTEXT)
    start,end = engine.source_helpers.interval("GATE")
    br,q,buy = v25.SOURCE_LOAD(btc_path,end)
    if not len(br[0]): raise ValueError("empty BTC")
    bf = v25.features(br,q,buy); out.mkdir(parents=True,exist_ok=True)
    engine.chronology.MINUTE_CACHE_DIR=cache
    engine.chronology.chronology.one_min=engine.audited_minutes
    engine.chronology.chronology.CACHE.clear();engine.official.INPUTS.clear()
    engine.minute_audit.MINUTE_INPUTS.clear();engine.minute_audit.MINUTE_SLICES.clear()
    engine.minute_audit.SLICE_DIR=out/"minute_evidence"
    engine.minute_audit.MINUTE_RAW_DIR=out/"minute_original_archives"
    engine.minute_audit.SLICE_DIR.mkdir(exist_ok=True);engine.minute_audit.MINUTE_RAW_DIR.mkdir(exist_ok=True)
    shutil.copyfile(source_check,out/"source_check.json");shutil.copyfile(contract,out/"diagnostic_registry.json")
    rows,counts,bad,coverage=[],Counter(),[],[]
    def save(complete):
        lp=out/"independent_candidates.csv.gz"
        pd.DataFrame(rows,columns=v25.COLUMNS).to_csv(lp,index=False,compression=dict(method="gzip",mtime=0))
        pd.DataFrame(bad,columns=["symbol","policy","entry_time","status"]).to_csv(out/"exclusions.csv",index=False)
        write_json(out/"minute_inputs.json",list(engine.minute_audit.MINUTE_INPUTS.values()))
        write_json(out/"minute_slices.json",list(engine.minute_audit.MINUTE_SLICES.values()))
        write_json(out/"scan_meta.json",dict(complete=complete,stage="GATE",shard=source["shards"][0],
            ledger_sha256=digest(lp),ledger_rows=len(rows),contract_sha256=digest(contract),
            source_context_sha256=CONTEXT_SHA,market_hashes=hashes,counts=dict(counts),
            exclusions=len(bad),policies=r["policies"],status=DIAGNOSTIC,coverage=coverage,
            btc_sha256=digest(btc_path),minute_months=len(engine.minute_audit.MINUTE_INPUTS),
            minute_official_checksums_verified=sum(bool(m.get("checksum_verified")) for m in engine.minute_audit.MINUTE_INPUTS.values())))
    try:
        for n,path in enumerate(paths,1):
            s=path.name[:-7];raw,q,buy=v25.SOURCE_LOAD(path,end)
            if not len(raw[0]) or raw[0][0]>=end-30*DAY:
                coverage.append(dict(symbol=s,status="NO_GATE_ELIGIBLE_HISTORY"));continue
            f=v25.features(raw,q,buy);btc=v25.align_context(raw[0],br,bf)
            found,excluded,mismatch=v25.policy_rows(s,r["policies"],raw,f,btc,start,end)
            for row in found:row["split"]="GATE"
            rows.extend(found);bad.extend(mismatch);counts.update({"GATE/"+k:v for k,v in excluded.items()})
            coverage.append(dict(symbol=s,status="SCANNED",outcomes=len(found)))
            engine.chronology.chronology.CACHE.clear()
            if n%16==0 or n==len(paths):save(False);print("V26_GATE_PROGRESS",n,len(paths),len(rows),flush=True)
    except BaseException:save(False);raise
    save(True);print("V26_GATE_DIAGNOSTIC_COMPLETE",len(rows),len(bad),flush=True)

def read_parts(parts,contract=REGISTRY):
    r,context=registry(contract),json.loads(v25.CONTEXT.read_text())
    paths=[p for folder in parts for p in folder.rglob("independent_candidates.csv.gz")]
    if len(paths)!=16:raise ValueError("expected eight DEV plus eight GATE shards")
    frames,seen,sources,counts=[],set(),{"DEV":{},"GATE":{}},Counter()
    for path in sorted(paths):
        m=json.loads((path.parent/"scan_meta.json").read_text());stage,shard=m["stage"],m["shard"]
        if stage not in sources or (stage,shard) in seen or shard not in range(8):raise ValueError("duplicate/unregistered diagnostic shard")
        if (not m["complete"] or m["status"]!=DIAGNOSTIC or m["policies"]!=r["policies"]
                or m["ledger_sha256"]!=digest(path) or m["contract_sha256"]!=digest(contract)
                or m["source_context_sha256"]!=CONTEXT_SHA):raise ValueError("altered/incomplete diagnostic inputs")
        seen.add((stage,shard))
        for s,sha in m["market_hashes"].items():
            if s in sources[stage] or context["expected_market_sha256"].get(s)!=sha:raise ValueError("inconsistent source catalogue")
            sources[stage][s]=sha
        f=pd.read_csv(path);validate_frame(f,stage,r["policies"])
        if len(f)!=m["ledger_rows"]:raise ValueError("diagnostic row mismatch")
        frames.append(f);counts.update(m["counts"])
    if seen!={(s,i) for s in sources for i in range(8)} or any(v!=context["expected_market_sha256"] for v in sources.values()):
        raise ValueError("incomplete diagnostic universe")
    f=pd.concat(frames,ignore_index=True)
    if f.duplicated(["symbol","variant","entry_time","split"]).any():raise ValueError("duplicate cross-shard rows")
    return f,counts

def prepare_market(data,btc,source_check,parts,out,contract=REGISTRY):
    f,_=read_parts(parts,contract);paths=sorted(data.rglob("*.csv.gz"))
    _,_,hashes=engine.verify_source(source_check,paths,btc,v25.CONTEXT)
    used=set(f.symbol)
    if not used.issubset(hashes):raise ValueError("missing account source")
    out.mkdir(parents=True,exist_ok=True)
    for path in paths:
        if path.name[:-7] in used:shutil.copyfile(path,out/path.name)
    write_json(out/"market_manifest.json",dict(status="VERIFIED",contract_sha256=digest(contract),
        source_context_sha256=CONTEXT_SHA,original_inventory=len(hashes),
        account_market_sha256={s:hashes[s] for s in sorted(used)},diagnostic_status=DIAGNOSTIC))
    print("V26_MARKET_UNIVERSE_VERIFIED",len(hashes),len(used),flush=True)

def cluster_stats(frame,pnl="net40_fraction",metric="net40_R"):
    if not len(frame):return dict(n=0,active_entry_dates=0,trade_weighted_mean=None,equal_date_mean=None,
        top5_positive_date_share=None,after_remove_best_date_mean=None,after_remove_best5_dates_mean=None,deletion_is_arithmetic_only=True)
    f=frame.copy();f["_date"]=(f.entry_time.to_numpy(np.int64)+account.KOREA_OFFSET)//DAY
    d=f.groupby("_date").agg(pnl=(pnl,"sum"),mean=(metric,"mean"),n=(metric,"size"))
    positive=d[d.pnl>0].sort_values("pnl",ascending=False,kind="stable")
    def without(k):
        kept=f[~f["_date"].isin(positive.index[:k])]
        return float(kept[metric].mean()) if len(kept) else None
    return dict(n=len(f),active_entry_dates=len(d),max_signals_on_date=int(d.n.max()),
        trade_weighted_mean=float(f[metric].mean()),equal_date_mean=float(d["mean"].mean()),
        top5_positive_date_share=float(positive.pnl.head(5).sum()/positive.pnl.sum()) if len(positive) else None,
        after_remove_best_date_mean=without(1),after_remove_best5_dates_mean=without(5),deletion_is_arithmetic_only=True)

def audit_account(r,tr,day,curve,start,end):
    pnl=tr.net_pnl.to_numpy() if len(tr) else np.array([])
    assert np.isclose(pnl.sum(),r["net_return_pct"]/100,atol=1e-10)
    observed=np.r_[1.,np.column_stack([curve.equity_pre_entry,curve.equity]).ravel()]
    peak=np.maximum.accumulate(observed)
    assert np.isclose(((peak-observed)/peak).max()*100,r["mdd_15m_pct"])
    if len(tr):
        assert tr.hold_min.between(0,10080,inclusive="right").all()
        assert (tr.notional/tr.entry_equity<=.300000001).all()
        assert (tr.reserved_risk/tr.entry_equity<=.005000001).all()
        pf=account.pf(pnl)
        assert (pf is None and r["pf"] is None) or np.isclose(pf,r["pf"])
    assert r["max_reserved_risk_at_entry_pct"]<=2.0000001 and curve.positions.max()<=6
    assert len(day)==account.korea_day(end-1)-account.korea_day(start)+1
    assert not day.day.duplicated().any()
    assert np.isclose(np.prod(1+day.return_pct/100),1+r["net_return_pct"]/100,atol=1e-10)
    assert np.isclose(day.covered_hours.sum(),(end-start)/3600000)
    assert np.isclose((day.return_pct>=.7).mean()*100,r["day_ge_0_7_pct"])
    assert np.isclose((day.return_pct>=2).mean()*100,r["day_ge_2_pct"])
    r["day_ge_1_pct"]=float((day.return_pct>=1).mean()*100)
    return dict(all_checks_passed=True,actual_max_hold_minutes=10080,calendar_days=len(day),calendar_compounding_reconciles=True)

def replay_probe(data,parts,out,probe_index,contract=REGISTRY):
    r=registry(contract)
    if probe_index not in range(8):raise ValueError("unregistered probe index")
    f,counts=read_parts(parts,contract);p=r["policies"][probe_index];ledger=f[f.variant==p["policy"]]
    m=json.loads((data/"market_manifest.json").read_text());context=json.loads(v25.CONTEXT.read_text())
    if m["contract_sha256"]!=digest(contract) or m["source_context_sha256"]!=CONTEXT_SHA:raise ValueError("altered market manifest")
    files={}
    for path in data.rglob("*.csv.gz"):
        s=path.name[:-7]
        if s in files:raise ValueError("duplicate account market file")
        files[s]=path
    if set(files)!=set(m["account_market_sha256"]):raise ValueError("incomplete market data")
    for s in set(ledger.symbol):
        if s not in files or digest(files[s])!=m["account_market_sha256"].get(s) or context["expected_market_sha256"].get(s)!=m["account_market_sha256"].get(s):
            raise ValueError("altered account source")
    market=account.Market({s:v25.SOURCE_LOAD(files[s],engine.base.GATE_END)[0] for s in sorted(set(ledger.symbol))})
    out.mkdir(parents=True,exist_ok=True);rows,periods,clusters,audits=[],[],[],[]
    for split in ("DEV","GATE"):
        start,end=engine.source_helpers.interval(split);sel=ledger[ledger.split==split]
        bad=sum(v for k,v in counts.items() if k.startswith(split+"/"+p["policy"]+"/") and k.rsplit("/",1)[-1] in {"DATA_GAP","ENTRY_MISMATCH","EXIT_MISMATCH"})
        clusters.append(dict(split=split,policy=p["policy"],scope="INDEPENDENT_CANONICAL_OUTCOMES",
            chronology_exclusions=bad,exclusion_rate=bad/(len(sel)+bad) if len(sel)+bad else 0.,**cluster_stats(sel)))
        for cost in (20,40):
            for guarded in (True,False):
                name=f'{split}__{p["policy"]}__{cost}bp__{"guarded" if guarded else "unguarded_diagnostic"}'
                result,tr,day,curve=account.simulate(sel,market,start,end,cost,guarded,all_kst_days=True)
                audit=audit_account(result,tr,day,curve,start,end)
                result.update(split=split,policy=p["policy"],probe_index=probe_index,independent_n=len(sel),diagnostic_status=DIAGNOSTIC)
                folder=out/"details"/name;folder.mkdir(parents=True,exist_ok=True)
                tr.to_csv(folder/"trades.csv.gz",index=False,compression="gzip")
                day.to_csv(folder/"daily.csv",index=False);curve.to_csv(folder/"curve.csv.gz",index=False,compression="gzip")
                audits.append(dict(scenario=name,**audit,sha256={n:digest(folder/n) for n in ("trades.csv.gz","daily.csv","curve.csv.gz")}))
                rows.append(result)
                if cost==40 and guarded:clusters.append(dict(split=split,policy=p["policy"],scope="EXECUTABLE_ACCOUNT_REALIZED_PNL",**cluster_stats(tr,"net_pnl","net_pnl")))
                dates=pd.to_datetime(day.day)
                for freq in ("Y","Q"):
                    for period,g in day.groupby(dates.dt.to_period(freq)):
                        periods.append(dict(scenario=name,split=split,policy=p["policy"],cost_bps=cost,guarded=guarded,
                            period=str(period),days=len(g),partial_days=int(g.partial_day.sum()),net_return_pct=float(100*(np.prod(1+g.return_pct/100)-1))))
                print("V26_ACCOUNT",name,json.dumps({k:result.get(k) for k in ("trades","net_return_pct","mdd_15m_pct","pf","day_ge_0_7_pct")}),flush=True)
    write_json(out/"summary.json",rows)
    pd.DataFrame([{k:v for k,v in r.items() if not isinstance(v,(dict,list))} for r in rows]).to_csv(out/"summary.csv",index=False)
    pd.DataFrame(periods).to_csv(out/"year_quarter.csv",index=False)
    write_json(out/"cluster_diagnostics.json",clusters);write_json(out/"audit.json",audits)
    write_json(out/"result_manifest.json",dict(status=DIAGNOSTIC,probe_index=probe_index,policy=p["policy"],
        contract_sha256=digest(contract),scenarios=8,all_checks_passed=True,v25_rejection_retained=True,qualified_candidates=0))

def collect(parts,out,contract=REGISTRY):
    r=registry(contract);paths=sorted(parts.rglob("result_manifest.json"))
    if len(paths)!=8:raise ValueError("incomplete account probes")
    seen,rows,periods,clusters,audits=set(),[],[],[],[]
    for path in paths:
        m=json.loads(path.read_text());i=m["probe_index"]
        if (i in seen or i not in range(8) or m["policy"]!=r["policies"][i]["policy"] or m["contract_sha256"]!=digest(contract)
                or m["status"]!=DIAGNOSTIC or m["scenarios"]!=8 or not m["all_checks_passed"] or not m["v25_rejection_retained"]):
            raise ValueError("altered/missing account probe")
        seen.add(i);original=json.loads((path.parent/"summary.json").read_text())
        if len(original)!=8:raise ValueError("missing scenario")
        rows.extend(original);periods.extend(pd.read_csv(path.parent/"year_quarter.csv").to_dict("records"))
        clusters.extend(json.loads((path.parent/"cluster_diagnostics.json").read_text()))
        for a in json.loads((path.parent/"audit.json").read_text()):
            if not a["all_checks_passed"]:raise ValueError("failed account arithmetic")
            for name,sha in a["sha256"].items():
                if digest(path.parent/"details"/a["scenario"]/name)!=sha:raise ValueError("altered scenario evidence")
            audits.append(a)
    table=pd.DataFrame([{k:v for k,v in r.items() if not isinstance(v,(dict,list))} for r in rows])
    expected={(p["policy"],s,c,g) for p in r["policies"] for s in ("DEV","GATE") for c in (20,40) for g in (True,False)}
    actual=set(map(tuple,table[["policy","split","cost_bps","guarded"]].to_numpy()))
    if len(table)!=64 or actual!=expected or len(audits)!=64:raise ValueError("incomplete/duplicate scenario matrix")
    out.mkdir(parents=True,exist_ok=True)
    table.to_csv(out/"summary.csv",index=False);pd.DataFrame(periods).to_csv(out/"year_quarter.csv",index=False)
    write_json(out/"summary.json",rows);write_json(out/"cluster_diagnostics.json",clusters);write_json(out/"audit.json",audits)
    write_json(out/"decision.json",dict(status=DIAGNOSTIC,scenarios=64,v25_rejection_retained=True,
        qualified_candidates=0,account_daily_target_claim=False,note="Post-V25 exploratory diagnosis on observed history; no automatic qualification."))
    print("V26_64_SCENARIOS_HASH_AND_ARITHMETIC_PASS",flush=True)

def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest="command",required=True)
    for command,names in [("prepare-dev",("parts","out")),("scan-gate",("data","btc","source-check","out","minute-cache")),
                          ("prepare-market",("data","btc","source-check","out")),("accounts",("data","out")),("collect",("parts","out"))]:
        p=sub.add_parser(command)
        for name in names:p.add_argument("--"+name,type=Path,required=True)
        if command in {"prepare-market","accounts"}:p.add_argument("--parts",type=Path,nargs="+",required=True)
        if command=="accounts":p.add_argument("--probe-index",type=int,required=True)
    a=ap.parse_args()
    if a.command=="prepare-dev":prepare_dev(a.parts,a.out)
    elif a.command=="scan-gate":scan_gate(a.data,a.btc,a.out,a.minute_cache,a.source_check)
    elif a.command=="prepare-market":prepare_market(a.data,a.btc,a.source_check,a.parts,a.out)
    elif a.command=="accounts":replay_probe(a.data,a.parts,a.out,a.probe_index)
    else:collect(a.parts,a.out)

if __name__=="__main__":main()
