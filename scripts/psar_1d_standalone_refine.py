import argparse,glob,json,numpy as np,pandas as pd
ap=argparse.ArgumentParser();ap.add_argument("--data",default="artifacts");ap.add_argument("--out",required=True);a=ap.parse_args()
rows=[]
for p in glob.glob(a.data+"/**/*.json",recursive=True):
    if "psar-distance-age-1d-" in p:
        with open(p) as f: rows+=json.load(f).get("rows",[])
d=pd.DataFrame(rows);assert len(d)>0
d=d[d.side=="BEAR"].copy();cut=1735689600000
d["period"]=np.where(d.ts<cut,"TRAIN","HOLDOUT")
# empirical quintiles frozen from TRAIN
for col in ["d0","dt"]:
    q=np.unique(d.loc[d.period=="TRAIN",col].quantile([0,.2,.4,.6,.8,1]).to_numpy())
    d[col+"q"]=pd.cut(d[col],q,labels=False,include_lowest=True)+1
def met(x,c=.40):
    v=x.astype(float)-c;n=len(v);gp=v[v>0].sum();gl=-v[v<0].sum()
    return dict(n=n,pf=float(gp/gl) if gl>0 else None,win=float((v>0).mean()*100) if n else None,avg=float(v.mean()) if n else None)
# exact age entry; one row/event by exact age. event id is shard-local so timestamp is the actual key.
train=[]
for age in range(3,9):
 for dq in range(1,6):
  for tq in range(1,6):
   x=d[(d.period=="TRAIN")&(d.age==age)&(d.d0q==dq)&(d.dtq==tq)]
   m=met(x.ret8)
   if m["n"]>=200: train.append(dict(age=age,d0q=dq,dtq=tq,**m))
# require meaningful sample, rank by PF then avg
train=sorted(train,key=lambda x:(x["pf"] or -1,x["avg"] or -999),reverse=True)
best=train[0];mask=(d.age==best["age"])&(d.d0q==best["d0q"])&(d.dtq==best["dtq"])
out={"definition":"1D BEAR; exact age + TRAIN-frozen D0/Dt quintiles; exit 8D OPEN; total cost 40bp","train_top10":train[:10],"selected":best,"holdout":met(d.loc[(d.period=="HOLDOUT")&mask,"ret8"]),"baseline_train":met(d.loc[d.period=="TRAIN","ret8"]),"baseline_holdout":met(d.loc[d.period=="HOLDOUT","ret8"])}
open(a.out,"w").write(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
