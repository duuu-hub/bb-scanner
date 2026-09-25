from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import continuation_mining as cm

OUT=Path("continuation_validation_results"); OUT.mkdir(exist_ok=True)
PRIMARY="y_t1.5_s0.75_h1"
FEATURES=cm.FEATURES

def prep():
 d=cm.load("market_data_store/bitget/research_auto100_15m")
 d=pd.concat([cm.add_features(g) for _,g in d.groupby("symbol",sort=False)],ignore_index=True)
 agg=d[["timestamp_ms","ret_1h","ret_4h"]].groupby("timestamp_ms").agg(
  breadth_1h=("ret_1h",lambda s:(s>0).mean()*100),breadth_4h=("ret_4h",lambda s:(s>0).mean()*100),
  market_med_1h=("ret_1h","median"),market_med_4h=("ret_4h","median")).reset_index()
 d=d.merge(agg,on="timestamp_ms",how="left")
 d["rel_vs_market_1h"]=d.ret_1h-d.market_med_1h; d["rel_vs_market_4h"]=d.ret_4h-d.market_med_4h
 long=(d.ret_1h>=1)&(d.ret_4h>0); short=(d.ret_1h<=-1)&(d.ret_4h<0)
 cand=d[long|short].copy(); cand["direction"]=np.where(long.loc[cand.index],"LONG","SHORT")
 groups={s:g.reset_index(drop=True) for s,g in d.groupby("symbol")}; recs=[]
 for r in cand.itertuples():
  g=groups[r.symbol]; i=int(np.searchsorted(g.timestamp_ms.to_numpy(),int(r.timestamp_ms))); z={}
  for h,bars in cm.HORIZONS.items():
   for t in cm.TARGETS:
    for st in cm.STOPS:z[f"y_t{t:g}_s{st:g}_h{h}"]=cm.barrier_label(g,i,r.direction,t,st,bars)
  # ATR-like true-range percentage, known at entry only
  prev=g.close.shift(1); tr=np.maximum(g.high-g.low,np.maximum((g.high-prev).abs(),(g.low-prev).abs()))
  atr=(tr.rolling(56,min_periods=20).mean()/g.close*100).iloc[i]
  z["atr14h_pct"]=float(atr) if pd.notna(atr) else np.nan
  # volatility-normalized barriers
  if pd.notna(atr) and atr>0:
   for mult in (0.5,1.0,1.5):
    for h,bars in cm.HORIZONS.items(): z[f"atr_{mult:g}_h{h}"]=cm.barrier_label(g,i,r.direction,float(atr*mult),float(atr*mult/2),bars)
  recs.append(z)
 return pd.concat([cand.reset_index(drop=True),pd.DataFrame(recs)],axis=1)

def rule_from_train(train,side):
 q=train[(train.direction==side)&(~train[PRIMARY].isin(["AMBIG","NO_ENTRY"]))].copy()
 s=cm.lift_table(q,PRIMARY,FEATURES)
 c=cm.combo_table(q,PRIMARY,s)
 if c.empty:return None
 return c.iloc[0]

def apply_rule(df,rule):
 m=pd.Series(True,index=df.index)
 for token in rule.rules.split(" & "):
  if "GE" in token: f,v=token.split("GE"); m &= df[f]>=float(v)
  else: f,v=token.split("LE"); m &= df[f]<=float(v)
 return m

def stats(q,label):
 q=q[~q[label].isin(["AMBIG","NO_ENTRY"])]; n=len(q)
 if not n:return dict(n=0,win_rate=np.nan,loss_rate=np.nan,timeout_rate=np.nan)
 return dict(n=n,win_rate=(q[label]=="WIN").mean(),loss_rate=(q[label]=="LOSS").mean(),timeout_rate=(q[label]=="TIMEOUT").mean())

