import glob,pandas as pd,numpy as np
V="E2.75_SB1.2_R0.75";CUT=1735689600000
fs=glob.glob("in/**/events_*.csv.gz",recursive=True);assert len(fs)==8
d=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
d=d[(d.side=="SHORT")&(d.variant==V)&d.outcome.isin(["win","loss"])&d.exit_ts.notna()].copy()
d.fill_ts=d.fill_ts.astype("int64");d.exit_ts=d.exit_ts.astype("int64")
q=d[d.signal_ts<CUT].atr_pct.quantile(.70);x=d[d.atr_pct>=q].sort_values(["fill_ts","stop_pct","symbol"],kind="mergesort").copy()
def stats(z):
 p=z.pnl_pct.astype(float);gp=p[p>0].sum();gl=-p[p<0].sum()
 return len(z),(p>0).mean()*100,gp/gl if gl else np.nan,p.mean()
print("THRESH",q,"RAW",stats(x))
for mode in ("same_only","cap_only","both"):
 op={};acc=[];rej_sym=[];rej_cap=[]
 for ts,g in x.groupby("fill_ts",sort=True):
  ts=int(ts)
  op={k:v for k,v in op.items() if v<=ts}
  for r in g.sort_values(["stop_pct","symbol"],kind="mergesort").itertuples():
   same=r.symbol in op
   cap=len(op)>=6
   if mode in ("same_only","both") and same: rej_sym.append(r.Index);continue
   if mode in ("cap_only","both") and cap: rej_cap.append(r.Index);continue
   op[r.symbol]=int(r.exit_ts);acc.append(r.Index)
 print("\nMODE",mode,"ACC",stats(x.loc[acc]))
 if rej_sym: print("REJ_SYM",stats(x.loc[rej_sym]))
 if rej_cap: print("REJ_CAP",stats(x.loc[rej_cap]))
# duration buckets and simultaneous crowding
x["days"]=(x.exit_ts-x.fill_ts)/86400000
x["durbin"]=pd.cut(x.days,[-1,1,3,7,14,30,90,1e9])
print("\nDURATION")
for b,g in x.groupby("durbin",observed=True):print(b,stats(g))
# selection ranking proxies
for col,asc in [("stop_pct",True),("atr_pct",False),("atr_pct",True)]:
 op={};ids=[]
 for ts,g in x.groupby("fill_ts",sort=True):
  ts=int(ts);op={k:v for k,v in op.items() if v<=ts}
  for r in g.sort_values([col,"symbol"],ascending=[asc,True],kind="mergesort").itertuples():
   if r.symbol in op or len(op)>=6:continue
   op[r.symbol]=int(r.exit_ts);ids.append(r.Index)
 print("RANK",col,asc,stats(x.loc[ids]))
