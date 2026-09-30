import ast, numpy as np
from pathlib import Path
s=Path("scripts/psar_4h_approach_long.py").read_text()
ast.parse(s)
ns={"np":np}
a=s.index("def psar_open_projection"); b=s.index("def main():",a)
exec(s[a:b],ns)
fn=ns["psar_open_projection"]
passed=[]
def ok(name,cond):
 if not cond: raise AssertionError(name)
 passed.append(name)
# 10 independent synthetic histories x 3 invariants = exactly 30 checks.
for k in range(10):
 rng=np.random.default_rng(1000+k); n=180
 base=100+np.cumsum(rng.normal(0,.5,n)); h=base+rng.uniform(.1,1,n); l=base-rng.uniform(.1,1,n)
 sar,bull=fn(h,l)
 cut=120
 # 1: changing CURRENT/FUTURE bars cannot change PSAR at cut OPEN
 h2=h.copy();l2=l.copy();h2[cut:]*=1+rng.uniform(.2,.8);l2[cut:]*=rng.uniform(.2,.8)
 s2,b2=fn(h2,l2)
 ok(f"{k+1:02d}-no-lookahead-psar",np.isclose(sar[cut],s2[cut],equal_nan=True))
 ok(f"{k+1:02d}-no-lookahead-side",bool(bull[cut])==bool(b2[cut]))
 # 2: previous history perturbation SHOULD be allowed to affect current projection;
 # invariant here is finite/open-time state after burn-in.
 ok(f"{k+1:02d}-finite-open-state",np.isfinite(sar[cut]) and isinstance(bool(bull[cut]),bool))
print(f"VALIDATION {len(passed)}/30 PASS")
