import argparse,glob,json,math
import numpy as np,pandas as pd

FEATURES=["gap_pct","atr_pct","trend_age","ret24_pct","ret72_pct","bounce24_pct","bounce72_pct","dd24_pct","dd72_pct"]
VARIANTS={"P4T26_DD8":("P4|T26",-8.0),"P9T27_DD9":("P9|T27",-9.0)}
CAPS=(4,6,8,10)
COST_PCT=0.20

def pf(vals):
    x=np.asarray(vals,float); gp=x[x>0].sum(); gl=-x[x<0].sum()
    return float(gp/gl) if gl>0 else None

def prepare(features_dir,sizing_dir):
    ffs=glob.glob(features_dir+"/**/features_*.csv.gz",recursive=True)
    efs=glob.glob(sizing_dir+"/**/events_*.csv.gz",recursive=True)
    assert len(ffs)==8,(len(ffs),ffs);assert len(efs)==8,(len(efs),efs)
    F=pd.concat([pd.read_csv(x) for x in ffs],ignore_index=True)
    E=pd.concat([pd.read_csv(x) for x in efs],ignore_index=True)
    assert "BTCUSDT" not in set(F.symbol) and "BTCUSDT" not in set(E.symbol)
    frames=[]
    for v,(cond,ddmin) in VARIANTS.items():
        f=F[(F.condition==cond)&(F.dd72_pct>=ddmin)].copy()
        e=E[E.variant==v].copy()
        # feature audit has resolved rows only; unresolved-at-dataset-end are omitted from ranking study.
        j=e.merge(f[["symbol","signal_ts","fill_ts",*FEATURES]],
                  on=["symbol","signal_ts","fill_ts"],how="inner",validate="many_to_one")
        j=j[j.outcome.isin(["win","loss"]) & j.pnl_pct.notna() & j.exit_ts.notna()].copy()
        j["variant"]=v
        j["year"]=pd.to_datetime(j.fill_ts,unit="ms",utc=True).dt.year.astype(int)
        frames.append(j)
    D=pd.concat(frames,ignore_index=True)
    D["net20_pct"]=D.pnl_pct-COST_PCT
    return D

class Scorer:
    def __init__(self,name,kind,feature=None,direction=1,lam=None):
        self.name=name;self.kind=kind;self.feature=feature;self.direction=direction;self.lam=lam
    def fit(self,tr):
        if self.kind=="single":return self
        X=tr[FEATURES].astype(float).to_numpy(); y=tr.net20_pct.astype(float).to_numpy()
        self.mu=np.nanmedian(X,axis=0)
        self.sd=np.nanstd(X,axis=0);self.sd=np.where(self.sd<1e-9,1.0,self.sd)
        X=(np.where(np.isfinite(X),X,self.mu)-self.mu)/self.sd
        # equalize train years so one listing-heavy year cannot dominate coefficients
        yrs=tr.year.to_numpy()
        counts=pd.Series(yrs).value_counts().to_dict()
        w=np.array([1.0/counts[int(y0)] for y0 in yrs],float)
        w=w/w.mean()
        X1=np.c_[np.ones(len(X)),X]
        A=X1.T@(X1*w[:,None]);b=X1.T@(y*w)
        reg=np.eye(X1.shape[1])*(self.lam or 10.0);reg[0,0]=0
        self.beta=np.linalg.solve(A+reg,b)
        return self
    def score(self,x):
        if self.kind=="single":
            return self.direction*x[self.feature].astype(float).to_numpy()
        X=x[FEATURES].astype(float).to_numpy()
        X=(np.where(np.isfinite(X),X,self.mu)-self.mu)/self.sd
        return np.c_[np.ones(len(X)),X]@self.beta

def select_cap(x,score,cap):
    z=x.copy();z["_score"]=score
    z=z.sort_values(["fill_ts","_score","dd72_pct","stop_pct","symbol"],
                    ascending=[True,False,False,True,True],kind="mergesort")
    open_pos={};keep=[];skip_symbol=0;skip_cap=0
    for ts,g in z.groupby("fill_ts",sort=True):
        ts=int(ts)
        for s,et in list(open_pos.items()):
            if et<=ts:del open_pos[s]
        for idx,r in g.iterrows():
            sym=str(r.symbol)
            if sym in open_pos:
                skip_symbol+=1;continue
            if len(open_pos)>=cap:
                skip_cap+=1;continue
            keep.append(idx);open_pos[sym]=int(r.exit_ts)
    return z.loc[keep].copy(),skip_symbol,skip_cap

def trade_metrics(x):
    return {
      "n":int(len(x)),"net20_pf":pf(x.net20_pct),
      "mean_net20_pct":float(x.net20_pct.mean()) if len(x) else None,
      "win_pct":float((x.pnl_pct>0).mean()*100) if len(x) else None,
      "median_hold_h":float(((x.exit_ts-x.fill_ts)/3600000).median()) if len(x) else None
    }

