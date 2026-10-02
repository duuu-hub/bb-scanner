import argparse,math
from pathlib import Path
import numpy as np,pandas as pd

RISK=.005;MAX_GROSS=2.0;CAP=6;BP=20
D1=pd.Timestamp("2024-01-01T00:00:00Z");D2=pd.Timestamp("2025-01-01T00:00:00Z")

def pf(v):
 a=np.asarray(v,float);gp=a[a>0].sum();gl=-a[a<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

def select_exec(g,omit=()):
 if omit:g=g[~g.symbol.isin(set(omit))]
 z=g.sort_values(["entry_time","impulse_atr","symbol"],ascending=[True,False,True]).copy()
 acc=[];op=[]
 for et,grp in z.groupby("entry_time",sort=True):
  et=int(et);op=[p for p in op if p["exit_time"]>=et]
  for _,r in grp.sort_values(["impulse_atr","symbol"],ascending=[False,True]).iterrows():
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
   rc=openrisk.pop(i);r=z.iloc[i];nr=(float(r.gross_return)-bp/10000.)/float(r.risk_pct)
   pnl=rc*nr;eq+=pnl;peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 0)
   real.append({"exit_time":ts,"entry_time":int(r.entry_time),"symbol":r.symbol,"net_r":nr,"pnl":pnl,"reason":r.reason})
 rz=pd.DataFrame(real).sort_values(["exit_time","entry_time","symbol"])
 cur=ls=0
 for x in rz.net_r:
  if x<0:cur+=1;ls=max(ls,cur)
  else:cur=0
 return {"return_pct":100*(eq-1),"mdd_pct":100*mdd,"pf":pf(rz.pnl),"max_ls":ls,"n":len(rz)},rz

def splits(d):return {"DEV":d[d.dt<D1],"VALID":d[(d.dt>=D1)&(d.dt<D2)],"EVAL":d[d.dt>=D2]}

