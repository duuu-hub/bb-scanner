import glob,pandas as pd,numpy as np
fs=glob.glob("in/**/*.csv",recursive=True);d=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
g=d.groupby(["side","threshold","tp","sl","limit15m"]).gross_pct
s=g.agg(n="size",mean="mean",median="median",wr=lambda x:(x>0).mean()*100,sum="sum").reset_index()
def pf(x,c=0):
 y=x-c; pos=y[y>0].sum(); neg=-y[y<0].sum(); return pos/neg if neg else np.inf
for c in [0,.2,.4]:
 z=d.assign(net=d.gross_pct-c).groupby(["side","threshold","tp","sl","limit15m"]).net.agg(n="size",mean="mean",wr=lambda x:(x>0).mean()*100,pf=lambda x:pf(x,0)).reset_index()
 z=z.sort_values(["side","pf","n"],ascending=[True,False,False])
 z.groupby("side").head(30).to_csv(f"top_{int(c*100)}bp.csv",index=False)
s.to_csv("all_summary.csv",index=False)
