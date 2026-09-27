import ast
from pathlib import Path
import numpy as np

SRC=Path("scripts/psar_open_canonical_compare.py").read_text()
tree=ast.parse(SRC)
keep=[n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom)) or (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="_ONE_MIN_CACHE" for t in n.targets)) or (isinstance(n,ast.FunctionDef) and n.name in {"_one_min","_resolve_1m"})]
ns={}
exec(compile(ast.Module(body=keep,type_ignores=[]),"<smoke>","exec"),ns)
resolve=ns["_resolve_1m"]

def run_case(name, bars, tp, sl, long, entry, expected):
    old=ns["_one_min"]
    ns["_one_min"]=lambda symbol,ts:(np.array([x[0] for x in bars],dtype=np.int64),np.array([x[1] for x in bars],float),np.array([x[2] for x in bars],float))
    resolve.__globals__["_one_min"]=ns["_one_min"]
    got=resolve("TEST",0,tp,sl,long,entry)
    resolve.__globals__["_one_min"]=old
    assert got==expected, f"{name}: got {got}, expected {expected}"
    print("PASS",name,got)

# long: pre-entry exit-looking candle ignored
run_case("long_preentry_tp_ignored",[(0,111,109),(60000,101,99),(120000,106,102)],105,95,True,100,"win")
# first entry minute also touches TP -> chronology unknowable => loss
run_case("long_entry_same1m_tp_loss",[(0,106,99)],105,95,True,100,"loss")
run_case("long_entry_same1m_sl_loss",[(0,101,94)],105,95,True,100,"loss")
run_case("long_entry_same1m_both_loss",[(0,106,94)],105,95,True,100,"loss")
# entry-only minute, later TP/SL
run_case("long_entry_then_tp",[(0,101,99),(60000,106,101)],105,95,True,100,"win")
run_case("long_entry_then_sl",[(0,101,99),(60000,101,94)],105,95,True,100,"loss")
# pre-entry TP is not an exit; after later entry with no exit in this 15m, position must continue
run_case("long_preentry_tp_then_entry_continue",[(0,106,102),(60000,101,99)],105,95,True,100,"continue")
# 15m claimed maker fill but 1m never contains entry => data integrity mismatch
run_case("long_entry_not_seen_data_error",[(0,110,106),(60000,104,102)],105,95,True,100,"data_error")
# established position: same 1m TP+SL collision remains conservative loss
run_case("long_established_same1m_both_loss",[(0,106,94)],105,95,True,None,"loss")
# short mirrors
run_case("short_entry_same1m_tp_loss",[(0,101,94)],95,105,False,100,"loss")
run_case("short_entry_same1m_sl_loss",[(0,106,99)],95,105,False,100,"loss")
run_case("short_entry_then_tp",[(0,101,99),(60000,99,94)],95,105,False,100,"win")
run_case("short_entry_then_sl",[(0,101,99),(60000,106,99)],95,105,False,100,"loss")
run_case("short_preentry_tp_then_entry_continue",[(0,98,94),(60000,101,99)],95,105,False,100,"continue")
print("ALL_CHRONOLOGY_SMOKE_PASS")


# Static invariants for the canonical engine added during deep audit.
src=SRC
assert "PSAR_BURNIN_BARS=100" in src
assert "max(15,PSAR_BURNIN_BARS)" in src
assert 'np.diff(t)<=0' in src
assert 'def contiguous_segments(t):' in src
assert 'np.diff(t)!=900000' in src
assert 'np.diff(v[0])!=60000' in src
assert 'z-a!=15' in src
assert 'invalid OHLC geometry' in src
assert 'invalid 1m high/low' in src
assert src.count('if (b and not fill>sl) or ((not b) and not fill<sl):continue')==2
assert 'duplicate symbol/time-range input' in src
assert 't[a]%span==0' in src
assert 'unexpected 1m ZIP members' in src
assert 'gap_policy' in src
assert 'gross_expectancy_R_amb_loss' in src
assert 'independent-signal gross edge scan' in src
assert 'q["loss"]+=int(rr!="win")' not in src
print("ALL_CANONICAL_INVARIANTS_PASS")
