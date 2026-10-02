import argparse,glob,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.compression_first_expansion_v1 as scan

RISK=.005
MAX_GROSS=2.0
CAP=6
TRAIN_DAYS=(pd.Timestamp("2025-01-01",tz="UTC")-pd.Timestamp("2021-01-01",tz="UTC")).days

def pf(v):
 a=np.asarray(v,float);gp=a[a>0].sum();gl=-a[a<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

def select_exec(g):
 if not len(g):return pd.DataFrame(),[],[]
 z=g.sort_values(["entry_time","expansion_strength","compression_strength","symbol"],
                 ascending=[True,False,False,True]).copy()
 acc=[];op=[];conc=[];expo=[];last_et=None
 for r in z.itertuples(index=False):
  et=int(r.entry_time)
  if last_et is None or et!=last_et:
   # A position exiting on this same bar is still occupying a slot at bar open.
   op=[p for p in op if p["exit_time"]>=et]
   last_et=et
  if len(op)>=CAP:continue
  if any(p["symbol"]==r.symbol for p in op):continue
  notional=RISK/float(r.risk_pct)
  gross=sum(p["notional"] for p in op)
  if gross+notional>MAX_GROSS+1e-12:continue
  acc.append(r._asdict())
  op.append({"symbol":r.symbol,"exit_time":int(r.exit_time),"notional":notional})
  conc.append(len(op));expo.append(gross+notional)
 return pd.DataFrame(acc),conc,expo

def account(sel,bp):
 if not len(sel):
  return {"return_pct":0.,"mdd_pct":0.,"pf":np.nan,"max_ls":0,"win_pct":np.nan,
          "mean_net_r":np.nan,"median_net_r":np.nan}
 z=sel.sort_values(["entry_time","exit_time","symbol"]).reset_index(drop=True)
 et=z["entry_time"].to_numpy(np.int64);xt=z["exit_time"].to_numpy(np.int64)
 gr=z["gross_return"].to_numpy(float);rp=z["risk_pct"].to_numpy(float)
 ev=[]
 for i in range(len(z)):
  a=int(et[i]);b=int(xt[i]);ev.append((a,1,i));ev.append((b,2 if b==a else 0,i))
 ev.sort(key=lambda x:(x[0],x[1],x[2]))
 eq=1.;peak=1.;mdd=0.;openrisk={};pnls=[];nrs=[]
 for ts,typ,i in ev:
  if typ==1:
   openrisk[i]=eq*RISK
  else:
   if i not in openrisk:continue
   rc=openrisk.pop(i);nr=(gr[i]-bp/10000.0)/rp[i]
   pnl=rc*nr;eq+=pnl;pnls.append(pnl);nrs.append(nr)
   peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 0)
 cur=ls=0
 for x in nrs:
  if x<0:cur+=1;ls=max(ls,cur)
  else:cur=0
 return {"return_pct":100*(eq-1),"mdd_pct":100*mdd,"pf":pf(pnls),"max_ls":ls,
         "win_pct":100*np.mean(np.asarray(nrs)>0) if nrs else np.nan,
         "mean_net_r":float(np.mean(nrs)) if nrs else np.nan,
         "median_net_r":float(np.median(nrs)) if nrs else np.nan}

def neighbors(name):
 cfg={x["name"]:x for x in scan.CONFIGS}[name]
 vals={
  "compression_max":list(scan.COMPRESSION),
  "expansion_min":list(scan.EXPANSION),
  "sl_atr96":list(scan.SL_ATR),
  "tp_r":list(scan.TP_R)
 }
 out=[]
 for key,grid in vals.items():
  i=grid.index(cfg[key])
  for j in (i-1,i+1):
   if 0<=j<len(grid):
    q=dict(cfg);q[key]=grid[j]
    for x in scan.CONFIGS:
     if all(x[k]==q[k] for k in ("compression_max","expansion_min","sl_atr96","tp_r")):
      out.append(x["name"])
 return sorted(set(out))