def f(v,d=2):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--out",default="robustness");a=ap.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 d=pd.read_csv(a.input,compression="gzip");d=d[d.hours==16].copy()
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True);d["year"]=d.dt.dt.year
 base=[];led={}
 for lab,g in splits(d).items():
  s=select_exec(g);m,r=account(s);base.append({"split":lab,"raw_n":len(g),"exec_n":len(s),**m});led[lab]=(s,r)
 bdf=pd.DataFrame(base);bdf.to_csv(out/"baseline.csv",index=False)

 yr=[]
 for y,g in d.groupby("year"):
  s=select_exec(g);m,r=account(s)
  nr=(g.gross_return.to_numpy(float)-.002)/g.risk_pct.to_numpy(float)
  yr.append({"year":int(y),"raw_n":len(g),"exec_n":len(s),"raw_pf20":pf(nr),**m})
 ydf=pd.DataFrame(yr);ydf.to_csv(out/"yearly.csv",index=False)

 sr=[]
 for lab,(s,r) in led.items():
  for sym,x in r.groupby("symbol"):
   sr.append({"split":lab,"symbol":sym,"n":len(x),"pnl":x.pnl.sum(),"sum_net_r":x.net_r.sum(),"pf":pf(x.pnl),"win_pct":100*(x.net_r>0).mean()})
 sdf=pd.DataFrame(sr);sdf.to_csv(out/"symbol_contrib.csv",index=False)

 conc=[]
 for lab,x in sdf.groupby("split"):
  pos=x[x.pnl>0].sort_values("pnl",ascending=False);gp=pos.pnl.sum()
  conc.append({"split":lab,"net_pnl":x.pnl.sum(),"profitable_symbols":len(pos),"losing_symbols":int((x.pnl<0).sum()),
   "top1_share_pos_pct":100*pos.pnl.head(1).sum()/gp if gp else np.nan,
   "top3_share_pos_pct":100*pos.pnl.head(3).sum()/gp if gp else np.nan,
   "top5_share_pos_pct":100*pos.pnl.head(5).sum()/gp if gp else np.nan})
 cdf=pd.DataFrame(conc);cdf.to_csv(out/"concentration.csv",index=False)

 loo=[]
 for om in sorted(d.symbol.unique()):
  for lab,g in splits(d).items():
   s=select_exec(g,[om]);m,r=account(s);loo.append({"omit":om,"split":lab,"exec_n":len(s),**m})
 ldf=pd.DataFrame(loo);ldf.to_csv(out/"leave_one_out.csv",index=False)

 devrank=sdf[sdf.split=="DEV"].sort_values("pnl",ascending=False).symbol.tolist();tk=[]
 for k in (1,2,3,5):
  om=devrank[:k]
  for lab,g in splits(d).items():
   s=select_exec(g,om);m,r=account(s);tk.append({"k":k,"omitted":",".join(om),"split":lab,"exec_n":len(s),**m})
 tdf=pd.DataFrame(tk);tdf.to_csv(out/"leave_dev_topk.csv",index=False)

 seq=[]
 for lab,(s,r) in led.items():
  v=r.net_r.to_numpy(float);row={"split":lab,"n":len(v),"max_loss_streak":account(s)[0]["max_ls"],"mean_net_r":v.mean(),"median_net_r":float(np.median(v))}
  for n in (5,10,20,40):
   z=pd.Series(v).rolling(n).sum().dropna();row[f"worst_{n}_sum_r"]=float(z.min());row[f"best_{n}_sum_r"]=float(z.max())
  seq.append(row)
 qdf=pd.DataFrame(seq);qdf.to_csv(out/"sequence_stats.csv",index=False)

 lines=["# Short Continuation Robustness V3","",
 "Frozen candidate: H1 >=2ATR bearish impulse -> immediate short; H4 bearish; SL=2ATR; TP=3R; max hold 16h.",
 "Portfolio: 0.5% risk/trade, cap 6, gross exposure <=200%, same-symbol overlap blocked, cost=20bp.",
 "Chronology fix: a position with exit_time == a new entry_time is still open at that bar open; same-bar trades enter before their exit is booked.","",
 "## Baseline","",
 "| Split | ExecN | Return | MDD | PF20 | Max loss streak |","|---|---:|---:|---:|---:|---:|"]
 for _,r in bdf.iterrows():lines.append(f"| {r.split} | {int(r.exec_n)} | {f(r.return_pct)}% | {f(r.mdd_pct)}% | {f(r.pf,3)} | {int(r.max_ls)} |")
 lines+=["","## Yearly","",
 "| Year | ExecN | Return | MDD | PF20 | Max loss streak |","|---:|---:|---:|---:|---:|---:|"]
 for _,r in ydf.iterrows():lines.append(f"| {int(r.year)} | {int(r.exec_n)} | {f(r.return_pct)}% | {f(r.mdd_pct)}% | {f(r.pf,3)} | {int(r.max_ls)} |")
 lines+=["","## Leave-one-symbol-out range","",
 "| Split | Worst return | Best return | Worst PF | Best PF |","|---|---:|---:|---:|---:|"]
 for lab,g in ldf.groupby("split"):
  lines.append(f"| {lab} | {f(g.return_pct.min())}% | {f(g.return_pct.max())}% | {f(g.pf.min(),3)} | {f(g.pf.max(),3)} |")
 lines+=["","## Concentration","",
 "| Split | Profitable symbols | Losing symbols | Top1 / positive PnL | Top3 | Top5 |","|---|---:|---:|---:|---:|---:|"]
 for _,r in cdf.iterrows():lines.append(f"| {r.split} | {int(r.profitable_symbols)} | {int(r.losing_symbols)} | {f(r.top1_share_pos_pct)}% | {f(r.top3_share_pos_pct)}% | {f(r.top5_share_pos_pct)}% |")
 lines+=["","## Sequential risk","",
 "| Split | Max losing streak | Mean net R | Median net R | Worst 20-trade sum R | Worst 40-trade sum R |","|---|---:|---:|---:|---:|---:|"]
 for _,r in qdf.iterrows():lines.append(f"| {r.split} | {int(r.max_loss_streak)} | {f(r.mean_net_r,3)} | {f(r.median_net_r,3)} | {f(r.worst_20_sum_r,2)} | {f(r.worst_40_sum_r,2)} |")
 (out/"robustness_report.md").write_text("\n".join(lines)+"\n")
 print("\n".join(lines))
if __name__=="__main__":main()
