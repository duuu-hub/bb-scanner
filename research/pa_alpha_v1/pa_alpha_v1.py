#!/usr/bin/env python3
from __future__ import annotations
import json, math, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

import research.aoa_price_action_v1.price_action_v1 as v1\nimport research.aoa_3way_oos.backtest_3way as b3

OUT=ROOT/"research"/"pa_alpha_v1"/"output"
DEV_START=pd.Timestamp("2022-01-01",tz="UTC")
DEV_END=pd.Timestamp("2025-01-01",tz="UTC")
HOLD_START=DEV_END
COSTS={"ZERO":0.0,"RT_004":0.0004/2,"RT_008":0.0008/2}

ENTRY_KEYS=["sret_1h","sret_4h","sret_24h","sret_3d","sbody_bps","sclose_loc","sloc_24h","sloc_3d","sdist_high_24h","sdist_low_24h","range_24h_bps"]

def fit_frozen_models(candles):
    # Refit the already-selected PA feature family on all AOA-era data through 2021.
    ep=pd.read_csv(v1.EP)
    ends=candles["bar_end_s"].to_numpy(dtype=np.int64)
    drows=[]
    for _,e in ep.iterrows():
        i=v1.completed_index(ends,e["st"])
        if i<0: continue
        r={f:float(candles.iloc[i][f]) if pd.notna(candles.iloc[i][f]) else np.nan for f in v1.DIR_FEATURES}
        r["y"]=int(e["d"]=="L")
        drows.append(r)
    ddf=pd.DataFrame(drows)
    dmodel=v1.make_pipe(0.25); dmodel.fit(ddf[v1.DIR_FEATURES],ddf["y"])

    h=v1.build_hazard(candles)
    hmodel=v1.make_pipe(0.25); hmodel.fit(h[v1.HAZARD_FEATURES],h["flip"])

    return dmodel,hmodel,{"direction_n":len(ddf),"hazard_n":len(h),"hazard_flips":int(h["flip"].sum())}

def compile_model(pipe,features):
    imp=pipe.named_steps["imp"]; sc=pipe.named_steps["sc"]; lr=pipe.named_steps["lr"]
    return {
        "features":features,
        "med":np.asarray(imp.statistics_,dtype=float),
        "mean":np.asarray(sc.mean_,dtype=float),
        "scale":np.asarray(sc.scale_,dtype=float),
        "coef":np.asarray(lr.coef_[0],dtype=float),
        "intercept":float(lr.intercept_[0]),
    }

def fast_prob(m,x):
    x=np.asarray(x,dtype=float)
    bad=~np.isfinite(x)
    if bad.any(): x=x.copy(); x[bad]=m["med"][bad]
    z=(x-m["mean"])/np.where(m["scale"]==0,1.0,m["scale"])
    s=m["intercept"]+float(np.dot(m["coef"],z))
    s=max(-50,min(50,s))
    return 1/(1+math.exp(-s))

def prepare(candles):
    df=candles.copy().reset_index(drop=True)
    cols=set(v1.DIR_FEATURES)
    # Everything required to construct signed hazard features.
    cols.update([
        "open","high","low","close","bar_start","bar_end_s",
        "ret_15m","ret_30m","ret_1h","ret_2h","ret_4h","ret_8h","ret_12h","ret_24h","ret_3d",
        "body_bps","upper_wick_bps","lower_wick_bps","close_loc","up_frac_1h","up_frac_4h",
        "loc_4h","loc_24h","loc_3d","loc_7d",
        "dist_prev_high_4h","dist_prev_low_4h","dist_prev_high_24h","dist_prev_low_24h",
        "break_up_4h","break_dn_4h","break_up_24h","break_dn_24h",
        "range_4h_bps","range_24h_bps","range_3d_bps"
    ])
    A={c:df[c].to_numpy() for c in cols}
    return df,A

