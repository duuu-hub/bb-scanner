import glob,os,re,json
import numpy as np,pandas as pd

LOOKBACKS={"15m":1,"1h":4,"4h":16}
HOLDS={"1h":4,"4h":16,"12h":48}
QS=(0.05,0.10,0.20)

def sym(p):
 b=os.path.basename(p); return b[:-7].upper()
def main():
 files=glob.glob("data/**/*.csv.gz",recursive=True)
 if not files: raise RuntimeError("no csv.gz")
 frames=[]
 for p in files:
  d=pd.read_csv(p,compression="gzip",usecols=["open_time","close"]).sort_values("open_time")
  if d.empty: continue
  d=d.drop_duplicates("open_time")
  c=d.close.astype(float)
  x=pd.DataFrame({"ts":d.open_time.astype("int64"),"symbol":sym(p),"close":c})
  for n,b in LOOKBACKS.items(): x["r_"+n]=c.pct_change(b)
  for n,b in HOLDS.items(): x["f_"+n]=c.shift(-b)/c-1
  frames.append(x)
 z=pd.concat(frames,ignore_index=True)
 # Require a meaningful contemporaneous universe; rank only information known at ts.
 counts=z.groupby("ts").symbol.transform("count")
 z=z[counts>=20].copy()
 out=[]
 for lb in LOOKBACKS:
  col="r_"+lb
  z["pct"]=z.groupby("ts")[col].rank(pct=True,method="average")
  for q in QS:
   for side,mask in [("LOSER",z.pct<=q),("WINNER",z.pct>=1-q)]:
    a=z.loc[mask]
    for h in HOLDS:
     v=a["f_"+h].dropna()
     if len(v)<100: continue
     out.append({"lookback":lb,"tail":q,"side":side,"hold":h,"n":int(len(v)),
      "mean_fwd_pct":float(v.mean()*100),"median_fwd_pct":float(v.median()*100),
      "winrate_pct":float((v>0).mean()*100)})
 r=pd.DataFrame(out)
 # Spread: winner minus loser; positive=momentum, negative=reversal.
 piv=r.pivot_table(index=["lookback","tail","hold"],columns="side",values=["mean_fwd_pct","n","winrate_pct"]).reset_index()
 piv.columns=["_".join([str(y) for y in x if str(y)!=""]).rstrip("_") for x in piv.columns]
 piv["winner_minus_loser_pct"]=piv["mean_fwd_pct_WINNER"]-piv["mean_fwd_pct_LOSER"]
 piv["direction"]=np.where(piv.winner_minus_loser_pct>0,"MOMENTUM","REVERSAL")
 piv["abs_spread_pct"]=piv.winner_minus_loser_pct.abs()
 piv=piv.sort_values("abs_spread_pct",ascending=False)
 r.to_csv("cross_sectional_cells.csv",index=False)
 piv.to_csv("cross_sectional_spreads.csv",index=False)
 with open("cross_sectional_summary.json","w") as f: json.dump({"symbols":int(z.symbol.nunique()),"rows":int(len(z)),"top":piv.head(30).to_dict("records")},f,indent=2)
 print("CROSS_SECTION_PASS symbols",z.symbol.nunique(),"rows",len(z))
 print(piv.head(30).to_string(index=False))
if __name__=="__main__": main()
