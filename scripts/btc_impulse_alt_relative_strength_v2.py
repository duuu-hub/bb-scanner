import argparse,glob,json,math,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd
import scripts.external_breakout_replay as ex
import scripts.btc_impulse_alt_lag_catchup_v1_merge as v1

TRAIN_END=v1.TRAIN_END
TRAIN_DAYS=v1.TRAIN_DAYS
TRAIN_YEARS=v1.TRAIN_YEARS

def opposite(side):
 return "short" if side=="long" else "long"

def evaluate_outcomes(features,data_root):
 fm=v1.files_by_symbol(data_root)
 unions=[];selections={}
 for lm in v1.LAG_MAX:
  for k in v1.TOPK:
   z=v1.pick_pairs(features,lm,k)
   selections[(lm,k)]=z
   if len(z):unions.append(z[["symbol","entry_time","side","lag_gap","alt_atr1h"]])
 if not unions:raise RuntimeError("no selected relative-strength pairs")
 u=pd.concat(unions,ignore_index=True).drop_duplicates(["symbol","entry_time"]).copy()

 outcomes=[];cnt=Counter()
 for si,(sym,g) in enumerate(u.groupby("symbol",sort=True),1):
  p=fm.get(sym)
  if p is None:
   cnt["symbol_file_missing"]+=len(g);continue
  t,o,h,l,c=ex.load(p);segs=ex.segments(t)
  for row in g.sort_values("entry_time").itertuples(index=False):
   et=int(row.entry_time);j=int(np.searchsorted(t,et))
   if j>=len(t) or int(t[j])!=et:
    cnt["entry_timestamp_missing"]+=1;continue
   sg=v1.segment_for_index(segs,j)
   if sg is None:
    cnt["segment_missing"]+=1;continue
   aa,bb=sg;local=j-aa
   tt=t[aa:bb];oo=o[aa:bb];hh=h[aa:bb];ll=l[aa:bb];cc=c[aa:bb]
   fill=float(oo[local]);atr=float(row.alt_atr1h)
   trade_side=opposite(row.side)

   for slm in v1.SL_ATR:
    risk=slm*atr
    sl=fill-risk if trade_side=="long" else fill+risk
    rp=abs(fill-sl)/fill
    if rp<v1.MIN_RISK or rp>v1.MAX_RISK or sl<=0:
     for rr in v1.TP_R:
      for hb in v1.HOLD_BARS:cnt[f"risk_filter_SL{slm:g}"]+=1
     continue
    for rr in v1.TP_R:
     tp=fill+rr*risk if trade_side=="long" else fill-rr*risk
     if tp<=0:
      for hb in v1.HOLD_BARS:cnt["bad_tp"]+=1
      continue
     for hb in v1.HOLD_BARS:
      if et+(hb+1)*v1.BAR>TRAIN_END:
       cnt["split_boundary_excluded"]+=1;continue
      z=v1.trade_explicit(sym,tt,oo,hh,ll,cc,local,trade_side,float(sl),float(tp),hb)
      if z["status"]!="resolved":
       cnt[z["status"]]+=1;continue
      xt=int(tt[z["exit_bar"]])
      if xt>=TRAIN_END:
       cnt["split_boundary_excluded"]+=1;continue
      outcomes.append({
       "symbol":sym,"entry_time":et,"leader_side":row.side,"side":trade_side,
       "lag_gap":float(row.lag_gap),
       "exit_key":f"SL{int(slm*10):02d}_R{int(rr*10):02d}_H{hb//4:02d}",
       "sl_atr":slm,"tp_r":rr,"hold_bars":hb,
       "entry":fill,"sl":float(sl),"tp":float(tp),"exit_time":xt,
       "exit":z["exit"],"reason":z["reason"],"risk_pct":z["risk_pct"],
       "gross_return":z["gross_return"],"gross_r":z["gross_r"],
       "net20_return":z["net20_return"],"net20_r":z["net20_r"],
       "net40_return":z["net40_return"],"net40_r":z["net40_r"]
      })
  ex.CACHE.clear()
  if si%25==0:
   print(f"OUTCOME_PROGRESS symbols={si}/{u.symbol.nunique()} rows={len(outcomes)}",flush=True)
 return pd.DataFrame(outcomes),selections,cnt

