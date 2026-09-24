from __future__ import annotations
from pathlib import Path
import numpy as np, pandas as pd
import continuation_validation as cv
import continuation_mining as cm

OUT=Path("continuation_execution_results"); OUT.mkdir(exist_ok=True)
CANDS=[("SHORT",5.,3.,6),("SHORT",4.,3.,6),("SHORT",4.,2.,6),("SHORT",3.,2.,6),("LONG",5.,3.,12),("LONG",5.,2.,12)]
COSTS=[("base",0.12,0.08),("stress1.5x",0.18,0.12),("stress2x",0.24,0.16)] # round-trip fee%, total slippage%
DELAY_BARS=[0,1] # 0=next bar open; 1=extra 15m delay (conservative proxy for +1m)

def exit_trade(g,entry_i,side,tp,sl,bars,entry_px):
 end=min(len(g)-1,entry_i+bars-1)
 for j in range(entry_i,end+1):
  hi,lo=float(g.iloc[j].high),float(g.iloc[j].low)
  if side=="LONG": hit_tp=hi>=entry_px*(1+tp/100); hit_sl=lo<=entry_px*(1-sl/100)
  else: hit_tp=lo<=entry_px*(1-tp/100); hit_sl=hi>=entry_px*(1+sl/100)
  if hit_tp and hit_sl: return None
  if hit_tp: return j,tp,"TP"
  if hit_sl: return j,-sl,"SL"
 px=float(g.iloc[end].close); ret=(px/entry_px-1)*100*(1 if side=="LONG" else -1)
 return end,ret,"TIME"

def maxdd(rs):
 eq=(1+pd.Series(rs)/100).cumprod(); peak=eq.cummax(); return float(((eq/peak)-1).min()*100)

def stats(df,cost):
 z=df.copy(); z["net"]=z.gross_ret_pct-cost
 return dict(n=len(z),win_rate=float((z.net>0).mean()),net_expectancy_pct=float(z.net.mean()),
  pf=float(z.loc[z.net>0,"net"].sum()/abs(z.loc[z.net<0,"net"].sum())) if (z.net<0).any() else np.inf,
  compounded_return_pct=float(((1+z.net/100).prod()-1)*100),mdd_pct=maxdd(z.net.tolist()),
  max_consecutive_losses=max((sum(1 for _ in grp) for val,grp in __import__("itertools").groupby(z.net<0) if val),default=0))

def main():
 x=cv.prep().sort_values("timestamp_ms").reset_index(drop=True)
 times=np.sort(x.timestamp_ms.unique()); split=times[int(len(times)*.70)]; tr=x[x.timestamp_ms<split]; te=x[x.timestamp_ms>=split]
 groups={s:g.sort_values("timestamp_ms").reset_index(drop=True) for s,g in x.groupby("symbol")}
 rules={s:cv.rule_from_train(tr,s) for s in ("LONG","SHORT")}
 alltr=[]
 for side,tp,sl,h in CANDS:
  sig=te[te.direction==side].copy(); sig=sig[cv.apply_rule(sig,rules[side])].sort_values("timestamp_ms")
  for delay in DELAY_BARS:
   for rr in sig.itertuples():
    g=groups[rr.symbol]; base=int(np.searchsorted(g.timestamp_ms.to_numpy(),int(rr.timestamp_ms)))
    ei=base+1+delay
    if ei>=len(g): continue
    ep=float(g.iloc[ei].open); ex=exit_trade(g,ei,side,tp,sl,int(h*4),ep)
    if ex is None: continue
    xi,ret,why=ex
    alltr.append(dict(side=side,tp=tp,sl=sl,horizon_h=h,delay_bars=delay,symbol=rr.symbol,
      signal_ts=int(rr.timestamp_ms),entry_ts=int(g.iloc[ei].timestamp_ms),exit_ts=int(g.iloc[xi].timestamp_ms),
      gross_ret_pct=ret,exit_reason=why))
 d=pd.DataFrame(alltr); d.to_csv(OUT/"trades.csv.gz",index=False,compression="gzip")
 rows=[]
 for keys,z in d.groupby(["side","tp","sl","horizon_h","delay_bars"]):
  for name,fee,slip in COSTS:
   st=stats(z,fee+slip); rows.append(dict(side=keys[0],tp=keys[1],sl=keys[2],horizon_h=keys[3],delay_bars=keys[4],
    cost_case=name,roundtrip_cost_pct=fee+slip,**st))
 out=pd.DataFrame(rows); out.to_csv(OUT/"execution_summary.csv",index=False)
 print("=== EXECUTION SUMMARY ==="); print(out.to_string(index=False))
 # leave top selected symbols out, base costs, no extra delay
 rob=[]
 base=d[d.delay_bars==0]
 for keys,z in base.groupby(["side","tp","sl","horizon_h"]):
  tops=z.symbol.value_counts().head(5).index.tolist()
  for exsym in ["ALL"]+tops:
   zz=z if exsym=="ALL" else z[z.symbol!=exsym]
   rob.append(dict(side=keys[0],tp=keys[1],sl=keys[2],horizon_h=keys[3],excluded=exsym,**stats(zz,0.20)))
 pd.DataFrame(rob).to_csv(OUT/"symbol_robustness.csv",index=False)
 print("\n=== SYMBOL ROBUSTNESS ==="); print(pd.DataFrame(rob).to_string(index=False))
if __name__=="__main__": main()
\n# trigger\n