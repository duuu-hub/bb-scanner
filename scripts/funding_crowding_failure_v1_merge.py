import argparse,glob,json,math,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.funding_crowding_failure_v1 as scan

RISK=.005; CAP=6; MAX_GROSS=2.0
TRAIN_START=pd.Timestamp("2021-01-01T00:00:00Z")
TRAIN_END=pd.Timestamp("2025-01-01T00:00:00Z")
TRAIN_DAYS=(TRAIN_END-TRAIN_START).days
TRAIN_YEARS=TRAIN_DAYS/365.25

def pf(v):
 a=np.asarray(v,float);gp=a[a>0].sum();gl=-a[a<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

def select_exec(g):
 if not len(g):return pd.DataFrame(),[],[]
 z=g.sort_values(["entry_time","funding_rate","tail_strength","symbol"],
                 key=lambda s: s.abs() if s.name=="funding_rate" else s,
                 ascending=[True,False,False,True]).copy()
 acc=[];op=[];conc=[];expo=[];last=None
 for r in z.itertuples(index=False):
  et=int(r.entry_time)
  if last is None or et!=last:
   op=[p for p in op if p["exit_time"]>=et];last=et
  if len(op)>=CAP:continue
  if any(p["symbol"]==r.symbol for p in op):continue
  notional=RISK/float(r.risk_pct);gross=sum(p["notional"] for p in op)
  if gross+notional>MAX_GROSS+1e-12:continue
  acc.append(r._asdict());op.append({"symbol":r.symbol,"exit_time":int(r.exit_time),"notional":notional})
  conc.append(len(op));expo.append(gross+notional)
 return pd.DataFrame(acc),conc,expo

def account(sel,bp,years=TRAIN_YEARS):
 if not len(sel):
  return {"ret":0.,"cagr":0.,"mdd":0.,"pf":np.nan,"win":np.nan,"mean_r":np.nan,"max_ls":0},pd.DataFrame()
 z=sel.sort_values(["entry_time","exit_time","symbol"]).reset_index(drop=True)
 et=z.entry_time.to_numpy(np.int64);xt=z.exit_time.to_numpy(np.int64)
 gr=z.gross_return.to_numpy(float);rp=z.risk_pct.to_numpy(float)
 ev=[]
 for i in range(len(z)):
  a=int(et[i]);b=int(xt[i]);ev.append((a,1,i));ev.append((b,2 if a==b else 0,i))
 ev.sort(key=lambda x:(x[0],x[1],x[2]))
 eq=1.;peak=1.;mdd=0.;openrisk={};real=[]
 for ts,typ,i in ev:
  if typ==1:openrisk[i]=eq*RISK
  elif i in openrisk:
   rc=openrisk.pop(i);nr=(gr[i]-bp/10000.0)/rp[i];pnl=rc*nr;eq+=pnl
   real.append({"entry_time":int(et[i]),"exit_time":int(xt[i]),"symbol":z.iloc[i].symbol,
                "side":z.iloc[i].side,"net_r":nr,"pnl":pnl})
   peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 0)
 r=pd.DataFrame(real);nrs=r.net_r.to_numpy(float) if len(r) else np.array([])
 cur=ls=0
 for x in nrs:
  if x<0:cur+=1;ls=max(ls,cur)
  else:cur=0
 cagr=(eq**(1/years)-1) if eq>0 and years>0 else (-1. if eq<=0 else 0.)
 return {"ret":100*(eq-1),"cagr":100*cagr,"mdd":100*mdd,"pf":pf(r.pnl if len(r) else []),
         "win":100*np.mean(nrs>0) if len(nrs) else np.nan,
         "mean_r":float(np.mean(nrs)) if len(nrs) else np.nan,"max_ls":ls},r

def cmap():return {x["name"]:x for x in scan.CONFIGS}

