import argparse,glob,json,math,os,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,pandas as pd

import scripts.external_breakout_replay as ex
import scripts.sweep_reclaim_research as base

BAR=900000
TRAIN_START=pd.Timestamp("2021-01-01T00:00:00Z").value//10**6
TRAIN_END=pd.Timestamp("2025-01-01T00:00:00Z").value//10**6
TRAIN_DAYS=(pd.Timestamp("2025-01-01T00:00:00Z")-pd.Timestamp("2021-01-01T00:00:00Z")).days
TRAIN_YEARS=TRAIN_DAYS/365.25

LAG_MAX=(.25,.50,.75)
TOPK=(3,6)
SL_ATR=(1.0,1.5)
TP_R=(1.5,2.5)
HOLD_BARS=(16,32)
RISK=.005
CAP=6
MAX_GROSS=2.0
MIN_RISK=.004
MAX_RISK=.05

def pname(lm,k,sl,r,h):
 return f"L{int(lm*100):02d}_K{k}_SL{int(sl*10):02d}_R{int(r*10):02d}_H{h//4:02d}"

PORT_CONFIGS=[
 {"name":pname(lm,k,sl,r,h),"lag_max":lm,"top_k":k,"sl_atr":sl,"tp_r":r,"hold_bars":h}
 for lm in LAG_MAX for k in TOPK for sl in SL_ATR for r in TP_R for h in HOLD_BARS
]

def files_by_symbol(root):
 fs=sorted(glob.glob(root+"/**/*.csv.gz",recursive=True));m={}
 for p in fs:
  try:s=ex.sym(p)
  except Exception:continue
  if s not in m:m[s]=p
 return m

def pick_pairs(f,lm,k):
 z=f[f.alt_norm_move<=lm].sort_values(["entry_time","lag_gap","symbol"],ascending=[True,False,True])
 if not len(z):return z
 return z.groupby("entry_time",sort=False,group_keys=False).head(k).copy()

def trade_explicit(symbol,t,o,h,l,c,start,side,sl,tp,maxbars):
 fill=float(o[start]);long=side=="long"
 if sl<=0 or tp<=0:return {"status":"bad_risk"}
 risk_pct=abs(fill-sl)/fill
 if risk_pct<=0:return {"status":"bad_risk"}
 ep=h[start]>=tp if long else l[start]<=tp
 es=l[start]<=sl if long else h[start]>=sl
 xb=xp=reason=None
 if ep or es:
  rr=base.entrybar_exit(symbol,int(t[start]),side,tp,sl)
  if rr=="data_gap":return {"status":"data_gap"}
  if rr=="win":xb=start;xp=tp;reason="TP"
  elif rr=="loss":xb=start;xp=sl;reason="SL"
  else:return {"status":"exit_mismatch"}
 end=min(start+maxbars+1,len(t))
 if xb is None:
  for j in range(start+1,end):
   ht=h[j]>=tp if long else l[j]<=tp
   hs=l[j]<=sl if long else h[j]>=sl
   if not (ht or hs):continue
   if ht and hs:
    rr=ex.established(symbol,int(t[j]),side,tp,sl)
    if rr=="data_gap":return {"status":"data_gap"}
    if rr=="exit_mismatch":return {"status":"exit_mismatch"}
    xb=j;xp=tp if rr=="win" else sl;reason="TP" if rr=="win" else "SL"
   elif hs:xb=j;xp=sl;reason="SL"
   else:xb=j;xp=tp;reason="TP"
   break
 if xb is None:
  if start+maxbars>=len(t):return {"status":"data_gap_horizon"}
  xb=start+maxbars;xp=float(c[xb]);reason="TIME"
 gross=(xp/fill-1) if long else ((fill-xp)/fill)
 out={"status":"resolved","exit_bar":int(xb),"exit":float(xp),"reason":reason,
      "risk_pct":float(risk_pct),"gross_return":float(gross),"gross_r":float(gross/risk_pct)}
 for bp in (20,40):
  out[f"net{bp}_return"]=float(gross-bp/10000)
  out[f"net{bp}_r"]=float((gross-bp/10000)/risk_pct)
 return out

def segment_for_index(segs,j):
 for aa,bb in segs:
  if aa<=j<bb:return aa,bb
 return None

