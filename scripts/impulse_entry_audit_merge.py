import argparse,glob,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.sweep_reclaim_merge as base

D1=pd.Timestamp("2024-01-01T00:00:00Z");D2=pd.Timestamp("2025-01-01T00:00:00Z")
COSTS=(0,5,10,15,20,40)

def pf(x):
 x=np.asarray(x,float);gp=x[x>0].sum();gl=-x[x<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

def add_cost_r(g,bp):
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

def summarize(g,split,mode,side):
 if side!="ALL":g=g[g.side==side.lower()]
 if not len(g):return {"split":split,"mode":mode,"side":side,"raw_n":0}
 sel,conc,expo=select_exec(g)
 q={"split":split,"mode":mode,"side":side,"raw_n":len(g),"exec_n":len(sel),
    "gross_pf":pf(g.gross_r),"win_pct":100*np.mean(g.gross_r>0),
    "avg_gross_r":g.gross_r.mean(),"avg_risk_pct":100*g.risk_pct.mean(),
    "avg_hold_min":g.hold_min.mean(),"symbols":g.symbol.nunique()}
 days=max((g.entry_time.max()-g.entry_time.min())/86400000,1);q["exec_per_day"]=len(sel)/days
 for bp in COSTS:q[f"pf{bp}"]=pf(add_cost_r(g,bp))
 if len(sel):
  a20=base.account_sim(sel,20);a40=base.account_sim(sel,40)
  q.update({f"acct20_{k}":v for k,v in a20.items()})
  q.update({f"acct40_{k}":v for k,v in a40.items()})
 else:
  q["acct20_return_pct"]=q["acct20_mdd_pct"]=np.nan
 return q

def paired(d,split):
 # Compare identical impulse events that have both immediate and corrected pullback ATR outcomes.
 a=d[d.mode=="IMMEDIATE_ATR"].copy()
 b=d[d.mode=="PULLBACK_ATR"].copy()
 keys=["symbol","impulse_time","side"]
 p=a.merge(b,on=keys,suffixes=("_imm","_pull"))
 rows=[]
 for side in ("ALL","LONG","SHORT"):
  x=p if side=="ALL" else p[p.side==side.lower()]
  if not len(x):
   rows.append({"split":split,"side":side,"paired_n":0});continue
  q={"split":split,"side":side,"paired_n":len(x)}
  for tag in ("imm","pull"):
   gr=x[f"gross_r_{tag}"].to_numpy(float)
   nr20=(x[f"gross_return_{tag}"].to_numpy(float)-.002)/x[f"risk_pct_{tag}"].to_numpy(float)
   q[f"{tag}_gross_pf"]=pf(gr);q[f"{tag}_pf20"]=pf(nr20)
   q[f"{tag}_win"]=100*np.mean(gr>0);q[f"{tag}_avg_r"]=np.mean(gr)
  rows.append(q)
 return rows

def f(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",default="artifacts");a=ap.parse_args()
 fs=sorted(glob.glob(str(Path(a.input)/"**/impulse_entry_audit_*.csv.gz"),recursive=True))
 if not fs:raise RuntimeError("no audit shards")
 d=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 if not len(d):raise RuntimeError("empty audit ledger")
 if d.duplicated(["mode","symbol","impulse_time","entry_time","side"]).any():
  raise RuntimeError("duplicate audit rows")
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True)
 splits={"DEV_2021_2023":d[d.dt<D1],"VALID_2024":d[(d.dt>=D1)&(d.dt<D2)],"EVAL_2025_2026":d[d.dt>=D2]}
 rows=[];pairs=[]
 for lab,g in splits.items():
  for mode in sorted(d.mode.unique()):
   gm=g[g.mode==mode]
   for side in ("ALL","LONG","SHORT"):rows.append(summarize(gm,lab,mode,side))
  pairs.extend(paired(g,lab))
 s=pd.DataFrame(rows);p=pd.DataFrame(pairs)
 s.to_csv("impulse_entry_audit_summary.csv",index=False)
 p.to_csv("impulse_entry_audit_paired.csv",index=False)
 d.drop(columns=["dt"]).to_csv("impulse_entry_audit_all.csv.gz",index=False,compression="gzip")
 lines=["# Impulse Entry Audit","",
 "AUDIT RUN: fixes short-return denominator and corrected >65% pullback invalidation.",
 "Modes use the same 2ATR H1 impulse, H4 EMA20/50 trend, 2R target, 6h max hold.",
 "IMMEDIATE/CONFIRM/PULLBACK_ATR all use the same 1H ATR stop so entry timing can be compared without stop-width confounding.",
 "PULLBACK_STRUCT uses corrected original pullback-extreme stop.",
 "",
 "| Split | Mode | Side | N | ExecN | /day | GrossPF | PF5 | PF10 | PF15 | PF20 | PF40 | WR | Risk% | Hold | Ret20 | MDD20 |",
 "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in s.iterrows():
  if r.side!="ALL":continue
  lines.append(f"| {r.split} | {r.mode} | {r.side} | {int(r.raw_n)} | {int(r.exec_n)} | {f(r.exec_per_day,2)} | {f(r.gross_pf)} | {f(r.pf5)} | {f(r.pf10)} | {f(r.pf15)} | {f(r.pf20)} | {f(r.pf40)} | {f(r.win_pct,1)}% | {f(r.avg_risk_pct,2)}% | {f(r.avg_hold_min,0)}m | {f(r.acct20_return_pct,1)}% | {f(r.acct20_mdd_pct,1)}% |")
 lines+=["","## Same-impulse paired comparison (Immediate vs corrected Pullback, common 1ATR stop)","",
 "| Split | Side | N | Imm grossPF | Pull grossPF | Imm PF20 | Pull PF20 | Imm WR | Pull WR |",
 "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in p.iterrows():
  lines.append(f"| {r.split} | {r.side} | {int(r.paired_n)} | {f(r.imm_gross_pf)} | {f(r.pull_gross_pf)} | {f(r.imm_pf20)} | {f(r.pull_pf20)} | {f(r.imm_win,1)}% | {f(r.pull_win,1)}% |")
 Path("impulse_entry_audit_report.md").write_text("\n".join(lines)+"\n")
 print("\n".join(lines))
if __name__=="__main__":main()
