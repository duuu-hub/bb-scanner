import argparse,glob,json,numpy as np,pandas as pd
ap=argparse.ArgumentParser();ap.add_argument("--data",default="artifacts");ap.add_argument("--out",required=True);a=ap.parse_args()
rows=[]
for p in glob.glob(a.data+"/**/*.json",recursive=True):
 if "psar-distance-age-1d-" in p:
  with open(p) as f: rows+=json.load(f).get("rows",[])
d=pd.DataFrame(rows);assert len(d)>0
x=d[(d.side=="BEAR")&(d.age==3)].copy();x["period"]=np.where(x.ts<1735689600000,"TRAIN","HOLDOUT")
# freeze D0 tercile boundaries from TRAIN only
q=x.loc[x.period=="TRAIN","d0"].quantile([0,1/3,2/3,1]).to_numpy()
assert np.all(np.diff(q)>0)
x["d0_band"]=pd.cut(x.d0,q,labels=["LOW","MID","HIGH"],include_lowest=True)
def met(z,c):
 v=z.ret8.astype(float).to_numpy()-c;n=len(v);gp=v[v>0].sum();gl=-v[v<0].sum()
 return {"n":n,"pf":float(gp/gl) if gl>0 else None,"win":float((v>0).mean()*100),"avg":float(v.mean()),"sum":float(v.sum())}
out={"rule":"1D PSAR BEAR, exact age=3 entry at OPEN, fixed 8D OPEN exit; D0 LOW excluded; TRAIN-frozen tercile boundaries","d0_edges":q.tolist(),"results":{}}
for per in ["TRAIN","HOLDOUT"]:
 z=x[x.period==per]
 out["results"][per]={}
 for c in [.20,.40]:
  base=met(z,c);filt=met(z[z.d0_band.isin(["MID","HIGH"])],c)
  out["results"][per][str(c)]={"BASE":base,"D0_MID_HIGH":filt,"retention_pct":100*filt["n"]/base["n"],"pf_delta":filt["pf"]-base["pf"],"avg_delta":filt["avg"]-base["avg"]}
open(a.out,"w").write(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
