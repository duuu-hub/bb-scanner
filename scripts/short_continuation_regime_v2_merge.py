import argparse,glob,json,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.sweep_reclaim_merge as base

D1=pd.Timestamp("2024-01-01T00:00:00Z")
D2=pd.Timestamp("2025-01-01T00:00:00Z")
REGIMES={
 "BASE":lambda x:np.ones(len(x),dtype=bool),
 "B50":lambda x:x.breadth>=.50,
 "B60":lambda x:x.breadth>=.60,
 "B70":lambda x:x.breadth>=.70,
 "BTC":lambda x:x.btc_bear==1,
 "B50BTC":lambda x:(x.breadth>=.50)&(x.btc_bear==1),
 "B60BTC":lambda x:(x.breadth>=.60)&(x.btc_bear==1),
 "B70BTC":lambda x:(x.breadth>=.70)&(x.btc_bear==1),
}
RANKS=("IMPULSE","DIST","GAP","COMBO")

def pf(x):
 x=np.asarray(x,float);gp=x[x>0].sum();gl=-x[x<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

def add_scores(g):
 z=g.copy()
 z["score_IMPULSE"]=z.impulse_atr
 z["score_DIST"]=z.ema_dist
 z["score_GAP"]=z.trend_gap
 # Cross-sectional percentile at each simultaneous entry. Causal: all values known at entry.
 for col in ("impulse_atr","ema_dist","trend_gap"):
  z[f"pct_{col}"]=z.groupby("entry_time")[col].rank(pct=True,method="average")
 z["score_COMBO"]=(z.pct_impulse_atr+z.pct_ema_dist+z.pct_trend_gap)/3.0
 return z

def select_exec(g,rank):
 z=g.sort_values(["entry_time",f"score_{rank}","symbol"],ascending=[True,False,True]).copy()
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

def account(sel,bp):
 if not len(sel):return {}
 z=sel.sort_values(["entry_time","exit_time"]).reset_index(drop=True)
 events=[]
 for i,r in z.iterrows():
  events.append((int(r.entry_time),1,i));events.append((int(r.exit_time),0,i))
 events.sort(key=lambda x:(x[0],x[1]))
 eq=1.;peak=1.;mdd=0.;openrisk={};pnls=[];rs=[]
 for ts,typ,i in events:
  if typ==0:
   if i not in openrisk:continue
   risk_cash=openrisk.pop(i);r=z.iloc[i]
   nr=(float(r.gross_return)-bp/10000.)/float(r.risk_pct)
   pnl=risk_cash*nr;eq+=pnl;pnls.append(pnl);rs.append(nr)
   peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 0)
  else:openrisk[i]=eq*base.RISK_BUDGET
 arr=np.asarray(rs,float)
 years=max((z.entry_time.max()-z.entry_time.min())/(365.25*86400000),1/365.25)
 cagr=(eq**(1/years)-1) if eq>0 else -1.
 return {"return_pct":100*(eq-1),"cagr_pct":100*cagr,"mdd_pct":100*mdd,
         "pf":pf(np.asarray(pnls,float)),"win_pct":100*np.mean(arr>0) if len(arr) else np.nan,
         "avg_r":arr.mean() if len(arr) else np.nan,"ending_equity":eq}

def summary(g,label,regime,rank):
 if not len(g):return {"split":label,"regime":regime,"rank":rank,"raw_n":0}
 sel,conc,expo=select_exec(g,rank)
 nr20=(g.gross_return.to_numpy(float)-.002)/g.risk_pct.to_numpy(float)
 nr10=(g.gross_return.to_numpy(float)-.001)/g.risk_pct.to_numpy(float)
 days=max((g.entry_time.max()-g.entry_time.min())/86400000,1)
 q={"split":label,"regime":regime,"rank":rank,"raw_n":len(g),"exec_n":len(sel),
    "exec_per_day":len(sel)/days,"raw_pf20":pf(nr20),"raw_pf10":pf(nr10),
    "raw_gross_pf":pf(g.gross_r),"avg_breadth":g.breadth.mean(),
    "avg_impulse":g.impulse_atr.mean(),"avg_dist":g.ema_dist.mean(),"avg_gap":g.trend_gap.mean(),
    "avg_concurrency":float(np.mean(conc)) if conc else 0.0,
    "max_exposure_pct":100*max(expo) if expo else 0.0}
 for bp in (10,20):
  a=account(sel,bp)
  for k,v in a.items():q[f"acct{bp}_{k}"]=v
 return q

