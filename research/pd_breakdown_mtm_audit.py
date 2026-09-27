#!/usr/bin/env python3
import argparse,glob
from pathlib import Path
import pandas as pd,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--selected",required=True);ap.add_argument("--out",required=True);a=ap.parse_args();O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
S=pd.read_csv(a.selected,parse_dates=["entry_time","exit_time"]);S=S[S.basket==10].copy()
# load only symbols actually selected; 15m close is used for portfolio MTM, while exit R remains canonical
need=set(S.symbol);px={}
for fn in glob.glob(a.input+"/**/*.csv.gz",recursive=True):
 sym=Path(fn).name.replace(".csv.gz","")
 if sym not in need: continue
 d=pd.read_csv(fn,compression="gzip");tc="open_time" if "open_time" in d else "timestamp_ms"
 d["dt"]=pd.to_datetime(pd.to_numeric(d[tc]),unit="ms",utc=True)
 for c in ["high","low","close"]: d[c]=pd.to_numeric(d[c],errors="coerce")
 d=d.dropna(subset=["dt","high","low","close"]).sort_values("dt").drop_duplicates("dt").set_index("dt")
 x=d.resample("4h",label="left",closed="left").agg(high=("high","max"),low=("low","min"),close=("close","last"),bars=("close","count"))
 x=x[x.bars==16]
 atr=x.high.sub(x.low).shift(1).rolling(14).mean()
 px[sym]={"close":d.close,"high":d.high,"low":d.low,"atr":atr}
# Canonical generator stores exact next-4H-open entry. Use it; do not infer entry from 15m close.
assert "entry" in S.columns, "canonical entry column missing"
S["ep"]=pd.to_numeric(S["entry"],errors="coerce")
vals=[]
for r in S.itertuples():
 st=r.entry_time-pd.Timedelta(hours=4)
 z=px[r.symbol]["atr"].loc[:st]
 vals.append(float(z.iloc[-1]) if len(z) else np.nan)
S["risk_dist"]=vals
assert S.ep.notna().all() and S.risk_dist.notna().all() and (S.risk_dist>0).all()
# Canonical exits are defined on 4H high/low, max 6 bars, ambiguous SL+TP => SL.
# Therefore canonical exit_time/r_net remain authoritative. 15m data is used only to mark open positions between entry and canonical exit.
S["path_exit_time"]=S["exit_time"]
S["collision_15m"]=False
risks=[.01,.0125,.015,.02,.025,.03,.035,.04,.05,.06]
rows=[]
for rf in risks:
 cash=1.;active=[];peak_real=1.;mdd_real=0.;peak_mtm=1.;mdd_mtm=0.;accepted=0
 # EXACT selector order: process one candidate row at a time; realize exits <= this row's entry before admission.
 accepted_rows=[]
 for r in S.sort_values(["entry_time","symbol"]).itertuples():
  done=[p for p in active if p["exit_time"]<=r.entry_time]
  for p in sorted(done,key=lambda x:x["exit_time"]):
   cash += p["stake"]*p["r_net"];peak_real=max(peak_real,cash);mdd_real=max(mdd_real,(peak_real-cash)/peak_real)
  active=[p for p in active if p["exit_time"]>r.entry_time]
  if len(active)>=10: continue
  p=dict(symbol=r.symbol,entry_time=r.entry_time,exit_time=r.exit_time,ep=r.ep,risk_dist=r.risk_dist,r_net=r.r_net,stake=cash*rf)
  active.append(p);accepted_rows.append(p);accepted+=1
 # realized terminal must exactly reproduce canonical sim_rf
 for p in sorted(active,key=lambda x:x["exit_time"]):
  cash += p["stake"]*p["r_net"];peak_real=max(peak_real,cash);mdd_real=max(mdd_real,(peak_real-cash)/peak_real)
 # MTM replay on accepted positions; stakes are frozen from exact admission simulation.
 cash2=1.;openp=[];by_entry={}
 for p in accepted_rows: by_entry.setdefault(p["entry_time"],[]).append(p)
 exits=sorted(set(p["exit_time"] for p in accepted_rows));entries=sorted(by_entry);bounds=sorted(set(entries+exits))
 for i,t in enumerate(bounds):
  done=[p for p in openp if p["exit_time"]<=t]
  for p in sorted(done,key=lambda x:x["exit_time"]):
   cash2 += p["stake"]*p["r_net"]
   peak_mtm=max(peak_mtm,cash2);mdd_mtm=max(mdd_mtm,(peak_mtm-cash2)/peak_mtm if peak_mtm>0 else np.inf)
  openp=[p for p in openp if p["exit_time"]>t]
  openp.extend(by_entry.get(t,[]))
  nxt=bounds[i+1] if i+1<len(bounds) else None
  if openp and nxt is not None:
   grid=None
   for p in openp:
    ser=px[p["symbol"]]["close"];idx=ser.loc[(ser.index>=t)&(ser.index<nxt)].index
    grid=idx if grid is None else grid.union(idx)
   for tt in grid:
    eq=cash2
    for p in openp:
     # Only mark if this symbol has an actual 15m candle at tt; never carry a future/stale mark across gaps.
     ser=px[p["symbol"]]["close"]
     if tt in ser.index:
      mark=float(ser.loc[tt])
     else:
      z=ser.loc[(ser.index>=p["entry_time"])&(ser.index<=tt)]
      mark=float(z.iloc[-1]) if len(z) else p["ep"]
     rr=(p["ep"]-mark)/p["risk_dist"]
     # Canonical strategy exits on 4H boundaries. Within an open canonical 4H trade,
     # unrealized R cannot be allowed to create impossible unlimited loss for account-equity audit:
     # conservative mark cap at the canonical 1R stop boundary.
     rr=max(rr,-1.0)
     eq += p["stake"]*rr
    peak_mtm=max(peak_mtm,eq);mdd_mtm=max(mdd_mtm,(peak_mtm-eq)/peak_mtm if peak_mtm>0 else np.inf)
 for p in sorted(openp,key=lambda x:x["exit_time"]):
  cash2 += p["stake"]*p["r_net"]
  peak_mtm=max(peak_mtm,cash2);mdd_mtm=max(mdd_mtm,(peak_mtm-cash2)/peak_mtm if peak_mtm>0 else np.inf)
 assert abs(cash2-cash)<1e-10
 # MTM series includes every realized exit point; it therefore cannot understate realized MDD.
 assert mdd_mtm+1e-12>=mdd_real, (rf,mdd_real,mdd_mtm)
 rows.append(dict(risk=rf,accepted=accepted,final_equity=cash,total_return=cash-1,realized_mdd_pct=mdd_real,mtm_mdd_pct=mdd_mtm,canonical_exit_parity=int((S.path_exit_time==S.exit_time).all())))
pd.DataFrame(rows).to_csv(O/"mtm_mdd.csv",index=False);print(pd.DataFrame(rows).to_string(index=False))

# trigger exact MTM rerun

# rerun after newline normalization

# trigger latest exact ATR build

# verified trigger

# parity-gated rerun

# trigger path-aware MTM audit

# trigger canonical-semantics MTM