def portfolio100(x,cap):
    frac=1.0/cap;equity=1.0;peak=1.0;mdd=0.0;open_pos={}
    x=x.sort_values(["fill_ts","_score","symbol"],ascending=[True,False,True],kind="mergesort")
    start=None;end=None
    def close(ts):
        nonlocal equity,peak,mdd,end
        due={}
        for s,p in list(open_pos.items()):
            if p["exit_ts"]<=ts:due.setdefault(p["exit_ts"],[]).append((s,p))
        for et in sorted(due):
            delta=0.0
            for s,p in due[et]:
                open_pos.pop(s,None)
                delta+=p["notional"]*(p["net20_pct"]/100.0)
            equity+=delta;end=max(end or int(et),int(et));peak=max(peak,equity)
            if peak>0:mdd=max(mdd,(peak-equity)/peak*100.0)
    for ts,g in x.groupby("fill_ts",sort=True):
        ts=int(ts);start=ts if start is None else start;close(ts)
        base=equity
        for r in g.itertuples():
            open_pos[str(r.symbol)]={"exit_ts":int(r.exit_ts),"notional":base*frac,"net20_pct":float(r.net20_pct)}
    close(10**19)
    years=((end-start)/1000/86400/365.25) if start and end and end>start else None
    cagr=((equity**(1/years)-1)*100) if equity>0 and years else None
    return {"equity_multiple":float(equity),"return_pct":float((equity-1)*100),
            "mdd_pct":float(mdd),"cagr_pct":None if cagr is None else float(cagr)}

def yearly_robust(x):
    vals=[]
    for y,g in x.groupby("year"):
        p=pf(g.net20_pct)
        if p is not None:vals.append(p)
    return {"min_year_pf":min(vals) if vals else None,"median_year_pf":float(np.median(vals)) if vals else None,
            "positive_pf_years":int(sum(v>1 for v in vals)),"years":int(len(vals))}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--features",default="features");ap.add_argument("--sizing",default="sizing");ap.add_argument("--out",default="ranking.json");a=ap.parse_args()
    D=prepare(a.features,a.sizing)
    scorers=[]
    for f in FEATURES:
        scorers += [Scorer(f+"_HIGH","single",f,1),Scorer(f+"_LOW","single",f,-1)]
    scorers += [Scorer("RIDGE_1","ridge",lam=1.0),Scorer("RIDGE_10","ridge",lam=10.0),Scorer("RIDGE_100","ridge",lam=100.0)]
    out={"definition":{"train":"2021-2024","validation":"2025-2026","cost":"20bp roundtrip","btc_excluded":True,
                       "variants":VARIANTS,"features":FEATURES,
                       "note":"ranking score uses only signal-open/prior-bar features; no future information"},
         "rows":int(len(D)),"variants":{}}
    for v in VARIANTS:
        x=D[D.variant==v].copy();tr=x[x.year<=2024].copy();va=x[x.year>=2025].copy()
        vr=[]
        for sc in scorers:
            sc.fit(tr)
            st=sc.score(tr);sv=sc.score(va)
            for cap in CAPS:
                atr,ss,sk=select_cap(tr,st,cap);ava,vss,vsk=select_cap(va,sv,cap)
                rec={"rule":sc.name,"cap":cap,
                     "train":{**trade_metrics(atr),**yearly_robust(atr),**portfolio100(atr,cap),
                              "skip_symbol":ss,"skip_cap":sk},
                     "valid":{**trade_metrics(ava),**yearly_robust(ava),**portfolio100(ava,cap),
                              "skip_symbol":vss,"skip_cap":vsk}}
                # selection score is TRAIN ONLY: reward PF/equity, penalize train MDD and weak years.
                t=rec["train"]
                rec["train_selection_score"]=(math.log(max(t["equity_multiple"],1e-12))
                    + 2.0*math.log(max(t["net20_pf"] or 1e-12,1e-12))
                    - 0.01*t["mdd_pct"]
                    + 0.25*math.log(max(t["median_year_pf"] or 1e-12,1e-12)))
                vr.append(rec)
        vr.sort(key=lambda r:r["train_selection_score"],reverse=True)
        out["variants"][v]={"train_top10":vr[:10],"all_results":vr}
        print("VAR",v,"TRAIN_TOP")
        for r in vr[:10]:
            print(r["rule"],"cap",r["cap"],"score",round(r["train_selection_score"],4),
                  "trainPF",round(r["train"]["net20_pf"],4),"trainEq",round(r["train"]["equity_multiple"],4),"trainMDD",round(r["train"]["mdd_pct"],2),
                  "validPF",round(r["valid"]["net20_pf"],4),"validEq",round(r["valid"]["equity_multiple"],4),"validMDD",round(r["valid"]["mdd_pct"],2),
                  flush=True)
    json.dump(out,open(a.out,"w"),indent=2)
    print("RANKING_PASS",len(D),flush=True)
if __name__=="__main__":main()
