import argparse,glob,math
from pathlib import Path
import numpy as np,pandas as pd

CUT=pd.Timestamp("2025-01-01T00:00:00Z")
RISK_BUDGET=0.005; MAX_DAILY=2; MAX_OPEN=3; MAX_GROSS=2.0

def pf(x):
 x=np.asarray(x,float);gp=x[x>0].sum();gl=-x[x<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

def streak(x):
 b=c=0
 for v in x:
  if v<0:c+=1;b=max(b,c)
  else:c=0
 return b

def select_exec(g):
 z=g.sort_values(["entry_time","score","symbol"],ascending=[True,False,True]).copy()
 accepted=[];openp=[];day_counts={};conc=[];expo=[]
 for _,r in z.iterrows():
  et=int(r.entry_time)
  openp=[p for p in openp if p["exit_time"]>et]
  day=pd.Timestamp(et,unit="ms",tz="UTC").strftime("%Y-%m-%d")
  if day_counts.get(day,0)>=MAX_DAILY:continue
  if len(openp)>=MAX_OPEN:continue
  if any(p["symbol"]==r.symbol for p in openp):continue
  notional=RISK_BUDGET/float(r.risk_pct)
  gross=sum(p["notional"] for p in openp)
  if gross+notional>MAX_GROSS+1e-12:continue
  accepted.append(r.to_dict())
  openp.append({"symbol":r.symbol,"exit_time":int(r.exit_time),"notional":notional})
  day_counts[day]=day_counts.get(day,0)+1
  conc.append(len(openp));expo.append(gross+notional)
 out=pd.DataFrame(accepted)
 return out,conc,expo

def account_sim(sel,cost):
 if not len(sel):return {}
 events=[]
 for i,r in sel.reset_index(drop=True).iterrows():
  events.append((int(r.entry_time),1,i))
  events.append((int(r.exit_time),0,i)) # exits first at same timestamp
 events.sort(key=lambda x:(x[0],x[1]))
 eq=1.0;peak=1.0;mdd=0.0;openrisk={};pnls=[];rets=[];eqs=[1.0]
 for ts,typ,i in events:
  if typ==0:
   if i not in openrisk:continue
   risk_cash=openrisk.pop(i);nr=float(sel.reset_index(drop=True).iloc[i][f"net{cost}_r"])
   pnl=risk_cash*nr;eq+=pnl;pnls.append(pnl);rets.append(nr);peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 0);eqs.append(eq)
  else:
   openrisk[i]=eq*RISK_BUDGET
 arr=np.asarray(rets,float)
 years=max((sel.entry_time.max()-sel.entry_time.min())/(365.25*86400000),1/365.25)
 cagr=(eq**(1/years)-1) if eq>0 else -1.0
 return {"return_pct":100*(eq-1),"cagr_pct":100*cagr,"mdd_pct":100*mdd,"pf":pf(np.asarray(pnls,float)),
         "win_pct":100*np.mean(arr>0) if len(arr) else np.nan,"avg_r":arr.mean() if len(arr) else np.nan,
         "max_loss_streak":streak(arr),"ending_equity":eq}

def rawstats(g):
 if not len(g):return {}
 return {"raw_n":len(g),"raw_pf20":pf(g.net20_r),"raw_pf40":pf(g.net40_r),
         "raw_win20":100*np.mean(g.net20_r>0),"raw_avg20":g.net20_r.mean(),
         "raw_hold":g.hold_min.mean(),"symbols":g.symbol.nunique()}

def metrics(g,label):
 raw=rawstats(g);sel,conc,expo=select_exec(g)
 a20=account_sim(sel,20);a40=account_sim(sel,40)
 days=max((g.entry_time.max()-g.entry_time.min())/86400000,1) if len(g) else 1
 q={"split":label,**raw,"exec_n":len(sel),"exec_per_day":len(sel)/days,
    "avg_concurrency":float(np.mean(conc)) if conc else 0.0,"max_concurrency":max(conc) if conc else 0,
    "avg_gross_exposure_pct":100*float(np.mean(expo)) if expo else 0.0,"max_gross_exposure_pct":100*max(expo) if expo else 0.0}
 for k,v in a20.items():q["c20_"+k]=v
 for k,v in a40.items():q["c40_"+k]=v
 return q

def fmt(v,d=2):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",default="artifacts");a=ap.parse_args()
 fs=sorted(glob.glob(str(Path(a.input)/"**/sweep_reclaim_raw_*.csv.gz"),recursive=True))
 if not fs:raise RuntimeError("no shard ledgers")
 d=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 if not len(d):raise RuntimeError("empty sweep reclaim ledger")
 key=["config","symbol","entry_time","exit_time","side"]
 if d.duplicated(key).any():raise RuntimeError(f"duplicate trades {int(d.duplicated(key).sum())}")
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True)
 rows=[]
 for cfg,g in d.groupby("config",sort=True):
  splits={"TRAIN_2021_2024":g[g.dt<CUT],"HOLDOUT_2025_2026":g[g.dt>=CUT]}
  for lab,x in splits.items():
   q=metrics(x,lab);q["config"]=cfg;rows.append(q)
 s=pd.DataFrame(rows);s.to_csv("sweep_reclaim_summary.csv",index=False)
 d.drop(columns=["dt"]).to_csv("sweep_reclaim_all_candidates.csv.gz",index=False,compression="gzip")
 lines=["# Sweep → Reclaim v1","",
 "Pre-registered before viewing results. One strategy family; 8 broad robustness cells only.",
 "",
 f"Portfolio: risk/trade={RISK_BUDGET*100:.2f}%, max daily={MAX_DAILY}, max open={MAX_OPEN}, max gross={MAX_GROSS*100:.0f}%.",
 "Universe: fixed liquid/long-history Binance USDT perpetual basket. 15m signal, closed 4H ATR, next-15m-open entry.",
 "Canonical 1m chronology is used for ambiguous TP/SL bars. Costs: 20bp and 40bp round trip.",
 "",
 "| Config | Split | Raw N | Exec N | /day | PF20 | PF40 | Ret20 | MDD20 | CAGR20 | WR20 | AvgR20 | Ret40 | MDD40 | Avg conc | Max exp |",
 "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in s.sort_values(["config","split"]).iterrows():
  lines.append(f"| {r.config} | {r.split} | {int(r.raw_n)} | {int(r.exec_n)} | {fmt(r.exec_per_day,2)} | {fmt(r.c20_pf,3)} | {fmt(r.c40_pf,3)} | {fmt(r.c20_return_pct,1)}% | {fmt(r.c20_mdd_pct,1)}% | {fmt(r.c20_cagr_pct,1)}% | {fmt(r.c20_win_pct,1)}% | {fmt(r.c20_avg_r,3)} | {fmt(r.c40_return_pct,1)}% | {fmt(r.c40_mdd_pct,1)}% | {fmt(r.avg_concurrency,2)} | {fmt(r.max_gross_exposure_pct,0)}% |")
 lines+=["","No parameter was selected or changed after viewing holdout in this run."]
 Path("sweep_reclaim_report.md").write_text("\n".join(lines)+"\n")
 print("\n".join(lines))
if __name__=="__main__":main()
