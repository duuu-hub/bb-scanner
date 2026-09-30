import glob,json
import numpy as np,pandas as pd
QS=(0.05,0.10,0.20); LOOKBACKS=("15m","1h","4h"); HOLDS=("1h","4h","12h")
fs=glob.glob("parts/**/*.csv.gz",recursive=True)
if len(fs)!=8: raise RuntimeError(f"expected 8 parts got {len(fs)}")
z=pd.concat([pd.read_csv(p) for p in fs],ignore_index=True)
counts=z.groupby("ts").symbol.transform("count"); z=z[counts>=20].copy()
out=[]
for lb in LOOKBACKS:
 z["pct"]=z.groupby("ts")["r_"+lb].rank(pct=True,method="average")
 for q in QS:
  for side,mask in [("LOSER",z.pct<=q),("WINNER",z.pct>=1-q)]:
   a=z.loc[mask]
   for h in HOLDS:
    v=a["f_"+h].dropna()
    out.append({"lookback":lb,"tail":q,"side":side,"hold":h,"n":int(len(v)),"mean_fwd_pct":float(v.mean()*100),"median_fwd_pct":float(v.median()*100),"winrate_pct":float((v>0).mean()*100)})
r=pd.DataFrame(out)
p=r.pivot_table(index=["lookback","tail","hold"],columns="side",values=["mean_fwd_pct","n","winrate_pct"]).reset_index()
p.columns=["_".join([str(y) for y in x if str(y)!=""]).rstrip("_") for x in p.columns]
p["winner_minus_loser_pct"]=p["mean_fwd_pct_WINNER"]-p["mean_fwd_pct_LOSER"]
p["direction"]=np.where(p.winner_minus_loser_pct>0,"MOMENTUM","REVERSAL"); p["abs_spread_pct"]=p.winner_minus_loser_pct.abs(); p=p.sort_values("abs_spread_pct",ascending=False)
r.to_csv("cross_sectional_cells.csv",index=False); p.to_csv("cross_sectional_spreads.csv",index=False)
json.dump({"symbols":int(z.symbol.nunique()),"rows":int(len(z)),"top":p.head(30).to_dict("records")},open("cross_sectional_summary.json","w"),indent=2)
print("MERGE_PASS symbols",z.symbol.nunique(),"rows",len(z)); print(p.head(30).to_string(index=False))
