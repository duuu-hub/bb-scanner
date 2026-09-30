import argparse,glob,json,numpy as np,pandas as pd
ap=argparse.ArgumentParser();ap.add_argument("--data",default="artifacts");ap.add_argument("--out",required=True);a=ap.parse_args()
rows=[]
for p in glob.glob(a.data+"/**/*.json",recursive=True):
 if "psar-distance-age-1d-" in p:
  with open(p) as f: rows+=json.load(f).get("rows",[])
d=pd.DataFrame(rows);assert len(d)>0
d=d[d.side=="BEAR"].copy();d["period"]=np.where(d.ts<1735689600000,"TRAIN","HOLDOUT")
# Broad standalone signal: first observable age=3, fixed 8D exit, 40bp cost.
x=d[d.age==3].copy();x["net"]=x.ret8.astype(float)-.40
# Market-regime proxies available at entry only, derived from event rows: D0/Dt quintiles + calendar year.
# No selection here: descriptive stability map only.
def met(g):
 v=g.net.to_numpy(float);gp=v[v>0].sum();gl=-v[v<0].sum()
 return pd.Series({"n":len(v),"pf":gp/gl if gl>0 else np.nan,"win":(v>0).mean()*100,"avg":v.mean()})
out={}
out["period"]=x.groupby("period").apply(met,include_groups=False).reset_index().to_dict("records")
x["year"]=pd.to_datetime(x.ts,unit="ms",utc=True).dt.year
out["year"]=x.groupby(["period","year"]).apply(met,include_groups=False).reset_index().to_dict("records")
# Frozen TRAIN quantile boundaries; report every bucket, don't optimize.
for col in ["d0","dt"]:
 q=np.unique(x.loc[x.period=="TRAIN",col].quantile([0,.2,.4,.6,.8,1]).to_numpy())
 x[col+"q"]=pd.cut(x[col],q,labels=False,include_lowest=True)+1
 out[col]=x.groupby(["period",col+"q"]).apply(met,include_groups=False).reset_index().to_dict("records")
# simple volatility proxy = D0 itself broad terciles frozen on train; diagnostic only
q=np.unique(x.loc[x.period=="TRAIN","d0"].quantile([0,1/3,2/3,1]).to_numpy());x["d0_terc"]=pd.cut(x.d0,q,labels=["LOW","MID","HIGH"],include_lowest=True)
out["d0_terc"]=x.groupby(["period","d0_terc"],observed=True).apply(met,include_groups=False).reset_index().to_dict("records")
open(a.out,"w").write(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
