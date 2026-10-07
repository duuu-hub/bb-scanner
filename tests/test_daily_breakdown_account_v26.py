import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import daily_breakdown_account_v26 as v

def intent(symbol="XUSDT",stage="DEV"):
    p=v.probes()[0];start,end=v.engine.source_helpers.interval(stage)
    return dict(symbol=symbol,variant=p["policy"],policy=p["policy"],split=stage,
        entry_time=start+v.DAY,exit_time=start+2*v.DAY,entry=100.,exit=100.,sl=110.,tp=80.,
        side=-1,status="RESOLVED",max_hold_bars=672,score=1.,reason="TIMEOUT",
        net40_fraction=-.0042,net40_R=-.04)

class V26Tests(unittest.TestCase):
    def test_registered_whole_short_slice(self):
        p=v.registry()["policies"]
        self.assertEqual(len(p),8)
        self.assertEqual({(r["channel_days"],r["btc_regime"],r["stop_atr"]) for r in p},
            {(d,b,a) for d in (5,20) for b in ("ANY","ALIGN20") for a in (1.5,2.5)})
        self.assertTrue(all(r["side"]==-1 and r["hold"]==672 and r["exit_type"]=="R20" for r in p))

    def test_registry_rejects_posthoc_threshold_change(self):
        with tempfile.TemporaryDirectory() as temp:
            r=json.loads(v.REGISTRY.read_text());r["policies"][0]["stop_atr"]=1.
            p=Path(temp)/"registry.json";v.write_json(p,r)
            with self.assertRaises(ValueError):v.registry(p)

    def test_valid_actual_risk_row_and_independent_exit(self):
        f=pd.DataFrame([intent()])
        v.validate_frame(f,"DEV",v.probes())
        with self.assertRaises(ValueError):
            v.validate_frame(f.assign(tp=90.),"DEV",v.probes())

    def test_split_chronology_and_week_budget(self):
        f=pd.DataFrame([intent()]);end=v.engine.base.DEV_END
        for altered in (f.assign(split="GATE"),f.assign(entry_time=end),f.assign(exit_time=end+v.BAR),
                        f.assign(exit_time=f.entry_time),f.assign(exit_time=f.entry_time+673*v.BAR),
                        f.assign(max_hold_bars=96),f.assign(side=1),f.assign(status="DATA_GAP")):
            with self.subTest(altered=altered.to_dict("records")),self.assertRaises(ValueError):
                v.validate_frame(altered,"DEV",v.probes())

    def test_duplicate_canonical_intents_rejected(self):
        f=pd.DataFrame([intent(),intent()])
        with self.assertRaises(ValueError):v.validate_frame(f,"DEV",v.probes())

    def test_missing_original_and_combined_shards_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            with self.assertRaises(ValueError):v.prepare_dev(p,p/"out")
            with self.assertRaises(ValueError):v.read_parts([p])

    def original_fixture(self,temp):
        root=Path(temp);parts=root/"original";out=root/"filtered"
        sources={f"X{i}USDT":str(i)*64 for i in range(8)}
        context=root/"context.json";v.write_json(context,dict(expected_market_sha256=sources,btc_sha256="a"*64))
        sha=v.digest(context);reg=json.loads(v.REGISTRY.read_text());reg["source_context_sha256"]=sha
        registry=root/"registry.json";v.write_json(registry,reg);expected=[]
        for i in range(8):
            folder=parts/str(i);folder.mkdir(parents=True)
            path=folder/"independent_candidates.csv.gz"
            pd.DataFrame([intent(f"X{i}USDT")]).to_csv(path,index=False,compression=dict(method="gzip",mtime=0))
            pd.DataFrame(columns=["symbol","policy","entry_time","status"]).to_csv(folder/"exclusions.csv",index=False)
            m=dict(complete=True,stage="DEV",shard=i,policies=v.v25.policies(),ledger_sha256=v.digest(path),
                ledger_rows=1,source_context_sha256=sha,btc_sha256="a"*64,market_hashes={f"X{i}USDT":sources[f"X{i}USDT"]},counts={})
            v.write_json(folder/"scan_meta.json",m)
            expected.append(dict(shard=i,ledger_sha256=v.digest(path),ledger_rows=1,
                                 scan_meta_git_blob_sha=v.git_blob_sha(folder/"scan_meta.json")))
        frozen=root/"frozen.json";v.write_json(frozen,dict(expected_shards=expected,total_parameterized_outcomes=8))
        return parts,out,context,sha,registry,frozen

    def test_original_eight_shard_filter_preserves_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            parts,out,context,sha,registry,frozen=self.original_fixture(temp)
            with patch.object(v,"CONTEXT_SHA",sha),patch.object(v.v25,"CONTEXT",context):
                v.prepare_dev(parts,out,registry,frozen)
            meta=json.loads((out/"dev-0/scan_meta.json").read_text())
            self.assertEqual(meta["status"],v.DIAGNOSTIC)
            self.assertEqual(meta["original_ledger_sha256"],json.loads(frozen.read_text())["expected_shards"][0]["ledger_sha256"])
            self.assertEqual(len(list(out.rglob("independent_candidates.csv.gz"))),8)

    def test_original_tampering_fails_before_replay(self):
        for kind in ("ledger","meta"):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                parts,out,context,sha,registry,frozen=self.original_fixture(temp)
                path=parts/"0"/("independent_candidates.csv.gz" if kind=="ledger" else "scan_meta.json")
                path.write_bytes(path.read_bytes()+b"tamper")
                with patch.object(v,"CONTEXT_SHA",sha),patch.object(v.v25,"CONTEXT",context),self.assertRaises((ValueError,json.JSONDecodeError)):
                    v.prepare_dev(parts,out,registry,frozen)

    def test_new_gate_listing_is_not_discarded_by_dev_listing_cutoff(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);data=root/"data";data.mkdir()
            n=64*96;t=v.engine.base.DEV_END+np.arange(n,dtype=np.int64)*v.BAR;c=np.full(n,100.)
            frame=pd.DataFrame(dict(open_time=t,open=c,high=c+1,low=c-1,close=c,
                quote_volume=np.full(n,1e6),taker_buy_quote=np.full(n,5e5)))
            frame.to_csv(data/"NEWUSDT.csv.gz",index=False,compression="gzip")
            btc=root/"BTC.csv.gz";frame.to_csv(btc,index=False,compression="gzip")
            source=root/"source.json";v.write_json(source,{})
            with patch.object(v.engine,"verify_source",return_value=({"shards":[0]}, {}, {"NEWUSDT":"x"})):
                v.scan_gate(data,btc,root/"out",root/"cache",source)
            m=json.loads((root/"out/scan_meta.json").read_text())
            self.assertEqual(m["coverage"][0]["status"],"SCANNED")
            self.assertEqual(m["stage"],"GATE")
            self.assertTrue(m["complete"])

    def test_trade_count_and_date_count_expose_clustering(self):
        start=v.engine.base.START
        rows=[dict(entry_time=start,net40_fraction=.01,net40_R=1.) for _ in range(10)]
        rows.append(dict(entry_time=start+v.DAY,net40_fraction=-.02,net40_R=-2.))
        got=v.cluster_stats(pd.DataFrame(rows))
        self.assertAlmostEqual(got["trade_weighted_mean"],8/11)
        self.assertAlmostEqual(got["equal_date_mean"],-.5)
        self.assertEqual(got["after_remove_best_date_mean"],-2.)
        self.assertEqual(got["active_entry_dates"],2)
        self.assertEqual(got["top5_positive_date_share"],1.)
        self.assertTrue(got["deletion_is_arithmetic_only"])

    def test_kst_dates_at_utc_fifteen_hour_boundary(self):
        start=v.engine.base.START
        f=pd.DataFrame([dict(entry_time=start+15*3600000-v.BAR,net40_fraction=.01,net40_R=1.),
                        dict(entry_time=start+15*3600000,net40_fraction=-.02,net40_R=-2.)])
        self.assertEqual(v.cluster_stats(f)["active_entry_dates"],2)

    def test_no_positive_date_or_zero_trades_has_no_fake_concentration(self):
        self.assertIsNone(v.cluster_stats(pd.DataFrame())["trade_weighted_mean"])
        f=pd.DataFrame([dict(entry_time=v.engine.base.START,net40_fraction=-.01,net40_R=-1.)])
        r=v.cluster_stats(f)
        self.assertIsNone(r["top5_positive_date_share"])
        self.assertEqual(r["after_remove_best5_dates_mean"],-1.)

    def account_fixture(self):
        start=v.engine.base.START;end=start+8*v.DAY;t=np.arange(start,end+v.BAR,v.BAR,dtype=np.int64)
        c=np.full(len(t),100.);market=v.account.Market({"XUSDT":(t,c,c+1,c-1,c)})
        row=intent();row.update(entry_time=start,exit_time=start+7*v.DAY)
        return start,end,v.account.simulate(pd.DataFrame([row]),market,start,end,40,True,all_kst_days=True)

    def test_real_account_seven_day_trade_budget_cash_and_calendar(self):
        start,end,(r,tr,day,curve)=self.account_fixture()
        self.assertEqual(tr.hold_min.iloc[0],10080)
        audit=v.audit_account(r,tr,day,curve,start,end)
        self.assertTrue(audit["all_checks_passed"])
        self.assertEqual(len(day),9)
        self.assertAlmostEqual(day.covered_hours.sum(),192)
        self.assertEqual(r["day_ge_1_pct"],0.)

    def test_split_end_flat_price_still_charges_adverse_fill_and_fees(self):
        row=intent();start=v.engine.base.START;end=start+v.DAY
        t=np.arange(start,end+v.BAR,v.BAR,dtype=np.int64);c=np.full(len(t),100.)
        row.update(entry_time=start,exit_time=end,reason="SPLIT_END")
        r,tr,day,curve=v.account.simulate(pd.DataFrame([row]),
            v.account.Market({"XUSDT":(t,c,c,c,c)}),start,end,40,True,all_kst_days=True)
        result=tr.iloc[0]
        self.assertAlmostEqual(result.exit,100.1)
        expected=-result.notional*.001-result.entry_fee-result.exit_fee-result.funding
        self.assertAlmostEqual(result.net_pnl,expected)
        v.audit_account(r,tr,day,curve,start,end)

    def test_account_audit_rejects_cash_or_calendar_corruption(self):
        start,end,(r,tr,day,curve)=self.account_fixture()
        bad=dict(r);bad["net_return_pct"]+=1
        with self.assertRaises(AssertionError):v.audit_account(bad,tr,day,curve,start,end)
        with self.assertRaises(AssertionError):v.audit_account(r,tr,day.iloc[1:],curve,start,end)
        with self.assertRaises(AssertionError):v.audit_account(r,tr.assign(hold_min=10081),day,curve,start,end)

    def test_incomplete_64_scenario_result_set_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            with self.assertRaises(ValueError):v.collect(p,p/"out")

    def global_fixture(self,temp):
        root=Path(temp);paths=[];hashes={}
        for i in range(8):
            p=root/f"X{i}USDT.csv.gz";p.write_bytes(b"known input"+bytes([i]));paths.append(p);hashes[f"X{i}USDT"]=v.digest(p)
        btc=root/"btc.bin";btc.write_bytes(b"BTC")
        ctx=root/"context.json";v.write_json(ctx,dict(expected_market_sha256=hashes,btc_sha256=v.digest(btc),baseline_sha256="a"*64))
        source=root/"source.json";v.write_json(source,dict(status="VERIFIED",shards=list(range(8)),
            baseline_sha256="a"*64,files=[dict(symbol=s,sha256=h) for s,h in hashes.items()]))
        return source,paths,btc,ctx,hashes

    def test_actual_eight_shard_market_adapter_accepts_complete_catalogue(self):
        with tempfile.TemporaryDirectory() as temp:
            source,paths,btc,ctx,hashes=self.global_fixture(temp)
            self.assertEqual(v.verify_full_market(source,paths,btc,ctx),hashes)
            with self.assertRaises(ValueError):v.engine.verify_source(source,paths,btc,ctx)

    def test_global_catalogue_detects_shard_omission_bytes_duplicate_and_btc(self):
        for problem in ("shards","duplicate","missing","data_bytes","btc","baseline"):
            with self.subTest(problem=problem),tempfile.TemporaryDirectory() as temp:
                source,paths,btc,ctx,_=self.global_fixture(temp);s=json.loads(source.read_text())
                if problem=="shards":s["shards"]=list(range(7))
                if problem=="duplicate":s["files"].append(s["files"][0])
                if problem=="missing":paths=paths[:-1]
                if problem=="data_bytes":paths[0].write_bytes(b"changed")
                if problem=="btc":btc.write_bytes(b"changed")
                if problem=="baseline":s["baseline_sha256"]="b"*64
                v.write_json(source,s)
                with self.assertRaises(ValueError):v.verify_full_market(source,paths,btc,ctx)

    def reused_fixture(self,temp):
        root=Path(temp);parts=root/"parts";parts.mkdir()
        f=pd.DataFrame([intent(stage="GATE")]);lp=parts/"independent_candidates.csv.gz"
        f.to_csv(lp,index=False,compression=dict(method="gzip",mtime=0))
        m=dict(complete=True,stage="GATE",shard=0,policies=v.probes(),ledger_rows=1,
            contract_sha256=v.digest(v.REGISTRY),source_context_sha256=v.CONTEXT_SHA)
        mp=parts/"scan_meta.json";v.write_json(mp,m)
        reuse=root/"reuse.json";v.write_json(reuse,dict(source_run_id=37588740960,contract_sha256=v.digest(v.REGISTRY),
            expected_shards=[dict(shard=0,ledger_sha256=v.digest(lp),ledger_rows=1,scan_meta_git_blob_sha=v.git_blob_sha(mp))]))
        return parts,reuse,lp,mp

    def test_reused_original_gate_has_exact_byte_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            parts,reuse,_,_=self.reused_fixture(temp);v.verify_reused_gate(parts,reuse)

    def test_reused_original_gate_rejects_changed_ledger_meta_and_run(self):
        for problem in ("ledger","meta","run"):
            with self.subTest(problem=problem),tempfile.TemporaryDirectory() as temp:
                parts,reuse,lp,mp=self.reused_fixture(temp)
                if problem=="ledger":lp.write_bytes(lp.read_bytes()+b"changed")
                if problem=="meta":
                    m=json.loads(mp.read_text());m["complete"]=False;v.write_json(mp,m)
                if problem=="run":
                    m=json.loads(reuse.read_text());m["source_run_id"]=2;v.write_json(reuse,m)
                with self.assertRaises(ValueError):v.verify_reused_gate(parts,reuse)

if __name__=="__main__":unittest.main()
