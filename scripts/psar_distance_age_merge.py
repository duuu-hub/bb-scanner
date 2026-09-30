import glob,json,zipfile,os
import pandas as pd, numpy as np
rows=[]
for z in glob.glob("artifacts/**/*.zip",recursive=True):
 try:
  with zipfile.ZipFile(z) as f:
   for n in f.namelist():
    if n.endswith(".json"):
     d=json.loads(f.read(n)); rows += d.get("rows",[])
 except zipfile.BadZipFile: pass
# gh download normally extracts files, not zip
for p in glob.glob("artifacts/**/*.json",recursive=True):
 with open(p) as f: rows += json.load(f).get("rows",[])
df=pd.DataFrame(rows)
if df.empty: raise RuntimeError("no rows")
for c in ["d0","dt"]: df[c+"q"]=pd.qcut(df[c],5,labels=["Q1","Q2","Q3","Q4","Q5"],duplicates="drop")
out={"n":len(df),"symbols":int(df.symbol.nunique())}
for dim in ["d0q","dtq","age_bin"]:
 g=df.groupby(["side",dim],observed=True).agg(n=("ret4","size"),ret1=("ret1","mean"),ret2=("ret2","mean"),ret4=("ret4","mean"),ret8=("ret8","mean"),ret16=("ret16","mean"),ret24=("ret24","mean"),mfe4=("mfe4","mean"),mae4=("mae4","mean")).reset_index()
 out[dim]=g.to_dict("records")
open("summary.json","w").write(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
