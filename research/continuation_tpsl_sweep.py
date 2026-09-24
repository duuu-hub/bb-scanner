from __future__ import annotations
from pathlib import Path
import numpy as np, pandas as pd
import continuation_validation as cv
import continuation_mining as cm
 # trigger workflow\nOUT=Path("continuation_tpsl_results"); OUT.mkdir(exist_ok=True)
TPS=(1.0,1.5,2.0,3.0,4.0,5.0); SLS=(0.5,0.75,1.0,1.5,2.0,3.0); HS={1:4,2:8,3:12,6:24,12:48}
def main():
 x=cv.prep().sort_values("timestamp_ms").reset_index(drop=True)
 times=np.sort(x.timestamp_ms.unique()); split=times[int(len(times)*.70)]; tr=x[x.timestamp_ms<split]; te=x[x.timestamp_ms>=split]
 groups={s:g.reset_index(drop=True) for s,g in x.groupby("symbol")}
 rules={}
 for side in ("LONG","SHORT"): rules[side]=cv.rule_from_train(tr,side)
 rows=[]
 for side,r in rules.items():
  q=te[te.direction==side].copy(); q=q[cv.apply_rule(q,r)].copy()
  labs={f"t{tp:g}_s{sl:g}_h{h}":[] for tp in TPS for sl in SLS for h in HS}
  for rr in q.itertuples():
   g=groups[rr.symbol]; i=int(np.searchsorted(g.timestamp_ms.to_numpy(),int(rr.timestamp_ms)))
   for tp in TPS:
    for sl in SLS:
     for h,bars in HS.items(): labs[f"t{tp:g}_s{sl:g}_h{h}"].append(cm.barrier_label(g,i,side,tp,sl,bars))
  for tp in TPS:
   for sl in SLS:
    for h in HS:
     a=np.array(labs[f"t{tp:g}_s{sl:g}_h{h}"],dtype=object); valid=a!="AMBIG"; a=a[valid]; n=len(a)
     if not n: continue
     w=(a=="WIN").mean(); loss=(a=="LOSS").mean(); timeout=(a=="TIMEOUT").mean()
     # gross expectancy in percent per signal; timeout conservatively zero before costs
     exp=w*tp-loss*sl
     be=sl/(tp+sl)
     rows.append(dict(side=side,tp_pct=tp,sl_pct=sl,horizon_h=h,n=n,win_rate=w,loss_rate=loss,timeout_rate=timeout,breakeven_win_rate=be,gross_expectancy_pct=exp,edge_vs_breakeven_pp=(w-be)*100,rules=r.rules))
 out=pd.DataFrame(rows)
 out.to_csv(OUT/"oos_tpsl_horizon_grid.csv",index=False)
 for side in ("LONG","SHORT"):
  z=out[out.side==side].sort_values(["gross_expectancy_pct","n"],ascending=[False,False]).head(25)
  print(f"=== {side} TOP OOS TP/SL/HORIZON ==="); print(z.to_string(index=False)); print()
 # robust shortlist requires n>=100, positive edge and timeout <50%
 sh=out[(out.n>=100)&(out.gross_expectancy_pct>0)&(out.timeout_rate<.5)].sort_values(["gross_expectancy_pct","n"],ascending=[False,False])
 sh.to_csv(OUT/"shortlist.csv",index=False)
 print("=== COMBINED SHORTLIST ==="); print(sh.head(40).to_string(index=False))
if __name__=="__main__": main()
