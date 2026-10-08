import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import daily_breakdown_intraday_v27 as v

def raw_fixture():
    n=110;t=v.engine.base.START+np.arange(n,dtype=np.int64)*v.BAR
    o=np.full(n,100.);h=o+.1;l=o-.1;c=o.copy()
    return t,o,h,l,c

def seed(raw):
    return dict(symbol="XUSDT",side=-1,entry_index=0,entry_time=int(raw[0][0]),
        entry=100.,sl=110.,score=2.,decision_time=int(raw[0][0]),key=v.configurations()[0]["key"])

def ledger_row(symbol,start,exit_time,score=1.):
    return dict(symbol=symbol,side=-1,entry_time=start,exit_time=exit_time,
        entry=100.,exit=100.,sl=110.,tp=80.,score=score,reason="TIME",max_hold_bars=96)

def market_fixture(start,end,symbols):
    t=np.arange(start,end+v.BAR,v.BAR,dtype=np.int64);p=np.full(len(t),100.)
    return v.account.Market({s:(t,p,p+.1,p-.1,p) for s in symbols})

class IntradayV27Tests(unittest.TestCase):
    def test_whole_family_and_matrix_frozen(self):
        r=v.registry();self.assertEqual(len(r["policies"]),16)
        self.assertEqual(r["scenarios"],384);self.assertEqual(r["daily_caps"],[None,1,2])
        self.assertEqual({p["delay_bars"] for p in r["policies"]},{0,1})
        self.assertTrue(all(p["hold"]==96 and p["side"]==-1 and p["exit_type"]=="R20" for p in r["policies"]))

    def test_registry_rejects_changed_horizon(self):
        with tempfile.TemporaryDirectory() as temp:
            r=json.loads(v.REGISTRY.read_text());r["policies"][0]["hold"]=672
            p=Path(temp)/"registry.json";v.write_json(p,r)
            with self.assertRaises(ValueError):v.registry(p)

    def test_immediate_entry_preserves_stop_and_target(self):
        raw=raw_fixture();tr,error=v.delayed_seed(seed(raw),raw,0,int(raw[0][-1]+v.BAR))
        self.assertIsNone(error);self.assertEqual((tr["sl"],tr["tp"]),(110.,80.))
        self.assertEqual(tr["nominal_entry_time"],tr["entry_time"])

    def test_delayed_fill_keeps_original_stop(self):
        raw=raw_fixture();raw[1][1]=95.;raw[2][1]=95.1;raw[3][1]=94.9
        tr,error=v.delayed_seed(seed(raw),raw,1,int(raw[0][-1]+v.BAR))
        self.assertIsNone(error);self.assertEqual(tr["sl"],110.)
        self.assertEqual(tr["tp"],65.);self.assertEqual(tr["risk_pct"],15/95)
        self.assertEqual(tr["score"],2.);self.assertEqual(tr["entry_time"],int(raw[0][1]))

    def test_stop_touched_while_waiting_cancels_even_after_recovery(self):
        raw=raw_fixture();raw[2][0]=110.;raw[1][1]=99.
        tr,error=v.delayed_seed(seed(raw),raw,1,int(raw[0][-1]+v.BAR))
        self.assertIsNone(tr);self.assertEqual(error,"DELAY_STOP_INVALIDATED")

    def test_adverse_delayed_open_is_not_rebased(self):
        raw=raw_fixture();raw[1][1]=111.
        tr,error=v.delayed_seed(seed(raw),raw,1,int(raw[0][-1]+v.BAR))
        self.assertIsNone(tr);self.assertEqual(error,"DELAY_STOP_INVALIDATED")

    def test_gap_in_delay_path_is_excluded(self):
        raw=raw_fixture();raw[0][1]+=v.BAR
        self.assertEqual(v.delayed_seed(seed(raw),raw,1,int(raw[0][-1]))[1],"DELAY_PATH_GAP")

    def test_delayed_entry_at_split_end_is_not_filled(self):
        raw=raw_fixture()
        self.assertEqual(v.delayed_seed(seed(raw),raw,1,int(raw[0][1]))[1],"DELAY_OUTSIDE_SPLIT")

    def test_future_waiting_bars_do_not_change_immediate_intent(self):
        raw=raw_fixture();first=v.delayed_seed(seed(raw),raw,0,int(raw[0][-1]))[0]
        raw[2][1:]=10000;raw[3][1:]=.001
        second=v.delayed_seed(seed(raw),raw,0,int(raw[0][-1]))[0]
        self.assertEqual(first,second)

    def test_24h_timeout_from_actual_delayed_fill(self):
        raw=raw_fixture();tr,_=v.delayed_seed(seed(raw),raw,1,int(raw[0][-1]+v.BAR))
        result=v.engine.canonical.resolve(tr,raw,{},"TP2",int(raw[0][-1]+v.BAR))
        self.assertEqual(result["exit_time"]-tr["entry_time"],v.DAY)
        self.assertEqual(result["reason"],"TIME")

    def test_parent_collision_calls_official_minute_authority(self):
        raw=raw_fixture();tr,_=v.delayed_seed(seed(raw),raw,1,int(raw[0][-1]+v.BAR))
        raw[2][2]=111.;raw[3][2]=79.
        with patch.object(v.engine.canonical.chrono,"resolve_minutes",return_value=("SL",int(raw[0][2]+60000),110.)) as resolve:
            result=v.engine.canonical.resolve(tr,raw,{},"TP2",int(raw[0][-1]+v.BAR))
        self.assertEqual(result["reason"],"SL");self.assertTrue(resolve.called)

    def test_entry_bar_tp_only_uses_minute_authority(self):
        raw=raw_fixture();tr,_=v.delayed_seed(seed(raw),raw,0,int(raw[0][-1]+v.BAR))
        raw[3][0]=79.
        with patch.object(v.engine.canonical.chrono,"resolve_minutes",return_value=("SL",int(raw[0][0]+60000),110.)) as resolve:
            result=v.engine.canonical.resolve(tr,raw,{},"TP2",int(raw[0][-1]+v.BAR))
        self.assertEqual(result["reason"],"SL");self.assertTrue(resolve.call_args.args[-1])

    def test_minute_data_gap_propagates_as_exclusion(self):
        raw=raw_fixture();tr,_=v.delayed_seed(seed(raw),raw,0,int(raw[0][-1]+v.BAR));raw[3][0]=79.
        with patch.object(v.engine.canonical.chrono,"resolve_minutes",return_value=("DATA_GAP",None,None)):
            result=v.engine.canonical.resolve(tr,raw,{},"TP2",int(raw[0][-1]+v.BAR))
        self.assertEqual(result["status"],"DATA_GAP")

    def test_actual_entry_cap_and_known_score_order(self):
        start=v.engine.base.START;end=start+2*v.DAY
        ledger=pd.DataFrame([ledger_row(s,start,start+v.BAR,score)
            for s,score in (("X",1.),("Z",3.),("A",3.))])
        market=market_fixture(start,end,["X","Z","A"])
        r,tr,day,curve=v.account.simulate(ledger,market,start,end,40,False,True,1)
        self.assertEqual(tr.symbol.tolist(),["A"])
        self.assertEqual(r["rejections"]["day_entry_cap"],2)
        v.audit_account(r,tr,day,curve,start,end,1)

    def test_rejected_same_symbol_does_not_consume_quota(self):
        start=v.engine.base.START;end=start+2*v.DAY
        ledger=pd.DataFrame([ledger_row("A",start,start+4*v.BAR,3.),
            ledger_row("A",start+v.BAR,start+3*v.BAR,3.),
            ledger_row("B",start+v.BAR,start+3*v.BAR,1.)])
        r,tr,day,curve=v.account.simulate(ledger,market_fixture(start,end,["A","B"]),start,end,40,False,True,2)
        self.assertEqual(set(tr.symbol),{"A","B"});self.assertEqual(r["rejections"]["same_symbol"],1)
        self.assertEqual(int(day.entries.sum()),2)

    def test_kst_midnight_reset_with_open_position(self):
        midnight=v.engine.base.START+15*3600000;start=midnight-v.BAR;end=midnight+v.DAY
        ledger=pd.DataFrame([ledger_row("A",start,midnight+v.BAR),
                             ledger_row("B",midnight,midnight+2*v.BAR)])
        r,tr,day,curve=v.account.simulate(ledger,market_fixture(start,end,["A","B"]),start,end,40,False,True,1)
        self.assertEqual(len(tr),2);self.assertTrue(day.entries.le(1).all())
        self.assertEqual(r["max_concurrent"],2)

    def test_unlimited_optional_argument_keeps_default_results(self):
        start=v.engine.base.START;end=start+2*v.DAY
        ledger=pd.DataFrame([ledger_row("A",start,start+v.BAR),ledger_row("B",start,start+v.BAR)])
        market=market_fixture(start,end,["A","B"])
        a=v.account.simulate(ledger,market,start,end,40,True,True)
        b=v.account.simulate(ledger,market,start,end,40,True,True,max_entries_per_kst_day=None)
        self.assertEqual(a[0],b[0])
        for x,y in zip(a[1:],b[1:]):pd.testing.assert_frame_equal(x,y)

    def test_invalid_cap_is_rejected(self):
        for cap in (0,-1,True,1.5,"1"):
            with self.subTest(cap=cap),self.assertRaises(ValueError):
                v.account.simulate(pd.DataFrame(),None,0,v.DAY,max_entries_per_kst_day=cap)

    def test_24h_audit_rejects_week_holding_or_over_quota(self):
        start=v.engine.base.START;end=start+2*v.DAY
        ledger=pd.DataFrame([ledger_row("A",start,start+v.BAR),ledger_row("B",start,start+v.BAR)])
        result=v.account.simulate(ledger,market_fixture(start,end,["A","B"]),start,end,40,False,True)
        r,tr,day,curve=result
        with self.assertRaises(AssertionError):v.audit_account(r,tr.assign(hold_min=1441),day,curve,start,end,2)
        with self.assertRaises(AssertionError):v.audit_account(r,tr,day,curve,start,end,1)

    def test_missing_scan_or_result_matrix_aborts(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            with self.assertRaises(ValueError):v.read_parts([p])
            with self.assertRaises(ValueError):v.collect(p,p/"out")

    def test_minute_scope_restores_shared_state_even_after_error(self):
        before=v.engine.chronology.chronology.one_min
        directory=v.engine.minute_audit.SLICE_DIR
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RuntimeError):
                with v.minute_scope(Path(temp)/"cache",Path(temp)):raise RuntimeError("fixture")
        self.assertIs(v.engine.chronology.chronology.one_min,before)
        self.assertEqual(v.engine.minute_audit.SLICE_DIR,directory)

    def screen_fixture(self):
        rows,years,clusters=[],[],[]
        for p in v.policies()[:2]:
            for split in ("DEV","GATE"):
                for cost in (20,40):
                    row=dict(entry_key=p["key"],policy=p["policy"],split=split,daily_cap="1",
                        cost_bps=cost,guarded=True,trades=100,mdd_15m_pct=5.,halt_time=None,
                        cagr_pct=25.,pf=1.5,net_return_pct=25.,daily_mean_pct=.1)
                    rows.append(row)
                    for year in ("2022","2023") if split=="DEV" else ("2024",):
                        years.append(dict(policy=p["policy"],split=split,daily_cap="1",guarded=True,
                            cost_bps=cost,period=year,net_return_pct=10.))
                clusters.append(dict(scope="EXECUTABLE_ACCOUNT_REALIZED_PNL",policy=p["policy"],
                    split=split,daily_cap="1",after_remove_best5_dates_mean=.001,
                    top5_positive_date_share=.2))
        # A real halt in another row makes pandas convert the no-halt values to NaN.
        rows.append(dict(rows[0],daily_cap="UNLIMITED",halt_time=123))
        return rows,years,clusters

    def test_screen_handles_missing_halt_without_false_failure(self):
        flags=v.material_screen(*self.screen_fixture())
        selected=next(f for f in flags if f["entry_key"]==v.configurations()[0]["key"] and f["daily_cap"]=="1")
        self.assertTrue(selected["further_audit_flag"]);self.assertFalse(selected["daily_mean_goal_met"])

    def test_negative_year_blocks_positive_aggregate(self):
        rows,years,clusters=self.screen_fixture();years[1]["net_return_pct"]=-1.
        selected=next(f for f in v.material_screen(rows,years,clusters)
            if f["entry_key"]==v.configurations()[0]["key"] and f["daily_cap"]=="1")
        self.assertFalse(selected["further_audit_flag"])
        self.assertIn("NONPOSITIVE_OR_MISSING_YEAR_2023",selected["failed_conditions"])

if __name__=="__main__":unittest.main()
