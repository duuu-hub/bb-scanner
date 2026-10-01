import pandas as pd,numpy as np,glob
e=pd.read_csv("ev/events_all.csv")
c=pd.concat([pd.read_csv(x) for x in glob.glob("cov/*/coverage.csv")])
first=c.groupby("symbol").first_date.min()
e["age_days"]=(pd.to_datetime(e.date)-pd.to_datetime(e.symbol.map(first))).dt.days
def pf(y):
 n=-y[y<0].sum(); return y[y>0].sum()/n if n else np.nan
rows=[]
for age in [0,90,180,365,730]:
 d=e[e.age_days>=age]
 for split,g in [("ALL",d),("TRAIN",d[d.year<=2024]),("HOLDOUT",d[d.year>=2025]),("2025",d[d.year==2025])]:
  for cost in [0,.2,.4]:
   y=g.gross_pct-cost
   rows.append([age,split,cost,len(g),(y>0).mean()*100 if len(g) else np.nan,y.mean() if len(g) else np.nan,pf(y)])
o=pd.DataFrame(rows,columns=["min_age_days","split","cost_pct","n","wr_pct","mean_pct","pf"])
o.to_csv("age_summary.csv",index=False);print(o.to_string(index=False))
print("MISSING",e.age_days.isna().sum(),"NEG",(e.age_days<0).sum())
