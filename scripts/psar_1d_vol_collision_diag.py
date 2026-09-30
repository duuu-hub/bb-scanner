import glob,pandas as pd,numpy as np
V="E2.75_SB1.2_R0.75";CUT=1735689600000;SIZE=.025
fs=glob.glob("in/**/events_*.csv.gz",recursive=True);assert len(fs)==8
d=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
d=d[(d.side=="SHORT")&(d.variant==V)&d.outcome.isin(["win","loss"])&d.exit_ts.notna()].copy()
d.fill_ts=d.fill_ts.astype("int64");d.exit_ts=d.exit_ts.astype("int64")
q=d[d.signal_ts<CUT].atr_pct.quantile(.70);x=d[d.atr_pct>=q].sort_values(["fill_ts","symbol"],kind="mergesort")
def select(z):
 op={};ids=[]
 for ts,g in z.groupby("fill_ts",sort=True):
  ts=int(ts)
  for sym in [k for k,v in op.items() if v<=ts]: del op[sym]
  for r in g.sort_values(["stop_pct","symbol"],kind="mergesort").itertuples():
   if r.symbol in op or len(op)>=6:continue
   op[r.symbol]=int(r.exit_ts);ids.append(r.Index)
 return ids
def stats(z,cost=0):
 p=z.pnl_pct.astype(float)-cost/100.;gp=p[p>0].sum();gl=-p[p<0].sum()
 return len(z),(p>0).mean()*100,gp/gl if gl else np.nan,p.mean()
ids1=select(x)
# independently repeated implementation for equality audit
op={};ids2=[]
for ts in sorted(x.fill_ts.unique()):
 for sym in list(op):
  if op[sym]<=int(ts): del op[sym]
 g=x[x.fill_ts==ts].sort_values(["stop_pct","symbol"],kind="mergesort")
 for r in g.itertuples():
  if r.symbol not in op and len(op)<6:
   op[r.symbol]=int(r.exit_ts);ids2.append(r.Index)
assert ids1==ids2,(len(ids1),len(ids2),set(ids1)^set(ids2))
print("THRESH",q,"RAW",stats(x),"ACCEPTED",stats(x.loc[ids1]),"20BP",stats(x.loc[ids1],20),"40BP",stats(x.loc[ids1],40))
# duration diagnostic is descriptive only, not a tradable filter
z=x.loc[ids1].copy();z["days"]=(z.exit_ts-z.fill_ts)/86400000
for lim in (1,3,7,14,30,90):
 print("REALIZED_WITHIN",lim,stats(z[z.days<=lim]))
print("DURATION_Q",z.days.quantile([.25,.5,.75,.9,.95,.99]).to_dict())
print("NOTE: true forced time exits require candle-path repricing at 3/7/14d; existing terminal pnl cannot simulate them without lookahead.")