def f(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",default="artifacts");a=ap.parse_args()
 fs=sorted(glob.glob(str(Path(a.input)/"**/short_continuation_regime_*.csv.gz"),recursive=True))
 if not fs:raise RuntimeError("no regime shards")
 d=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 if not len(d):raise RuntimeError("empty regime ledger")
 if d.duplicated(["symbol","impulse_time","entry_time"]).any():raise RuntimeError("duplicate rows")
 d=add_scores(d)
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True)
 rows=[]
 for reg,fn in REGIMES.items():
  r=d[fn(d)].copy()
  splits={"DEV_2021_2023":r[r.dt<D1],"VALID_2024":r[(r.dt>=D1)&(r.dt<D2)],"EVAL_2025_2026":r[r.dt>=D2]}
  for lab,g in splits.items():
   for rank in RANKS:rows.append(summary(g,lab,reg,rank))
 s=pd.DataFrame(rows);s.to_csv("short_continuation_regime_summary.csv",index=False)

 # DEV-only selection; freeze top candidates before reading VALID/EVAL.
 dev=s[s.split=="DEV_2021_2023"].copy()
 # Prefer realizable account return at 20bp; tie-break lower MDD then more exec trades.
 dev=dev.sort_values(["acct20_return_pct","acct20_mdd_pct","exec_n"],ascending=[False,True,False])
 dev.head(12).to_csv("short_continuation_regime_dev_top.csv",index=False)

 # Yearly raw diagnostics for each regime (ranking-independent).
 yr=[]
 d["year"]=d.dt.dt.year
 for reg,fn in REGIMES.items():
  r=d[fn(d)].copy()
  for y,g in r.groupby("year"):
   nr20=(g.gross_return.to_numpy(float)-.002)/g.risk_pct.to_numpy(float)
   yr.append({"regime":reg,"year":int(y),"n":len(g),"raw_pf20":pf(nr20),"gross_pf":pf(g.gross_r),
              "avg_breadth":g.breadth.mean()})
 pd.DataFrame(yr).to_csv("short_continuation_regime_yearly.csv",index=False)

 lines=["# Short Continuation Regime/Ranking V2","",
 "Frozen base: immediate short entry, H1 >=2ATR impulse, H4 bearish, SL=2ATR, TP=3R, max hold 6h.",
 "Regime filters use only H4 states already closed before the H1 impulse starts.",
 "Breadth = bearish share of the fixed 18-symbol universe with dynamic availability denominator (minimum 8).",
 "Ranking candidates: impulse strength, EMA20 distance/ATR, EMA20-50 gap/ATR, and same-timestamp percentile combo.",
 "DEV-only selection: 2021-23. VALID=2024. EVAL=2025-26 (not pristine OOS).",
 "",
 "| Regime | Rank | Split | RawN | ExecN | /day | RawPF20 | Acct20Ret | MDD20 | AcctPF20 | Acct10Ret |",
 "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in s.sort_values(["regime","rank","split"]).iterrows():
  lines.append(f"| {r.regime} | {r['rank']} | {r.split} | {int(r.raw_n)} | {int(r.exec_n)} | {f(r.exec_per_day,2)} | {f(r.raw_pf20)} | {f(r.acct20_return_pct,1)}% | {f(r.acct20_mdd_pct,1)}% | {f(r.acct20_pf)} | {f(r.acct10_return_pct,1)}% |")
 lines+=["","## DEV-only top 12","",
 "| Regime | Rank | ExecN | Acct20Ret | MDD20 | AcctPF20 | RawPF20 |",
 "|---|---|---:|---:|---:|---:|---:|"]
 for _,r in dev.head(12).iterrows():
  lines.append(f"| {r.regime} | {r['rank']} | {int(r.exec_n)} | {f(r.acct20_return_pct,1)}% | {f(r.acct20_mdd_pct,1)}% | {f(r.acct20_pf)} | {f(r.raw_pf20)} |")
 Path("short_continuation_regime_report.md").write_text("\n".join(lines)+"\n")
 d.drop(columns=["dt"]).to_csv("short_continuation_regime_all.csv.gz",index=False,compression="gzip")
 print("\n".join(lines))
if __name__=="__main__":main()