def neighbors(name):
 cfg=cmap()[name]
 grids={"tail_q":list(scan.TAIL_Q),"abs_floor":list(scan.ABS_FLOOR),"mode":list(scan.MODES),
        "sl_atr1h":list(scan.SL_ATR),"tp_r":list(scan.TP_R),"hold_hours":list(scan.HOLD_HOURS)}
 out=[]
 for key,grid in grids.items():
  i=grid.index(cfg[key])
  for j in (i-1,i+1):
   if 0<=j<len(grid):
    q=dict(cfg);q[key]=grid[j]
    for x in scan.CONFIGS:
     if all(x[k]==q[k] for k in grids):out.append(x["name"])
 return sorted(set(out))

def fmt(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--input",default="artifacts");ap.add_argument("--out",default="funding_crowding_failure_v1_train")
 a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 fs=sorted(glob.glob(str(Path(a.input)/"**/funding_crowding_failure_v1_*.csv.gz"),recursive=True))
 metas=sorted(glob.glob(str(Path(a.input)/"**/funding_crowding_failure_v1_meta_*.json"),recursive=True))
 snaps=sorted(glob.glob(str(Path(a.input)/"**/funding_snapshot_v1_*.csv.gz"),recursive=True))
 if len(fs)!=12 or len(metas)!=12 or len(snaps)!=12:
  raise RuntimeError(f"expected 12 shards got trades={len(fs)} meta={len(metas)} funding={len(snaps)}")
 d=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 if not len(d):raise RuntimeError("zero trade rows")
 if d.duplicated(["config","symbol","funding_time"]).any():raise RuntimeError("duplicate trades")
 if (d.entry_time>=scan.TRAIN_END).any() or (d.exit_time>=scan.TRAIN_END).any():raise RuntimeError("validation leakage")
 d["year"]=pd.to_datetime(d.entry_time,unit="ms",utc=True).dt.year
 commits=set();cnt=Counter();symn=0
 for p in metas:
  m=json.loads(Path(p).read_text());commits.add(m.get("commit_sha"));cnt.update(m.get("counters",{}));symn+=int(m.get("symbols",0))
 if len(commits)!=1:raise RuntimeError(f"mixed commits {commits}")
 funding=pd.concat([pd.read_csv(x) for x in snaps],ignore_index=True)
 if len(funding):funding=funding.drop_duplicates(["symbol","fundingTime"]).sort_values(["symbol","fundingTime"])
 funding.to_csv(out/"funding_snapshot_all.csv.gz",index=False,compression="gzip")

 rows=[];yearly=[];concrows=[]
 for cfg in sorted(d.config.unique()):
  g=d[d.config==cfg]
  sel,conc,expo=select_exec(g)
  m20,r20=account(sel,20);m40,_=account(sel,40)
  rows.append({
   "config":cfg,"raw_n":len(g),"exec_n":len(sel),"exec_per_day":len(sel)/TRAIN_DAYS,
   "independent_event_n":int(sel.funding_time.nunique()) if len(sel) else 0,
   "max_conc":max(conc) if conc else 0,"avg_conc_at_entry":float(np.mean(conc)) if conc else 0.,
   "max_gross_pct":100*max(expo) if expo else 0.,
   "ret20":m20["ret"],"cagr20":m20["cagr"],"mdd20":m20["mdd"],"pf20":m20["pf"],
   "win20":m20["win"],"mean_r20":m20["mean_r"],"max_ls20":m20["max_ls"],
   "ret40":m40["ret"],"cagr40":m40["cagr"],"mdd40":m40["mdd"],"pf40":m40["pf"],"max_ls40":m40["max_ls"]
  })
  for y in (2021,2022,2023,2024):
   yg=g[g.year==y];ys,_,_=select_exec(yg);ym20,_=account(ys,20,1.0);ym40,_=account(ys,40,1.0)
   yearly.append({"config":cfg,"year":y,"exec_n":len(ys),"ret20":ym20["ret"],"mdd20":ym20["mdd"],"pf20":ym20["pf"],
                  "ret40":ym40["ret"],"mdd40":ym40["mdd"],"pf40":ym40["pf"]})
  if len(r20):
   by=r20.groupby("symbol").pnl.sum().sort_values(ascending=False);pos=by[by>0];gp=pos.sum()
   concrows.append({"config":cfg,"symbols":int(r20.symbol.nunique()),
    "top1_pos_share_pct":100*pos.head(1).sum()/gp if gp>0 else np.nan,
    "top3_pos_share_pct":100*pos.head(3).sum()/gp if gp>0 else np.nan,
    "top5_pos_share_pct":100*pos.head(5).sum()/gp if gp>0 else np.nan})
  else:concrows.append({"config":cfg,"symbols":0})
 s=pd.DataFrame(rows);ydf=pd.DataFrame(yearly);cdf=pd.DataFrame(concrows)
 yr=ydf.groupby("config").agg(positive_years=("ret20",lambda x:int((x>0).sum())),
                              min_year_pf20=("pf20","min"),worst_year_ret20=("ret20","min")).reset_index()
 s=s.merge(yr,on="config",how="left")
 s["base_gate"]=(s.exec_n>=300)&(s.exec_per_day>=.20)&(s.ret20>0)&(s.ret40>0)&(s.pf20>=1.08)&(s.pf40>=1.00)&\
                 (s.mdd20<=35)&(s.positive_years>=3)&(s.min_year_pf20>=.85)
 good=set(s[(s.pf20>1.03)&(s.ret20>0)].config)
 s["adjacent_good"]=s.config.map(lambda n:any(x in good for x in neighbors(n)))
 s["registered_pass"]=s.base_gate&s.adjacent_good
 rank=s.sort_values(["registered_pass","ret20","mdd20","pf40","exec_n"],ascending=[False,False,True,False,False])
 s.to_csv(out/"summary.csv",index=False);ydf.to_csv(out/"yearly.csv",index=False);cdf.to_csv(out/"symbol_concentration.csv",index=False)
 d.drop(columns=["year"]).to_csv(out/"all_trades_train.csv.gz",index=False,compression="gzip")
 Path(out/"provenance.json").write_text(json.dumps({
  "experiment_id":"funding_crowding_failure_v1_train","classification":"EXPLORATORY",
  "price_source_run":36095439671,"funding_source":"Binance /fapi/v1/fundingRate snapshot in artifact",
  "shard_commit_sha":next(iter(commits)),"shards":12,"symbols_sum":symn,
  "validation_opened":False,"costs_round_trip_bp":[20,40],"funding_pnl":"excluded","counters":dict(cnt)
 },indent=2)+"\n")
 lines=["# Funding Crowding Failure V1 TRAIN","",
 "Classification: EXPLORATORY. 2021-2024 TRAIN only; 2025-2026 validation was not opened.",
 "Signal: extreme realized funding tail + immediate price non-confirmation; trade opposite funding sign at first 15m open strictly after settlement.",
 "Portfolio: 0.5% equity risk/trade, cap6, gross<=200%, same-symbol overlap blocked. Costs 20/40bp round trip. Funding PnL excluded in V1 by design.","",
 "| Config | ExecN | /day | Events | Ret20 | MDD20 | PF20 | Ret40 | PF40 | +Years | Pass |",
 "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|"]
 for _,r in rank.head(20).iterrows():
  lines.append(f"| {r.config} | {int(r.exec_n)} | {fmt(r.exec_per_day,2)} | {int(r.independent_event_n)} | {fmt(r.ret20,1)}% | {fmt(r.mdd20,1)}% | {fmt(r.pf20)} | {fmt(r.ret40,1)}% | {fmt(r.pf40)} | {int(r.positive_years)}/4 | {'YES' if r.registered_pass else 'NO'} |")
 passed=s[s.registered_pass]
 lines+=["",f"Registered-pass configs: **{len(passed)} / {len(s)}**.","",f"Exclusion/fetch counters: {dict(cnt)}"]
 if len(passed):lines+=["","Passed: "+", ".join(passed.sort_values("ret20",ascending=False).config.tolist())]
 Path(out/"report.md").write_text("\n".join(lines)+"\n")
 print("\n".join(lines))
if __name__=="__main__":main()
