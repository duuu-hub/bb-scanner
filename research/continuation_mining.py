from __future__ import annotations
# Research-only continuation mining; never imported by live trading.
import argparse, json
from pathlib import Path
from itertools import combinations
import numpy as np
import pandas as pd
TARGETS=(1.0,1.5,2.0); STOPS=(0.5,0.75,1.0); HORIZONS={1:4,2:8,3:12}
FEATURES=["ret_30m","ret_1h","ret_2h","ret_4h","ret_24h","accel_1h","rv_4h","rv_24h","vol_surge_1h","range_1h","close_pos_1h","pullback_from_1h_high","dist_24h_high","breadth_1h","breadth_4h","market_med_1h","market_med_4h","rel_vs_market_1h","rel_vs_market_4h"]
def args():
 p=argparse.ArgumentParser(); p.add_argument("--root",default="market_data_store/bitget/research_auto100_15m"); p.add_argument("--outdir",default="continuation_mining_results"); p.add_argument("--min-impulse",type=float,default=1.0); p.add_argument("--sample-step",type=int,default=1); return p.parse_args()
def load(root):
 files=sorted(Path(root).glob("20??/??/*.csv.gz"))
 if not files: raise SystemExit("no AUTO100 15m files")
 frames=[]; need=["symbol","timestamp_ms","open","high","low","close","quote_volume"]
 for f in files:
  x=pd.read_csv(f,compression="gzip"); cols={c.lower():c for c in x.columns}
  if not all(k in cols for k in need): raise SystemExit(f"{f}: missing OHLCV columns; got {list(x.columns)}")
  x=x[[cols[k] for k in need]].copy(); x.columns=need; frames.append(x)
 d=pd.concat(frames,ignore_index=True)
 for c in need[1:]: d[c]=pd.to_numeric(d[c],errors="coerce")
 d=d.dropna(subset=need[:-1]); d=d[d.close>0]; d.timestamp_ms=d.timestamp_ms.astype("int64")
 return d.drop_duplicates(["symbol","timestamp_ms"],keep="last").sort_values(["symbol","timestamp_ms"]).reset_index(drop=True)
def add_features(g):
 g=g.copy(); c=g.close
 for n,name in [(2,"ret_30m"),(4,"ret_1h"),(8,"ret_2h"),(16,"ret_4h"),(96,"ret_24h")]: g[name]=(c/c.shift(n)-1)*100
 g["accel_1h"]=g.ret_1h-g.ret_4h/4; lr=np.log(c).diff(); g["rv_4h"]=lr.rolling(16).std(ddof=0)*100; g["rv_24h"]=lr.rolling(96).std(ddof=0)*100
 q=g.quote_volume.fillna(0); g["vol_surge_1h"]=q.rolling(4).sum()/(q.shift(4).rolling(96).sum()/24).replace(0,np.nan)
 hi4=g.high.rolling(4).max(); lo4=g.low.rolling(4).min(); g["range_1h"]=(hi4/lo4-1)*100; g["close_pos_1h"]=(c-lo4)/(hi4-lo4).replace(0,np.nan); g["pullback_from_1h_high"]=(c/hi4-1)*100; g["dist_24h_high"]=(c/g.high.rolling(96).max()-1)*100
 return g
def barrier_label(g,i,direction,target,stop,bars):
 entry=float(g.close.iloc[i]); end=min(len(g),i+1+bars)
 for j in range(i+1,end):
  hi=float(g.high.iloc[j]); lo=float(g.low.iloc[j]); tp=(hi>=entry*(1+target/100)) if direction=="LONG" else (lo<=entry*(1-target/100)); sl=(lo<=entry*(1-stop/100)) if direction=="LONG" else (hi>=entry*(1+stop/100))
  if tp and sl:return "AMBIG"
  if tp:return "WIN"
  if sl:return "LOSS"
 return "TIMEOUT"
def lift_table(df,label,features):
 base=(df[label]=="WIN").mean(); rows=[]
 for f in features:
  z=df[[f,label]].dropna()
  if len(z)<100 or z[f].nunique()<10:continue
  for q in np.unique(z[f].quantile([.1,.2,.3,.4,.5,.6,.7,.8,.9]).values):
   for op,mask in [("LE",z[f]<=q),("GE",z[f]>=q)]:
    h=z[mask]
    if len(h)<50:continue
    wr=(h[label]=="WIN").mean(); rows.append(dict(feature=f,op=op,threshold=q,n=len(h),win_rate=wr,baseline=base,lift=wr/base if base else np.nan))
 return pd.DataFrame(rows).sort_values(["lift","n"],ascending=[False,False]) if rows else pd.DataFrame(columns=["feature","op","threshold","n","win_rate","baseline","lift"])
def combo_table(df,label,singles):
 base=(df[label]=="WIN").mean(); rows=[]; cand=singles[(singles.n>=100)&(singles.lift>1.05)].drop_duplicates("feature").head(12); rules=[r for _,r in cand.iterrows()]
 for k in (2,3):
  for comb in combinations(rules,k):
   mask=pd.Series(True,index=df.index); names=[]
   for r in comb:
    v=df[r.feature]; mask&=(v<=r.threshold) if r.op=="LE" else (v>=r.threshold); names.append(f"{r.feature}{r.op}{r.threshold:.4g}")
   h=df[mask & df[label].notna()]
   if len(h)<80:continue
   wr=(h[label]=="WIN").mean(); rows.append(dict(k=k,rules=" & ".join(names),n=len(h),win_rate=wr,baseline=base,lift=wr/base if base else np.nan,events_per_day=len(h)/max(1,(df.timestamp_ms.max()-df.timestamp_ms.min())/86400000)))
 return pd.DataFrame(rows).sort_values(["lift","n"],ascending=[False,False]) if rows else pd.DataFrame()
