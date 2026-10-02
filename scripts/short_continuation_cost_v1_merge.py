import argparse,glob,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.sweep_reclaim_merge as base

D1=pd.Timestamp("2024-01-01T00:00:00Z")
D2=pd.Timestamp("2025-01-01T00:00:00Z")
COSTS=(0,5,10,15,20,25,30,40)

def pf(x):
 x=np.asarray(x,float);gp=x[x>0].sum();gl=-x[x<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

def net_r(g,bp):
 return (g.gross_return.to_numpy(float)-bp/10000.0)/g.risk_pct.to_numpy(float)

def select_exec(g):
 z=g.sort_values(["entry_time","impulse_atr","symbol"],ascending=[True,False,True]).copy()
 accepted=[];openp=[];conc=[];expo=[]
 for _,r in z.iterrows():
  et=int(r.entry_time)
  openp=[p for p in openp if p["exit_time"]>et]
  if len(openp)>=base.MAX_OPEN:continue
  if any(p["symbol"]==r.symbol for p in openp):continue
  notional=base.RISK_BUDGET/float(r.risk_pct)
  gross=sum(p["notional"] for p in openp)
  if gross+notional>base.MAX_GROSS+1e-12:continue
  accepted.append(r.to_dict())
  openp.append({"symbol":r.symbol,"exit_time":int(r.exit_time),"notional":notional})
  conc.append(len(openp));expo.append(gross+notional)
 return pd.DataFrame(accepted),conc,expo

def account_sim(sel,bp):
 if not len(sel):return {}
 z=sel.sort_values(["entry_time","exit_time"]).reset_index(drop=True)
 events=[]
 for i,r in z.iterrows():
  events.append((int(r.entry_time),1,i))
  events.append((int(r.exit_time),0,i))
 events.sort(key=lambda x:(x[0],x[1]))
 eq=1.0;peak=1.0;mdd=0.0;openrisk={};pnls=[];rs=[]
 for ts,typ,i in events:
  if typ==0:
   if i not in openrisk:continue
   risk_cash=openrisk.pop(i)
   r=z.iloc[i]
   nr=(float(r.gross_return)-bp/10000.0)/float(r.risk_pct)
   pnl=risk_cash*nr;eq+=pnl;pnls.append(pnl);rs.append(nr)
   peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 0)
  else:
   openrisk[i]=eq*base.RISK_BUDGET
 arr=np.asarray(rs,float)
 years=max((z.entry_time.max()-z.entry_time.min())/(365.25*86400000),1/365.25)
 cagr=(eq**(1/years)-1) if eq>0 else -1.0
 return {"return_pct":100*(eq-1),"cagr_pct":100*cagr,"mdd_pct":100*mdd,
         "pf":pf(np.asarray(pnls,float)),"win_pct":100*np.mean(arr>0) if len(arr) else np.nan,
         "avg_r":arr.mean() if len(arr) else np.nan,"ending_equity":eq}

def summarize(g,label):
 if not len(g):return {"split":label,"raw_n":0}
 sel,conc,expo=select_exec(g)
 days=max((g.entry_time.max()-g.entry_time.min())/86400000,1)
 q={"split":label,"raw_n":len(g),"exec_n":len(sel),"exec_per_day":len(sel)/days,
    "gross_pf":pf(g.gross_r),"gross_win_pct":100*np.mean(g.gross_r>0),
    "avg_risk_pct":100*g.risk_pct.mean(),"avg_hold_min":g.hold_min.mean(),
    "symbols":g.symbol.nunique(),"avg_concurrency":float(np.mean(conc)) if conc else 0.0,
    "max_exposure_pct":100*max(expo) if expo else 0.0}
 for bp in COSTS:q[f"pf{bp}"]=pf(net_r(g,bp))
 for bp in (10,20):
  a=account_sim(sel,bp)
  for k,v in a.items():q[f"acct{bp}_{k}"]=v
 # break-even cost on 1bp grid
 be=-1
 for bp in range(0,51):
  v=pf(net_r(g,bp))
  if np.isfinite(v) and v>=1:be=bp
 q["breakeven_bp"]=be
 return q

def f(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",default="artifacts");a=ap.parse_args()
 fs=sorted(glob.glob(str(Path(a.input)/"**/short_continuation_cost_*.csv.gz"),recursive=True))
 if not fs:raise RuntimeError("no shards")
 d=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 if not len(d):raise RuntimeError("empty ledger")
 if d.duplicated(["config","symbol","impulse_time","entry_time"]).any():raise RuntimeError("duplicates")
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True)
 d["year"]=d.dt.dt.year
 rows=[]
 for cfg,g in d.groupby("config",sort=True):
  splits={"DEV_2021_2023":g[g.dt<D1],"VALID_2024":g[(g.dt>=D1)&(g.dt<D2)],"EVAL_2025_2026":g[g.dt>=D2]}
  for lab,x in splits.items():
   q=summarize(x,lab);q["config"]=cfg;rows.append(q)
 s=pd.DataFrame(rows);s.to_csv("short_continuation_cost_summary.csv",index=False)

 yr=[]
 for (cfg,y),g in d.groupby(["config","year"],sort=True):
  q=summarize(g,str(y));q["config"]=cfg;yr.append(q)
 ydf=pd.DataFrame(yr);ydf.to_csv("short_continuation_cost_yearly.csv",index=False)

 lines=["# Short Continuation Cost-Resistance V1","",
 "Fixed hypothesis: SHORT only; H1 >=2ATR impulse; H4 EMA20<EMA50 and close<EMA20; 2 entry modes.",
 "Grid: entry IMM/CONF x SL 1/1.5/2 H1 ATR x TP 1.5/2/2.5/3R. Max hold 6h.",
 "Costs: 0/5/10/15/20/25/30/40bp; corrected short-return formula; canonical Binance 1m chronology.",
 "Portfolio diagnostics: 0.5% account risk/trade, no daily cap, max 3 open, max gross 200%.",
 "",
 "| Config | Split | N | Exec | /day | GrossPF | PF5 | PF10 | PF15 | PF20 | PF30 | BE bp | Risk% | Hold | Ret10 | MDD10 | Ret20 | MDD20 |",
 "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in s.sort_values(["config","split"]).iterrows():
  lines.append(f"| {r.config} | {r.split} | {int(r.raw_n)} | {int(r.exec_n)} | {f(r.exec_per_day,2)} | {f(r.gross_pf)} | {f(r.pf5)} | {f(r.pf10)} | {f(r.pf15)} | {f(r.pf20)} | {f(r.pf30)} | {int(r.breakeven_bp)} | {f(r.avg_risk_pct,2)}% | {f(r.avg_hold_min,0)}m | {f(r.acct10_return_pct,1)}% | {f(r.acct10_mdd_pct,1)}% | {f(r.acct20_return_pct,1)}% | {f(r.acct20_mdd_pct,1)}% |")
 lines+=["","2025-26 is evaluation, not pristine untouched OOS because related impulse-family results were previously inspected."]
 Path("short_continuation_cost_report.md").write_text("\n".join(lines)+"\n")
 d.drop(columns=["dt"]).to_csv("short_continuation_cost_all.csv.gz",index=False,compression="gzip")
 print("\n".join(lines))
if __name__=="__main__":main()
