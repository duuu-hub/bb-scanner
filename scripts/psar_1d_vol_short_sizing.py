import glob,pandas as pd,numpy as np,json
SIZES=np.arange(.025,.525,.025)
COSTS=(20,40); MAX_POS=6; MAX_GROSS=2.0
VARIANTS=("E2.75_SB1.2_R0.75","E2.75_SB1_R0.75","E2.75_SB1_R1")
CUT=1735689600000  # 2025-01-01 UTC

def select(x,size):
    slots=min(MAX_POS,int(np.floor(MAX_GROSS/size+1e-12)))
    op={}; ids=[]
    for ts,g in x.groupby("fill_ts",sort=True):
        ts=int(ts)
        for sym in [k for k,v in op.items() if v<=ts]: del op[sym]
        for r in g.sort_values(["stop_pct","symbol"],kind="mergesort").itertuples():
            if r.symbol in op or len(op)>=slots: continue
            op[r.symbol]=int(r.exit_ts);ids.append(r.Index)
    return ids,slots

def replay(x,ids,size,cost):
    y=x.loc[ids].sort_values(["fill_ts","symbol"],kind="mergesort")
    eq=1.;peak=1.;mdd=0.;wins=losses=0;gp=gl=0.
    # realized-equity replay; notional fixed at entry as fraction of then-current equity
    events=[]
    for r in y.itertuples():
        events.append((int(r.fill_ts),1,r))
        events.append((int(r.exit_ts),0,r))
    events.sort(key=lambda z:(z[0],z[1])) # exits before entries at same ts
    notionals={}
    for ts,kind,r in events:
        if kind==1: notionals[r.Index]=eq*size
        else:
            n=notionals.pop(r.Index,None)
            if n is None: continue
            ret=(float(r.pnl_pct)-cost/100.)/100.
            pnl=n*ret;eq+=pnl
            if pnl>0:wins+=1;gp+=pnl
            elif pnl<0:losses+=1;gl-=pnl
            peak=max(peak,eq)
            if peak>0:mdd=max(mdd,(peak-eq)/peak*100)
    return dict(final_multiple=eq,total_return_pct=(eq-1)*100,mdd_pct=mdd,pf=gp/gl if gl else np.nan,win_pct=100*wins/(wins+losses) if wins+losses else np.nan,n=len(y))

fs=glob.glob("in/**/events_*.csv.gz",recursive=True);assert len(fs)==8
d=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
d=d[(d.side=="SHORT")&d.variant.isin(VARIANTS)&d.outcome.isin(["win","loss"])&d.exit_ts.notna()].copy()
d.fill_ts=d.fill_ts.astype("int64");d.exit_ts=d.exit_ts.astype("int64")
rows=[]
for v in VARIANTS:
 base=d[d.variant==v].copy()
 train=base[base.signal_ts<CUT]
 for pct in (30,20):
  q=train.atr_pct.quantile(1-pct/100)
  filt=base[base.atr_pct>=q].sort_values(["fill_ts","symbol"],kind="mergesort")
  for size in SIZES:
   ids,slots=select(filt,size)
   for cost in COSTS:
    z=replay(filt,ids,size,cost);z.update(variant=v,top_pct=pct,atr_threshold=q,size_pct=size*100,cost_bp=cost,slots=slots)
    rows.append(z)
r=pd.DataFrame(rows);r.to_csv("sizing.csv",index=False)
for cost in COSTS:
 print("\nCOST",cost)
 print(r[r.cost_bp==cost].sort_values(["final_multiple","mdd_pct"],ascending=[False,True]).head(20).to_string(index=False))
json.dump({"note":"ATR percentile thresholds fit on pre-2025 train only; accepted trade IDs are cost-independent per size; max 6 positions and slot-equivalent 200% gross cap","rows":rows},open("sizing.json","w"),indent=2)
