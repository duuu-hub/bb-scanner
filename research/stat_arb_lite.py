from __future__ import annotations
import argparse, json, math
from itertools import combinations
from pathlib import Path
import numpy as np
import pandas as pd

def pf(x):
    x=pd.Series(x).dropna()
    w=x[x>0].sum(); l=-x[x<0].sum()
    return float(w/l) if l>0 else (float("inf") if w>0 else float("nan"))

def load(root):
    fs=sorted(Path(root).glob("20??/??/*.csv.gz"))
    if not fs: raise FileNotFoundError(root)
    x=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
    t="timestamp_ms" if "timestamp_ms" in x else "ts"
    x[t]=pd.to_numeric(x[t],errors="coerce"); x["close"]=pd.to_numeric(x["close"],errors="coerce")
    x=x.dropna(subset=[t,"symbol","close"]); x=x[x.close>0]
    return x.pivot_table(index=t,columns="symbol",values="close",aggfunc="last").sort_index()

def fit_pair(a,b):
    z=pd.concat([np.log(a),np.log(b)],axis=1).dropna()
    if len(z)<1000:return None
    x=z.iloc[:,1].values; y=z.iloc[:,0].values
    beta=np.cov(y,x,ddof=1)[0,1]/np.var(x,ddof=1)
    alpha=y.mean()-beta*x.mean()
    s=y-alpha-beta*x
    sd=s.std(ddof=1)
    if not math.isfinite(sd) or sd<=0:return None
    # AR(1) persistence: lower is faster mean reversion; reject explosive/non-reverting spreads.
    rho=np.corrcoef(s[:-1],s[1:])[0,1]
    if not math.isfinite(rho) or rho<=0 or rho>=0.999:return None
    hl=-math.log(2)/math.log(rho)
    return alpha,beta,sd,float(rho),float(hl),len(z)

def trade_oos(a,b,fit,entry=2.0,exit_z=.5,stop_z=4.0,cost_bps=10.0,max_hold=96):
    alpha,beta,sd,*_=fit
    z=pd.concat([np.log(a),np.log(b)],axis=1).dropna()
    s=z.iloc[:,0]-alpha-beta*z.iloc[:,1]
    # fixed train mean implied by alpha/beta; no OOS refit
    zz=s/sd
    rows=[]; pos=0; ent=None; ent_i=None
    vals=z.values
    for i in range(1,len(z)):
        q=float(zz.iloc[i-1]) # signal known from prior completed bar
        if pos==0:
            if q>=entry: pos=-1; ent=vals[i].copy(); ent_i=i
            elif q<=-entry: pos=1; ent=vals[i].copy(); ent_i=i
            continue
        held=i-ent_i
        close=(abs(q)<=exit_z or abs(q)>=stop_z or held>=max_hold)
        if close:
            cur=vals[i]
            # log-price legs, beta hedge. Normalize gross leg weights to 1.
            w1=1/(1+abs(beta)); w2=abs(beta)/(1+abs(beta))
            sign2=-np.sign(beta)*pos
            gross=pos*w1*(math.exp(cur[0]-ent[0])-1)+sign2*w2*(math.exp(cur[1]-ent[1])-1)
            net=gross-cost_bps/10000.0
            rows.append(net)
            pos=0; ent=None; ent_i=None
    return rows

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",default="market_data_store/bitget/research_auto100_15m")
    p.add_argument("--train-frac",type=float,default=.67)
    p.add_argument("--top",type=int,default=50)
    p.add_argument("--cost-bps",type=float,default=10)
    p.add_argument("--out",default="research/results/stat_arb_lite")
    a=p.parse_args()
    px=load(a.root)
    cut=int(len(px)*a.train_frac); tr=px.iloc[:cut]; te=px.iloc[cut:]
    fits=[]
    for A,B in combinations(px.columns,2):
        f=fit_pair(tr[A],tr[B])
        if f and 4<=f[4]<=96:
            fits.append((f[4],A,B,f))
    fits.sort(key=lambda x:x[0])
    chosen=fits[:a.top] # selection is TRAIN only
    rows=[]
    for rank,(_,A,B,f) in enumerate(chosen,1):
        r=trade_oos(te[A],te[B],f,cost_bps=a.cost_bps)
        rows.append({"rank":rank,"a":A,"b":B,"train_half_life_bars":f[4],
                     "train_obs":f[5],"oos_trades":len(r),"oos_pf":pf(r),
                     "oos_sum_pct":100*sum(r),"oos_win_pct":100*np.mean(np.array(r)>0) if r else np.nan})
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    d=pd.DataFrame(rows);d.to_csv(out/"pair_oos.csv",index=False)
    traded=d[d.oos_trades>0]
    meta={"bars":len(px),"symbols":len(px.columns),"train_bars":len(tr),"oos_bars":len(te),
          "all_pairs":int(len(px.columns)*(len(px.columns)-1)/2),"train_eligible_pairs":len(fits),
          "selected_pairs":len(chosen),"selected_pct_of_all":100*len(chosen)/(len(px.columns)*(len(px.columns)-1)/2),
          "oos_pairs_with_trades":len(traded),"oos_profitable_pairs":int((traded.oos_sum_pct>0).sum()),
          "oos_pf_gt1_pairs":int((traded.oos_pf>1).sum()),"cost_bps_roundtrip":a.cost_bps,
          "warning":"Current-survivor universe; survivorship bias remains. Pair ranking and hedge fit use train only; OOS parameters are frozen."}
    (out/"summary.json").write_text(json.dumps(meta,indent=2),encoding="utf-8")
    print(json.dumps(meta,indent=2))
    if len(d): print(d.sort_values("oos_sum_pct",ascending=False).head(20).to_string(index=False))
if __name__=="__main__":main()