def f(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",default="artifacts");ap.add_argument("--out",default="compression_first_expansion_v1_train")
 a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 fs=sorted(glob.glob(str(Path(a.input)/"**/compression_first_expansion_v1_*.csv.gz"),recursive=True))
 if not fs:raise RuntimeError("no shard csvs")
 frames=[pd.read_csv(x) for x in fs]
 d=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
 if not len(d):raise RuntimeError("zero resolved rows")
 keys=["config","symbol","signal_time","entry_time","side"]
 if d.duplicated(keys).any():raise RuntimeError("duplicate signal rows")
 if (d.entry_time>=scan.TRAIN_END).any() or (d.exit_time>=scan.TRAIN_END).any():
  raise RuntimeError("validation/boundary leakage")
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True)
 d["year"]=d.dt.dt.year

 rows=[];yearly=[]
 for cfg in sorted(d.config.unique()):
  base=d[d.config==cfg]
  for mode in ("BOTH","LONG","SHORT"):
   g=base if mode=="BOTH" else base[base.side==mode.lower()]
   sel,conc,expo=select_exec(g)
   m20=account(sel,20);m40=account(sel,40)
   rows.append({
    "config":cfg,"mode":mode,"raw_n":len(g),"exec_n":len(sel),
    "exec_per_day":len(sel)/TRAIN_DAYS,
    "max_conc":max(conc) if conc else 0,
    "avg_conc_at_entry":float(np.mean(conc)) if conc else 0.,
    "max_gross_pct":100*max(expo) if expo else 0.,
    "ret20":m20["return_pct"],"mdd20":m20["mdd_pct"],"pf20":m20["pf"],
    "win20":m20["win_pct"],"mean_r20":m20["mean_net_r"],"median_r20":m20["median_net_r"],"max_ls20":m20["max_ls"],
    "ret40":m40["return_pct"],"mdd40":m40["mdd_pct"],"pf40":m40["pf"],"max_ls40":m40["max_ls"]
   })
   for y in (2021,2022,2023,2024):
    yg=g[g.year==y];ys,_,_=select_exec(yg);ym20=account(ys,20);ym40=account(ys,40)
    yearly.append({"config":cfg,"mode":mode,"year":y,"raw_n":len(yg),"exec_n":len(ys),
                   "ret20":ym20["return_pct"],"mdd20":ym20["mdd_pct"],"pf20":ym20["pf"],
                   "ret40":ym40["return_pct"],"mdd40":ym40["mdd_pct"],"pf40":ym40["pf"]})

 s=pd.DataFrame(rows);ydf=pd.DataFrame(yearly)
 both=s[s["mode"]=="BOTH"].copy()

 # Registered acceptance gates.
 both["base_gate"]=(both.exec_n>=700)&(both.exec_per_day>=.45)&(both.pf20>=1.08)&(both.pf40>=1.00)&\
                   (both.mdd20<=35)&(both.ret20>0)&(both.ret40>0)
 good_neighbor=set(both[(both.pf20>1.03)&(both.ret20>0)].config)
 both["adjacent_good"]=both.config.map(lambda x:any(n in good_neighbor for n in neighbors(x)))
 both["registered_pass"]=both.base_gate&both.adjacent_good

 # Merge flags back for report.
 s=s.merge(both[["config","base_gate","adjacent_good","registered_pass"]],on="config",how="left")
 s.to_csv(out/"summary.csv",index=False);ydf.to_csv(out/"yearly.csv",index=False)
 d.drop(columns=["dt"]).to_csv(out/"all_resolved_train.csv.gz",index=False,compression="gzip")

 top=both.sort_values(["registered_pass","ret20","pf20"],ascending=[False,False,False]).head(16)
 lines=["# Compression -> First Expansion V1 TRAIN","",
 "Classification: EXPLORATORY. TRAIN only (2021-2024); 2025-2026 was not opened by this merge.",
 "Signal: 15m 4h box first close-break after ATR16/ATR96 compression, expansion candle confirmation, next-15m-open entry.",
 "Portfolio: 0.5% risk/trade, cap6, gross<=200%, same-symbol overlap blocked. Funding excluded.",
 "Canonical parent-bar collision handling: shared Binance 1m engine. Costs: 20bp and 40bp round trip.","",
 "Primary selection is BOTH directions. LONG/SHORT are diagnostics only; choosing one after this run would be a new exploratory generation.","",
 "| Config | ExecN | /day | Ret20 | MDD20 | PF20 | Ret40 | PF40 | Pass |",
 "|---|---:|---:|---:|---:|---:|---:|---:|:---:|"]
 for _,r in top.iterrows():
  lines.append(f"| {r.config} | {int(r.exec_n)} | {f(r.exec_per_day,2)} | {f(r.ret20,1)}% | {f(r.mdd20,1)}% | {f(r.pf20)} | {f(r.ret40,1)}% | {f(r.pf40)} | {'YES' if r.registered_pass else 'NO'} |")
 passed=both[both.registered_pass]
 lines+=["",f"Registered-pass configs: **{len(passed)} / {len(both)}**."]
 if len(passed):
  lines+=["","Passed: "+", ".join(passed.sort_values("ret20",ascending=False).config.tolist())]
 Path(out/"report.md").write_text("\n".join(lines)+"\n")
 print("\n".join(lines))

if __name__=="__main__":main()
