import argparse,glob,json,math
import numpy as np,pandas as pd
ap=argparse.ArgumentParser();ap.add_argument("--data",default="artifacts");ap.add_argument("--tf",choices=["4h","1d"],required=True);ap.add_argument("--out",required=True);a=ap.parse_args()
H=[4,8,24]; COSTS=[.20,.40]; cutoff=1735689600000
rows=[]
for p in glob.glob(a.data+"/**/*.json",recursive=True):
 if f"psar-distance-age-{a.tf}-" not in p: continue
 with open(p) as f: rows += json.load(f).get("rows",[])
df=pd.DataFrame(rows); assert len(df)>0
# one entry per PSAR event: first bar whose age is 3..8; avoids counting same event repeatedly
x=df[(df.side=="BEAR") & df.age.between(3,8)].sort_values(["symbol","event","age"]).drop_duplicates(["symbol","event"],keep="first").copy()
x["period"]=np.where(x.ts<cutoff,"TRAIN","HOLDOUT")
def metrics(v):
 v=np.asarray(v,float); n=len(v); wins=v[v>0].sum(); losses=-v[v<0].sum(); pf=float(wins/losses) if losses>0 else None
 eq=np.cumprod(1+v/100); peak=np.maximum.accumulate(np.r_[1.,eq]); curve=np.r_[1.,eq]; mdd=float(np.min(curve/peak-1)*100)
 return {"n":n,"winrate":float((v>0).mean()*100),"avg":float(v.mean()),"pf":pf,"total_compound":float((eq[-1]-1)*100),"mdd":mdd}
out={"tf":a.tf,"entry":"first OPEN with PSAR BEAR age 3..8; one trade per PSAR event","exit":"fixed horizon OPEN; exploratory candidates chosen on TRAIN only","results":{}}
# select horizon using TRAIN at 40bp (strict cost), then freeze for holdout
train={}
for h in H:
 train[str(h)]={str(c):metrics(x.loc[x.period=="TRAIN",f"ret{h}"]-c) for c in COSTS}
best=max(H,key=lambda h:(train[str(h)]["0.4"]["pf"] or -1))
out["train_candidates"]=train;out["selected_horizon"]=best
for per in ["TRAIN","HOLDOUT"]:
 out["results"][per]={str(c):metrics(x.loc[x.period==per,f"ret{best}"]-c) for c in COSTS}
open(a.out,"w").write(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
