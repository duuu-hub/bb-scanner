import argparse,math
from pathlib import Path
import numpy as np,pandas as pd

RISK=.005; MAX_GROSS=2.0; CAP=6; BP=20
D1=pd.Timestamp("2024-01-01T00:00:00Z"); D2=pd.Timestamp("2025-01-01T00:00:00Z")
WINDOWS=(20,40,60)
# Keep grid deliberately small: use completed shadow net-R only.
GATES=("SUMR_POS","PF_GT_1")
WARMUP_MULT=1

def pf(v):
 a=np.asarray(v,float);gp=a[a>0].sum();gl=-a[a<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

def net_r(row,bp=BP):
 return (float(row.gross_return)-bp/10000.0)/float(row.risk_pct)

def build_shadow_gate(d,window,gate):
 # Causal global shadow ledger: each raw candidate contributes only AFTER its exit_time.
 # At entry t, state uses trades with exit_time < t only. Same-bar exits are not yet known.
 z=d.sort_values(["entry_time","impulse_atr","symbol"],ascending=[True,False,True]).copy()
 exits=[]
 for i,r in z.iterrows():
  exits.append((int(r.exit_time),int(r.entry_time),str(r.symbol),net_r(r),i))
 exits.sort(key=lambda x:(x[0],x[1],x[2]))

 hist=[];p=0;allowed={}
 for et,grp in z.groupby("entry_time",sort=True):
  et=int(et)
  while p<len(exits) and exits[p][0] < et:
   hist.append(float(exits[p][3]));p+=1
  if len(hist) < window*WARMUP_MULT:
   on=False
  else:
   w=np.asarray(hist[-window:],float)
   if gate=="SUMR_POS": on=bool(w.sum()>0)
   elif gate=="PF_GT_1": on=bool(pf(w)>1.0)
   else: raise RuntimeError(gate)
  for idx in grp.index: allowed[idx]=on
 return pd.Series(allowed).reindex(d.index).fillna(False).astype(bool)

def select_exec(g,gate_mask=None):
 z=g.sort_values(["entry_time","impulse_atr","symbol"],ascending=[True,False,True]).copy()
 acc=[];op=[]
 for et,grp in z.groupby("entry_time",sort=True):
  et=int(et);op=[p for p in op if p["exit_time"]>=et]
  for idx,r in grp.sort_values(["impulse_atr","symbol"],ascending=[False,True]).iterrows():
   if gate_mask is not None and not bool(gate_mask.loc[idx]):continue
   if len(op)>=CAP:break
   if any(p["symbol"]==r.symbol for p in op):continue
   notional=RISK/float(r.risk_pct);gross=sum(p["notional"] for p in op)
   if gross+notional>MAX_GROSS+1e-12:continue
   acc.append(r.to_dict());op.append({"symbol":r.symbol,"exit_time":int(r.exit_time),"notional":notional})
 return pd.DataFrame(acc)

def account(sel,bp=BP):
 if not len(sel):return {"return_pct":0.,"mdd_pct":0.,"pf":np.nan,"max_ls":0,"n":0},pd.DataFrame()
 z=sel.sort_values(["entry_time","exit_time","symbol"]).reset_index(drop=True)
 ev=[]
 for i,r in z.iterrows():
  et=int(r.entry_time);xt=int(r.exit_time)
  ev.append((et,1,i));ev.append((xt,2 if xt==et else 0,i))
 ev.sort(key=lambda x:(x[0],x[1],x[2]))
 eq=1.;peak=1.;mdd=0.;openrisk={};real=[]
 for ts,typ,i in ev:
  if typ==1:openrisk[i]=eq*RISK
  else:
   if i not in openrisk:continue
   rc=openrisk.pop(i);r=z.iloc[i];nr=net_r(r,bp)
   pnl=rc*nr;eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 0)
   real.append({"exit_time":ts,"entry_time":int(r.entry_time),"symbol":r.symbol,"net_r":nr,"pnl":pnl})
 rz=pd.DataFrame(real).sort_values(["exit_time","entry_time","symbol"])
 cur=ls=0
 for x in rz.net_r:
  if x<0:cur+=1;ls=max(ls,cur)
  else:cur=0
 return {"return_pct":100*(eq-1),"mdd_pct":100*mdd,"pf":pf(rz.pnl),"max_ls":ls,"n":len(rz)},rz

