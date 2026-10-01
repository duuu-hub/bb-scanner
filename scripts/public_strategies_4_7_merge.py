import argparse,glob,math
from pathlib import Path
import numpy as np,pandas as pd
CUT=pd.Timestamp("2025-01-01T00:00:00Z")
def pf(x):
 x=np.asarray(x,float);gp=x[x>0].sum();gl=-x[x<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)
def streak(x):
 b=c=0
 for v in x:
  if v<0:c+=1;b=max(b,c)
  else:c=0
 return b
def met(g,label):
 if not len(g):return {"split":label,"n":0}
 z=g.sort_values(["entry_time","symbol"]);a=z.net20_r.to_numpy(float);b=z.net40_r.to_numpy(float)
 return {"split":label,"n":len(z),"symbols":z.symbol.nunique(),"pf20":pf(a),"pf40":pf(b),"win20_pct":100*np.mean(a>0),"avg20_r":a.mean(),"avg40_r":b.mean(),"sum20_r":a.sum(),"sum40_r":b.sum(),"avg_hold_min":z.hold_min.mean(),"median_hold_min":z.hold_min.median(),"max_loss_streak20":streak(a),"tp_like":int(z.reason.astype(str).str.contains("TP|ANCHOR").sum()),"sl_like":int(z.reason.astype(str).str.contains("SL|SAFETY").sum()),"time_n":int((z.reason=="TIME").sum())}
def f(v,d=3):
 if pd.isna(v):return ""
 if math.isinf(v):return "inf"
 return f"{v:.{d}f}"
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",default="artifacts");a=ap.parse_args()
 fs=sorted(glob.glob(str(Path(a.input)/"**/public_strategies_4_7_shard_*.csv.gz"),recursive=True))
 if not fs:raise RuntimeError("no shards")
 d=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 if not len(d):raise RuntimeError("empty ledger")
 key=["config","symbol","entry_time","exit_time","side"]
 if d.duplicated(key).any():raise RuntimeError(f"duplicates {int(d.duplicated(key).sum())}")
 d["entry_dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True);rows=[]
 for cfg,g in d.groupby("config",sort=True):
  for lab,x in {"ALL":g,"TRAIN_2021_2024":g[g.entry_dt<CUT],"HOLDOUT_2025_2026":g[g.entry_dt>=CUT]}.items():
   q=met(x,lab);q.update({"config":cfg,"group":g.group.iloc[0],"family":g.family.iloc[0]});rows.append(q)
 s=pd.DataFrame(rows);s.to_csv("public_strategies_4_7_summary.csv",index=False)
 tr=s[s.split=="TRAIN_2021_2024"].set_index("config");ho=s[s.split=="HOLDOUT_2025_2026"].set_index("config");z=[]
 for cfg in sorted(set(tr.index)&set(ho.index)):
  x=tr.loc[cfg];y=ho.loc[cfg];z.append({"config":cfg,"group":x.group,"family":x.family,"train_n":int(x.n),"train_pf20":x.pf20,"train_pf40":x.pf40,"train_avg20_r":x.avg20_r,"train_win20_pct":x.win20_pct,"train_hold_min":x.avg_hold_min,"hold_n":int(y.n),"hold_pf20":y.pf20,"hold_pf40":y.pf40,"hold_avg20_r":y.avg20_r,"hold_win20_pct":y.win20_pct,"hold_hold_min":y.avg_hold_min})
 q=pd.DataFrame(z);q.to_csv("public_strategies_4_7_train_holdout.csv",index=False)
 lines=["# Public Strategy Reconstructions 4–7","","Frozen concept screen. No holdout-driven tuning.","","- S4 SurfBot: structural crypto proxy only; FX carry/swap cannot be reconstructed from Binance OHLC.","- S5 ADX Pullback: public-source rules reconstructed directly.","- S6 XAU Bar Break: public entry rule + two pre-registered exit proxies.","- S7 Nexus: H4 trend and split exits preserved; proprietary M1 trigger approximated with 15m data.","- Costs: 20bp and 40bp round trip. Train < 2025-01-01 UTC; Holdout >= 2025-01-01 UTC.","","| Config | Train N | PF20 | PF40 | Win20 | AvgR20 | Hold N | PF20 | PF40 | Win20 | AvgR20 | Hold min |","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in q.sort_values(["group","config"]).iterrows():
  lines.append(f"| {r.config} | {int(r.train_n)} | {f(r.train_pf20)} | {f(r.train_pf40)} | {f(r.train_win20_pct,1)} | {f(r.train_avg20_r,4)} | {int(r.hold_n)} | {f(r.hold_pf20)} | {f(r.hold_pf40)} | {f(r.hold_win20_pct,1)} | {f(r.hold_avg20_r,4)} | {f(r.hold_hold_min,1)} |")
 Path("public_strategies_4_7_report.md").write_text("\n".join(lines)+"\n")
 d.drop(columns=["entry_dt"]).to_csv("public_strategies_4_7_all_trades.csv.gz",index=False,compression="gzip")
 print("\n".join(lines))
if __name__=="__main__":main()