def main():
 x=prep().sort_values("timestamp_ms").reset_index(drop=True)
 times=np.sort(x.timestamp_ms.unique()); split=times[int(len(times)*0.70)]
 train=x[x.timestamp_ms<split]; test=x[x.timestamp_ms>=split]
 rows=[]; rules=[]
 for side in ("LONG","SHORT"):
  r=rule_from_train(train,side)
  if r is None:continue
  rules.append(dict(side=side,rules=r.rules,train_n=int(r.n),train_win_rate=float(r.win_rate),train_lift=float(r.lift)))
  for name,z in (("TRAIN",train),("OOS",test)):
   q=z[(z.direction==side)&(~z[PRIMARY].isin(["AMBIG","NO_ENTRY"]))]; base=stats(q,PRIMARY); sel=q[apply_rule(q,r)]; st=stats(sel,PRIMARY)
   rows.append(dict(split=name,side=side,rules=r.rules,baseline_n=base["n"],baseline_win_rate=base["win_rate"],selected_n=st["n"],selected_win_rate=st["win_rate"],lift=st["win_rate"]/base["win_rate"] if base["win_rate"] else np.nan))
 # walk-forward: derive on prior 90d, test next 30d
 start=pd.to_datetime(x.timestamp_ms.min(),unit="ms",utc=True); end=pd.to_datetime(x.timestamp_ms.max(),unit="ms",utc=True); wf=[]; cur=start+pd.Timedelta(days=90)
 while cur+pd.Timedelta(days=30)<=end:
  a=int((cur-pd.Timedelta(days=90)).timestamp()*1000); b=int(cur.timestamp()*1000); c=int((cur+pd.Timedelta(days=30)).timestamp()*1000)
  tr=x[(x.timestamp_ms>=a)&(x.timestamp_ms<b)]; te=x[(x.timestamp_ms>=b)&(x.timestamp_ms<c)]
  for side in ("LONG","SHORT"):
   r=rule_from_train(tr,side)
   if r is None:continue
   q=te[(te.direction==side)&(te[PRIMARY]!="AMBIG")]; base=stats(q,PRIMARY); sel=q[apply_rule(q,r)]; st=stats(sel,PRIMARY)
   wf.append(dict(test_start=str(cur.date()),side=side,rules=r.rules,baseline_n=base["n"],baseline_win_rate=base["win_rate"],selected_n=st["n"],selected_win_rate=st["win_rate"],lift=st["win_rate"]/base["win_rate"] if base["win_rate"] else np.nan))
  cur+=pd.Timedelta(days=30)
 # fixed vs ATR-normalized descriptive by vol tercile, no frequency cap
 vol=x.atr14h_pct; x["vol_bucket"]=pd.qcut(vol,3,labels=["LOW","MID","HIGH"],duplicates="drop")
 vr=[]
 for side in ("LONG","SHORT"):
  for bucket in ("LOW","MID","HIGH"):
   q=x[(x.direction==side)&(x.vol_bucket==bucket)]
   for lab,kind in [(PRIMARY,"fixed_1.5_0.75")]+[(f"atr_{m:g}_h1",f"atr_{m:g}_halfstop") for m in (0.5,1.0,1.5)]:
    if lab in q: vr.append(dict(side=side,vol_bucket=bucket,label=kind,**stats(q,lab)))
 # robustness: frozen 70/30 rules, leave-one-symbol-out on OOS and friction proxy
 rob=[]
 for rr in rules:
  side=rr["side"]; r=next(rule_from_train(train,s) for s in (side,))
  q=test[(test.direction==side)&(~test[PRIMARY].isin(["AMBIG","NO_ENTRY"]))]; sel=q[apply_rule(q,r)]
  for sym in ["ALL"]+sorted(sel.symbol.value_counts().head(10).index.tolist()):
   z=sel if sym=="ALL" else sel[sel.symbol!=sym]; st=stats(z,PRIMARY)
   rob.append(dict(side=side,excluded_symbol=sym,**st))
 pd.DataFrame(rows).to_csv(OUT/"chronological_oos.csv",index=False); pd.DataFrame(wf).to_csv(OUT/"walk_forward.csv",index=False)
 pd.DataFrame(vr).to_csv(OUT/"volatility_normalization.csv",index=False); pd.DataFrame(rob).to_csv(OUT/"robustness.csv",index=False)
 pd.DataFrame(rules).to_csv(OUT/"frozen_rules.csv",index=False)
 meta={"rows":len(x),"symbols":int(x.symbol.nunique()),"split_ms":int(split),"frequency_cap":False,"note":"No signal frequency reduction. Rules are derived only from each training window, then frozen for its test window. Same-candle ambiguity excluded."}
 (OUT/"meta.json").write_text(json.dumps(meta,indent=2),encoding="utf-8")
 print("=== CHRONO OOS ==="); print(pd.DataFrame(rows).to_string(index=False))
 print("\n=== WALK FORWARD ==="); print(pd.DataFrame(wf).to_string(index=False))
 print("\n=== VOL NORMALIZATION ==="); print(pd.DataFrame(vr).to_string(index=False))
 print("\n=== ROBUSTNESS ==="); print(pd.DataFrame(rob).to_string(index=False))
if __name__=="__main__": main()