def static_signed(A,i,side):
    g={}
    for n in ["15m","30m","1h","2h","4h","8h","12h","24h","3d"]:
        g[f"sret_{n}"]=side*float(A[f"ret_{n}"][i])
    g["sbody_bps"]=side*float(A["body_bps"][i])
    up=float(A["upper_wick_bps"][i]); lo=float(A["lower_wick_bps"][i])
    g["supper_wick_bps"]=up if side>0 else lo
    g["slower_wick_bps"]=lo if side>0 else up
    g["sclose_loc"]=(float(A["close_loc"][i])-.5)*side
    g["sup_frac_1h"]=(float(A["up_frac_1h"][i])-.5)*side
    g["sup_frac_4h"]=(float(A["up_frac_4h"][i])-.5)*side
    for n in ["4h","24h","3d","7d"]:
        g[f"sloc_{n}"]=(float(A[f"loc_{n}"][i])-.5)*side
    if side>0:
        g["sdist_high_4h"]=float(A["dist_prev_high_4h"][i]); g["sdist_low_4h"]=float(A["dist_prev_low_4h"][i])
        g["sdist_high_24h"]=float(A["dist_prev_high_24h"][i]); g["sdist_low_24h"]=float(A["dist_prev_low_24h"][i])
        g["sbreak_4h"]=float(A["break_up_4h"][i]-A["break_dn_4h"][i])
        g["sbreak_24h"]=float(A["break_up_24h"][i]-A["break_dn_24h"][i])
    else:
        g["sdist_high_4h"]=-float(A["dist_prev_low_4h"][i]); g["sdist_low_4h"]=-float(A["dist_prev_high_4h"][i])
        g["sdist_high_24h"]=-float(A["dist_prev_low_24h"][i]); g["sdist_low_24h"]=-float(A["dist_prev_high_24h"][i])
        g["sbreak_4h"]=float(A["break_dn_4h"][i]-A["break_up_4h"][i])
        g["sbreak_24h"]=float(A["break_dn_24h"][i]-A["break_up_24h"][i])
    for n in ["4h","24h","3d"]:
        g[f"range_{n}_bps"]=float(A[f"range_{n}_bps"][i])
    g["side_long"]=float(side>0)
    return g

def entry_ctx(A,i,side):
    g=static_signed(A,i,side)
    return {f"entry_{k}":g[k] for k in ENTRY_KEYS}

def hazard_x(A,i,side,entry_px,entry_time_s,ectx):
    g=static_signed(A,i,side)
    leg=side*(float(A["close"][i])/entry_px-1.0)*10000.0
    hrs=max(0.0,(float(A["bar_end_s"][i])-entry_time_s)/3600.0)
    g.update({
        "leg_bps":leg,"leg_pos_bps":max(leg,0.0),"leg_neg_bps":max(-leg,0.0),
        "duration_log":math.log1p(hrs),
        "age_lt1h":float(hrs<1),"age_1_6h":float(1<=hrs<6),"age_6_24h":float(6<=hrs<24),
        "age_24_72h":float(24<=hrs<72),"age_72h_plus":float(hrs>=72),
    })
    g.update(ectx)
    return [g.get(f,np.nan) for f in v1.HAZARD_FEATURES],g

def direction_prob(dfast,A,i):
    x=[float(A[f][i]) for f in v1.DIR_FEATURES]
    return fast_prob(dfast,x)

def veto(kind,g):
    if kind=="NONE": return False
    if kind=="BASIC":
        return g["sret_4h"]>0 and g["sret_12h"]>0 and g["sloc_24h"]>0
    if kind=="STRONG":
        return g["sret_4h"]>=25 and g["sret_12h"]>=50 and g["sloc_24h"]>=0.20
    if kind=="BREAKOUT":
        return g["sbreak_24h"]>0 or (g["sret_4h"]>0 and g["sret_24h"]>0 and g["sloc_24h"]>0)
    raise ValueError(kind)

