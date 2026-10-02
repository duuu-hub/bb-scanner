import argparse,glob,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd

RISK=.005;MAX_GROSS=2.0;CAP=6;BP=20
D1=pd.Timestamp("2024-01-01T00:00:00Z");D2=pd.Timestamp("2025-01-01T00:00:00Z")

def pf(v):
 a=np.asarray(v,float);gp=a[a>0].sum();gl=-a[a<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

def net_r(r):return (float(r.gross_return)-BP/10000.0)/float(r.risk_pct)

def select_exec(g):
 z=g.sort_values(["entry_time","impulse_atr","strategy","symbol"],ascending=[True,False,True,True]).copy()
 acc=[];op=[]
 for et,grp in z.groupby("entry_time",sort=True):
  et=int(et);op=[p for p in op if p["exit_time"]>=et]
  for _,r in grp.sort_values(["impulse_atr","strategy","symbol"],ascending=[False,True,True]).iterrows():
   if len(op)>=CAP:break
   if any(p["symbol"]==r.symbol for p in op):continue
   notional=RISK/float(r.risk_pct);gross=sum(p["notional"] for p in op)
   if gross+notional>MAX_GROSS+1e-12:continue
   acc.append(r.to_dict());op.append({"symbol":r.symbol,"exit_time":int(r.exit_time),"notional":notional})
 return pd.DataFrame(acc)

def account(sel):
 if not len(sel):return {"return_pct":0.,"mdd_pct":0.,"pf":np.nan,"max_ls":0,"n":0},pd.DataFrame()
 z=sel.sort_values(["entry_time","exit_time","strategy","symbol"]).reset_index(drop=True)
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
   rc=openrisk.pop(i);r=z.iloc[i];nr=net_r(r)
   pnl=rc*nr;eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 0)
   real.append({"exit_time":ts,"entry_time":int(r.entry_time),"strategy":r.strategy,"symbol":r.symbol,"net_r":nr,"pnl":pnl})
 rz=pd.DataFrame(real).sort_values(["exit_time","entry_time","strategy","symbol"])
 cur=ls=0
 for x in rz.net_r:
  if x<0:cur+=1;ls=max(ls,cur)
  else:cur=0
 return {"return_pct":100*(eq-1),"mdd_pct":100*mdd,"pf":pf(rz.pnl),"max_ls":ls,"n":len(rz)},rz

def split(d,lab):
 if lab=="DEV":return d[d.dt<D1]
 if lab=="VALID":return d[(d.dt>=D1)&(d.dt<D2)]
 if lab=="EVAL":return d[d.dt>=D2]
 raise RuntimeError(lab)

