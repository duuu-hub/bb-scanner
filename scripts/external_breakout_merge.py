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
 z=g.sort_values(["entry_time","symbol"]);r20=z.net20_r.to_numpy(float);r40=z.net40_r.to_numpy(float)
 return {"split":label,"n":len(z),"symbols":z.symbol.nunique(),"long_n":int((z.side=="long").sum()),"short_n":int((z.side=="short").sum()),"tp_n":int((z.reason=="TP").sum()),"sl_n":int((z.reason=="SL").sum()),"time_n":int((z.reason=="TIME").sum()),"gross_pf":pf(z.gross_r),"gross_avg_r":z.gross_r.mean(),"pf20":pf(r20),"win20_pct":100*np.mean(r20>0),"avg20_r":r20.mean(),"sum20_r":r20.sum(),"pf40":pf(r40),"win40_pct":100*np.mean(r40>0),"avg40_r":r40.mean(),"sum40_r":r40.sum(),"avg_hold_min":z.hold_min.mean(),"median_hold_min":z.hold_min.median(),"max_loss_streak20":streak(r20)}
def fmt(v,d=3):
 if pd.isna(v):return ""
 if math.isinf(v):return "inf"
 return f"{v:.{d}f}"
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",default="artifacts");a=ap.parse_args()
 fs=sorted(glob.glob(str(Path(a.input)/"**/external_breakout_trades_shard_*.csv.gz"),recursive=True))
 if not fs:raise RuntimeError("no shard trades")
 d=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 if not len(d):raise RuntimeError("empty ledger")
 key=["config","symbol","entry_time","exit_time","side"]
 if d.duplicated(key).any():raise RuntimeError(f"duplicate trades {int(d.duplicated(key).sum())}")
 d["entry_dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True);rows=[]
 for cfg,g in d.groupby("config",sort=True):
  for label,x in {"ALL":g,"TRAIN_2021_2024":g[g.entry_dt<CUT],"HOLDOUT_2025_2026":g[g.entry_dt>=CUT]}.items():
   m=met(x,label);m.update({"config":cfg,"group":g.group.iloc[0],"family":g.family.iloc[0]});rows.append(m)
 s=pd.DataFrame(rows);s.to_csv("external_breakout_summary.csv",index=False)
 tr=s[s.split=="TRAIN_2021_2024"].set_index("config");ho=s[s.split=="HOLDOUT_2025_2026"].set_index("config");q=[]
 for cfg in sorted(set(tr.index)&set(ho.index)):
  x=tr.loc[cfg];y=ho.loc[cfg];q.append({"config":cfg,"group":x.group,"train_n":int(x.n),"train_pf20":x.pf20,"train_pf40":x.pf40,"train_avg20_r":x.avg20_r,"hold_n":int(y.n),"hold_pf20":y.pf20,"hold_pf40":y.pf40,"hold_avg20_r":y.avg20_r,"hold_win20_pct":y.win20_pct})
 q=pd.DataFrame(q);q.to_csv("external_breakout_train_holdout.csv",index=False)
 lines=["# External Breakout Replay — Fortune + Blue Strike Proxy","","Concept reconstruction, not vendor-code reproduction.","","- Data: Binance USD-M 15m run 36095439671, resampled to 1H.","- Frozen split: train before 2025-01-01 UTC; holdout from 2025-01-01 UTC.","- Fortune: prior high/low stop breakout, 1 ATR stop, 3R TP; hidden trailing not guessed.","- Blue Strike: six pre-registered proxy breakout families; proprietary A-F definitions unknown.","- Official Binance 1m resolves competing stop entries, entry-bar exits and later TP/SL collisions.","- Costs: round-trip 20bp and 40bp.","- One position at a time per symbol/config; max hold 24h.","","## Train vs holdout","","| Config | Train N | Train PF20 | Train PF40 | Hold N | Hold PF20 | Hold PF40 | Hold avg R20 | Hold win%20 |","|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in q.sort_values(["group","config"]).iterrows():lines.append(f"| {r.config} | {int(r.train_n)} | {fmt(r.train_pf20)} | {fmt(r.train_pf40)} | {int(r.hold_n)} | {fmt(r.hold_pf20)} | {fmt(r.hold_pf40)} | {fmt(r.hold_avg20_r,4)} | {fmt(r.hold_win20_pct,1)} |")
 lines+=["","## Integrity","", "No threshold is selected or changed after viewing holdout in this workflow. Any refinement must be defined from TRAIN only in a separate frozen run."]
 Path("external_breakout_report.md").write_text("\n".join(lines)+"\n")
 d.drop(columns=["entry_dt"]).to_csv("external_breakout_all_trades.csv.gz",index=False,compression="gzip")
 print("\n".join(lines))
if __name__=="__main__":main()
