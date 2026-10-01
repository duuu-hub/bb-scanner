import argparse,glob,math
from pathlib import Path
import numpy as np,pandas as pd
CUT=pd.Timestamp("2025-01-01T00:00:00Z")
def pf(x):
 x=np.asarray(x,float);gp=x[x>0].sum();gl=-x[x<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)
def met(g,label):
 if not len(g):return {"split":label,"n":0}
 z=g.sort_values(["entry_time","symbol"]);r20=z.net20_r.to_numpy(float);r40=z.net40_r.to_numpy(float)
 return {"split":label,"n":len(z),"symbols":z.symbol.nunique(),"pf20":pf(r20),"pf40":pf(r40),"win20_pct":100*np.mean(r20>0),"avg20_r":r20.mean(),"avg40_r":r40.mean(),"sum20_r":r20.sum(),"sum40_r":r40.sum(),"avg_hold_min":z.hold_min.mean(),"median_hold_min":z.hold_min.median(),"tp_n":int((z.reason=="TP").sum()),"sl_n":int((z.reason=="SL").sum()),"time_n":int((z.reason=="TIME").sum())}
def fmt(v,d=3):
 if pd.isna(v):return ""
 if math.isinf(v):return "inf"
 return f"{v:.{d}f}"
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",default="artifacts");a=ap.parse_args()
 fs=sorted(glob.glob(str(Path(a.input)/"**/breakout_algo3_trades_shard_*.csv.gz"),recursive=True))
 if not fs:raise RuntimeError("no shard trades")
 d=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 if not len(d):raise RuntimeError("empty ledger")
 key=["config","symbol","entry_time","exit_time","side"]
 if d.duplicated(key).any():raise RuntimeError(f"duplicate trades {int(d.duplicated(key).sum())}")
 d["entry_dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True);rows=[]
 for cfg,g in d.groupby("config",sort=True):
  for label,x in {"ALL":g,"TRAIN_2021_2024":g[g.entry_dt<CUT],"HOLDOUT_2025_2026":g[g.entry_dt>=CUT]}.items():
   q=met(x,label);q.update({"config":cfg,"family":g.family.iloc[0]});rows.append(q)
 s=pd.DataFrame(rows);s.to_csv("breakout_algo3_summary.csv",index=False)
 tr=s[s.split=="TRAIN_2021_2024"].set_index("config");ho=s[s.split=="HOLDOUT_2025_2026"].set_index("config");out=[]
 for cfg in sorted(set(tr.index)&set(ho.index)):
  x=tr.loc[cfg];y=ho.loc[cfg];out.append({"config":cfg,"train_n":int(x.n),"train_pf20":x.pf20,"train_pf40":x.pf40,"train_avg20_r":x.avg20_r,"train_avg_hold_min":x.avg_hold_min,"hold_n":int(y.n),"hold_pf20":y.pf20,"hold_pf40":y.pf40,"hold_avg20_r":y.avg20_r,"hold_win20_pct":y.win20_pct,"hold_avg_hold_min":y.avg_hold_min})
 q=pd.DataFrame(out);q.to_csv("breakout_algo3_train_holdout.csv",index=False)
 lines=["# Breakout_Algo #3 Proxy Replay","","Not a vendor-code reproduction. Six pre-registered proxies derived only from public observable characteristics.","","- Frozen split: train before 2025-01-01 UTC; holdout from 2025-01-01 UTC.","- 1H signal context; 1H entry TTL; max hold 4H.","- Fixed 1 ATR stop and 1R TP to match the public account's roughly 1:1 realized win/loss shape.","- Costs 20bp / 40bp round trip.","- Canonical official Binance 1m chronology.","","| Config | Train N | Train PF20 | Train PF40 | Train hold(min) | Hold N | Hold PF20 | Hold PF40 | Hold avgR20 | Hold win% | Hold hold(min) |","|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in q.iterrows():lines.append(f"| {r.config} | {int(r.train_n)} | {fmt(r.train_pf20)} | {fmt(r.train_pf40)} | {fmt(r.train_avg_hold_min,1)} | {int(r.hold_n)} | {fmt(r.hold_pf20)} | {fmt(r.hold_pf40)} | {fmt(r.hold_avg20_r,4)} | {fmt(r.hold_win20_pct,1)} | {fmt(r.hold_avg_hold_min,1)} |")
 lines+=["","No holdout-driven retuning in this workflow."]
 Path("breakout_algo3_report.md").write_text("\n".join(lines)+"\n")
 d.drop(columns=["entry_dt"]).to_csv("breakout_algo3_all_trades.csv.gz",index=False,compression="gzip")
 print("\n".join(lines))
if __name__=="__main__":main()
