"""Frozen V27 validation, including the exact V26 and earlier regression inventory."""
import argparse, ast, json, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts import daily_breakdown_intraday_v27 as v
NEW=["test_daily_breakdown_intraday_v27.py"]

def previous_files():
    tree=ast.parse((ROOT/"scripts/validate_daily_breakdown_account_v26.py").read_text())
    values={}
    for node in tree.body:
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in {"NEW","OLD"}:
                    values[target.id]=ast.literal_eval(node.value)
    return values["NEW"]+values["OLD"]

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--only-new",action="store_true");args=ap.parse_args()
    r=v.registry()
    assert v.digest(v.CONTEXT)=="1a5266b4f9f79d40bc20cf8d794685806561f6366000e0183bab6977aaa4872d"
    assert v.digest(v.REGISTRY)=="b03035bd82bb7ca30ddee04deb886b2e4096c7c9598e7048b6904d62472bc7bd"
    assert v.digest(v.v25.CONTEXT)=="dab527bef32197364070e1d1cd17b5f9260cbf17f099e9698070ce5843c87faa"
    assert len(json.loads(v.CONTEXT.read_text())["expected_market_sha256"])==856
    new=unittest.TestSuite();old=unittest.TestSuite()
    for name in NEW:new.addTests(unittest.defaultTestLoader.discover(str(ROOT/"tests"),pattern=name))
    for name in previous_files():old.addTests(unittest.defaultTestLoader.discover(str(ROOT/"tests"),pattern=name))
    assert new.countTestCases()==23,(new.countTestCases(),23)
    assert old.countTestCases()==593,(old.countTestCases(),593)
    suite=new if args.only_new else unittest.TestSuite([new,old])
    print("V27_FROZEN_16_POLICY_384_ACCOUNT_AND_SOURCE_CONTRACT_PASS",flush=True)
    print("V27_REGISTERED_SUITE_SIZE",suite.countTestCases(),flush=True)
    result=unittest.TextTestRunner(verbosity=2,stream=sys.__stderr__).run(suite)
    print("V27_TEST_RESULT",result.testsRun,len(result.failures),len(result.errors),result.wasSuccessful(),flush=True)
    sys.exit(0 if result.wasSuccessful() else 1)

if __name__=="__main__":main()