def f(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--long",required=True);ap.add_argument("--short",required=True);ap.add_argument("--out",default="mirror_combo_v1");a=ap.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 lfs=sorted(glob.glob(str(Path(a.long)/"**/long_continuation_mirror_*.csv.gz"),recursive=True))
 if not lfs:raise RuntimeError("no long shards")
 L=pd.concat([pd.read_csv(x) for x in lfs],ignore_index=True);L["strategy"]="LONG"
 S=pd.read_csv(a.short,compression="gzip");S=S[S.hours==16].copy();S["strategy"]="SHORT"
 # Use each shared column exactly once. Both ledgers already contain strategy.
 cols=[c for c in L.columns if c in S.columns]
 if len(cols)!=len(set(cols)):raise RuntimeError("duplicate shared column names")
 d=pd.concat([L[cols],S[cols]],ignore_index=True)
 if d.duplicated(["strategy","symbol","impulse_time","entry_time"]).any():raise RuntimeError("duplicates")
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True);d["year"]=d.dt.dt.year

 rows=[];led={}
 for mode in ("LONG","SHORT","BOTH"):
  x=d if mode=="BOTH" else d[d.strategy==mode]
  for lab in ("DEV","VALID","EVAL"):
   g=split(x,lab);sel=select_exec(g);m,r=account(sel)
   rows.append({"mode":mode,"split":lab,"raw_n":len(g),"exec_n":len(sel),**m})
   led[(mode,lab)]=r
 s=pd.DataFrame(rows);s.to_csv(out/"summary.csv",index=False)

 yr=[]
 for mode in ("LONG","SHORT","BOTH"):
  x=d if mode=="BOTH" else d[d.strategy==mode]
  for y,g in x.groupby("year"):
   sel=select_exec(g);m,r=account(sel)
   yr.append({"mode":mode,"year":int(y),"raw_n":len(g),"exec_n":len(sel),**m})
 ydf=pd.DataFrame(yr);ydf.to_csv(out/"yearly.csv",index=False)

 # Contribution of each side inside combined portfolio.
 combo=[]
 for lab in ("DEV","VALID","EVAL"):
  r=led[("BOTH",lab)]
  if not len(r):continue
  for st,g in r.groupby("strategy"):
   combo.append({"split":lab,"strategy":st,"n":len(g),"pnl":g.pnl.sum(),"sum_net_r":g.net_r.sum(),"pf":pf(g.pnl)})
 pd.DataFrame(combo).to_csv(out/"combined_side_contrib.csv",index=False)

 # Daily realized PnL correlation using standalone portfolios.
 corr=[]
 for lab in ("DEV","VALID","EVAL"):
  a1=led[("LONG",lab)];a2=led[("SHORT",lab)]
  if not len(a1) or not len(a2):continue
  for x in (a1,a2):x["day"]=pd.to_datetime(x.exit_time,unit="ms",utc=True).dt.floor("D")
  p1=a1.groupby("day").pnl.sum();p2=a2.groupby("day").pnl.sum()
  j=pd.concat([p1.rename("long"),p2.rename("short")],axis=1).fillna(0)
  corr.append({"split":lab,"daily_pnl_corr":j.long.corr(j.short),"days":len(j)})
 pd.DataFrame(corr).to_csv(out/"daily_correlation.csv",index=False)

 lines=["# Long Mirror + Short Continuation Combination V1","",
 "LONG is the exact directional mirror of the frozen SHORT candidate: H1 >=2ATR impulse, H4 EMA20/50 aligned, immediate entry, SL=2ATR, TP=3R, max 16h.",
 "Portfolio: 0.5% risk/trade, cap6, gross <=200%, same-symbol overlap blocked, 20bp. No long-side tuning after results.","",
 "| Mode | Split | ExecN | Return | MDD | PF20 | MaxLS |",
 "|---|---|---:|---:|---:|---:|---:|"]
 for _,r in s.sort_values(["mode","split"]).iterrows():
  lines.append(f"| {r['mode']} | {r.split} | {int(r.exec_n)} | {f(r.return_pct,2)}% | {f(r.mdd_pct,2)}% | {f(r.pf,3)} | {int(r.max_ls)} |")
 lines+=["","## Yearly","",
 "| Year | LONG Ret | SHORT Ret | BOTH Ret | LONG PF | SHORT PF | BOTH PF |",
 "|---:|---:|---:|---:|---:|---:|---:|"]
 for y in sorted(ydf.year.unique()):
  z=ydf[ydf.year==y].set_index("mode")
  lines.append(f"| {int(y)} | {f(z.loc['LONG','return_pct'],2)}% | {f(z.loc['SHORT','return_pct'],2)}% | {f(z.loc['BOTH','return_pct'],2)}% | {f(z.loc['LONG','pf'],3)} | {f(z.loc['SHORT','pf'],3)} | {f(z.loc['BOTH','pf'],3)} |")
 (out/"report.md").write_text("\n".join(lines)+"\n")
 d.drop(columns=["dt"]).to_csv(out/"combined_raw.csv.gz",index=False,compression="gzip")
 print("\n".join(lines))
if __name__=="__main__":main()
