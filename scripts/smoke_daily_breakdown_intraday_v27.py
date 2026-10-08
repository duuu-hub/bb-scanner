"""Synthetic full scanning/source/16-policy/384-account test; no market evidence."""
import json, sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import daily_breakdown_intraday_v27 as v

def run(out):
    out.mkdir(parents=True,exist_ok=True)
    start=v.engine.base.START;end=start+16*v.DAY
    intervals={"DEV":(start,start+8*v.DAY),"GATE":(start+8*v.DAY,end)}
    t=np.arange(start-40*v.DAY,end+v.DAY+v.BAR,v.BAR,dtype=np.int64)
    price=np.full(len(t),100.)
    for begin,signal_close,fill in ((start,97.,94.),(start+8*v.DAY,90.,80.)):
        price[(t>=begin)&(t<begin+3*v.DAY)]=fill
        price[t==begin-v.BAR]=signal_close
    market=out/"market";market.mkdir()
    hashes={}
    for i in range(8):
        s=f"X{i}USDT";p=market/f"{s}.csv.gz"
        pd.DataFrame(dict(open_time=t,open=price,high=price+.1,low=price-.1,close=price,
            quote_volume=np.full(len(t),1e6),taker_buy_quote=np.full(len(t),5e5))).to_csv(
                p,index=False,compression=dict(method="gzip",mtime=0))
        hashes[s]=v.digest(p)
    btc=out/"BTC.csv.gz";btc.write_bytes((market/"X0USDT.csv.gz").read_bytes())
    context=out/"context.json"
    v.write_json(context,dict(expected_market_sha256=hashes,btc_sha256=v.digest(btc),baseline_sha256="b"*64))
    reg=json.loads(v.REGISTRY.read_text());reg["source_context_sha256"]=v.digest(context)
    contract=out/"registry.json";v.write_json(contract,reg)
    source=out/"source-check.json"
    v.write_json(source,dict(status="VERIFIED",shards=list(range(8)),baseline_sha256="b"*64,
        files=[dict(symbol=s,sha256=h) for s,h in hashes.items()]))
    parts=out/"parts"
    with patch.object(v,"CONTEXT",context),patch.object(v.engine.source_helpers,"interval",
            side_effect=lambda stage:intervals[stage]),patch.object(v.engine.base,"GATE_END",end):
        for i in range(8):
            shard=out/"shards"/str(i);shard.mkdir(parents=True)
            p=market/f"X{i}USDT.csv.gz";(shard/p.name).write_bytes(p.read_bytes())
            check=out/f"source-{i}.json"
            v.write_json(check,dict(status="VERIFIED",shards=[i],baseline_sha256="b"*64,
                files=[dict(symbol=f"X{i}USDT",sha256=hashes[f"X{i}USDT"])]))
            for stage in ("DEV","GATE"):
                v.scan(shard,btc,parts/stage/str(i),out/"cache",stage,check,contract)
        frame,_=v.read_parts([parts],contract)
        assert len(frame)==256 and len(frame.variant.unique())==16
        v.prepare_market(market,btc,source,[parts],out/"account-market",contract)
        for i in range(16):
            v.replay_probe(out/"account-market",[parts],out/"results"/str(i),i,contract)
        v.collect(out/"results",out/"collected",contract)
    decision=json.loads((out/"collected/decision.json").read_text())
    assert decision["scenarios"]==384 and decision["qualified_candidates"]==0
    assert decision["v25_rejection_retained"] and not decision["account_daily_target_claim"]
    summaries=pd.read_csv(out/"collected/summary.csv")
    assert len(summaries)==384
    audits=json.loads((out/"collected/audit.json").read_text())
    assert len(audits)==384 and all(x["all_checks_passed"] and x["quota_verified"] for x in audits)
    v.write_json(out/"smoke.json",dict(synthetic_only=True,scan_shards=16,policies=16,
        independent_rows=256,actual_account_scenarios_audited=384,holding_limit_minutes=1440,
        economic_profitability_claim=False))
    print("V27_SYNTHETIC_8_SOURCE_16_SCAN_16_POLICY_384_ACCOUNT_PIPELINE_PASS",flush=True)

if __name__=="__main__":run(Path(sys.argv[1]))
