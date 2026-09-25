# AUDIT_RERUN_2026_09_25: corrected entry-bar semantics; workflow uses canonical release shards.
#!/usr/bin/env python3
# Coarse re-search of L2 after fixing execution: signal 15m entry bar IS included.\n# trigger retry 2026-09-25
from pathlib import Path
import math
import numpy as np, pandas as pd
from core_l2_portfolio_audit import ROOT, TF, L2FEE

RANKS=[5,6,7]
RET4S=[20,25,30,35,40,50]
TPS=[5,8,10,12,15,20]
SLS=[2.5,4,6,8,10]
HOLDS=[1,2,4,8,16]  # 15m bars = 15m..4h

def prep(df):
 df=df.sort_values("open_time").drop_duplicates("open_time").copy()
 df["dt"]=pd.to_datetime(df.open_time,unit="ms",utc=True)
 b=df.set_index("dt")[["open","high","low","close"]].astype(float); idx=b.index; A={}
 for name,rule in TF.items():
  r=b.resample(rule,origin="epoch",label="left",closed="left").agg({"close":"last"}).dropna(); cc=r.close
  s=cc.rolling(19).sum().shift(1); ss=(cc*cc).rolling(19).sum().shift(1)
  buckets=idx.floor(rule) if name!="1W" else idx.floor("7D")
  sm=pd.Series(s.reindex(buckets).to_numpy(),index=idx); sqm=pd.Series(ss.reindex(buckets).to_numpy(),index=idx)
  po=b.open; mean=(sm+po)/20; var=(sqm+po*po)/20-mean*mean
  A[name]=po>mean+2*np.sqrt(var.clip(lower=0))
 return b,pd.DataFrame(A,index=idx).sum(axis=1),b.open.pct_change(16)*100

def trade(b,t,tpv,slv,hold):
 pos=b.index.get_indexer([t])[0]; path=b.iloc[pos:pos+hold]
 if path.empty:return None
 en=float(b.open.loc[t]); tp=en*(1+tpv/100); sl=en*(1-slv/100); ex=float(path.iloc[-1].close)
 for _,z in path.iterrows():
  ht=z.high>=tp; hs=z.low<=sl
  if hs: ex=sl; break  # conservative: same 15m bar collision = SL
  if ht: ex=tp; break
 return (ex/en-1)*100-L2FEE

def main():
 rows=[]
 cache=[]
 for p in sorted(ROOT.glob("*.parquet")):
  try: cache.append((p.stem,*prep(pd.read_parquet(p))))
  except Exception as e: print("ERR",p.stem,e)
 print("UNIVERSE",len(cache))
 for rank in RANKS:
  for r4 in RET4S:
   sigs=[]
   for sym,b,exact,ret4 in cache:
    raw=(exact>=rank)&(ret4>=r4); trig=raw & ~raw.shift(1,fill_value=False)
    sigs.extend((sym,b,t) for t in b.index[trig])
   for tp in TPS:
    for sl in SLS:
     for hold in HOLDS:
      vals=[]; yrs={}
      for sym,b,t in sigs:
       v=trade(b,t,tp,sl,hold)
       if v is None: continue
       vals.append(v); yrs.setdefault(t.year,[]).append(v)
      if not vals: continue
      a=np.array(vals); gp=a[a>0].sum(); gl=-a[a<0].sum(); pf=gp/gl if gl else np.inf
      yp={y:(sum(v for v in z if v>0)/-sum(v for v in z if v<0) if sum(v for v in z if v<0)<0 else np.inf) for y,z in yrs.items()}
      full=[y for y in range(2021,2026) if y in yp]; posyrs=sum(yp[y]>1 for y in full)
      rows.append([rank,r4,tp,sl,hold*15,len(a),a.mean(),pf,posyrs,min([yp[y] for y in full],default=np.nan),yp.get(2026,np.nan)])
 out=pd.DataFrame(rows,columns=["rank","ret4","tp","sl","hold_min","n","avg","pf","pos_years_2021_25","worst_pf_2021_25","pf_2026"])
 out.to_csv("artifacts/l2_corrected_coarse_sweep.csv",index=False)
 # robust ranking: require >=300 trades, reward PF + breadth, not single best point
 q=out[out.n>=300].copy(); q["score"]=q.pf+0.08*q.pos_years_2021_25+0.10*np.minimum(q.worst_pf_2021_25,1.5)
 print("TOP_ROBUST")
 print(q.sort_values(["score","pf"],ascending=False).head(40).to_string(index=False))
 print("NEIGHBORHOOD_COUNTS",{"pf_gt_1":int((q.pf>1).sum()),"pf_gt_1_1":int((q.pf>1.1).sum()),"pf_gt_1_2":int((q.pf>1.2).sum()),"all_5_years_pf_gt1":int((q.pos_years_2021_25==5).sum())})
if __name__=="__main__": main()
