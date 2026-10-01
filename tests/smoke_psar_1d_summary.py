"""End-to-end summary/manifest fixture, independent of downloaded prices."""
from pathlib import Path
import tempfile
import sys
import json
import subprocess
import importlib
from unittest.mock import patch

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import psar_1d_short_timelimit as E

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);src=root/"in";src.mkdir();base=root/"baseline";base.mkdir()
    for shard in range(8):
        parts=[]
        for date in ("2022-01-01","2025-01-01"):
            origin=int(pd.Timestamp(date,tz="UTC").timestamp()*1000)
            n=115*96;t=origin+np.arange(n,dtype=np.int64)*E.STEP
            o=np.full(n,99.);h=np.full(n,99.5);l=np.full(n,98.5);c=np.full(n,99.)
            with patch.object(E,"psar_open_projection",side_effect=lambda hh,ll:(np.full(len(hh),102.),np.zeros(len(hh),bool))),patch.object(E,"prior_atr",side_effect=lambda hh,ll,cc:np.full(len(hh),20.)):
                parts.extend(E.one(t,o,h,l,c,f"T{shard}USDT"))
        d=pd.DataFrame(parts,columns=E.COLUMNS);E.validate(d)
        p=src/f"tl_{shard}.csv.gz";d.to_csv(p,index=False)
        m=dict(shards=8,shard=shard,selected_files=1,sources=[{"symbol":f"T{shard}USDT"}],variant=list(E.V),limits=list(E.LIMITS),atr_threshold=E.THRESHOLD,events=len(d),commit="fixture",script_sha256="one",helper_sha256="two",total_input_files=8,diagnostics={})
        Path(str(p)+".meta.json").write_text(json.dumps(m))
        b=d[d.limit_days==14].copy()
        b["variant"]="E2.75_SB1.2_R0.75";b["side"]="SHORT"
        b["outcome"]="win";b["exit_ts"]=b.fill_ts+20*E.DAY
        b["pnl_pct"]=(b["fill"]-b.tp)/b["fill"]*100.
        b.to_csv(base/f"events_{shard}.csv.gz",index=False)
    out=root/"out"
    proc=subprocess.run([sys.executable,str(Path(E.__file__).with_name("psar_1d_timelimit_summarize.py")),"--input",str(src),"--baseline",str(base),"--output",str(out)],capture_output=True,text=True)
    if proc.returncode:
        print(proc.stdout,proc.stderr);raise SystemExit(proc.returncode)
    z=json.loads((out/"summary.json").read_text())
    assert z["train_40bp_candidates"]==[]
    assert z["input_files"]==8
    assert z["baseline_check"]["natural_outcome_mismatches"]==0
    assert z["common_signals"]==32
    raw=pd.read_csv(out/"raw_summary.csv")
    assert len(raw)==72
    assert np.allclose(raw[(raw.cost_bp==40)].mean_pct,-.4)
    port=pd.read_csv(out/"portfolio_diagnostic.csv")
    assert port.groupby(["limit_days","period"]).accepted_ids_sha256.nunique().max()==1
    assert set(port.cost_bp)=={20,40}
    assert (out/"report.md").exists()
    print("SUMMARY_FIXTURE_PASS",len(raw),len(port),z["decision"])
workflow=yaml.load(Path(".github/workflows/psar-1d-short-timelimit.yml").read_text(),Loader=yaml.BaseLoader)
assert workflow["on"]["push"]["branches"]==["research-rank5-binance-15m-5y"]
assert workflow["jobs"]["run"]["needs"]=="smoke"
assert workflow["jobs"]["summarize"]["needs"]=="run"
assert workflow["jobs"]["run"]["strategy"]["matrix"]["shard"]==list(map(str,range(8)))
assert workflow["defaults"]["run"]["shell"]=="bash"
print("WORKFLOW_WIRING_PASS")
