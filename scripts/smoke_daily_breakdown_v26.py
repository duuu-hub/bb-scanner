"""Synthetic 8 original shards -> 16 diagnostic inputs -> 64 actual account audits."""
import json,sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import daily_breakdown_account_v26 as v

def run(out):
    out.mkdir(parents=True,exist_ok=True)
    start=v.engine.base.START;end=start+16*v.DAY
    intervals={"DEV":(start,start+8*v.DAY),"GATE":(start+8*v.DAY,end)}
    t=np.arange(start,end+v.BAR,v.BAR,dtype=np.int64)
    price=100.-(t-start)/v.DAY*.1
    market=out/"market";market.mkdir()
    hashes={}
    for i in range(8):
        s=f"X{i}USDT";p=market/f"{s}.csv.gz"
        pd.DataFrame(dict(open_time=t,open=price,high=price+.01,low=price-.01,close=price,
            quote_volume=np.full(len(t),1e6),taker_buy_quote=np.full(len(t),5e5))).to_csv(p,index=False,compression="gzip")
        hashes[s]=v.digest(p)
    btc=out/"BTC.csv.gz";btc.write_bytes((market/"X0USDT.csv.gz").read_bytes());btc_sha=v.digest(btc)
    context=out/"context.json";v.write_json(context,dict(expected_market_sha256=hashes,btc_sha256=btc_sha,baseline_sha256="b"*64))
    source=out/"source-check.json";v.write_json(source,dict(status="VERIFIED",shards=list(range(8)),
        baseline_sha256="b"*64,files=[dict(symbol=s,sha256=h) for s,h in hashes.items()]))
    sha=v.digest(context);reg=json.loads(v.REGISTRY.read_text());reg["source_context_sha256"]=sha
    contract=out/"registry.json";v.write_json(contract,reg)
    originals=out/"original";gate=out/"gate";expected=[]
    for stage in ("DEV","GATE"):
        for i in range(8):
            s=f"X{i}USDT";raw=v.v25.SOURCE_LOAD(market/f"{s}.csv.gz")[0]
            entry=intervals[stage][0];j=int(np.searchsorted(t,entry));rows=[]
            for p in v.probes():
                tr=dict(symbol=s,variant=p["policy"],policy=p["policy"],key=p["key"],side=-1,
                    entry_time=entry,entry_index=j,entry=float(price[j]),sl=float(price[j]+10),
                    tp=float(price[j]-20),max_hold_bars=672,score=float(8-i),split=stage)
                result=v.engine.canonical.resolve(tr,raw,{},"TP2",intervals[stage][1])
                assert result["status"]=="RESOLVED"
                tr.update(result);tr.pop("entry_index")
                ratio=tr["exit"]/tr["entry"];hold=(tr["exit_time"]-tr["entry_time"])/v.DAY
                tr["net40_fraction"]=1-ratio-.002*(1+ratio)-.0002*hold
                tr["net40_R"]=tr["net40_fraction"]/v.account.stop_loss_fraction(tr,.002)
                rows.append(tr)
            folder=(originals if stage=="DEV" else gate)/str(i);folder.mkdir(parents=True)
            lp=folder/"independent_candidates.csv.gz";pd.DataFrame(rows).to_csv(lp,index=False,compression=dict(method="gzip",mtime=0))
            pd.DataFrame(columns=["symbol","policy","entry_time","status"]).to_csv(folder/"exclusions.csv",index=False)
            m=dict(complete=True,stage=stage,shard=i,policies=v.v25.policies() if stage=="DEV" else v.probes(),
                ledger_sha256=v.digest(lp),ledger_rows=len(rows),source_context_sha256=sha,btc_sha256=btc_sha,
                market_hashes={s:hashes[s]},counts={},contract_sha256=v.digest(contract),status=v.DIAGNOSTIC,exclusions=0)
            v.write_json(folder/"scan_meta.json",m)
            if stage=="DEV":expected.append(dict(shard=i,ledger_sha256=v.digest(lp),ledger_rows=len(rows),
                scan_meta_git_blob_sha=v.git_blob_sha(folder/"scan_meta.json")))
    frozen=out/"frozen.json";v.write_json(frozen,dict(expected_shards=expected,total_parameterized_outcomes=64))
    with patch.object(v,"CONTEXT_SHA",sha),patch.object(v.v25,"CONTEXT",context),\
         patch.object(v.engine.source_helpers,"interval",side_effect=lambda stage:intervals[stage]),\
         patch.object(v.engine.base,"GATE_END",end):
        dev=out/"dev";v.prepare_dev(originals,dev,contract,frozen)
        v.prepare_market(market,btc,source,[dev,gate],out/"account-market",contract)
        for i in range(8):v.replay_probe(out/"account-market",[dev,gate],out/"results"/str(i),i,contract)
        v.collect(out/"results",out/"collected",contract)
    result=json.loads((out/"collected/decision.json").read_text())
    assert result["scenarios"]==64 and result["qualified_candidates"]==0 and result["v25_rejection_retained"]
    assert len(pd.read_csv(out/"collected/summary.csv"))==64
    v.write_json(out/"smoke.json",dict(synthetic_only=True,original_shards=8,diagnostic_shards=16,
        actual_account_scenarios_audited=64,canonical_week_hold=True,market_profitability_claim=False))
    print("V26_SYNTHETIC_8_TO_16_TO_64_PIPELINE_PASS",flush=True)

if __name__=="__main__":run(Path(sys.argv[1]))