def simulate(df,A,dfast,hfast,start,end,threshold,min_hold_bars,confirm,veto_kind,side_cost,collect_scores=False):
    idx=np.flatnonzero((df["bar_start"]>=start)&(df["bar_start"]<end))
    if len(idx)==0: raise RuntimeError("empty period")
    first=int(idx[0]); prev=max(0,first-1)
    side=1 if direction_prob(dfast,A,prev)>=.5 else -1
    entry_px=float(A["open"][first]); entry_time_s=int(pd.Timestamp(df.iloc[first]["bar_start"]).timestamp())
    ectx=entry_ctx(A,prev,side)

    equity=1.0
    equity-=equity*side_cost
    qty=side*equity/entry_px
    last_px=entry_px
    leg_eq=equity
    bars=0; streak=0; pending=False; pending_ctx=None
    legs=[]; curve=[]; scores=[]

    for i in idx:
        i=int(i); op=float(A["open"][i]); cl=float(A["close"][i])
        equity += qty*(op-last_px); last_px=op

        if pending:
            equity-=abs(qty)*op*side_cost
            legs.append({"ts":df.iloc[i]["bar_start"],"return_pct":(equity/leg_eq-1)*100,"bars":bars,"direction":side})
            side=-side
            equity-=equity*side_cost
            qty=side*equity/op
            entry_px=op
            entry_time_s=int(pd.Timestamp(df.iloc[i]["bar_start"]).timestamp())
            ectx=pending_ctx
            leg_eq=equity; bars=0; streak=0; pending=False

        equity += qty*(cl-last_px); last_px=cl; bars+=1
        x,g=hazard_x(A,i,side,entry_px,entry_time_s,ectx)
        p=fast_prob(hfast,x)
        if collect_scores: scores.append(p)
        trig=(bars>=min_hold_bars and p>=threshold and not veto(veto_kind,g))
        streak=streak+1 if trig else 0
        if streak>=confirm:
            pending=True; pending_ctx=entry_ctx(A,i,-side)
        curve.append({"ts":df.iloc[i]["bar_start"],"equity":equity,"side":side,"p":p})

    equity-=abs(qty)*last_px*side_cost
    legs.append({"ts":df.iloc[idx[-1]]["bar_start"],"return_pct":(equity/leg_eq-1)*100,"bars":bars,"direction":side})
    return summarize(curve,legs),pd.DataFrame(curve),pd.DataFrame(legs),scores

def summarize(curve,legs):
    c=pd.DataFrame(curve); l=pd.DataFrame(legs)
    eq=c["equity"].to_numpy(); dd=eq/np.maximum.accumulate(eq)-1
    r=c["equity"].pct_change().fillna(c["equity"].iloc[0]-1).to_numpy()
    days=(c["ts"].iloc[-1]-c["ts"].iloc[0]).total_seconds()/86400
    yrs=max(days/365.25,1e-9); final=float(eq[-1])
    cagr=(final**(1/yrs)-1)*100 if final>0 else -100.0
    gp=l.loc[l.return_pct>0,"return_pct"].sum(); gl=-l.loc[l.return_pct<0,"return_pct"].sum()
    return {
        "return_pct":(final-1)*100,"cagr_pct":cagr,"mdd_pct":float(dd.min()*100),
        "pf":float(gp/max(gl,1e-12)),"win_rate_pct":float((l.return_pct>0).mean()*100),
        "legs":int(len(l)),"median_hold_h":float(l["bars"].median()*.25),
    }