def evaluate_outcomes(features,data_root):
 fm=files_by_symbol(data_root)
 unions=[];selections={}
 for lm in LAG_MAX:
  for k in TOPK:
   z=pick_pairs(features,lm,k)
   selections[(lm,k)]=z
   if len(z):unions.append(z[["symbol","entry_time","side","lag_gap","alt_atr1h"]])
 if not unions:raise RuntimeError("no selected lag pairs")
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
   sg=segment_for_index(segs,j)
   if sg is None:
    cnt["segment_missing"]+=1;continue
   aa,bb=sg
   local=j-aa
   tt=t[aa:bb];oo=o[aa:bb];hh=h[aa:bb];ll=l[aa:bb];cc=c[aa:bb]
   fill=float(oo[local]);atr=float(row.alt_atr1h)
   for slm in SL_ATR:
    risk=slm*atr;sl=fill-risk if row.side=="long" else fill+risk
    rp=abs(fill-sl)/fill
    if rp<MIN_RISK or rp>MAX_RISK or sl<=0:
     for rr in TP_R:
      for hb in HOLD_BARS:cnt[f"risk_filter_SL{slm:g}"]+=1
     continue
    for rr in TP_R:
     tp=fill+rr*risk if row.side=="long" else fill-rr*risk
     if tp<=0:
      for hb in HOLD_BARS:cnt["bad_tp"]+=1
      continue
     for hb in HOLD_BARS:
      if et+(hb+1)*BAR>TRAIN_END:
       cnt["split_boundary_excluded"]+=1;continue
      z=trade_explicit(sym,tt,oo,hh,ll,cc,local,row.side,float(sl),float(tp),hb)
      if z["status"]!="resolved":
       cnt[z["status"]]+=1;continue
      xt=int(tt[z["exit_bar"]])
      if xt>=TRAIN_END:
       cnt["split_boundary_excluded"]+=1;continue
      outcomes.append({
       "symbol":sym,"entry_time":et,"side":row.side,"lag_gap":float(row.lag_gap),
       "exit_key":f"SL{int(slm*10):02d}_R{int(rr*10):02d}_H{hb//4:02d}",
       "sl_atr":slm,"tp_r":rr,"hold_bars":hb,
       "entry":fill,"sl":float(sl),"tp":float(tp),"exit_time":xt,"exit":z["exit"],"reason":z["reason"],
       "risk_pct":z["risk_pct"],"gross_return":z["gross_return"],"gross_r":z["gross_r"],
       "net20_return":z["net20_return"],"net20_r":z["net20_r"],
       "net40_return":z["net40_return"],"net40_r":z["net40_r"]
      })
  ex.CACHE.clear()
  if si%25==0:print(f"OUTCOME_PROGRESS symbols={si}/{u.symbol.nunique()} rows={len(outcomes)}",flush=True)
 return pd.DataFrame(outcomes),selections,cnt

def attach_selected(sel,out,sl,r,h):
 if not len(sel):return pd.DataFrame()
 key=f"SL{int(sl*10):02d}_R{int(r*10):02d}_H{h//4:02d}"
 oo=out[out.exit_key==key]
 return sel.merge(oo,on=["symbol","entry_time","side","lag_gap"],how="inner",validate="one_to_one")

def select_exec(g):
 if not len(g):return pd.DataFrame(),[],[]
 z=g.sort_values(["entry_time","lag_gap","symbol"],ascending=[True,False,True]).copy()
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

def pf(v):
 a=np.asarray(v,float);gp=a[a>0].sum();gl=-a[a<0].sum()
 return float("inf") if gl==0 and gp>0 else (float(gp/gl) if gl>0 else np.nan)

def account(sel,bp,years=TRAIN_YEARS):
 if not len(sel):
  return {"return_pct":0.,"cagr_pct":0.,"mdd_pct":0.,"pf":np.nan,"max_ls":0,"win_pct":np.nan,"mean_r":np.nan},pd.DataFrame()
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
   real.append({"entry_time":int(et[i]),"exit_time":int(xt[i]),"symbol":z.iloc[i].symbol,"net_r":nr,"pnl":pnl})
   peak=max(peak,eq);mdd=max(mdd,(peak-eq)/peak if peak>0 else 0)
 rr=pd.DataFrame(real);nrs=rr.net_r.to_numpy(float) if len(rr) else np.array([])
 cur=ls=0
 for x in nrs:
  if x<0:cur+=1;ls=max(ls,cur)
  else:cur=0
 cagr=(eq**(1/years)-1) if eq>0 and years>0 else (-1. if eq<=0 else 0.)
 return {"return_pct":100*(eq-1),"cagr_pct":100*cagr,"mdd_pct":100*mdd,
         "pf":pf(rr.pnl if len(rr) else []),"max_ls":ls,
         "win_pct":100*np.mean(nrs>0) if len(nrs) else np.nan,
         "mean_r":float(np.mean(nrs)) if len(nrs) else np.nan},rr

