import argparse,glob,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd

D1=pd.Timestamp("2024-01-01T00:00:00Z");D2=pd.Timestamp("2025-01-01T00:00:00Z")
RISK=.005;MAX_GROSS=2.0;COSTS=(10,12,15,18,20)
CAPS=(3,6,99)

def pf(x):
 x=np.asarray(x,float);gp=x[x>0].sum();gl=-x[x<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

def select_exec(g,cap):
 z=g.sort_values(["entry_time","impulse_atr","symbol"],ascending=[True,False,True]).copy()
 acc=[];op=[];conc=[];expo=[]
 for _,r in z.iterrows():
  et=int(r.entry_time);op=[p for p in op if p["exit_time"]>et]
  if cap<99 and len(op)>=cap:continue
  if any(p["symbol"]==r.symbol for p in op):continue
  notional=RISK/float(r.risk_pct);gross=sum(p["notional"] for p in op)
  if gross+notional>MAX_GROSS+1e-12:continue
  acc.append(r.to_dict());op.append({"symbol":r.symbol,"exit_time":int(r.exit_time),"notional":notional})
  conc.append(len(op));expo.append(gross+notional)
 return pd.DataFrame(acc),conc,expo

def acct(sel,bp):
 if not len(sel):return {}
 z=sel.sort_values(["entry_time","exit_time"]).reset_index(drop=True)
 ev=[]
 for i,r in z.iterrows():ev.append((int(r.entry_time),1,i));ev.append((int(r.exit_time),0,i))
 ev.sort(key=lambda x:(x[0],x[1]))
 eq=1.;peak=1.;mdd=0.;openrisk={};pnls=[]
 for ts,typ,i in ev:
  if typ==0:
   if i not in openrisk:continue
   rc=openrisk.pop(i);r=z.iloc[i];nr=(float(r.gross_return)-bp/10000.)/float(r.risk_pct)
   pnl=rc*nr;eq+=pnl;pnls.append(pnl);peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 0)
  else:openrisk[i]=eq*RISK
 return {"ret":100*(eq-1),"mdd":100*mdd,"pf":pf(pnls)}

def f(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",default="artifacts");a=ap.parse_args()
 fs=sorted(glob.glob(str(Path(a.input)/"**/short_continuation_horizon_*.csv.gz"),recursive=True))
 if not fs:raise RuntimeError("no shards")
 d=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 if d.duplicated(["config","symbol","impulse_time","entry_time"]).any():raise RuntimeError("duplicates")
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True)
 rows=[]
 for hrs,g in d.groupby("hours",sort=True):
  splits={"DEV":g[g.dt<D1],"VALID":g[(g.dt>=D1)&(g.dt<D2)],"EVAL":g[g.dt>=D2]}
  for lab,x in splits.items():
   for cap in CAPS:
    sel,conc,expo=select_exec(x,cap)
    q={"hours":int(hrs),"split":lab,"cap":cap,"raw_n":len(x),"exec_n":len(sel),
       "max_conc":max(conc) if conc else 0,"avg_conc":float(np.mean(conc)) if conc else 0.,
       "max_exp_pct":100*max(expo) if expo else 0.}
    for bp in COSTS:
     z=acct(sel,bp)
     q[f"ret{bp}"]=z.get("ret",np.nan);q[f"mdd{bp}"]=z.get("mdd",np.nan);q[f"pf{bp}"]=z.get("pf",np.nan)
    rows.append(q)
 s=pd.DataFrame(rows);s.to_csv("short_continuation_horizon_summary.csv",index=False)
 lines=["# Short Continuation Horizon V2","",
 "Frozen signal: immediate short, H1 >=2ATR impulse, H4 bearish, SL=2ATR, TP=3R.",
 "Canonical Binance 1m chronology. Portfolio: 0.5% risk/trade, max gross 200%, cap 3/6/exposure-only.",
 "",
 "| H | Cap | Split | ExecN | PF10 | PF15 | PF18 | PF20 | Ret18 | MDD18 | Ret20 | MDD20 | MaxConc |",
 "|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in s.sort_values(["hours","cap","split"]).iterrows():
  lines.append(f"| {int(r.hours)} | {int(r.cap)} | {r.split} | {int(r.exec_n)} | {f(r.pf10)} | {f(r.pf15)} | {f(r.pf18)} | {f(r.pf20)} | {f(r.ret18,1)}% | {f(r.mdd18,1)}% | {f(r.ret20,1)}% | {f(r.mdd20,1)}% | {int(r.max_conc)} |")
 Path("short_continuation_horizon_report.md").write_text("\n".join(lines)+"\n")
 d.drop(columns=["dt"]).to_csv("short_continuation_horizon_all.csv.gz",index=False,compression="gzip")
 print("\n".join(lines))
if __name__=="__main__":main()
