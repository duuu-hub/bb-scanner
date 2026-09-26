from pathlib import Path
import zipfile, pandas as pd, numpy as np
ZIP=Path("input.zip"); W=Path("w"); O=Path("continuation_robustness"); W.mkdir(exist_ok=True); O.mkdir(exist_ok=True)
with zipfile.ZipFile(ZIP) as z:z.extractall(W)
d=pd.read_csv(W/"trades_final.csv.gz").sort_values(["entry_ts","signal_ts"]).reset_index(drop=True)
def metrics(x,cost):
 r=x.gross_ret_pct.to_numpy()-cost; pos=r[r>0].sum(); neg=-r[r<0].sum()
 return dict(n=len(r),win_rate=(r>0).mean() if len(r) else np.nan,expectancy_pct=r.mean() if len(r) else np.nan,pf=pos/neg if neg>0 else np.inf)
# cost stress
pd.DataFrame([{"cost_rt_pct":c,**metrics(d,c)} for c in [.20,.30,.45,.70]]).to_csv(O/"cost_stress.csv",index=False)
# chronological quartiles, no re-fitting
d["entry_dt"]=pd.to_datetime(d.entry_ts,unit="ms",utc=True)
chunks=np.array_split(np.arange(len(d)),4); rows=[]
for i,ix in enumerate(chunks,1):
 x=d.iloc[ix]; rows.append({"segment":f"Q{i}","start":x.entry_dt.min(),"end":x.entry_dt.max(),**metrics(x,.20)})
pd.DataFrame(rows).to_csv(O/"oos_quartiles.csv",index=False)
# portfolio: global slot cap, one-symbol stream already enforced. Equal seed/slot sizing.
def portfolio(slots,cost=.20,seed=1.0):
 free=[0]*slots; eq=seed; peak=seed; mdd=0.; accepted=0; rejected=0; curve=[]
 for r in d.itertuples():
  t=int(r.entry_ts); avail=[i for i,x in enumerate(free) if x<=t]
  if not avail: rejected+=1; continue
  k=avail[0]; alloc=eq/slots; pnl=alloc*((float(r.gross_ret_pct)-cost)/100.0)
  # Realized at exit; equity update here is conservative bookkeeping, not intratrade MTM.
  eq+=pnl; accepted+=1; free[k]=int(r.exit_ts); peak=max(peak,eq); mdd=min(mdd,eq/peak-1); curve.append((r.exit_ts,eq))
 return dict(slots=slots,accepted=accepted,rejected=rejected,capture_pct=accepted/len(d)*100,final_equity=eq,total_return_pct=(eq/seed-1)*100,realized_mdd_pct=mdd*100)
p=pd.DataFrame([portfolio(s) for s in [1,3,5,10]]);p.to_csv(O/"slot_portfolio.csv",index=False)
print("COST");print(pd.read_csv(O/"cost_stress.csv").to_string(index=False))
print("QUARTILES");print(pd.read_csv(O/"oos_quartiles.csv").to_string(index=False))
print("SLOTS");print(p.to_string(index=False))