def splitmask(d,label):
 if label=="DEV":return d.dt<D1
 if label=="VALID":return (d.dt>=D1)&(d.dt<D2)
 if label=="EVAL":return d.dt>=D2
 raise RuntimeError(label)

def f(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--out",default="killswitch_v4");a=ap.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 d=pd.read_csv(a.input,compression="gzip");d=d[d.hours==16].copy()
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True);d["year"]=d.dt.dt.year

 # Baseline gate always ON.
 gates={("BASE",0):pd.Series(True,index=d.index)}
 for w in WINDOWS:
  for g in GATES:gates[(g,w)]=build_shadow_gate(d,w,g)

 rows=[]
 for (gname,w),mask in gates.items():
  for lab in ("DEV","VALID","EVAL"):
   ix=splitmask(d,lab);x=d[ix];gm=mask[ix]
   sel=select_exec(x,gm);m,_=account(sel)
   rows.append({"gate":gname,"window":w,"split":lab,"raw_n":len(x),"gate_on_n":int(gm.sum()),
                "gate_on_pct":100*float(gm.mean()) if len(gm) else np.nan,"exec_n":len(sel),**m})
 s=pd.DataFrame(rows);s.to_csv(out/"summary.csv",index=False)

 # Select using DEV only: maximize return, then lower MDD, then more trades.
 dev=s[s.split=="DEV"].copy().sort_values(["return_pct","mdd_pct","exec_n"],ascending=[False,True,False])
 dev.to_csv(out/"dev_ranking.csv",index=False)
 best=dev.iloc[0];bg=(best.gate,int(best.window))
 bestmask=gates[bg]
 pd.DataFrame([{"selected_gate":bg[0],"selected_window":bg[1]}]).to_csv(out/"selected_dev.csv",index=False)

 yr=[]
 for y,x in d.groupby("year"):
  gm=bestmask.loc[x.index];sel=select_exec(x,gm);m,_=account(sel)
  yr.append({"year":int(y),"raw_n":len(x),"gate_on_n":int(gm.sum()),"gate_on_pct":100*gm.mean(),"exec_n":len(sel),**m})
 ydf=pd.DataFrame(yr);ydf.to_csv(out/"selected_yearly.csv",index=False)

 # Gate state chronology diagnostics by month.
 mo=[]
 z=d.copy();z["month"]=z.dt.dt.to_period("M").astype(str)
 for month,x in z.groupby("month"):
  gm=bestmask.loc[x.index]
  mo.append({"month":month,"signals":len(x),"gate_on_pct":100*gm.mean(),"gate_on_n":int(gm.sum())})
 pd.DataFrame(mo).to_csv(out/"selected_monthly_gate.csv",index=False)

 lines=["# Short Continuation Causal Kill-Switch V4","",
 "Frozen strategy: 16h / cap6 / 20bp. No signal/SL/TP changes.",
 "Gate uses ONLY globally completed shadow trades. At entry time t, trades with exit_time >= t are unknown and excluded.",
 "Grid: last 20/40/60 completed shadow trades; gate if rolling net-R sum >0 or PF>1. DEV selects one rule; VALID/EVAL are read only after selection.","",
 "| Gate | W | Split | Gate ON | ExecN | Return | MDD | PF20 | MaxLS |",
 "|---|---:|---|---:|---:|---:|---:|---:|---:|"]
 for _,r in s.sort_values(["gate","window","split"]).iterrows():
  lines.append(f"| {r.gate} | {int(r.window)} | {r.split} | {f(r.gate_on_pct,1)}% | {int(r.exec_n)} | {f(r.return_pct,2)}% | {f(r.mdd_pct,2)}% | {f(r.pf,3)} | {int(r.max_ls)} |")
 lines+=["",f"## DEV-selected: {bg[0]} / window {bg[1]}","",
 "| Year | Gate ON | ExecN | Return | MDD | PF20 | MaxLS |","|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in ydf.iterrows():
  lines.append(f"| {int(r.year)} | {f(r.gate_on_pct,1)}% | {int(r.exec_n)} | {f(r.return_pct,2)}% | {f(r.mdd_pct,2)}% | {f(r.pf,3)} | {int(r.max_ls)} |")
 (out/"report.md").write_text("\n".join(lines)+"\n")
 print("\n".join(lines))
if __name__=="__main__":main()
