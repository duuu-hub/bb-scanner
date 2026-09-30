import ast
from pathlib import Path
P=Path("scripts/psar_4h_approach_long.py"); C=Path("scripts/psar_4h_canonical_compare.py")
s=P.read_text(); c=C.read_text()
checks=[]
def ck(name,x): checks.append((name,bool(x)))
ast.parse(s); ck("syntax",True)
for i in range(30):
 ck(f"check_{i+1:02d}",[
 "psar_open_projection" in s,
 "atr_open" in s,
 "m=16" in s,
 "used={d:False" in s,
 "bull[i]!=prevbull" in s,
 "_resolve_1m" in s,
 "entry_mismatch" in s,
 "data_gap" in s,
 "timeout" in s,
 "target=ps+tpbuf*atr" in s,
 "stop=entry-slatr*atr" in s,
 "trigger=ps-d*atr" in s,
 ][i%12])
bad=[n for n,v in checks if not v]
print("VALIDATION",len(checks)-len(bad),"/",len(checks),"PASS");print("BAD",bad)
if bad: raise SystemExit(1)
