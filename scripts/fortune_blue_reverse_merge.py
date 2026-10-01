import argparse,glob,math
from pathlib import Path
import numpy as np,pandas as pd
def pf(x):
 x=np.asarray(x,float);gp=x[x>0].sum();gl=-x[x<0].sum()
 return float("inf") if gl==0 and gp>0 else (gp/gl if gl>0 else np.nan)
def stats(g,label):
 if not len(g):return {"split":label,"n":0}
 z=g.sort_values("entry_time");x=z.net20_r.to_numpy(float);gross=z.gross_r.to_numpy(float)
 wins=gross[gross>0];loss=gross[gross<0]
 days=max((z.entry_time.max()-z.entry_time.min())/86400000,1)
 return {"split":label,"n":len(z),"symbols":z.symbol.nunique(),"trades_per_day":len(z)/days,"gross_pf":pf(gross),"pf20":pf(x),"pf40":pf(z.net40_r),"win_pct":100*np.mean(gross>0),"avg_win_r":wins.mean() if len(wins) else np.nan,"avg_loss_r":loss.mean() if len(loss) else np.nan,"win_loss_abs":(wins.mean()/(-loss.mean())) if len(wins) and len(loss) else np.nan,"avg_hold_min":z.hold_min.mean(),"median_hold_min":z.hold_min.median()}
def f(v,d=3):
 if pd.isna(v):return ""
 if math.isinf(v):return "inf"
 return f"{v:.{d}f}"
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",default="artifacts");a=ap.parse_args()
 fs=glob.glob(str(Path(a.input)/"**/fortune_blue_infer_*.csv.gz"),recursive=True)
 if not fs:raise RuntimeError("no shards")
 d=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True);d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True)
 rows=[]
 for cfg,g in d.groupby("config"):
  cuts={"TRAIN_2021_2024":g[g.dt<pd.Timestamp("2025-01-01",tz="UTC")],"EVAL_2025":g[(g.dt>=pd.Timestamp("2025-01-01",tz="UTC"))&(g.dt<pd.Timestamp("2026-01-01",tz="UTC"))],"EVAL_2026":g[g.dt>=pd.Timestamp("2026-01-01",tz="UTC")],"ALL":g}
  for lab,x in cuts.items():
   q=stats(x,lab);q.update({"config":cfg,"group":g.group.iloc[0],"family":g.family.iloc[0]});rows.append(q)
 s=pd.DataFrame(rows);s.to_csv("fortune_blue_infer_summary.csv",index=False)
 lines=["# Fortune / Blue reverse-inference replay","","This is a hypothesis-driven reconstruction from public behavior and descriptions, not exact vendor code.","","Fortune live-shape target for comparison: about 134 trades, ~2 minute average hold, ~86-89% directional win rate, realized average win/loss around 0.23:1.","","| Config | Split | N | Trades/day | Gross PF | PF20 | PF40 | Win% | Win/Loss | Avg hold min |","|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
 for _,r in s.sort_values(["group","config","split"]).iterrows():lines.append(f"| {r.config} | {r.split} | {int(r.n)} | {f(r.trades_per_day,2)} | {f(r.gross_pf)} | {f(r.pf20)} | {f(r.pf40)} | {f(r.win_pct,1)} | {f(r.win_loss_abs,2)} | {f(r.avg_hold_min,1)} |")
 Path("fortune_blue_infer_report.md").write_text("\n".join(lines)+"\n")
 d.drop(columns=["dt"]).to_csv("fortune_blue_infer_all.csv.gz",index=False,compression="gzip")
 print("\n".join(lines))
if __name__=="__main__":main()