def cfg_neighbors(cfg):
 dims={"lag_max":list(LAG_MAX),"top_k":list(TOPK),"sl_atr":list(SL_ATR),"tp_r":list(TP_R),"hold_bars":list(HOLD_BARS)}
 out=[]
 for key,grid in dims.items():
  i=grid.index(cfg[key])
  for j in (i-1,i+1):
   if 0<=j<len(grid):
    q=dict(cfg);q[key]=grid[j]
    out.append(pname(q["lag_max"],q["top_k"],q["sl_atr"],q["tp_r"],q["hold_bars"]))
 return out

def fnum(v,d=3):
 if v is None or (isinstance(v,float) and np.isnan(v)):return ""
 if isinstance(v,float) and math.isinf(v):return "inf"
 return f"{v:.{d}f}"

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--features",default="feature_artifacts");ap.add_argument("--data",default="data");ap.add_argument("--out",default="btc_lag_catchup_v1_train")
 a=ap.parse_args();outdir=Path(a.out);outdir.mkdir(parents=True,exist_ok=True)

 fs=sorted(glob.glob(str(Path(a.features)/"**/btc_lag_features_*.csv.gz"),recursive=True))
 ms=sorted(glob.glob(str(Path(a.features)/"**/btc_lag_features_meta_*.json"),recursive=True))
 if len(fs)!=12 or len(ms)!=12:raise RuntimeError(f"expected 12 feature shards csv={len(fs)} meta={len(ms)}")
 f=pd.concat([pd.read_csv(x) for x in fs],ignore_index=True)
 if not len(f):raise RuntimeError("zero feature rows")
 if f.duplicated(["symbol","entry_time"]).any():raise RuntimeError("duplicate symbol/event features")
 if (f.entry_time>=TRAIN_END).any():raise RuntimeError("validation leakage in features")
 metas=[json.loads(Path(x).read_text()) for x in ms]
 commits={m.get("commit_sha") for m in metas}
 if len(commits)!=1:raise RuntimeError(f"mixed feature commits {commits}")

 outcomes,selections,xcnt=evaluate_outcomes(f,a.data)
 if not len(outcomes):raise RuntimeError("zero resolved outcomes")
 outcomes.to_csv(outdir/"outcomes.csv.gz",index=False,compression="gzip")

 rows=[];yearly=[];concrows=[]
 for cfg in PORT_CONFIGS:
  name=cfg["name"];sel0=selections[(cfg["lag_max"],cfg["top_k"])]
  g=attach_selected(sel0,outcomes,cfg["sl_atr"],cfg["tp_r"],cfg["hold_bars"])
  exsel,conc,expo=select_exec(g)
  m20,r20=account(exsel,20);m40,_=account(exsel,40)
  rows.append({
   "config":name,"lag_max":cfg["lag_max"],"top_k":cfg["top_k"],"sl_atr":cfg["sl_atr"],"tp_r":cfg["tp_r"],"hold_bars":cfg["hold_bars"],
   "btc_events":int(f.entry_time.nunique()),"feature_n":len(f),"selected_pair_n":len(sel0),
   "resolved_pair_n":len(g),"exec_n":len(exsel),"exec_per_day":len(exsel)/TRAIN_DAYS,
   "independent_event_n":int(exsel.entry_time.nunique()) if len(exsel) else 0,
   "max_conc":max(conc) if conc else 0,"avg_conc_at_entry":float(np.mean(conc)) if conc else 0.,
   "max_gross_pct":100*max(expo) if expo else 0.,
   "ret20":m20["return_pct"],"cagr20":m20["cagr_pct"],"mdd20":m20["mdd_pct"],"pf20":m20["pf"],"win20":m20["win_pct"],"mean_r20":m20["mean_r"],"max_ls20":m20["max_ls"],
   "ret40":m40["return_pct"],"cagr40":m40["cagr_pct"],"mdd40":m40["mdd_pct"],"pf40":m40["pf"],"max_ls40":m40["max_ls"]
  })
  if len(exsel):
   dt=pd.to_datetime(exsel.entry_time,unit="ms",utc=True)
   for y in (2021,2022,2023,2024):
    yg=exsel[dt.dt.year==y];ym20,_=account(yg,20,1.0);ym40,_=account(yg,40,1.0)
    yearly.append({"config":name,"year":y,"exec_n":len(yg),"ret20":ym20["return_pct"],"mdd20":ym20["mdd_pct"],"pf20":ym20["pf"],
                   "ret40":ym40["return_pct"],"mdd40":ym40["mdd_pct"],"pf40":ym40["pf"]})
   by=r20.groupby("symbol").pnl.sum().sort_values(ascending=False) if len(r20) else pd.Series(dtype=float)
   pos=by[by>0];gp=pos.sum()
   concrows.append({"config":name,"symbols":int(exsel.symbol.nunique()),
                    "top1_pos_share_pct":100*pos.head(1).sum()/gp if gp>0 else np.nan,
                    "top3_pos_share_pct":100*pos.head(3).sum()/gp if gp>0 else np.nan,
                    "top5_pos_share_pct":100*pos.head(5).sum()/gp if gp>0 else np.nan})
  else:
   for y in (2021,2022,2023,2024):yearly.append({"config":name,"year":y,"exec_n":0,"ret20":0.,"mdd20":0.,"pf20":np.nan,"ret40":0.,"mdd40":0.,"pf40":np.nan})
   concrows.append({"config":name,"symbols":0})

 s=pd.DataFrame(rows);ydf=pd.DataFrame(yearly);cdf=pd.DataFrame(concrows)
 yr=ydf.groupby("config").agg(positive_years=("ret20",lambda x:int((x>0).sum())),min_year_pf20=("pf20","min"),worst_year_ret20=("ret20","min")).reset_index()
 s=s.merge(yr,on="config",how="left")
 s["base_gate"]=(s.exec_n>=500)&(s.exec_per_day>=.34)&(s.ret20>0)&(s.ret40>0)&(s.pf20>=1.08)&(s.pf40>=1.00)&(s.mdd20<=35)&(s.positive_years>=3)&(s.min_year_pf20>=.85)
 good=set(s[(s.pf20>1.03)&(s.ret20>0)].config)
 cmap={x["name"]:x for x in PORT_CONFIGS}
 s["adjacent_good"]=s.config.map(lambda n:any(x in good for x in cfg_neighbors(cmap[n])))
 s["registered_pass"]=s.base_gate&s.adjacent_good
 rank=s.sort_values(["registered_pass","ret20","mdd20","pf40","exec_n"],ascending=[False,False,True,False,False])

 s.to_csv(outdir/"summary.csv",index=False);ydf.to_csv(outdir/"yearly.csv",index=False);cdf.to_csv(outdir/"symbol_concentration.csv",index=False)
 Path(outdir/"provenance.json").write_text(json.dumps({
  "experiment_id":"btc_impulse_alt_lag_catchup_v1_train","classification":"EXPLORATORY",
  "source_data_run":36095439671,"feature_shard_commit_sha":next(iter(commits)),
  "feature_shards":12,"validation_opened":False,"costs_round_trip_bp":[20,40],"funding":"excluded",
  "liquidity_filter":"unavailable/unknown","point_in_time_universe":"unknown",
  "outcome_exclusions":dict(xcnt)
 },indent=2)+"\n")

 lines=["# BTC Impulse -> Alt Lag Catch-up V1 TRAIN","",
 "Classification: EXPLORATORY. 2021-2024 TRAIN only; 2025-2026 validation was not opened.",
 "Mechanism: completed BTC 1h impulse -> rank alts that lagged on normalized 1h body -> next 15m OPEN in BTC direction.",
 "Cross-sectional pair selection occurs before outcomes. Portfolio: 0.5% risk/trade, cap6, gross<=200%, same-symbol overlap blocked.",
 "Costs: 20/40bp round trip. Funding excluded. Liquidity and point-in-time universe filters are unavailable/unknown, so any positive result remains provisional until audited.","",
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