def attach_selected(sel,out,sl,r,h):
 if not len(sel):return pd.DataFrame()
 key=f"SL{int(sl*10):02d}_R{int(r*10):02d}_H{h//4:02d}"
 z=sel.copy()
 z["leader_side"]=z["side"]
 z["side"]=z["side"].map(opposite)
 oo=out[out.exit_key==key]
 return z.merge(oo,on=["symbol","entry_time","side","lag_gap","leader_side"],how="inner",validate="one_to_one")

def fnum(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser()
 ap.add_argument("--features",default="feature_artifacts")
 ap.add_argument("--data",default="data")
 ap.add_argument("--out",default="btc_lag_relative_strength_v2_train")
 a=ap.parse_args();outdir=Path(a.out);outdir.mkdir(parents=True,exist_ok=True)

 fs=sorted(glob.glob(str(Path(a.features)/"**/btc_lag_features_*.csv.gz"),recursive=True))
 ms=sorted(glob.glob(str(Path(a.features)/"**/btc_lag_features_meta_*.json"),recursive=True))
 if len(fs)!=12 or len(ms)!=12:raise RuntimeError(f"expected 12 feature shards csv={len(fs)} meta={len(ms)}")
 f=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 if not len(f):raise RuntimeError("zero feature rows")
 if f.duplicated(["symbol","entry_time"]).any():raise RuntimeError("duplicate symbol/event features")
 if (f.entry_time>=TRAIN_END).any():raise RuntimeError("validation leakage")
 metas=[json.loads(Path(x).read_text()) for x in ms]
 commits={m.get("commit_sha") for m in metas}
 if len(commits)!=1:raise RuntimeError(f"mixed feature commits {commits}")

 outcomes,selections,xcnt=evaluate_outcomes(f,a.data)
 if not len(outcomes):raise RuntimeError("zero resolved outcomes")
 outcomes.to_csv(outdir/"outcomes.csv.gz",index=False,compression="gzip")

 rows=[];yearly=[];concrows=[]
 for cfg in v1.PORT_CONFIGS:
  name=cfg["name"];sel0=selections[(cfg["lag_max"],cfg["top_k"])]
  g=attach_selected(sel0,outcomes,cfg["sl_atr"],cfg["tp_r"],cfg["hold_bars"])
  exsel,conc,expo=v1.select_exec(g)
  m20,r20=v1.account(exsel,20);m40,_=v1.account(exsel,40)
  rows.append({
   "config":name,"lag_max":cfg["lag_max"],"top_k":cfg["top_k"],
   "sl_atr":cfg["sl_atr"],"tp_r":cfg["tp_r"],"hold_bars":cfg["hold_bars"],
   "btc_events":int(f.entry_time.nunique()),"feature_n":len(f),
   "selected_pair_n":len(sel0),"resolved_pair_n":len(g),
   "exec_n":len(exsel),"exec_per_day":len(exsel)/TRAIN_DAYS,
   "independent_event_n":int(exsel.entry_time.nunique()) if len(exsel) else 0,
   "max_conc":max(conc) if conc else 0,
   "avg_conc_at_entry":float(np.mean(conc)) if conc else 0.,
   "max_gross_pct":100*max(expo) if expo else 0.,
   "ret20":m20["return_pct"],"cagr20":m20["cagr_pct"],"mdd20":m20["mdd_pct"],
   "pf20":m20["pf"],"win20":m20["win_pct"],"mean_r20":m20["mean_r"],"max_ls20":m20["max_ls"],
   "ret40":m40["return_pct"],"cagr40":m40["cagr_pct"],"mdd40":m40["mdd_pct"],
   "pf40":m40["pf"],"max_ls40":m40["max_ls"]
  })
  if len(exsel):
   years=pd.to_datetime(exsel.entry_time,unit="ms",utc=True).dt.year
   for y in (2021,2022,2023,2024):
    yg=exsel[years==y];ym20,_=v1.account(yg,20,1.0);ym40,_=v1.account(yg,40,1.0)
    yearly.append({"config":name,"year":y,"exec_n":len(yg),
     "ret20":ym20["return_pct"],"mdd20":ym20["mdd_pct"],"pf20":ym20["pf"],
     "ret40":ym40["return_pct"],"mdd40":ym40["mdd_pct"],"pf40":ym40["pf"]})
   by=r20.groupby("symbol").pnl.sum().sort_values(ascending=False) if len(r20) else pd.Series(dtype=float)
   pos=by[by>0];gp=pos.sum()
   concrows.append({"config":name,"symbols":int(exsel.symbol.nunique()),
    "top1_pos_share_pct":100*pos.head(1).sum()/gp if gp>0 else np.nan,
    "top3_pos_share_pct":100*pos.head(3).sum()/gp if gp>0 else np.nan,
    "top5_pos_share_pct":100*pos.head(5).sum()/gp if gp>0 else np.nan})
  else:
   for y in (2021,2022,2023,2024):
    yearly.append({"config":name,"year":y,"exec_n":0,"ret20":0.,"mdd20":0.,"pf20":np.nan,
                   "ret40":0.,"mdd40":0.,"pf40":np.nan})
   concrows.append({"config":name,"symbols":0})

 s=pd.DataFrame(rows);ydf=pd.DataFrame(yearly);cdf=pd.DataFrame(concrows)
 yr=ydf.groupby("config").agg(
   positive_years=("ret20",lambda x:int((x>0).sum())),
   min_year_pf20=("pf20","min"),worst_year_ret20=("ret20","min")
 ).reset_index()
 s=s.merge(yr,on="config",how="left")
 s["base_gate"]=(s.exec_n>=500)&(s.exec_per_day>=.34)&(s.ret20>0)&(s.ret40>0)&\
                 (s.pf20>=1.08)&(s.pf40>=1.00)&(s.mdd20<=35)&\
                 (s.positive_years>=3)&(s.min_year_pf20>=.85)
 good=set(s[(s.pf20>1.03)&(s.ret20>0)].config)
 cmap={x["name"]:x for x in v1.PORT_CONFIGS}
 s["adjacent_good"]=s.config.map(lambda n:any(x in good for x in v1.cfg_neighbors(cmap[n])))
 s["registered_pass"]=s.base_gate&s.adjacent_good
 rank=s.sort_values(["registered_pass","ret20","mdd20","pf40","exec_n"],
                    ascending=[False,False,True,False,False])

 s.to_csv(outdir/"summary.csv",index=False)
 ydf.to_csv(outdir/"yearly.csv",index=False)
 cdf.to_csv(outdir/"symbol_concentration.csv",index=False)
 Path(outdir/"provenance.json").write_text(json.dumps({
  "experiment_id":"btc_impulse_alt_relative_strength_v2_train",
  "classification":"EXPLORATORY","parent_run":37078429326,
  "feature_source_run":37078429326,"raw_source_run":36095439671,
  "feature_shard_commit_sha":next(iter(commits)),"feature_shards":12,
  "validation_opened":False,"costs_round_trip_bp":[20,40],"funding":"excluded",
  "trade_direction":"opposite BTC direction using same V1 features/ranking",
  "outcome_exclusions":dict(xcnt),
  "point_in_time_universe":"unknown","liquidity_filter":"unavailable/unknown"
 },indent=2)+"\n")

 lines=["# BTC Impulse -> Alt Relative-Strength V2 TRAIN","",
 "Classification: EXPLORATORY. 2021-2024 TRAIN only; 2025-2026 validation was not opened.",
 "V2 reuses V1 BTC impulse and lag ranking exactly, but trades the selected laggards OPPOSITE BTC direction.",
 "Interpretation: BTC-up laggard = relative weakness SHORT; BTC-down laggard/resilient alt = relative strength LONG.",
 "Portfolio: 0.5% risk/trade, cap6, gross<=200%, same-symbol overlap blocked. 20/40bp round-trip costs; funding excluded.",
 "Liquidity and point-in-time universe are unavailable/unknown; positive results remain provisional until audited.","",
 "| Config | ExecN | /day | Events | Ret20 | MDD20 | PF20 | Ret40 | PF40 | +Years | Pass |",
 "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|"]
 for _,r in rank.head(20).iterrows():
  lines.append(f"| {r.config} | {int(r.exec_n)} | {fnum(r.exec_per_day,2)} | {int(r.independent_event_n)} | {fnum(r.ret20,1)}% | {fnum(r.mdd20,1)}% | {fnum(r.pf20)} | {fnum(r.ret40,1)}% | {fnum(r.pf40)} | {int(r.positive_years)}/4 | {'YES' if r.registered_pass else 'NO'} |")
 passed=s[s.registered_pass]
 lines+=["",f"Registered-pass configs: **{len(passed)} / {len(s)}**.","",f"Outcome exclusions: {dict(xcnt)}"]
 if len(passed):lines+=["","Passed: "+", ".join(passed.sort_values("ret20",ascending=False).config.tolist())]
 Path(outdir/"report.md").write_text("\n".join(lines)+"\n")
 print("\n".join(lines))

if __name__=="__main__":main()
