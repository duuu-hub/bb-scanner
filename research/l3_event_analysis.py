from __future__ import annotations
from pathlib import Path
import pandas as pd
from research.robustness_killtest import load, policies

OUT=Path("l3_event_analysis_results"); OUT.mkdir(exist_ok=True)

def event_cluster(g, hours):
    g=g.sort_values("signal_ts").copy()
    gap=hours*3600*1000
    ev=[]; eid=-1; last=None
    for ts in g.signal_ts:
        if last is None or ts-last>gap: eid+=1
        ev.append(eid); last=ts
    g["event_id"]=ev
    return g

def main():
    en=load(); p=policies(en)["L3_BREADTH_SWITCH"].copy()
    # Delay=1 is the primary execution assumption. Report both all rows and test30.
    rows=[]; syms=[]
    for split in ("all","train70","test30"):
      base=p[p.delay_min==1].copy()
      if split!="all": base=base[base.split==split]
      for direction in ("LONG","SHORT"):
        g=base[base.direction==direction].copy()
        if g.empty: continue
        for hours in (6,12,24,48):
          z=event_cluster(g,hours)
          ec=z.groupby("event_id").agg(start_ts=("signal_ts","min"),trades=("net_pct","size"),
              symbols=("symbol","nunique"),event_pnl=("net_pct","sum")).reset_index()
          pos=ec[ec.event_pnl>0]
          rows.append({"split":split,"direction":direction,"cluster_hours":hours,
             "trades":len(z),"unique_symbols":z.symbol.nunique(),"independent_events":len(ec),
             "profitable_events":len(pos),"profitable_event_pct":100*len(pos)/len(ec),
             "avg_event_pnl":ec.event_pnl.mean(),"median_event_pnl":ec.event_pnl.median(),
             "sum_pnl":ec.event_pnl.sum(),
             "top1_event_share_abs_pct":100*ec.event_pnl.abs().nlargest(1).sum()/ec.event_pnl.abs().sum(),
             "top3_event_share_abs_pct":100*ec.event_pnl.abs().nlargest(min(3,len(ec))).sum()/ec.event_pnl.abs().sum()})
        c=g.groupby("symbol").agg(trades=("net_pct","size"),pnl=("net_pct","sum")).sort_values("pnl",ascending=False)
        total=len(c)
        abs_sum=c.pnl.abs().sum()
        for k in (1,3,5):
          kk=min(k,total)
          syms.append({"split":split,"direction":direction,"top_k":kk,"total_symbols":total,
             "top_k_pct_of_symbols":100*kk/total if total else 0,
             "top_k_abs_pnl_share_pct":100*c.pnl.abs().head(kk).sum()/abs_sum if abs_sum else 0})
    pd.DataFrame(rows).to_csv(OUT/"l3_independent_events.csv",index=False)
    pd.DataFrame(syms).to_csv(OUT/"l3_symbol_share_with_denominator.csv",index=False)
    print(pd.DataFrame(rows).to_string(index=False))
    print("\n=== symbol denominator ===")
    print(pd.DataFrame(syms).to_string(index=False))

if __name__=="__main__": main()
