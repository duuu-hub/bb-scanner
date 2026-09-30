import argparse,glob,os
import pandas as pd
LOOKBACKS={"15m":1,"1h":4,"4h":16}; HOLDS={"1h":4,"4h":16,"12h":48}
def sym(p): return os.path.basename(p)[:-7].upper()
ap=argparse.ArgumentParser(); ap.add_argument("--data",required=True); ap.add_argument("--out",required=True); a=ap.parse_args()
frames=[]
for p in glob.glob(a.data+"/**/*.csv.gz",recursive=True):
 d=pd.read_csv(p,compression="gzip",usecols=["open_time","close"]).sort_values("open_time").drop_duplicates("open_time")
 if d.empty: continue
 c=d.close.astype(float); x=pd.DataFrame({"ts":d.open_time.astype("int64"),"symbol":sym(p)})
 for n,b in LOOKBACKS.items(): x["r_"+n]=c.pct_change(b)
 for n,b in HOLDS.items(): x["f_"+n]=c.shift(-b)/c-1
 frames.append(x)
if not frames: raise RuntimeError("no input")
z=pd.concat(frames,ignore_index=True)
z.to_csv(a.out,index=False,compression="gzip")
print("PREP_PASS",a.out,"symbols",z.symbol.nunique(),"rows",len(z))
