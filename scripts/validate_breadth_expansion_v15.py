"""Frozen V15 pre-market suite; stdout is execution evidence, not market results."""
import argparse,hashlib,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TEST_FILES=['test_breadth_expansion_continuation_v15.py','test_market_breadth_recovery_v14.py','test_breakout_level_retest_v13.py','test_taker_absorption_release_v12.py','test_aggressive_flow_cascade_v11.py',
    'test_residual_reversion_v10.py','test_btc_factor_lag_v6.py','test_compression_expansion_v4.py',
    'test_cross_sectional_leader_v9.py','test_day_edge_canonical.py','test_day_edge_lab.py',
    'test_funding_crowding_v7.py','test_liquidity_sweep_v5.py','test_premium_absorption_v8.py',
    'test_relative_pullback_v1.py','test_research_evidence_archive.py','test_saved_calendar_audit.py',
    'test_shock_confirmation_v3.py']
ap=argparse.ArgumentParser();ap.add_argument('--only-new',action='store_true');args=ap.parse_args()
p=ROOT/'research/breadth-expansion-continuation-v15/FROZEN_INPUT_HASHES.json';d=json.loads(p.read_text())
assert d['source_data_run']==36095439671 and len(d['expected_csv_sha256'])==256
assert hashlib.sha256(p.read_bytes()).hexdigest()=='0c3f09f41127e29cbf7130891e9f2c1389c99798d43e470825c973acbcd5121f'
context=json.loads((p.parent/'FROZEN_CONTEXT.json').read_text());assert len(context['expected_market_sha256'])==856
assert hashlib.sha256((p.parent/'FROZEN_CONTEXT.json').read_bytes()).hexdigest()=='4cbf469c0c0d4efa9c63d19c9650bbaf1e8ded8f38bc4195ec2d702d2eea9614'
assert all(context['expected_market_sha256'].get(k)==v for k,v in d['expected_csv_sha256'].items())
for name in ['PLAN.md','IMPLEMENTATION_CONTRACT.md']:
    assert (p.parent/name).is_file(),name
print('FROZEN_V15_INPUT_CONTRACT_PASS',256,856,flush=True)
suite=unittest.TestSuite()
for name in TEST_FILES[:1] if args.only_new else TEST_FILES:
    suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=name))
assert suite.countTestCases()==(39 if args.only_new else 416)
print('V15_REGISTERED_SUITE_SIZE',suite.countTestCases(),flush=True)
result=unittest.TextTestRunner(verbosity=2,stream=sys.__stderr__).run(suite)
print('V15_TEST_RESULT',result.testsRun,len(result.failures),len(result.errors),result.wasSuccessful(),flush=True)
sys.exit(0 if result.wasSuccessful() else 1)
