import glob,pandas as pd,numpy as np,re,json
files=glob.glob("in/**/events_*.csv.gz",recursive=True)
assert len(files)==8,files
parts=[]
for f in files:
 d=pd.read_csv(f,usecols=["variant","outcome","pnl_pct","R","entry_atr","sb_atr"])
 d=d[d.outcome.isin(["win","loss"])].copy()
 parts.append(d)
x=pd.concat(parts,ignore_index=True)
rows=[]
for v,g in x.groupby("variant"):
 w=int((g.outcome=="win").sum());l=int((g.outcome=="loss").sum());n=w+l
 r=float(g.R.iloc[0]);e=float(g.entry_atr.iloc[0]);sb=float(g.sb_atr.iloc[0])
 gp=float(g.loc[g.pnl_pct>0,"pnl_pct"].sum());gl=float(-g.loc[g.pnl_pct<0,"pnl_pct"].sum())
 rows.append(dict(variant=v,E=e,SB=sb,R=r,n=n,wins=w,losses=l,win_pct=100*w/n,pf_pct=gp/gl if gl else np.inf,sum_pnl_pct=gp-gl,avg_pnl_pct=(gp-gl)/n))
z=pd.DataFrame(rows)
z.to_csv("summary.csv",index=False)
for col in ["sum_pnl_pct","avg_pnl_pct","pf_pct"]:
 print("\nTOP_"+col.upper())
 print(z.sort_values(col,ascending=False).head(20).to_string(index=False))
# robust neighborhood: rank by total pnl first, print best by R
print("\nBEST_BY_R_TOTAL_PNL")
print(z.sort_values("sum_pnl_pct",ascending=False).groupby("R",as_index=False).first().sort_values("R").to_string(index=False))
json.dump({"resolved":len(x),"variants":len(z)},open("meta.json","w"))
