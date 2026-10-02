import argparse,glob,json,math,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.compression_first_expansion_rejection_v3 as scan

RISK=.005
MAX_GROSS=2.0
CAP=6
TRAIN_START=pd.Timestamp("2021-01-01T00:00:00Z")
TRAIN_END=pd.Timestamp("2025-01-01T00:00:00Z")
TRAIN_DAYS=(TRAIN_END-TRAIN_START).days
TRAIN_YEARS=TRAIN_DAYS/365.25

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

def account(sel,bp,years=TRAIN_YEARS):
 if not len(sel):
  return {"return_pct":0.,"cagr_pct":0.,"mdd_pct":0.,"pf":np.nan,"max_ls":0,
          "win_pct":np.nan,"mean_net_r":np.nan,"median_net_r":np.nan},pd.DataFrame()
 z=sel.sort_values(["entry_time","exit_time","symbol"]).reset_index(drop=True)
 et=z["entry_time"].to_numpy(np.int64);xt=z["exit_time"].to_numpy(np.int64)
 gr=z["gross_return"].to_numpy(float);rp=z["risk_pct"].to_numpy(float)
 ev=[]
 for i in range(len(z)):
  a=int(et[i]);b=int(xt[i]);ev.append((a,1,i));ev.append((b,2 if b==a else 0,i))
 ev.sort(key=lambda x:(x[0],x[1],x[2]))
 eq=1.;peak=1.;mdd=0.;openrisk={};rows=[]
 for ts,typ,i in ev:
  if typ==1:openrisk[i]=eq*RISK
  else:
   if i not in openrisk:continue
   rc=openrisk.pop(i);nr=(gr[i]-bp/10000.0)/rp[i]
   pnl=rc*nr;eq+=pnl
   rows.append({"entry_time":int(et[i]),"exit_time":int(xt[i]),"symbol":z.iloc[i]["symbol"],
                "net_r":float(nr),"pnl":float(pnl)})
   peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 0)
 r=pd.DataFrame(rows);nrs=r.net_r.to_numpy(float) if len(r) else np.array([])
 cur=ls=0
 for x in nrs:
  if x<0:cur+=1;ls=max(ls,cur)
  else:cur=0
 cagr=(eq**(1/years)-1) if eq>0 and years>0 else (-1. if eq<=0 else 0.)
 return {"return_pct":100*(eq-1),"cagr_pct":100*cagr,"mdd_pct":100*mdd,
         "pf":pf(r.pnl if len(r) else []),"max_ls":ls,
         "win_pct":100*np.mean(nrs>0) if len(nrs) else np.nan,
         "mean_net_r":float(np.mean(nrs)) if len(nrs) else np.nan,
         "median_net_r":float(np.median(nrs)) if len(nrs) else np.nan},r

def cfgmap():
 return {x["name"]:x for x in scan.CONFIGS}

def neighbors(name):
 cm=cfgmap();cfg=cm[name]
 grids={
  "retrace_fraction":list(scan.RETRACE),
  "retest_ttl_bars":list(scan.RETEST_TTL),
  "confirm_mode":list(scan.CONFIRM_MODES),
  "confirm_ttl_bars":list(scan.CONFIRM_TTL),
  "sl_atr96":list(scan.SL_ATR),
  "tp_r":list(scan.TP_R)
 }
 out=[]
 for key,grid in grids.items():
  i=grid.index(cfg[key])
  for j in (i-1,i+1):
   if 0<=j<len(grid):
    q=dict(cfg);q[key]=grid[j]
    for x in scan.CONFIGS:
     if all(x[k]==q[k] for k in grids):out.append(x["name"])
 return sorted(set(out))

