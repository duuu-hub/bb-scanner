import argparse,glob,json,math
from pathlib import Path
import numpy as np,pandas as pd

BAR15=15*60_000
BAR4H=4*60*60_000
BURN=100

def psar_open_projection(h,l,af0=.02,step=.02,afmax=.2):
    n=len(h); out=np.full(n,np.nan); bull=np.ones(n,bool)
    if n<3:return out,bull
    sar=l[0]; trend=True; ep=h[1]; af=af0
    out[1]=sar; bull[1]=trend
    for i in range(2,n):
        z=sar+af*(ep-sar)
        if trend:z=min(z,l[i-1],l[i-2])
        else:z=max(z,h[i-1],h[i-2])
        out[i]=z; bull[i]=trend
        if trend:
            if l[i]<z: trend=False; sar=ep; ep=l[i]; af=af0
            else:
                sar=z
                if h[i]>ep: ep=h[i]; af=min(af+step,afmax)
        else:
            if h[i]>z: trend=True; sar=ep; ep=h[i]; af=af0
            else:
                sar=z
                if l[i]<ep: ep=l[i]; af=min(af+step,afmax)
    return out,bull

def load_raw(root):
    files=sorted(Path(root).glob("20??/??/*.csv.gz"))
    if not files: raise RuntimeError(f"no raw files under {root}")
    need=["symbol","timestamp_ms","open","high","low","close"]
    frames=[]
    for f in files:
        x=pd.read_csv(f,compression="gzip")
        cols={c.lower():c for c in x.columns}
        if not all(k in cols for k in need):continue
        y=x[[cols[k] for k in need]].copy();y.columns=need
        frames.append(y)
    d=pd.concat(frames,ignore_index=True)
    for c in need[1:]:d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d.dropna(subset=need).drop_duplicates(["symbol","timestamp_ms"],keep="last")
    d=d[(d.open>0)&(d.high>0)&(d.low>0)&(d.close>0)]
    return d.sort_values(["symbol","timestamp_ms"]).reset_index(drop=True)

def build_refs(raw):
    rows=[]
    for sym,g in raw.groupby("symbol",sort=False):
        g=g.sort_values("timestamp_ms")
        t=g.timestamp_ms.to_numpy(np.int64);o=g.open.to_numpy(float);h=g.high.to_numpy(float);l=g.low.to_numpy(float);c=g.close.to_numpy(float)
        # split on gaps; never carry PSAR state across missing 15m data
        cuts=np.r_[0,np.flatnonzero(np.diff(t)!=BAR15)+1,len(t)]
        for aa,bb in zip(cuts[:-1],cuts[1:]):
            tt=t[aa:bb];oo=o[aa:bb];hh=h[aa:bb];ll=l[aa:bb];cc=c[aa:bb]
            if len(tt)<16*(BURN+2):continue
            bucket=tt//BAR4H
            cut=np.r_[0,np.flatnonzero(bucket[1:]!=bucket[:-1])+1,len(tt)]
            st=cut[:-1];en=cut[1:];good=(en-st)==16;st=st[good];en=en[good]
            if not len(st):continue
            ok=np.array([tt[a]%BAR4H==0 and np.all(np.diff(tt[a:b])==BAR15) for a,b in zip(st,en)],bool)
            st=st[ok];en=en[ok]
            if len(st)<=BURN:continue
            rt=tt[st]; rh=np.array([hh[a:b].max() for a,b in zip(st,en)]); rl=np.array([ll[a:b].min() for a,b in zip(st,en)])
            sar,bull=psar_open_projection(rh,rl)
            for i in range(BURN,len(rt)):
                if np.isfinite(sar[i]):
                    rows.append((sym,int(rt[i]),float(sar[i]),bool(bull[i])))
    return pd.DataFrame(rows,columns=["symbol","bar4h_ts","psar_ref","psar_bull"])

