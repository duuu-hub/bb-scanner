import argparse,glob,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.sweep_reclaim_merge as base

D1=pd.Timestamp("2024-01-01T00:00:00Z")
D2=pd.Timestamp("2025-01-01T00:00:00Z")

def pf(x):
 x=np.asarray(x,float);gp=x[x>0].sum();gl=-x[x<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

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

def metrics(g,label):
 if not len(g):return {"split":label,"raw_n":0}
 sel,conc,expo=select_exec(g)
 a20=base.account_sim(sel,20);a40=base.account_sim(sel,40)
 days=max((g.entry_time.max()-g.entry_time.min())/86400000,1)
 q={"split":label,"raw_n":len(g),"raw_gross_pf":pf(g.gross_r),"raw_pf20":pf(g.net20_r),"raw_pf40":pf(g.net40_r),
    "raw_win_pct":100*np.mean(g.gross_r>0),"raw_avg_gross_r":g.gross_r.mean(),"raw_hold_min":g.hold_min.mean(),
    "symbols":g.symbol.nunique(),"exec_n":len(sel),"exec_per_day":len(sel)/days,
    "avg_concurrency":float(np.mean(conc)) if conc else 0.0,"max_concurrency":max(conc) if conc else 0,
    "avg_gross_exposure_pct":100*float(np.mean(expo)) if expo else 0.0,"max_gross_exposure_pct":100*max(expo) if expo else 0.0}
 for k,v in a20.items():q["c20_"+k]=v
 for k,v in a40.items():q["c40_"+k]=v
 return q

def f(v,d=2):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",default="artifacts");a=ap.parse_args()
 fs=sorted(glob.glob(str(Path(a.input)/"**/impulse_pullback_v1_raw_*.csv.gz"),recursive=True))
 if not fs:raise RuntimeError("no shards")
 d=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 if not len(d):raise RuntimeError("empty ledger")
 key=["config","symbol","entry_time","exit_time","side"]
 if d.duplicated(key).any():raise RuntimeError(f"duplicates {int(d.duplicated(key).sum())}")
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True);rows=[]
 for cfg,g in d.groupby("config",sort=True):
  splits={"DEV_2021_2023":g[g.dt<D1],"VALID_2024":g[(g.dt>=D1)&(g.dt<D2)],"EVAL_2025_2026":g[g.dt>=D2]}
  for lab,x in splits.items():
   q=metrics(x,lab);q["config"]=cfg;rows.append(q)
 s=pd.DataFrame(rows);s.to_csv("impulse_pullback_v1_summary.csv",index=False)
 d.drop(columns=["dt"]).to_csv("impulse_pullback_v1_all_trades.csv.gz",index=False,compression="gzip")
 lines=["# Impulse Pullback Continuation V1","",
 "New strategy family, pre-registered before results.",
 "",
 "Signal: 4H EMA20/50 trend alignment -> large H1 impulse -> 15m 30-65% pullback -> 15m continuation confirmation -> next 15m open.",
 "Portfolio: 0.5% account risk/trade, no daily cap, max 3 open, max gross 200%.",
 "Costs 20bp/40bp. Canonical Binance 1m chronology for ambiguous TP/SL.",
 "",
 "| Config | Split | RawN | ExecN | /day | GrossPF | PF20 | PF40 | Ret20 | MDD20 | CAGR20 | WR20 | AvgR20 | Ret40 | MDD40 |",
 "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in s.sort_values(["config","split"]).iterrows():
  lines.append(f"| {r.config} | {r.split} | {int(r.raw_n)} | {int(r.exec_n)} | {f(r.exec_per_day,2)} | {f(r.raw_gross_pf,3)} | {f(r.c20_pf,3)} | {f(r.c40_pf,3)} | {f(r.c20_return_pct,1)}% | {f(r.c20_mdd_pct,1)}% | {f(r.c20_cagr_pct,1)}% | {f(r.c20_win_pct,1)}% | {f(r.c20_avg_r,3)} | {f(r.c40_return_pct,1)}% | {f(r.c40_mdd_pct,1)}% |")
 lines+=["","No parameter changed after this run starts. 2025-26 is evaluation, not pristine untouched OOS."]
 Path("impulse_pullback_v1_report.md").write_text("\n".join(lines)+"\n")
 print("\n".join(lines))
if __name__=="__main__":main()
