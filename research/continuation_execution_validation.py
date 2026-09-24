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


# Portfolio sizing / overlap study: preserve every cross-symbol opportunity while
# suppressing duplicate same-symbol entries during an already-open position.
SIZES=[0.02,0.03,0.04,0.05,0.075,0.10,0.125,0.15,0.20,0.25]
def portfolio_study(d):
 rows=[]; overlap=[]
 base=d[d.delay_bars==0].copy()
 for keys,z in base.groupby(["side","tp","sl","horizon_h"]):
  z=z.sort_values(["signal_ts","symbol"]).copy()
  accepted=[]; open_until={}; same_open=0; total=0
  for r in z.itertuples():
   total+=1
   if open_until.get(r.symbol,-1)>=r.entry_ts:
    same_open+=1; continue
   accepted.append(r)
   open_until[r.symbol]=r.exit_ts
  a=pd.DataFrame([r._asdict() for r in accepted])
  overlap.append(dict(side=keys[0],tp=keys[1],sl=keys[2],horizon_h=keys[3],
   raw_signals=total,accepted_signals=len(a),same_symbol_while_open=same_open,
   same_symbol_while_open_pct=100*same_open/total if total else 0))
  if a.empty: continue
  for size in SIZES:
   cash=1.0; positions=[]; peak=1.0; mdd=0.; rejected=0; max_open=0
   events=sorted(set(a.entry_ts.tolist()+a.exit_ts.tolist()))
   by_entry={t:g for t,g in a.groupby("entry_ts")}; by_exit={t:g for t,g in a.groupby("exit_ts")}
   active={}
   for t in events:
    if t in by_exit:
     for r in by_exit[t].itertuples():
      k=(r.symbol,r.entry_ts)
      if k in active:
       alloc=active.pop(k); cash += alloc*(1+(r.gross_ret_pct-0.20)/100)
    if t in by_entry:
     for r in by_entry[t].itertuples():
      alloc=size
      if cash+1e-12 < alloc: rejected+=1; continue
      cash-=alloc; active[(r.symbol,r.entry_ts)]=alloc
    eq=cash+sum(active.values()); peak=max(peak,eq); mdd=min(mdd,(eq/peak-1)*100); max_open=max(max_open,len(active))
   final=cash+sum(active.values())
   rows.append(dict(side=keys[0],tp=keys[1],sl=keys[2],horizon_h=keys[3],
    position_size_pct=size*100,accepted_signals=len(a),rejected_for_capital=rejected,
    max_simultaneous_positions=max_open,total_return_pct=(final-1)*100,mdd_pct=mdd))
 pd.DataFrame(overlap).to_csv(OUT/"overlap_summary.csv",index=False)
 pd.DataFrame(rows).to_csv(OUT/"position_sizing.csv",index=False)
 print("\n=== OVERLAP SUMMARY ==="); print(pd.DataFrame(overlap).to_string(index=False))
 print("\n=== POSITION SIZING ==="); print(pd.DataFrame(rows).to_string(index=False))




SLOTS=[5,10,15,20]
def slot_study(d):
 base=d[d.delay_bars==0].copy()
 rows=[]
 for keys,z in base.groupby(["side","tp","sl","horizon_h"]):
  z=z.sort_values(["signal_ts","symbol"]).copy()
  # same-symbol one-position-at-a-time first; preserve chronological opportunities
  accepted=[]; open_until={}
  for r in z.itertuples():
   if open_until.get(r.symbol,-1)>=r.entry_ts: continue
   accepted.append(r); open_until[r.symbol]=r.exit_ts
  a=pd.DataFrame([r._asdict() for r in accepted])
  if a.empty: continue
  for slots in SLOTS:
   # equal fixed slot allocation = 1/slots of initial equity per open trade
   alloc=1.0/slots; cash=1.0; active={}; peak=1.0; mdd=0.; taken=0; missed=0; max_open=0
   # rank simultaneous candidates by frozen-rule continuation strength proxies:
   # SHORT: larger rv24, rv4 and more-negative ret24; LONG: larger rv24/accel and deeper pullback.
   events=sorted(set(a.entry_ts.tolist()+a.exit_ts.tolist()))
   by_entry={t:g.copy() for t,g in a.groupby("entry_ts")}; by_exit={t:g for t,g in a.groupby("exit_ts")}
   for t in events:
    if t in by_exit:
     for r in by_exit[t].itertuples():
      k=(r.symbol,r.entry_ts)
      if k in active:
       stake=active.pop(k); cash += stake*(1+(r.gross_ret_pct-0.20)/100)
    if t in by_entry:
     g=by_entry[t]
     # deterministic ranking; strongest absolute realized pre-entry signal cannot be reconstructed
     # from trade rows, so use stable ordering here. Slot-count impact is the target of this pass.
     for r in g.sort_values("symbol").itertuples():
      if len(active)>=slots or cash+1e-12<alloc: missed+=1; continue
      cash-=alloc; active[(r.symbol,r.entry_ts)]=alloc; taken+=1
    eq=cash+sum(active.values()); peak=max(peak,eq); mdd=min(mdd,(eq/peak-1)*100); max_open=max(max_open,len(active))
   final=cash+sum(active.values())
   rows.append(dict(side=keys[0],tp=keys[1],sl=keys[2],horizon_h=keys[3],slots=slots,
    position_size_pct=100/slots,available_signals=len(a),taken= taken,missed=missed,
    capture_rate_pct=100*taken/len(a),max_open=max_open,total_return_pct=(final-1)*100,mdd_pct=mdd))
 out=pd.DataFrame(rows); out.to_csv(OUT/"slot_study.csv",index=False)
 print("\n=== SLOT STUDY ==="); print(out.to_string(index=False))

if __name__=="__main__": main(); portfolio_study(pd.read_csv(OUT/"trades.csv.gz")); slot_study(pd.read_csv(OUT/"trades.csv.gz"))