def main():
 a=args(); out=Path(a.outdir); out.mkdir(parents=True,exist_ok=True); d=load(a.root); d=pd.concat([add_features(g) for _,g in d.groupby("symbol",sort=False)],ignore_index=True)
 agg=d[["timestamp_ms","ret_1h","ret_4h"]].groupby("timestamp_ms").agg(breadth_1h=("ret_1h",lambda s:(s>0).mean()*100),breadth_4h=("ret_4h",lambda s:(s>0).mean()*100),market_med_1h=("ret_1h","median"),market_med_4h=("ret_4h","median")).reset_index(); d=d.merge(agg,on="timestamp_ms",how="left"); d["rel_vs_market_1h"]=d.ret_1h-d.market_med_1h; d["rel_vs_market_4h"]=d.ret_4h-d.market_med_4h
 long=(d.ret_1h>=a.min_impulse)&(d.ret_4h>0); short=(d.ret_1h<=-a.min_impulse)&(d.ret_4h<0); cand=d[long|short].copy(); cand["direction"]=np.where(long.loc[cand.index],"LONG","SHORT")
 if a.sample_step>1:cand=cand.iloc[::a.sample_step].copy()
 groups={s:g.reset_index(drop=True) for s,g in d.groupby("symbol")}; labels=[]
 for r in cand.itertuples():
  g=groups[r.symbol]; ii=int(np.searchsorted(g.timestamp_ms.to_numpy(),int(r.timestamp_ms))); rec={}
  for h,bars in HORIZONS.items():
   for t in TARGETS:
    for st in STOPS:rec[f"y_t{t:g}_s{st:g}_h{h}"]=barrier_label(g,ii,r.direction,t,st,bars)
  labels.append(rec)
 cand=pd.concat([cand.reset_index(drop=True),pd.DataFrame(labels)],axis=1); summaries=[]; all_single=[]; all_combo=[]
 for h in HORIZONS:
  lab=f"y_t1.5_s0.75_h{h}"; z=cand[cand[lab]!="AMBIG"].copy()
  for side in ("ALL","LONG","SHORT"):
   q=z if side=="ALL" else z[z.direction==side]
   if q.empty:continue
   n=len(q); wins=int((q[lab]=="WIN").sum()); losses=int((q[lab]=="LOSS").sum()); timeouts=int((q[lab]=="TIMEOUT").sum()); summaries.append(dict(horizon_h=h,side=side,n=n,wins=wins,losses=losses,timeouts=timeouts,win_rate=wins/n,loss_rate=losses/n,timeout_rate=timeouts/n,events_per_day=n/max(1,(q.timestamp_ms.max()-q.timestamp_ms.min())/86400000)))
   s=lift_table(q,lab,FEATURES); s["horizon_h"]=h; s["side"]=side; all_single.append(s); c=combo_table(q,lab,s)
   if not c.empty:c["horizon_h"]=h;c["side"]=side;all_combo.append(c)
 pd.DataFrame(summaries).to_csv(out/"primary_summary.csv",index=False); pd.concat(all_single,ignore_index=True).to_csv(out/"single_feature_lifts.csv",index=False)
 if all_combo:pd.concat(all_combo,ignore_index=True).to_csv(out/"feature_intersections.csv",index=False)
 cand.to_csv(out/"labeled_continuation_events.csv.gz",index=False,compression="gzip"); grid=[]
 for h in HORIZONS:
  for t in TARGETS:
   for st in STOPS:
    lab=f"y_t{t:g}_s{st:g}_h{h}"
    for side in ("ALL","LONG","SHORT"):
     q=cand if side=="ALL" else cand[cand.direction==side]; q=q[q[lab]!="AMBIG"]; n=len(q)
     if n:grid.append(dict(horizon_h=h,target=t,stop=st,side=side,n=n,win_rate=(q[lab]=="WIN").mean(),loss_rate=(q[lab]=="LOSS").mean(),timeout_rate=(q[lab]=="TIMEOUT").mean()))
 pd.DataFrame(grid).to_csv(out/"target_stop_horizon_grid.csv",index=False); meta={"rows_market":len(d),"candidate_events":len(cand),"symbols":int(cand.symbol.nunique()),"start_ms":int(cand.timestamp_ms.min()),"end_ms":int(cand.timestamp_ms.max()),"candidate_definition":f"|ret_1h| >= {a.min_impulse}% and ret_4h same sign","primary_label":"+1.5% before -0.75%, horizons 1/2/3h","anti_overfit":"descriptive only; chronological OOS required"}; (out/"meta.json").write_text(json.dumps(meta,indent=2),encoding="utf-8")
 print("=== META ==="); print(json.dumps(meta,indent=2)); print(); print("=== PRIMARY ==="); print(pd.DataFrame(summaries).to_string(index=False))
 if all_single:
  s=pd.concat(all_single,ignore_index=True); print(); print("=== TOP SINGLE FEATURE LIFTS (n>=100) ==="); print(s[s.n>=100].sort_values(["lift","n"],ascending=[False,False]).head(30).to_string(index=False))
 if all_combo:
  c=pd.concat(all_combo,ignore_index=True); print(); print("=== TOP INTERSECTIONS (n>=80) ==="); print(c.sort_values(["lift","n"],ascending=[False,False]).head(30).to_string(index=False))
if __name__=="__main__":main()
