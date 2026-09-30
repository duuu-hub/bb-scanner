import argparse,glob,json,os
import pandas as pd
ap=argparse.ArgumentParser();ap.add_argument("--tf",required=True,choices=["1h","4h","1d"]);a=ap.parse_args()
rows=[]
for p in glob.glob("artifacts/**/*.json",recursive=True):
 if f"psar-distance-age-{a.tf}-" not in p: continue
 with open(p) as fh: rows += json.load(fh).get("rows",[])
df=pd.DataFrame(rows)
if df.empty: raise RuntimeError(f"no rows for {a.tf}")
for x in ["d0","dt"]: df[x+"q"]=pd.qcut(df[x],5,labels=["Q1","Q2","Q3","Q4","Q5"],duplicates="drop")
out={"tf":a.tf,"n":len(df),"symbols":int(df.symbol.nunique())}
for dim in ["d0q","dtq","age_bin"]:
 g=df.groupby(["side",dim],observed=True).agg(n=("ret4","size"),ret1=("ret1","mean"),ret2=("ret2","mean"),ret4=("ret4","mean"),ret8=("ret8","mean"),ret16=("ret16","mean"),ret24=("ret24","mean"),mfe4=("mfe4","mean"),mae4=("mae4","mean")).reset_index()
 out[dim]=g.to_dict("records")
open(f"summary-{a.tf}.json","w").write(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
