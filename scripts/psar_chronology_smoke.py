import ast
from pathlib import Path
import numpy as np

SRC=Path("scripts/psar_open_canonical_compare.py").read_text()
tree=ast.parse(SRC)
keep=[n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom)) or (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="_ONE_MIN_CACHE" for t in n.targets)) or (isinstance(n,ast.FunctionDef) and n.name in {"_one_min","_resolve_1m","resample","psar_open_projection"})]
ns={}
exec(compile(ast.Module(body=keep,type_ignores=[]),"<smoke>","exec"),ns)
resolve=ns["_resolve_1m"]

def run_case(name, bars, tp, sl, long, entry, expected):
    last_t=bars[-1][0] if bars else -60000
    neutral=(entry if entry is not None else (tp+sl)/2)
    while len(bars)<15:
        last_t+=60000
        bars.append((last_t,neutral,neutral))
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
assert 'overlapping symbol/time-range input' in src
assert 't[a]%span==0' in src
assert 'unexpected 1m ZIP members' in src
assert 'gap_policy' in src
assert 'gross_expectancy_R_amb_loss' in src
assert 'independent-signal gross edge scan' in src
assert 'q["loss"]+=int(rr!="win")' not in src
assert 'misaligned 1m timestamps' in src
assert 'misaligned 15m timestamps' in src
assert '1m/15m price mismatch' in src
assert 'rh=np.array([np.max(h[a:b]) for a,b in zip(st,en)],dtype=float)' in src
assert 'rl=np.array([np.min(l[a:b]) for a,b in zip(st,en)],dtype=float)' in src
assert 'accounting invariant failed order types' in src
assert 'accounting invariant failed outcomes' in src
assert 'invalid shard selection' in src
assert 'empty 15m input' in src
assert 'strategy-bar to 15m timestamp mapping mismatch' in src
assert '_ONE_MIN_CACHE.clear()' in src
assert 'source_data_run' in src
assert 'workflow_commit_sha' in src
assert 'engine_blob_sha' in src
assert 'invalid 1m CSV schema' in src
assert 'maker_fillbar_amb' not in src
# Execute resample, not just static-string check.
resample=ns["resample"]
tt=np.arange(0,8*900000,900000,dtype=np.int64)
oo=np.arange(10,18,dtype=float); hh=oo+2; ll=oo-2; cc=oo+1
rt,ro,rh,rl,rc=resample(tt,oo,hh,ll,cc,4)
assert len(rt)==2 and rt.tolist()==[0,3600000]
assert ro.tolist()==[10.0,14.0] and rc.tolist()==[14.0,18.0]
assert rh.tolist()==[15.0,19.0] and rl.tolist()==[8.0,12.0]
print("PASS resample_exact_buckets")

# Execute PSAR projection causality: changing current bar H/L must not change its open projection.
psar=ns["psar_open_projection"]
hh1=np.array([10.,11.,12.,13.,14.,15.]); ll1=np.array([8.,9.,10.,11.,12.,13.])
p1,b1=psar(hh1,ll1)
hh2=hh1.copy(); ll2=ll1.copy(); hh2[4]=100.; ll2[4]=1.
p2,b2=psar(hh2,ll2)
assert p1[4]==p2[4] and b1[4]==b2[4]
print("PASS psar_open_causality")

# Burn-in sanity: after 100 bars, perturbing only the first two initialization bars
# must no longer affect the projected PSAR on a reversal-rich deterministic path.
x=np.arange(180,dtype=float)
base=100+4*np.sin(x/3.0)+2*np.sin(x/11.0)
hh3=base+1.5; ll3=base-1.5
p3,b3=psar(hh3,ll3)
hh4=hh3.copy(); ll4=ll3.copy(); hh4[:2]+=25; ll4[:2]-=25
p4,b4=psar(hh4,ll4)
assert np.allclose(p3[100:],p4[100:],rtol=0,atol=1e-12) and np.array_equal(b3[100:],b4[100:])
print("PASS psar_burnin_initialization_sanity")
print("ALL_CANONICAL_INVARIANTS_PASS")