def f(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser()
 ap.add_argument("--input",default="artifacts")
 ap.add_argument("--out",default="compression_rejection_v3_train")
 a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)

 fs=sorted(glob.glob(str(Path(a.input)/"**/compression_rejection_v3_*.csv.gz"),recursive=True))
 metas=sorted(glob.glob(str(Path(a.input)/"**/compression_rejection_v3_meta_*.json"),recursive=True))
 if len(fs)!=12 or len(metas)!=12:raise RuntimeError(f"expected 12 shards, got csv={len(fs)} meta={len(metas)}")
 frames=[pd.read_csv(x) for x in fs]
 d=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
 if not len(d):raise RuntimeError("zero resolved rows")
 keys=["config","symbol","signal_time","entry_time"]
 if d.duplicated(keys).any():raise RuntimeError("duplicate resolved rows")
 if (d.entry_time>=scan.TRAIN_END).any() or (d.exit_time>=scan.TRAIN_END).any():
  raise RuntimeError("validation/split leakage")
 d["dt"]=pd.to_datetime(d.entry_time,unit="ms",utc=True);d["year"]=d["dt"].dt.year

 cfgcnt={x["name"]:Counter() for x in scan.CONFIGS};global_cnt=Counter();commits=set();symbols=0
 for p in metas:
  m=json.loads(Path(p).read_text());commits.add(m.get("commit_sha"));symbols+=int(m.get("symbols",0))
  global_cnt.update(m.get("global_counters",{}))
  for k,v in m.get("config_counters",{}).items():cfgcnt.setdefault(k,Counter()).update(v)
 if len(commits)!=1:raise RuntimeError(f"mixed shard commits: {commits}")

 rows=[];yearly=[];concrows=[]
 for cfg in sorted(d.config.unique()):
  g=d[d.config==cfg];sel,conc,expo=select_exec(g)
  m20,r20=account(sel,20);m40,_=account(sel,40)
  cc=cfgcnt.get(cfg,Counter())
  signals=int(cc.get("signals",0));retest=int(cc.get("retest_touched",0));confirmed=int(cc.get("confirmed",0));resolved=int(cc.get("resolved",len(g)))
  rows.append({
   "config":cfg,"signals":signals,
   "retest_n":retest,"retest_pct":100*retest/signals if signals else np.nan,
   "confirmed_n":confirmed,"confirm_pct_of_retest":100*confirmed/retest if retest else np.nan,
   "resolved_n":resolved,"exec_n":len(sel),"exec_per_day":len(sel)/TRAIN_DAYS,
   "max_conc":max(conc) if conc else 0,"avg_conc_at_entry":float(np.mean(conc)) if conc else 0.,
   "max_gross_pct":100*max(expo) if expo else 0.,
   "data_gap":int(cc.get("data_gap",0)),"entry_mismatch":int(cc.get("entry_mismatch",0)),
   "exit_mismatch":int(cc.get("exit_mismatch",0)),"canonical_excluded":int(cc.get("canonical_excluded",0)),
   "risk_filter":int(cc.get("risk_filter",0)),"split_boundary_excluded":int(cc.get("split_boundary_excluded",0)),
   "ret20":m20["return_pct"],"cagr20":m20["cagr_pct"],"mdd20":m20["mdd_pct"],"pf20":m20["pf"],
   "win20":m20["win_pct"],"mean_r20":m20["mean_net_r"],"median_r20":m20["median_net_r"],"max_ls20":m20["max_ls"],
   "ret40":m40["return_pct"],"cagr40":m40["cagr_pct"],"mdd40":m40["mdd_pct"],"pf40":m40["pf"],"max_ls40":m40["max_ls"]
  })
  for y in (2021,2022,2023,2024):
   yg=g[g.year==y];ys,_,_=select_exec(yg);ym20,_=account(ys,20,1.0);ym40,_=account(ys,40,1.0)
   yearly.append({"config":cfg,"year":y,"raw_n":len(yg),"exec_n":len(ys),
                  "ret20":ym20["return_pct"],"mdd20":ym20["mdd_pct"],"pf20":ym20["pf"],
                  "ret40":ym40["return_pct"],"mdd40":ym40["mdd_pct"],"pf40":ym40["pf"]})
  if len(r20):
   by=r20.groupby("symbol").pnl.sum().sort_values(ascending=False);pos=by[by>0];gp=pos.sum()
   concrows.append({"config":cfg,"symbols":int(r20.symbol.nunique()),
                    "top1_pos_share_pct":100*pos.head(1).sum()/gp if gp>0 else np.nan,
                    "top3_pos_share_pct":100*pos.head(3).sum()/gp if gp>0 else np.nan,
                    "top5_pos_share_pct":100*pos.head(5).sum()/gp if gp>0 else np.nan})
  else:concrows.append({"config":cfg,"symbols":0})

 s=pd.DataFrame(rows);ydf=pd.DataFrame(yearly);cdf=pd.DataFrame(concrows)
 yragg=ydf.groupby("config").agg(
   positive_years=("ret20",lambda x:int((x>0).sum())),
   min_year_pf20=("pf20","min"),worst_year_ret20=("ret20","min")
 ).reset_index()
 s=s.merge(yragg,on="config",how="left")
 s["base_gate"]=(s.exec_n>=400)&(s.exec_per_day>=.27)&(s.ret20>0)&(s.ret40>0)&\
                 (s.pf20>=1.08)&(s.pf40>=1.00)&(s.mdd20<=35)&\
                 (s.positive_years>=3)&(s.min_year_pf20>=.85)
 good=set(s[(s.pf20>1.03)&(s.ret20>0)].config)
 s["adjacent_good"]=s.config.map(lambda x:any(n in good for n in neighbors(x)))
 s["registered_pass"]=s.base_gate&s.adjacent_good

 rank=s.sort_values(["registered_pass","ret20","mdd20","pf40","exec_n"],
                    ascending=[False,False,True,False,False]).copy()
 s.to_csv(out/"summary.csv",index=False);ydf.to_csv(out/"yearly.csv",index=False);cdf.to_csv(out/"symbol_concentration.csv",index=False)
 d.drop(columns=["dt"]).to_csv(out/"all_resolved_train.csv.gz",index=False,compression="gzip")
 Path(out/"provenance.json").write_text(json.dumps({
   "experiment_id":"compression_first_expansion_rejection_v3_train","classification":"EXPLORATORY",
   "source_data_run":36095439671,"shard_commit_sha":next(iter(commits)),"shards":12,"symbols_sum":symbols,
   "global_counters":dict(global_cnt),"validation_opened":False,
   "costs_round_trip_bp":[20,40],"funding":"excluded"
 },indent=2)+"\n")

 lines=["# Compression -> First Expansion Rejection V3 TRAIN","",
 "Classification: EXPLORATORY. 2021-2024 TRAIN only; 2025-2026 validation was not opened.",
 "V3: SHORT compression/breakout -> retest touch -> completed-bar rejection confirmation -> next 15m OPEN entry.",
 "Portfolio: 0.5% equity risk/trade, cap6, gross<=200%, same-symbol overlap blocked; 20/40bp round-trip costs; funding excluded.","",
 "| Config | Signals | Retest% | Confirm% | ExecN | /day | Ret20 | MDD20 | PF20 | Ret40 | PF40 | +Years | Pass |",
 "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|"]
 for _,r in rank.head(20).iterrows():
  lines.append(f"| {r.config} | {int(r.signals)} | {f(r.retest_pct,1)} | {f(r.confirm_pct_of_retest,1)} | {int(r.exec_n)} | {f(r.exec_per_day,2)} | {f(r.ret20,1)}% | {f(r.mdd20,1)}% | {f(r.pf20)} | {f(r.ret40,1)}% | {f(r.pf40)} | {int(r.positive_years)}/4 | {'YES' if r.registered_pass else 'NO'} |")
 passed=s[s.registered_pass].sort_values(["ret20","mdd20"],ascending=[False,True])
 lines+=["",f"Registered-pass configs: **{len(passed)} / {len(s)}**."]
 if len(passed):lines+=["","Passed: "+", ".join(passed.config.tolist())]
 Path(out/"report.md").write_text("\n".join(lines)+"\n")
 print("\n".join(lines))

if __name__=="__main__":main()
