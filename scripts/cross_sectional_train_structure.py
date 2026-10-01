import glob,json
import numpy as np,pandas as pd
# TRAIN ONLY: 2021-01-01 <= ts < 2025-01-01. Holdout is intentionally inaccessible here.
START=pd.Timestamp("2021-01-01",tz="UTC").value//10**6
END=pd.Timestamp("2025-01-01",tz="UTC").value//10**6
fs=glob.glob("parts/**/*.csv.gz",recursive=True)
if len(fs)!=8: raise RuntimeError(f"expected 8 parts got {len(fs)}")
z=pd.concat([pd.read_csv(p) for p in fs],ignore_index=True)
z=z[(z.ts>=START)&(z.ts<END)].copy()
# timestamp market state from cross section, using only contemporaneous/past 15m returns
g=z.groupby("ts")["r_15m"]
z["pct"]=g.rank(pct=True,method="average")
z["mkt_med"]=g.transform("median")
z["breadth_up"]=g.transform(lambda s:(s>0).mean())
z["disp"]=g.transform(lambda s:s.quantile(.75)-s.quantile(.25))
z["abs15"]=z.r_15m.abs(); z["excess15"]=z.r_15m-z.mkt_med
# broad structural buckets, predeclared; no holdout tuning
abs_bins=[0,.005,.01,.02,.04,np.inf]
disp_q=z[["ts","disp"]].drop_duplicates().disp.quantile([.5,.75,.9]).to_list()
z["abs_bin"]=pd.cut(z.abs15,abs_bins,right=False)
z["disp_bin"]=pd.cut(z.disp,[-np.inf]+disp_q+[np.inf],labels=["D0_50","D50_75","D75_90","D90_100"])
z["breadth_bin"]=pd.cut(z.breadth_up,[0,.3,.45,.55,.7,1.000001],labels=["B0_30","B30_45","B45_55","B55_70","B70_100"],include_lowest=True)
rows=[]
for q in [.05,.10]:
 for side,mask in [("LONG_LOSER",z.pct<=q),("SHORT_WINNER",z.pct>=1-q)]:
  a=z[mask].copy()
  a["signed_fwd"]=np.where(side=="LONG_LOSER",a.f_1h,-a.f_1h)
  for dims in [[],["abs_bin"],["disp_bin"],["breadth_bin"],["abs_bin","disp_bin"],["abs_bin","breadth_bin"]]:
   groups=[("ALL",a)] if not dims else a.groupby(dims,observed=True)
   for key,b in groups:
    v=b.signed_fwd.dropna()
    if len(v)<1000: continue
    key=(key,) if not isinstance(key,tuple) else key
    d={"tail":q,"side":side,"dims":"+".join(dims) or "ALL","n":len(v),"mean_gross_bp":v.mean()*10000,"median_bp":v.median()*10000,"wr_pct":(v>0).mean()*100}
    for i,k in enumerate(dims): d[k]=str(key[i])
    rows.append(d)
r=pd.DataFrame(rows)
r["net20_bp"]=r.mean_gross_bp-20; r["net40_bp"]=r.mean_gross_bp-40
r=r.sort_values("mean_gross_bp",ascending=False)
r.to_csv("cross_sectional_train_structure.csv",index=False)
json.dump({"train":"2021-2024","rows":int(len(z)),"symbols":int(z.symbol.nunique()),"disp_quantiles":disp_q,"top":r.head(50).to_dict("records")},open("cross_sectional_train_structure.json","w"),indent=2)
print("TRAIN_STRUCTURE_PASS symbols",z.symbol.nunique(),"rows",len(z))
print(r.head(50).to_string(index=False))