def attach(trades,refs,entry_ts_col,entry_px_col):
    z=trades.copy()
    z["symbol"]=z.symbol.astype(str)
    z["entry_ts_num"]=pd.to_numeric(z[entry_ts_col],errors="coerce").astype("Int64")
    z["entry_px_num"]=pd.to_numeric(z[entry_px_col],errors="coerce")
    z=z.dropna(subset=["entry_ts_num","entry_px_num"]).copy()
    z["bar4h_ts"]=(z.entry_ts_num.astype("int64")//BAR4H)*BAR4H
    z=z.merge(refs,on=["symbol","bar4h_ts"],how="left",validate="many_to_one")
    z["psar_dist_pct"]=(z.psar_ref-z.entry_px_num)/z.entry_px_num*100.0
    z["psar_short_valid"]=(~z.psar_bull.fillna(True))&(z.psar_dist_pct>0)
    return z

def pf(arr):
    x=pd.to_numeric(arr,errors="coerce").dropna()
    gp=float(x[x>0].sum());gl=float(-x[x<0].sum())
    return gp/gl if gl>0 else (math.inf if gp>0 else np.nan)

def metrics(z,ret_col,extra_cost=0.0):
    x=pd.to_numeric(z[ret_col],errors="coerce").dropna()-extra_cost
    if not len(x):return {"n":0,"pf":None,"win_pct":None,"avg_pct":None,"sum_pct":0.0}
    return {"n":int(len(x)),"pf":float(pf(x)),"win_pct":float((x>0).mean()*100),"avg_pct":float(x.mean()),"sum_pct":float(x.sum())}

def groups(z):
    return {
      "BASE":np.ones(len(z),bool),
      "PSAR_SHORT_ANY":z.psar_short_valid.fillna(False).to_numpy(),
      "PSAR_0_1":(z.psar_short_valid & z.psar_dist_pct.gt(0)&z.psar_dist_pct.le(1)).to_numpy(),
      "PSAR_1_2":(z.psar_short_valid & z.psar_dist_pct.gt(1)&z.psar_dist_pct.le(2)).to_numpy(),
      "PSAR_2_3":(z.psar_short_valid & z.psar_dist_pct.gt(2)&z.psar_dist_pct.le(3)).to_numpy(),
      "PSAR_2_4":(z.psar_short_valid & z.psar_dist_pct.gt(2)&z.psar_dist_pct.le(4)).to_numpy(),
      "PSAR_2_5":(z.psar_short_valid & z.psar_dist_pct.gt(2)&z.psar_dist_pct.le(5)).to_numpy(),
      "PSAR_3_5":(z.psar_short_valid & z.psar_dist_pct.gt(3)&z.psar_dist_pct.le(5)).to_numpy(),
    }

def summarize(name,z,ret_col,costs):
    rows=[];base_n=len(z)
    for label,m in groups(z).items():
        q=z.loc[m].copy()
        for cost in costs:
            s=metrics(q,ret_col,cost)
            rows.append({"strategy":name,"filter":label,"extra_cost_pct":cost,"retention_pct":100*len(q)/base_n if base_n else 0,**s})
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--raw-root",required=True)
    ap.add_argument("--cont-root",required=True)
    ap.add_argument("--l3-root",required=True)
    ap.add_argument("--out",default="psar_overlay.json")
    a=ap.parse_args()
    raw=load_raw(a.raw_root);refs=build_refs(raw)
    print("RAW",len(raw),"symbols",raw.symbol.nunique(),"REFS",len(refs),flush=True)
    allrows=[];details={}

    cf=glob.glob(a.cont_root+"/**/trades_one_position_per_symbol.csv.gz",recursive=True)
    if cf:
        c=pd.read_csv(cf[0])
        c=attach(c,refs,"entry_ts","entry_px")
        details["continuation_match_pct"]=float(c.psar_ref.notna().mean()*100)
        allrows+=summarize("CONTINUATION_SHORT_OOS",c,"gross_ret_pct",[.20,.40])
        c.to_csv("continuation_psar_overlay.csv.gz",index=False,compression="gzip")
        print("CONT",len(c),"match",round(details["continuation_match_pct"],2),flush=True)

    # L3 artifact contains both directions and several delays/regimes.
    lf=glob.glob(a.l3_root+"/**/*l3*trades.csv.gz",recursive=True)
    lparts=[]
    for f in lf:
        x=pd.read_csv(f)
        if not {"direction","signal_ts","entry_ts","entry_price","net_pct"}.issubset(x.columns):continue
        # Frozen finding of interest: BEAR regime SHORT, delay=1. Use 60/40 if available.
        reg="regime_60_40" if "regime_60_40" in x.columns else ("regime_55_45" if "regime_55_45" in x.columns else None)
        q=x[(x.direction=="SHORT")&(pd.to_numeric(x.delay_min,errors="coerce")==1)].copy()
        if reg:q=q[q[reg]=="BEAR"].copy()
        q["source_file"]=Path(f).name
        lparts.append(q)
    if lparts:
        l=pd.concat(lparts,ignore_index=True)
        l=attach(l,refs,"entry_ts","entry_price")
        details["l3_match_pct"]=float(l.psar_ref.notna().mean()*100)
        # net_pct already includes the L3 engine's base fee. Stress adds extra 0/.13/.38 => approx total .12/.25/.50.
        allrows+=summarize("L3_BEAR_SHORT_D1",l,"net_pct",[0.0,.13,.38])
        l.to_csv("l3_psar_overlay.csv.gz",index=False,compression="gzip")
        print("L3",len(l),"match",round(details["l3_match_pct"],2),flush=True)

    out=pd.DataFrame(allrows)
    if out.empty:raise RuntimeError("no compatible strategy trades found")
    out.to_csv("psar_overlay_summary.csv",index=False)
    payload={"definition":{
      "psar":"4H PSAR projected at each 4H OPEN using only prior closed bars; state reset across 15m data gaps",
      "distance":"(PSAR_ref - actual_entry_price)/actual_entry_price * 100 for SHORT",
      "purpose":"overlay/confirmation filter only; existing strategy entries/exits unchanged",
      "filters":["0-1","1-2","2-3","2-4","2-5","3-5"],
      "anti_lookahead":"PSAR_ref frozen from the containing 4H candle OPEN; no current 4H H/L/C used"
      },"details":details,"rows":out.to_dict(orient="records")}
    Path(a.out).write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print("\n=== OVERLAY SUMMARY ===")
    print(out.to_string(index=False))
    # highlight 2-3 versus baseline at each cost
    for strat,g in out.groupby("strategy"):
        print("\nFOCUS",strat)
        print(g[g["filter"].isin(["BASE","PSAR_2_3","PSAR_2_4","PSAR_2_5"])].to_string(index=False))
    print("PSAR_OVERLAY_PASS",flush=True)

if __name__=="__main__":main()
