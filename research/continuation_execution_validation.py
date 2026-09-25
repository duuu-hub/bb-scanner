from __future__ import annotations
from pathlib import Path
import numpy as np, pandas as pd
import continuation_validation as cv
import continuation_mining as cm
import continuation_execution_core as exec_core

OUT=Path("continuation_execution_results"); OUT.mkdir(exist_ok=True)
CANDS=[("SHORT",5.,3.,6),("SHORT",4.,3.,6),("SHORT",4.,2.,6),("SHORT",3.,2.,6),("LONG",5.,3.,12),("LONG",5.,2.,12)]
COSTS=[("base",0.12,0.08),("stress1.5x",0.18,0.12),("stress2x",0.24,0.16)] # round-trip fee%, total slippage%
DELAY_BARS=[0,1] # 0=next bar open; 1=extra 15m delay (conservative proxy for +1m)

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
 raw=cm.load("market_data_store/bitget/research_auto100_15m")
 groups={s:g.sort_values("timestamp_ms").reset_index(drop=True) for s,g in raw.groupby("symbol")}
 del raw
 rules={s:cv.rule_from_train(tr,s) for s in ("LONG","SHORT")}
 # Phase 1 only: frozen SHORT 5/3/6h, next-bar-open, full 15m path.
 side,tp,sl,h="SHORT",5.,3.,6
 sig=te[te.direction==side].copy()
 sig=sig[cv.apply_rule(sig,rules[side])].sort_values("timestamp_ms")
 rows=[]; skipped_gap=0
 for rr in sig.itertuples():
  g=groups[rr.symbol]; ts=g.timestamp_ms.to_numpy()
  base=int(np.searchsorted(ts,int(rr.timestamp_ms))); ei=base+1
  if ei>=len(g) or int(g.iloc[ei].timestamp_ms)!=int(rr.timestamp_ms)+900_000:
   skipped_gap+=1; continue
  r=exec_core.replay_trade(g,base,side,tp,sl,int(h*4))
  if r is None:
   skipped_gap+=1; continue
  ei=int(r["entry_i"]); xi=int(r["exit_i"])
  rows.append(dict(symbol=rr.symbol,signal_ts=int(rr.timestamp_ms),entry_ts=int(g.iloc[ei].timestamp_ms),
   exit_ts=int(g.iloc[xi].timestamp_ms),entry_px=float(r["entry_px"]),gross_ret_pct=float(r["gross_ret_pct"]),exit_reason=r["exit_reason"]))
 d=pd.DataFrame(rows)
 d.to_csv(OUT/"audit_trades.csv.gz",index=False,compression="gzip")
 amb=d[d.exit_reason.eq("BOTH_SL")].copy()
 amb.to_csv(OUT/"ambiguous_15m.csv",index=False)
 s=stats(d,0.20)
 audit=dict(raw_signals=len(sig),executed=len(d),skipped_entry_gap=skipped_gap,
  ambiguous_15m=len(amb),ambiguous_pct=(100*len(amb)/len(d) if len(d) else 0),**s)
 pd.DataFrame([audit]).to_csv(OUT/"audit_summary.csv",index=False)
 print("=== PHASE1 EXECUTION AUDIT ===",flush=True)
 print(pd.DataFrame([audit]).to_string(index=False),flush=True)
 print("=== AMBIGUOUS SAMPLE ===",flush=True)
 print(amb.head(20).to_string(index=False),flush=True)

if __name__=="__main__":
 main()
