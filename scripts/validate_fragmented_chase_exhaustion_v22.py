"""Frozen V22 pre-market suite; validation output is not profitability evidence."""
import argparse,hashlib,json,sys,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
NEW_TEST_FILES=['test_fragmented_chase_exhaustion_v22.py']
TEST_FILES=NEW_TEST_FILES+['test_mark_price_archive_v21.py','test_contract_mark_dislocation_v21.py',
    'test_session_opening_range_sweep_v20.py','test_session_opening_impulse_v18.py',
    'test_session_vwap_failed_auction_v17.py','test_breadth_pullback_reclaim_v16.py',
    'test_breadth_expansion_continuation_v15.py','test_market_breadth_recovery_v14.py',
    'test_breakout_level_retest_v13.py','test_taker_absorption_release_v12.py',
    'test_aggressive_flow_cascade_v11.py','test_residual_reversion_v10.py',
    'test_btc_factor_lag_v6.py','test_compression_expansion_v4.py',
    'test_cross_sectional_leader_v9.py','test_day_edge_canonical.py','test_day_edge_lab.py',
    'test_funding_crowding_v7.py','test_liquidity_sweep_v5.py','test_premium_absorption_v8.py',
    'test_relative_pullback_v1.py','test_research_evidence_archive.py','test_saved_calendar_audit.py',
    'test_shock_confirmation_v3.py']

ap=argparse.ArgumentParser();ap.add_argument('--only-new',action='store_true');args=ap.parse_args()
p=ROOT/'research/fragmented-chase-exhaustion-v22/FROZEN_INPUT_HASHES.json';d=json.loads(p.read_text())
assert d['source_data_run']==36095439671 and len(d['expected_csv_sha256'])==256
assert hashlib.sha256(p.read_bytes()).hexdigest()=='0c3f09f41127e29cbf7130891e9f2c1389c99798d43e470825c973acbcd5121f'
cp=p.parent/'FROZEN_CONTEXT.json';context=json.loads(cp.read_text())
assert len(context['expected_market_sha256'])==856
assert context['study']=='V22_FRAGMENTED_CHASE_EXHAUSTION'
assert context['plan_commit']=='72fe5ff070f763cb6438700a18574f2aaabcd7c9'
assert all(context['expected_market_sha256'].get(k)==v for k,v in d['expected_csv_sha256'].items())
for name in ['PLAN.md','IMPLEMENTATION_CONTRACT.md']:assert (p.parent/name).is_file(),name
print('FROZEN_V22_INPUT_CONTRACT_PASS',256,856,hashlib.sha256(cp.read_bytes()).hexdigest(),flush=True)
suite=unittest.TestSuite()
for name in NEW_TEST_FILES if args.only_new else TEST_FILES:
    suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=name))
expected=17 if args.only_new else 536
assert suite.countTestCases()==expected,(suite.countTestCases(),expected)
print('V22_REGISTERED_SUITE_SIZE',suite.countTestCases(),flush=True)
result=unittest.TextTestRunner(verbosity=2,stream=sys.__stderr__).run(suite)
print('V22_TEST_RESULT',result.testsRun,len(result.failures),len(result.errors),result.wasSuccessful(),flush=True)
sys.exit(0 if result.wasSuccessful() else 1)
