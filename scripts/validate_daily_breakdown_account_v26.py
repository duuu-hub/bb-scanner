"""V26 fixed exploratory cohort plus all previous research regressions."""
import argparse,hashlib,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts import daily_breakdown_account_v26 as v
NEW=['test_daily_breakdown_account_v26.py']
OLD=['test_daily_channel_breakout_v25.py', 'test_failed_resumption_reversal_v24.py', 'test_path_efficiency_resumption_v23.py', 'test_fragmented_chase_exhaustion_v22.py', 'test_mark_price_archive_v21.py', 'test_contract_mark_dislocation_v21.py', 'test_session_opening_range_sweep_v20.py', 'test_session_opening_impulse_v18.py', 'test_session_vwap_failed_auction_v17.py', 'test_breadth_pullback_reclaim_v16.py', 'test_breadth_expansion_continuation_v15.py', 'test_market_breadth_recovery_v14.py', 'test_breakout_level_retest_v13.py', 'test_taker_absorption_release_v12.py', 'test_aggressive_flow_cascade_v11.py', 'test_residual_reversion_v10.py', 'test_btc_factor_lag_v6.py', 'test_compression_expansion_v4.py', 'test_cross_sectional_leader_v9.py', 'test_day_edge_canonical.py', 'test_day_edge_lab.py', 'test_funding_crowding_v7.py', 'test_liquidity_sweep_v5.py', 'test_premium_absorption_v8.py', 'test_relative_pullback_v1.py', 'test_research_evidence_archive.py', 'test_saved_calendar_audit.py', 'test_shock_confirmation_v3.py']
ap=argparse.ArgumentParser();ap.add_argument('--only-new',action='store_true');a=ap.parse_args()
assert v.registry()['plan_commit']=='fe223bc5fe375097cb342c6d85cd6b42222d15d5'
assert v.digest(v.REGISTRY)=='28f06bf8c0173c33f11d16350a4655122849ff5ed0609146f368e7af808a14a7'
assert v.digest(v.FROZEN)=='0a465ae66d8e131951542055a89464362a74d60247185351a3ec6e41a9b697ea'
f=json.loads(v.FROZEN.read_text());assert f['original_run_id']==37559595896 and f['total_parameterized_outcomes']==327848
assert {m['shard'] for m in f['expected_shards']}==set(range(8))
assert (v.STUDY/'PLAN.md').is_file() and (v.STUDY/'PREMARKET_EXECUTION_ADDENDUM.md').is_file()
baseline=ROOT/'research/path-efficiency-resumption-v23/FROZEN_INPUT_HASHES.json'
assert v.digest(baseline)=='0c3f09f41127e29cbf7130891e9f2c1389c99798d43e470825c973acbcd5121f'
assert len(json.loads(v.v25.CONTEXT.read_text())['expected_market_sha256'])==856
print('V26_FROZEN_COHORT_AND_ORIGINAL_LEDGER_CONTRACT_PASS',flush=True)
suite=unittest.TestSuite()
for name in NEW if a.only_new else NEW+OLD:suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=name))
expected=16 if a.only_new else 589
assert suite.countTestCases()==expected,(suite.countTestCases(),expected)
print('V26_REGISTERED_SUITE_SIZE',suite.countTestCases(),flush=True)
r=unittest.TextTestRunner(verbosity=2,stream=sys.__stderr__).run(suite)
print('V26_TEST_RESULT',r.testsRun,len(r.failures),len(r.errors),r.wasSuccessful(),flush=True)
sys.exit(0 if r.wasSuccessful() else 1)