def yearly(curve):
    c=curve.copy()
    c["r"]=c["equity"].pct_change().fillna(c["equity"].iloc[0]-1)
    c["year"]=c["ts"].dt.year
    out=[]
    for y,g in c.groupby("year"):
        w=float(np.prod(1+g["r"].to_numpy()))
        out.append({"year":int(y),"return_pct":(w-1)*100})
    return out

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    raw=b3.load_raw(b3.BTC_DIR)
    raw["bar_start"]=raw["datetime_utc"]
    raw["bar_end_s"]=(raw["timestamp_ms"]//1000+900).astype("int64")
    candles=v1.add_price_action_features(raw[["timestamp_ms","open","high","low","close","bar_start","bar_end_s"]].copy())
    dmodel,hmodel,fitmeta=fit_frozen_models(candles)
    dfast=compile_model(dmodel,v1.DIR_FEATURES); hfast=compile_model(hmodel,v1.HAZARD_FEATURES)
    df,A=prepare(candles)

    # Development threshold scale from a single fixed baseline pass over 2022 only.
    _,_,_,scores=simulate(df,A,dfast,hfast,pd.Timestamp("2022-01-01",tz="UTC"),pd.Timestamp("2023-01-01",tz="UTC"),0.50,4,1,"NONE",0.0,True)
    s=pd.Series(scores)
    qmap={q:float(s.quantile(q)) for q in [.90,.95,.975,.99]}

    candidates=[]; dev_year_rows=[]
    for q,th in qmap.items():
        for mh in [4,16,48]:
            for cb in [1,2]:
                for vk in ["NONE","BASIC","STRONG","BREAKOUT"]:
                    m,c,l,_=simulate(df,A,dfast,hfast,DEV_START,DEV_END,th,mh,cb,vk,COSTS["RT_004"])
                    yy=yearly(c)
                    pos=sum(x["return_pct"]>0 for x in yy)
                    median_y=float(np.median([x["return_pct"] for x in yy]))
                    score=median_y+0.5*m["cagr_pct"]-0.5*abs(m["mdd_pct"])
                    eligible=(m["pf"]>1.0 and pos>=2 and m["legs"]>=30)
                    rec={"q":q,"threshold":th,"min_hold_bars":mh,"confirm":cb,"veto":vk,"eligible":eligible,"positive_years":pos,"selection_score":score,**m}
                    candidates.append(rec)
                    for x in yy: dev_year_rows.append({**{k:rec[k] for k in ["q","min_hold_bars","confirm","veto"]},**x})

    cand=pd.DataFrame(candidates).sort_values(["eligible","selection_score"],ascending=[False,False]).reset_index(drop=True)
    cand.to_csv(OUT/"development_candidates.csv",index=False)
    pd.DataFrame(dev_year_rows).to_csv(OUT/"development_yearly.csv",index=False)
    pool=cand[cand["eligible"]]
    selected=(pool.iloc[0] if len(pool) else cand.iloc[0])
    sel={k:(bool(selected[k]) if k=="eligible" else selected[k].item() if hasattr(selected[k],"item") else selected[k]) for k in selected.index}

    # Final holdout only after selection is fixed.
    hold_rows=[]; hold_years=[]; curves=[]; legs=[]
    for cname,cost in COSTS.items():
        m,c,l,_=simulate(df,A,dfast,hfast,HOLD_START,df["bar_start"].iloc[-1]+pd.Timedelta(minutes=15),
                         float(selected["threshold"]),int(selected["min_hold_bars"]),int(selected["confirm"]),str(selected["veto"]),cost)
        hold_rows.append({"cost":cname,**m})
        for x in yearly(c): hold_years.append({"cost":cname,**x})
        c["cost"]=cname; curves.append(c); l["cost"]=cname; legs.append(l)

    pd.DataFrame(hold_rows).to_csv(OUT/"holdout_summary.csv",index=False)
    pd.DataFrame(hold_years).to_csv(OUT/"holdout_yearly.csv",index=False)
    pd.concat(curves,ignore_index=True).to_csv(OUT/"holdout_curves.csv.gz",index=False,compression="gzip")
    pd.concat(legs,ignore_index=True).to_csv(OUT/"holdout_legs.csv",index=False)

    # Buy & hold references.
    def bh(a,b):
        z=df[(df["bar_start"]>=a)&(df["bar_start"]<b)]
        return float((z["close"].iloc[-1]/z["open"].iloc[0]-1)*100)
    meta={
        "model_fit":fitmeta,
        "threshold_quantiles_2022_baseline":qmap,
        "selected_policy":sel,
        "dev_btc_buy_hold_pct":bh(DEV_START,DEV_END),
        "holdout_btc_buy_hold_pct":bh(HOLD_START,df["bar_start"].iloc[-1]+pd.Timedelta(minutes=15)),
        "holdout_end":str(df["bar_start"].iloc[-1]),
        "note":"Selection used only 2022-2024. 2025+ was evaluated only after one policy was selected."
    }
    (OUT/"meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2,default=float),encoding="utf-8")

    print("=== SELECTED ===");print(json.dumps(meta,ensure_ascii=False,indent=2,default=float))
    print("\n=== TOP DEV ===");print(cand.head(12).to_string(index=False))
    print("\n=== HOLDOUT ===");print(pd.DataFrame(hold_rows).to_string(index=False))
    print("\n=== HOLDOUT YEARLY ===");print(pd.DataFrame(hold_years).to_string(index=False))

if __name__=="__main__":
    main()
