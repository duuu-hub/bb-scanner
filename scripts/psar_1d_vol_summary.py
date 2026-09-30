import glob,pandas as pd,numpy as np
fs=glob.glob("in/**/events_*.csv.gz",recursive=True); assert len(fs)==8,fs
d=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
d=d[d.outcome.isin(["win","loss"])].copy()
rows=[]
for side in ("LONG","SHORT"):
 x=d[d.side==side].copy()
 for pct in (100,30,20,10,5):
  if pct==100:y=x
  else:
   q=x.atr_pct.quantile(1-pct/100);y=x[x.atr_pct>=q]
  for v,g in y.groupby("variant"):
   p=g.pnl_pct.astype(float);gp=p[p>0].sum();gl=-p[p<0].sum()
   rows.append(dict(side=side,top_pct=pct,variant=v,n=len(g),win_pct=(p>0).mean()*100,pf=gp/gl if gl else np.inf,avg_pct=p.mean(),sum_pct=p.sum()))
r=pd.DataFrame(rows)
r.to_csv("vol_summary.csv",index=False)
for side in ("LONG","SHORT"):
 print("\nSIDE",side)
 z=r[r.side==side]
 for pct in (100,30,20,10,5):
  a=z[z.top_pct==pct].sort_values("sum_pct",ascending=False).head(5)
  print("\nTOP",pct);print(a.to_string(index=False))
