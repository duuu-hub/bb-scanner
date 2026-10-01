import glob,json
import numpy as np,pandas as pd
QS=(.01,.02,.05); LBS=("15m","1h","4h"); HOLDS=("1h","4h","12h"); CDS={"1h":4,"4h":16,"12h":48}
fs=glob.glob("parts/**/*.csv.gz",recursive=True); z=pd.concat([pd.read_csv(p) for p in fs],ignore_index=True)
z=z[z.groupby("ts").symbol.transform("count")>=20].copy()
z["dt"]=pd.to_datetime(z.ts,unit="ms",utc=True); z["split"]=np.where(z.dt.dt.year<=2024,"TRAIN","HOLDOUT")
rows=[]
for lb in LBS:
 z["pct"]=z.groupby("ts")["r_"+lb].rank(pct=True,method="average")
 for q in QS:
  for side,cond in [("LOSER",z.pct<=q),("WINNER",z.pct>=1-q)]:
   a=z.loc[cond,["ts","symbol","split"]+["f_"+h for h in HOLDS]].sort_values(["symbol","ts"])
   for hold in HOLDS:
    # de-cluster: after accepted event, block same symbol for full holding window
    gap=CDS[hold]*15*60*1000; keep=[]; last={}
    for i,(s,t) in enumerate(zip(a.symbol.values,a.ts.values)):
     if t-last.get(s, t-gap-1)>=gap: keep.append(i); last[s]=t
    b=a.iloc[keep]
    for split in ("TRAIN","HOLDOUT"):
     v=b.loc[b.split==split,"f_"+hold].dropna()
     rows.append(dict(lookback=lb,tail=q,side=side,hold=hold,split=split,n=len(v),mean_pct=v.mean()*100,median_pct=v.median()*100,wr=(v>0).mean()*100))
r=pd.DataFrame(rows); r.to_csv("event_cells.csv",index=False)
p=r.pivot_table(index=["lookback","tail","hold","split"],columns="side",values=["mean_pct","n","wr"]).reset_index()
p.columns=["_".join(str(y) for y in x if str(y)!="").rstrip("_") for x in p.columns]
p["spread_pct"]=p.mean_pct_WINNER-p.mean_pct_LOSER; p["direction"]=np.where(p.spread_pct>0,"MOMENTUM","REVERSAL")
p.to_csv("event_spreads.csv",index=False)
train=p[p.split=="TRAIN"].copy(); train["abs_spread"]=train.spread_pct.abs(); train=train.sort_values("abs_spread",ascending=False)
json.dump({"top_train":train.head(20).to_dict("records")},open("event_summary.json","w"),indent=2)
print("EVENT_PASS"); print(train.head(20).to_string(index=False))
