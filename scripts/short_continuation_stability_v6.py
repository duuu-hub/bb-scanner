import argparse,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.short_continuation_robustness_v3 as rob

RISK=.005;BP=20
BOOT_N=10000
SEED=20261002

def pf(v):
 a=np.asarray(v,float);gp=a[a>0].sum();gl=-a[a<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

def f(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def fixed_r_return(v):
 a=np.asarray(v,float)
 x=1.0+RISK*a
 if np.any(x<=0):return -1.0
 return float(np.prod(x)-1.0)

def rolling_stats(led,months):
 x=led.copy()
 x["month"]=pd.to_datetime(x.entry_time,unit="ms",utc=True).dt.to_period("M")
 mn=x.month.min();mx=x.month.max()
 grid=pd.period_range(mn,mx,freq="M")
 rows=[]
 for end_i in range(months-1,len(grid)):
  win=grid[end_i-months+1:end_i+1]
  g=x[x.month.isin(win)]
  if not len(g):continue
  v=g.net_r.to_numpy(float)
  rows.append({"months":months,"start":str(win[0]),"end":str(win[-1]),"n":len(g),
               "sum_r":float(v.sum()),"pf":pf(v),"win_pct":100*np.mean(v>0),
               "approx_return_pct":100*fixed_r_return(v)})
 return pd.DataFrame(rows)

def bootstrap_month_blocks(led,label):
 x=led.copy()
 x["month"]=pd.to_datetime(x.entry_time,unit="ms",utc=True).dt.to_period("M")
 blocks=[g.net_r.to_numpy(float) for _,g in x.groupby("month",sort=True)]
 if not blocks:return {"split":label,"months":0}
 rng=np.random.default_rng(SEED+sum(ord(c) for c in label))
 nr=[];pfs=[];rets=[]
 m=len(blocks)
 for _ in range(BOOT_N):
  idx=rng.integers(0,m,size=m)
  v=np.concatenate([blocks[i] for i in idx])
  nr.append(float(v.sum()));pfs.append(pf(v));rets.append(100*fixed_r_return(v))
 a=np.asarray(nr);p=np.asarray(pfs);r=np.asarray(rets)
 return {"split":label,"months":m,"boot_n":BOOT_N,
         "p_sumr_pos":100*np.mean(a>0),"p_pf_gt1":100*np.mean(p>1),
         "sumr_p05":np.percentile(a,5),"sumr_p50":np.percentile(a,50),"sumr_p95":np.percentile(a,95),
         "ret_p05":np.percentile(r,5),"ret_p50":np.percentile(r,50),"ret_p95":np.percentile(r,95)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--out",default="stability_v6");a=ap.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 d=pd.read_csv(a.input,compression="gzip");d=d[d.hours==16].copy()
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True);d["year"]=d.dt.dt.year

 sel=rob.select_exec(d);met,led=rob.account(sel)
 led["dt"]=pd.to_datetime(led.entry_time,unit="ms",utc=True);led["year"]=led.dt.dt.year
 led.to_csv(out/"executed_ledger.csv",index=False)
 pd.DataFrame([met]).to_csv(out/"baseline.csv",index=False)

 # Leave one calendar year out, recomputing portfolio selection.
 loo=[]
 years=sorted(int(y) for y in d.year.unique())
 for y in years:
  g=d[d.year!=y];s=rob.select_exec(g);m,_=rob.account(s)
  loo.append({"omit":str(y),"raw_n":len(g),"exec_n":len(s),**m})
 # Explicit concentration stress: remove the two strongest observed years; diagnostic only.
 if 2022 in years and 2025 in years:
  g=d[~d.year.isin([2022,2025])];s=rob.select_exec(g);m,_=rob.account(s)
  loo.append({"omit":"2022+2025_DIAGNOSTIC","raw_n":len(g),"exec_n":len(s),**m})
 ldf=pd.DataFrame(loo);ldf.to_csv(out/"leave_one_year_out.csv",index=False)

 # Monthly and quarterly realized net-R diagnostics from the full causal portfolio.
 led["month"]=led.dt.dt.to_period("M");led["quarter"]=led.dt.dt.to_period("Q")
 mon=[]
 for m,g in led.groupby("month",sort=True):
  v=g.net_r.to_numpy(float)
  mon.append({"month":str(m),"n":len(g),"sum_r":v.sum(),"pf":pf(v),"win_pct":100*np.mean(v>0),
              "approx_return_pct":100*fixed_r_return(v)})
 mdf=pd.DataFrame(mon);mdf.to_csv(out/"monthly.csv",index=False)
 qua=[]
 for q,g in led.groupby("quarter",sort=True):
  v=g.net_r.to_numpy(float)
  qua.append({"quarter":str(q),"n":len(g),"sum_r":v.sum(),"pf":pf(v),"win_pct":100*np.mean(v>0),
              "approx_return_pct":100*fixed_r_return(v)})
 qdf=pd.DataFrame(qua);qdf.to_csv(out/"quarterly.csv",index=False)

 rolls=[]
 for n in (3,6,12):
  x=rolling_stats(led,n)
  if len(x):rolls.append(x)
 rdf=pd.concat(rolls,ignore_index=True);rdf.to_csv(out/"rolling.csv",index=False)
 rsum=[]
 for n,g in rdf.groupby("months"):
  rsum.append({"months":int(n),"windows":len(g),"sumr_pos_pct":100*np.mean(g.sum_r>0),
               "pf_gt1_pct":100*np.mean(g.pf>1),"worst_sum_r":g.sum_r.min(),"median_sum_r":g.sum_r.median(),
               "best_sum_r":g.sum_r.max(),"worst_pf":g.pf.min(),"median_pf":g.pf.median(),"best_pf":g.pf.max()})
 rsdf=pd.DataFrame(rsum);rsdf.to_csv(out/"rolling_summary.csv",index=False)

 # Month-block bootstrap for full history and the pre-defined splits.
 boots=[]
 splits={
  "ALL":led,
  "DEV":led[led.dt<pd.Timestamp("2024-01-01",tz="UTC")],
  "VALID":led[(led.dt>=pd.Timestamp("2024-01-01",tz="UTC"))&(led.dt<pd.Timestamp("2025-01-01",tz="UTC"))],
  "EVAL":led[led.dt>=pd.Timestamp("2025-01-01",tz="UTC")]
 }
 for lab,g in splits.items():boots.append(bootstrap_month_blocks(g,lab))
 bdf=pd.DataFrame(boots);bdf.to_csv(out/"month_block_bootstrap.csv",index=False)

 # Concentration by months.
 pos=mdf[mdf.sum_r>0].sort_values("sum_r",ascending=False)
 gp=pos.sum_r.sum()
 concentration={
  "months":len(mdf),"positive_month_pct":100*np.mean(mdf.sum_r>0),
  "top1_share_positive_sumr_pct":100*pos.sum_r.head(1).sum()/gp if gp else np.nan,
  "top3_share_positive_sumr_pct":100*pos.sum_r.head(3).sum()/gp if gp else np.nan,
  "top5_share_positive_sumr_pct":100*pos.sum_r.head(5).sum()/gp if gp else np.nan,
  "worst_month":str(mdf.loc[mdf.sum_r.idxmin(),"month"]),"worst_month_sum_r":mdf.sum_r.min(),
  "best_month":str(mdf.loc[mdf.sum_r.idxmax(),"month"]),"best_month_sum_r":mdf.sum_r.max()
 }
 pd.DataFrame([concentration]).to_csv(out/"month_concentration.csv",index=False)

 lines=["# Short Continuation Stability V6","",
 "Frozen candidate only: immediate SHORT, H1 >=2ATR, H4 bearish, SL=2ATR, TP=3R, 16h, cap6, gross<=200%, cost=20bp.",
 "No parameter selection is performed here. This is a stability/significance audit of the surviving candidate.",
 "",
 "## Baseline","",
 f"- N={met['n']}, Return={f(met['return_pct'],2)}%, MDD={f(met['mdd_pct'],2)}%, PF20={f(met['pf'],3)}, max losing streak={int(met['max_ls'])}.",
 "",
 "## Leave-one-year-out","",
 "| Omit | ExecN | Return | MDD | PF20 | MaxLS |","|---|---:|---:|---:|---:|---:|"]
 for _,r in ldf.iterrows():
  lines.append(f"| {r.omit} | {int(r.exec_n)} | {f(r.return_pct,2)}% | {f(r.mdd_pct,2)}% | {f(r.pf,3)} | {int(r.max_ls)} |")
 lines+=["","## Rolling stability","",
 "| Window | N windows | SumR >0 | PF>1 | Worst SumR | Median PF | Worst PF |","|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in rsdf.iterrows():
  lines.append(f"| {int(r.months)}m | {int(r.windows)} | {f(r.sumr_pos_pct,1)}% | {f(r.pf_gt1_pct,1)}% | {f(r.worst_sum_r,2)} | {f(r.median_pf,3)} | {f(r.worst_pf,3)} |")
 lines+=["","## Month-block bootstrap","",
 "| Split | Months | P(SumR>0) | P(PF>1) | SumR 5/50/95% | Approx return 5/50/95% |","|---|---:|---:|---:|---|---|"]
 for _,r in bdf.iterrows():
  lines.append(f"| {r.split} | {int(r.months)} | {f(r.p_sumr_pos,1)}% | {f(r.p_pf_gt1,1)}% | {f(r.sumr_p05,1)} / {f(r.sumr_p50,1)} / {f(r.sumr_p95,1)} | {f(r.ret_p05,1)}% / {f(r.ret_p50,1)}% / {f(r.ret_p95,1)}% |")
 lines+=["","Bootstrap approximate returns compound 0.5% fixed risk per realized trade; use them for stability diagnostics, not as exact overlapping-position account returns."]
 (out/"report.md").write_text("\n".join(lines)+"\n")
 print("\n".join(lines))
if __name__=="__main__":main()
