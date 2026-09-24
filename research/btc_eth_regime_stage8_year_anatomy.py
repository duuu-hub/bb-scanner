from __future__ import annotations

import io, math, time, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import requests

OUT=Path("research_output/btc_eth_regime_stage8_year_anatomy")
SYMS=("BTCUSDT","ETHUSDT")
BASE="https://data.binance.vision/data/spot/monthly/klines"
START=pd.Timestamp("2017-08-01",tz="UTC")
# Include all completed monthly Binance archives available through the current month.\nEND=pd.Timestamp("2026-09-01",tz="UTC")
WIN=30
ER_T=0.193654
RT=0.25\n# flat-period run trigger 20260925

def months(a,b):
    x=a
    while x<=b:
        yield x
        x=x+pd.offsets.MonthBegin(1)

def todt(v):
    z=pd.to_numeric(v,errors="coerce")
    ms=np.where(z>1e14,z/1000,z)
    return pd.to_datetime(ms,unit="ms",utc=True,errors="coerce")

def load(sym):
    cols=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
    s=requests.Session(); s.headers.update({"User-Agent":"bb-stage8/1"})
    fs=[]
    for m in months(START,END):
        ym=m.strftime("%Y-%m"); name=f"{sym}-1d-{ym}.zip"
        r=s.get(f"{BASE}/{sym}/1d/{name}",timeout=30)
        if r.status_code==404: continue
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            mem=[q for q in z.namelist() if q.endswith(".csv")]
            if mem:
                with z.open(mem[0]) as fh:
                    fs.append(pd.read_csv(fh,header=None,names=cols))
        time.sleep(.003)
    x=pd.concat(fs,ignore_index=True)
    x["datetime_utc"]=todt(x["open_time"])
    for c in ("open","high","low","close","volume","quote_volume"):
        x[c]=pd.to_numeric(x[c],errors="coerce")
    return x.dropna(subset=["datetime_utc","open","close"]).drop_duplicates("datetime_utc").sort_values("datetime_utc").reset_index(drop=True)

def panel(data):
    ps={}
    for s in SYMS:
        d=data[s][["datetime_utc","open","high","low","close","quote_volume"]].copy()
        c=d["close"]
        d[f"{s}_ret1d"]=c.pct_change()*100
        d[f"{s}_fwd1d"]=c.pct_change().shift(-1)*100
        d[f"{s}_ret7"]=c/c.shift(7)-1
        d[f"{s}_ret30"]=c/c.shift(WIN)-1
        d[f"{s}_ret90"]=c/c.shift(90)-1
        path=c.diff().abs().rolling(WIN,min_periods=15).sum()
        d[f"{s}_er30"]=(c-c.shift(WIN)).abs()/path.replace(0,np.nan)
        d[f"{s}_rv30"]=d[f"{s}_ret1d"].rolling(WIN,min_periods=15).std(ddof=0)*math.sqrt(365)
        d[f"{s}_dd90"]=c/c.shift(1).rolling(90,min_periods=30).max()-1
        d=d.rename(columns={"open":f"{s}_open","close":f"{s}_close"})
        ps[s]=d.drop(columns=["high","low","quote_volume"])
    x=ps["BTCUSDT"].merge(ps["ETHUSDT"],on="datetime_utc",how="inner")
    x["agree_up"]=(x["BTCUSDT_ret30"]>0)&(x["ETHUSDT_ret30"]>0)
    x["market_er"]=(x["BTCUSDT_er30"]+x["ETHUSDT_er30"])/2
    x["active"]=(x["agree_up"]&(x["market_er"]>=ER_T)).astype(int)
    x["trend7_avg"]=(x["BTCUSDT_ret7"]+x["ETHUSDT_ret7"])/2
    x["trend30_avg"]=(x["BTCUSDT_ret30"]+x["ETHUSDT_ret30"])/2
    x["trend90_avg"]=(x["BTCUSDT_ret90"]+x["ETHUSDT_ret90"])/2
    x["trend30_min"]=np.minimum(x["BTCUSDT_ret30"],x["ETHUSDT_ret30"])
    x["trend30_gap"]=(x["BTCUSDT_ret30"]-x["ETHUSDT_ret30"]).abs()
    x["rv30_avg"]=(x["BTCUSDT_rv30"]+x["ETHUSDT_rv30"])/2
    x["dd90_avg"]=(x["BTCUSDT_dd90"]+x["ETHUSDT_dd90"])/2
    x["btc_eth_corr30"]=x["BTCUSDT_ret1d"].rolling(30,min_periods=15).corr(x["ETHUSDT_ret1d"])
    x["both_up_next"]=((x["BTCUSDT_fwd1d"]>0)&(x["ETHUSDT_fwd1d"]>0)).astype(int)
    x["both_down_next"]=((x["BTCUSDT_fwd1d"]<0)&(x["ETHUSDT_fwd1d"]<0)).astype(int)
    x["split_next"]=1-x["both_up_next"]-x["both_down_next"]

    # next-day open->close execution for the long-only candidate
    p=x["active"].shift(1).fillna(0.0)
    intr=((x["BTCUSDT_close"]/x["BTCUSDT_open"]-1)+(x["ETHUSDT_close"]/x["ETHUSDT_open"]-1))*50
    turn=p.diff().abs().fillna(p.abs())
    x["strategy_ret"]=p*intr-turn*(RT/2)
    x["position"]=p
    return x

def perf(a):
    a=np.asarray(a,float); a=a[np.isfinite(a)]
    if len(a)==0:return {}
    w=np.prod(1+a/100); yrs=len(a)/365.25
    curve=np.cumprod(1+a/100); full=np.r_[1.,curve]; dd=(full/np.maximum.accumulate(full)-1)*100
    sd=np.std(a)
    return {"return_pct":(w-1)*100,"sharpe":np.mean(a)/sd*math.sqrt(365.25) if sd>0 else math.nan,"mdd_pct":float(dd.min())}

def fwd_underlying(x,i,n):
    j=min(i+n,len(x)-1)
    if j<=i:return math.nan
    a=x.iloc[i]; b=x.iloc[j]
    return float(((b["BTCUSDT_close"]/a["BTCUSDT_close"]-1)+(b["ETHUSDT_close"]/a["ETHUSDT_close"]-1))*50)

def episodes(x):
    rows=[]; p=x["position"].to_numpy(); start=None
    for i in range(len(p)):
        if p[i]==1 and (i==0 or p[i-1]==0): start=i
        if start is not None and (p[i]==0 or i==len(p)-1):
            end=i-1 if p[i]==0 else i
            g=x.iloc[start:end+1]
            vals=g["strategy_ret"].to_numpy(float)
            wealth=np.prod(1+vals/100)
            sig=x.iloc[start-1] if start>0 else x.iloc[start]
            rows.append({
                "signal_date":sig["datetime_utc"],
                "entry":g["datetime_utc"].iloc[0],"exit":g["datetime_utc"].iloc[-1],
                "year":int(g["datetime_utc"].iloc[0].year),
                "days":len(g),"ret_pct":(wealth-1)*100,
                "start_btc30":float(sig["BTCUSDT_ret30"]),
                "start_eth30":float(sig["ETHUSDT_ret30"]),
                "start_trend7":float(sig["trend7_avg"]),
                "start_trend30":float(sig["trend30_avg"]),
                "start_trend90":float(sig["trend90_avg"]),
                "start_er":float(sig["market_er"]),
                "start_gap":float(sig["trend30_gap"]),
                "start_rv30":float(sig["rv30_avg"]),
                "start_dd90":float(sig["dd90_avg"]),
                "start_corr30":float(sig["btc_eth_corr30"]),
                "fwd1_pct":fwd_underlying(x,start,1),
                "fwd3_pct":fwd_underlying(x,start,3),
                "fwd7_pct":fwd_underlying(x,start,7),
                "fwd14_pct":fwd_underlying(x,start,14),
                "bear_rally_like":bool(g["trend90_avg"].iloc[0]<0),
                "false_break_3d":bool(len(g)<=3 and (wealth-1)*100<=0),
            })
            start=None
    return pd.DataFrame(rows)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    data={s:load(s) for s in SYMS}
    x=panel(data)
    e=episodes(x)
    # Full-year labels only: 2018-2025. 2017/2026 are partial and excluded from core comparison.
    full=e[(e["year"]>=2018)&(e["year"]<=2025)].copy()
    loss_years={2018,2019,2022}
    full["year_group"]=np.where(full["year"].isin(loss_years),"LOSS_YEAR","WIN_YEAR")
    full["episode_group"]=np.where(full["ret_pct"]>0,"WIN_EP","LOSS_EP")
    e.to_csv(OUT/"episodes.csv",index=False)
    epcols=["days","ret_pct","start_btc30","start_eth30","start_trend7","start_trend30","start_trend90","start_er","start_gap","start_rv30","start_dd90","start_corr30","fwd1_pct","fwd3_pct","fwd7_pct","fwd14_pct","bear_rally_like","false_break_3d"]
    comp=[]
    for group_col in ["year_group","episode_group"]:
        for name,z in full.groupby(group_col):
            row={"comparison":group_col,"group":name,"n":len(z)}
            for q in epcols:
                if z[q].dtype==bool: row[q+"_pct"]=float(z[q].mean()*100)
                else: row[q+"_median"]=float(z[q].median())
            comp.append(row)
    pd.DataFrame(comp).to_csv(OUT/"episode_structure_compare.csv",index=False)

    # Untuned quartile diagnostics using signal-day features only.
    qrows=[]
    for feat in ["start_er","start_gap","start_trend90","start_dd90","start_rv30"]:
        z=full.dropna(subset=[feat]).copy()
        z["quartile"]=pd.qcut(z[feat],4,labels=["Q1","Q2","Q3","Q4"],duplicates="drop")
        for q,h in z.groupby("quartile",observed=True):
            qrows.append({"feature":feat,"quartile":str(q),"n":len(h),
                "feature_median":float(h[feat].median()),
                "episode_win_pct":float((h["ret_pct"]>0).mean()*100),
                "episode_return_median":float(h["ret_pct"].median()),
                "episode_days_median":float(h["days"].median()),
                "fwd7_median":float(h["fwd7_pct"].median()),
                "fwd14_median":float(h["fwd14_pct"].median())})
    qdf=pd.DataFrame(qrows)
    qdf.to_csv(OUT/"signal_day_quartiles.csv",index=False)

    # Year-by-year rank correlation: consistency check, not threshold optimization.
    yrows=[]
    for year,h in full.groupby("year"):
        for feat in ["start_er","start_gap"]:
            z=h[[feat,"ret_pct"]].dropna()
            yrows.append({"year":int(year),"feature":feat,"n":len(z),
                "spearman":float(z[feat].rank(method="average").corr(z["ret_pct"].rank(method="average"))) if len(z)>=3 else math.nan})
    ycorr=pd.DataFrame(yrows)
    ycorr.to_csv(OUT/"signal_day_yearly_rankcorr.csv",index=False)

    # ER x gap 2x2 using medians fixed from the full 2018-2025 sample; descriptive only.
    er_med=float(full["start_er"].median()); gap_med=float(full["start_gap"].median())
    full["er_half"]=np.where(full["start_er"]>=er_med,"HIGH_ER","LOW_ER")
    full["gap_half"]=np.where(full["start_gap"]<=gap_med,"LOW_GAP","HIGH_GAP")
    joint=[]
    for (a,b),h in full.groupby(["er_half","gap_half"]):
        joint.append({"er_half":a,"gap_half":b,"n":len(h),
            "episode_win_pct":float((h["ret_pct"]>0).mean()*100),
            "episode_return_median":float(h["ret_pct"].median()),
            "episode_days_median":float(h["days"].median()),
            "fwd7_median":float(h["fwd7_pct"].median()),
            "fwd14_median":float(h["fwd14_pct"].median())})
    jdf=pd.DataFrame(joint)
    jdf.to_csv(OUT/"signal_day_er_gap_joint.csv",index=False)

    # Early persistence anatomy: descriptive path after actual entry, no optimized exit rule.
    prows=[]
    for _,r in full.iterrows():
        entry_i=x.index[x["datetime_utc"].eq(r["entry"])]
        if len(entry_i)==0: continue
        i=int(entry_i[0])
        row={"year":int(r["year"]),"entry":r["entry"],"episode_ret_pct":float(r["ret_pct"]),
             "episode_group":"WIN_EP" if r["ret_pct"]>0 else "LOSS_EP","episode_days":int(r["days"])}
        for n in [1,2,3,5,7]:
            j=min(i+n,len(x)-1)
            h=x.iloc[i:j+1]
            row[f"path_{n}d_close_pct"]=fwd_underlying(x,i,n)
            row[f"path_{n}d_min_pct"]=float(min(
                ((h["BTCUSDT_close"]/x.iloc[i]["BTCUSDT_close"]-1)+(h["ETHUSDT_close"]/x.iloc[i]["ETHUSDT_close"]-1))*50))
            row[f"active_survives_{n}d"]=bool(r["days"]>n)
        prows.append(row)
    pdf=pd.DataFrame(prows)
    pdf.to_csv(OUT/"early_persistence_paths.csv",index=False)
    pcomp=[]
    for name,h in pdf.groupby("episode_group"):
        row={"group":name,"n":len(h)}
        for n in [1,2,3,5,7]:
            row[f"path_{n}d_close_median"]=float(h[f"path_{n}d_close_pct"].median())
            row[f"path_{n}d_min_median"]=float(h[f"path_{n}d_min_pct"].median())
            row[f"survive_{n}d_pct"]=float(h[f"active_survives_{n}d"].mean()*100)
        pcomp.append(row)
    pcdf=pd.DataFrame(pcomp)
    pcdf.to_csv(OUT/"early_persistence_compare.csv",index=False)

    # Fixed zero-return checkpoints only (not threshold search): how informative is early sign?
    erows=[]
    for n in [1,2,3,5]:
        z=pdf.dropna(subset=[f"path_{n}d_close_pct"]).copy()
        z["early_sign"]=np.where(z[f"path_{n}d_close_pct"]>0,"POSITIVE","NONPOSITIVE")
        for sign,h in z.groupby("early_sign"):
            erows.append({"checkpoint_days":n,"early_sign":sign,"n":len(h),
                "final_episode_win_pct":float((h["episode_ret_pct"]>0).mean()*100),
                "final_episode_return_median":float(h["episode_ret_pct"].median()),
                "final_episode_days_median":float(h["episode_days"].median())})
    esdf=pd.DataFrame(erows)
    esdf.to_csv(OUT/"early_sign_outcomes.csv",index=False)

    # Causal 3-day early-exit test: entry unchanged; after 3 completed position days,
    # exit from the following day if close-to-close BTC/ETH basket return <= 0.
    # This avoids using day-3 close to claim an exit at that same close.
    base=x["position"].to_numpy(float)
    early=np.zeros(len(x),dtype=float)
    entry_i=None; forced=False
    for i in range(len(x)):
        if base[i]==1 and (i==0 or base[i-1]==0):
            entry_i=i; forced=False
        if base[i]==0:
            entry_i=None; forced=False
        if base[i]==1 and not forced:
            early[i]=1.0
            if entry_i is not None and i-entry_i==3:
                r3=fwd_underlying(x,entry_i,3)
                if np.isfinite(r3) and r3<=0:
                    forced=True
        # after forced exit, remain flat until original regime resets
    intr=((x["BTCUSDT_close"]/x["BTCUSDT_open"]-1)+(x["ETHUSDT_close"]/x["ETHUSDT_open"]-1))*50
    turn=pd.Series(early,index=x.index).diff().abs().fillna(pd.Series(early,index=x.index).abs())
    x["early3_position"]=early
    x["early3_strategy_ret"]=early*intr-turn*(RT/2)

    brows=[]
    for label,col in [("BASE","strategy_ret"),("EARLY3","early3_strategy_ret")]:
        for year,g in x[(x["datetime_utc"].dt.year>=2018)&(x["datetime_utc"].dt.year<=2025)].groupby(x["datetime_utc"].dt.year):
            pp=perf(g[col].to_numpy(float))
            brows.append({"variant":label,"period":str(int(year)),**pp,
                "active_day_pct":float((g["position" if label=="BASE" else "early3_position"]==1).mean()*100)})
        g=x[(x["datetime_utc"].dt.year>=2018)&(x["datetime_utc"].dt.year<=2025)]
        pp=perf(g[col].to_numpy(float))
        brows.append({"variant":label,"period":"2018-2025",**pp,
            "active_day_pct":float((g["position" if label=="BASE" else "early3_position"]==1).mean()*100)})
    bdf=pd.DataFrame(brows)
    bdf.to_csv(OUT/"early3_backtest_compare.csv",index=False)

    # Anatomy of episodes that are non-positive at the causal day-3 checkpoint:
    # compare eventual recoveries vs eventual losers; descriptive only.
    d3=pdf[pdf["path_3d_close_pct"]<=0].copy()
    d3["eventual_group"]=np.where(d3["episode_ret_pct"]>0,"RECOVERED_WIN","STAYED_LOSS")
    # Merge signal-day features already stored in full episode table.
    featcols=["entry","start_btc30","start_eth30","start_trend7","start_trend30","start_trend90",
              "start_er","start_gap","start_rv30","start_dd90","start_corr30","fwd7_pct","fwd14_pct"]
    d3=d3.merge(full[featcols],on="entry",how="left")
    d3rows=[]
    metrics=["episode_days","episode_ret_pct","path_1d_close_pct","path_2d_close_pct","path_3d_close_pct",
             "path_3d_min_pct","start_btc30","start_eth30","start_trend7","start_trend30","start_trend90",
             "start_er","start_gap","start_rv30","start_dd90","start_corr30","fwd7_pct","fwd14_pct"]
    for name,h in d3.groupby("eventual_group"):
        row={"group":name,"n":len(h)}
        for m in metrics: row[m+"_median"]=float(h[m].median())
        d3rows.append(row)
    d3cdf=pd.DataFrame(d3rows)
    d3.to_csv(OUT/"day3_nonpositive_episode_detail.csv",index=False)
    d3cdf.to_csv(OUT/"day3_nonpositive_recovery_compare.csv",index=False)

    # Coarse robustness grid, predeclared checkpoints/thresholds only.
    # Goal is plateau detection, not selecting the single best cell.
    grid=[]
    base_arr=x["position"].to_numpy(float)
    for chk in [2,3,4,5]:
        for thr in [0.0,-1.0,-2.0,-3.0]:
            pos=np.zeros(len(x),dtype=float); ent=None; forced=False
            for i in range(len(x)):
                if base_arr[i]==1 and (i==0 or base_arr[i-1]==0):
                    ent=i; forced=False
                if base_arr[i]==0:
                    ent=None; forced=False
                if base_arr[i]==1 and not forced:
                    pos[i]=1.0
                    if ent is not None and i-ent==chk:
                        rr=fwd_underlying(x,ent,chk)
                        if np.isfinite(rr) and rr<=thr: forced=True
            turn=pd.Series(pos,index=x.index).diff().abs().fillna(pd.Series(pos,index=x.index).abs())
            rr=pd.Series(pos,index=x.index)*intr-turn*(RT/2)
            mask=(x["datetime_utc"].dt.year>=2018)&(x["datetime_utc"].dt.year<=2025)
            pp=perf(rr[mask].to_numpy(float))
            row={"checkpoint_days":chk,"threshold_pct":thr,**pp,
                 "active_day_pct":float((pd.Series(pos,index=x.index)[mask]==1).mean()*100)}
            # independent yearly outcomes for robustness, not only pooled total
            yrrets=[]
            for yr in range(2018,2026):
                ym=x["datetime_utc"].dt.year==yr
                yrrets.append(perf(rr[ym].to_numpy(float))["return_pct"])
            row["positive_years"]=int(sum(v>0 for v in yrrets))
            row["losing_years"]=int(sum(v<=0 for v in yrrets))
            row["worst_year_pct"]=float(min(yrrets))
            grid.append(row)
    gdf=pd.DataFrame(grid)
    gdf.to_csv(OUT/"early_exit_robustness_grid.csv",index=False)

    # Full-year table across all available years, plus BTC-only and ETH-only legs
    # under the exact same joint BTC+ETH regime and early-exit decisions.
    fy=[]
    variants=[("BASE",None,None),("D2_0",2,0.0),("D2_M1",2,-1.0),("D2_M2",2,-2.0),("D2_M3",2,-3.0)]
    for vname,chk,thr in variants:
        if chk is None:
            pos=base_arr.copy()
        else:
            pos=np.zeros(len(x),dtype=float); ent=None; forced=False
            for i in range(len(x)):
                if base_arr[i]==1 and (i==0 or base_arr[i-1]==0): ent=i; forced=False
                if base_arr[i]==0: ent=None; forced=False
                if base_arr[i]==1 and not forced:
                    pos[i]=1.0
                    if ent is not None and i-ent==chk:
                        rr0=fwd_underlying(x,ent,chk)
                        if np.isfinite(rr0) and rr0<=thr: forced=True
        ps=pd.Series(pos,index=x.index)
        trn=ps.diff().abs().fillna(ps.abs())
        legs={
          "BASKET":ps*intr-trn*(RT/2),
          "BTC":ps*(x["BTCUSDT_close"]/x["BTCUSDT_open"]-1)*100-trn*(RT/2),
          "ETH":ps*(x["ETHUSDT_close"]/x["ETHUSDT_open"]-1)*100-trn*(RT/2)}
        for leg,rrs in legs.items():
            for yr,gidx in x.groupby(x["datetime_utc"].dt.year).groups.items():
                if int(yr)<2018: continue
                pp=perf(rrs.loc[gidx].to_numpy(float))
                fy.append({"variant":vname,"leg":leg,"year":int(yr),**pp})
    fydf=pd.DataFrame(fy)
    fydf.to_csv(OUT/"early_exit_full_years_btc_eth.csv",index=False)

    rows=[]
    for year,g in x.groupby(x["datetime_utc"].dt.year):
        if year<2018: continue
        pg=perf(g["strategy_ret"].to_numpy(float))
        ag=g[g["position"]==1]
        eg=e[e["year"]==year]
        btc_y=(g["BTCUSDT_close"].iloc[-1]/g["BTCUSDT_close"].iloc[0]-1)*100
        eth_y=(g["ETHUSDT_close"].iloc[-1]/g["ETHUSDT_close"].iloc[0]-1)*100
        rows.append({
            "year":int(year),
            **pg,
            "btc_year_pct":btc_y,
            "eth_year_pct":eth_y,
            "active_day_pct":float((g["position"]==1).mean()*100),
            "episodes":len(eg),
            "episode_win_pct":float((eg["ret_pct"]>0).mean()*100) if len(eg) else math.nan,
            "episode_mean_pct":float(eg["ret_pct"].mean()) if len(eg) else math.nan,
            "episode_median_pct":float(eg["ret_pct"].median()) if len(eg) else math.nan,
            "episode_median_days":float(eg["days"].median()) if len(eg) else math.nan,
            "best_episode_pct":float(eg["ret_pct"].max()) if len(eg) else math.nan,
            "worst_episode_pct":float(eg["ret_pct"].min()) if len(eg) else math.nan,
            "active_trend30_avg":float(ag["trend30_avg"].mean()) if len(ag) else math.nan,
            "active_trend30_min_avg":float(ag["trend30_min"].mean()) if len(ag) else math.nan,
            "active_er_avg":float(ag["market_er"].mean()) if len(ag) else math.nan,
            "active_gap_avg":float(ag["trend30_gap"].mean()) if len(ag) else math.nan,
            "active_rv30_avg":float(ag["rv30_avg"].mean()) if len(ag) else math.nan,
            "active_corr30_avg":float(ag["btc_eth_corr30"].mean()) if len(ag) else math.nan,
            "active_dd90_avg":float(ag["dd90_avg"].mean()) if len(ag) else math.nan,
            "next_both_up_pct":float(ag["both_up_next"].mean()*100) if len(ag) else math.nan,
            "next_both_down_pct":float(ag["both_down_next"].mean()*100) if len(ag) else math.nan,
            "next_split_pct":float(ag["split_next"].mean()*100) if len(ag) else math.nan,
            "churn_per_100_active_days":float(len(eg)/max(len(ag),1)*100),
        })
    y=pd.DataFrame(rows)
    y["outcome"]="WIN" 
    y.loc[y["return_pct"]<0,"outcome"]="LOSS"
    y.to_csv(OUT/"yearly_anatomy.csv",index=False)

    compare=[]
    feats=[
        "btc_year_pct","eth_year_pct","active_day_pct","episodes","episode_win_pct",
        "episode_mean_pct","episode_median_days","active_trend30_avg","active_er_avg",
        "active_gap_avg","active_rv30_avg","active_corr30_avg","active_dd90_avg",
        "next_both_up_pct","next_both_down_pct","next_split_pct","churn_per_100_active_days"
    ]
    for f in feats:
        w=y[y["outcome"]=="WIN"][f].dropna()
        l=y[y["outcome"]=="LOSS"][f].dropna()
        compare.append({
            "feature":f,
            "win_mean":float(w.mean()) if len(w) else math.nan,
            "loss_mean":float(l.mean()) if len(l) else math.nan,
            "difference_win_minus_loss":float(w.mean()-l.mean()) if len(w) and len(l) else math.nan,
        })
    c=pd.DataFrame(compare)
    c.to_csv(OUT/"win_loss_compare.csv",index=False)

    print("=== YEARLY ANATOMY ===")
    print(y.to_string(index=False))
    print("\\n=== WIN VS LOSS ===")
    print(c.to_string(index=False))
    print("\\n=== EPISODE STRUCTURE COMPARE ===")
    print(pd.DataFrame(comp).to_string(index=False))
    print("\\n=== SIGNAL-DAY QUARTILES ===")
    print(qdf.to_string(index=False))
    print("\\n=== SIGNAL-DAY YEARLY RANK CORR ===")
    print(ycorr.to_string(index=False))
    print("\\n=== SIGNAL-DAY ER X GAP ===")
    print(jdf.to_string(index=False))
    print("\\n=== EARLY PERSISTENCE WIN VS LOSS ===")
    print(pcdf.to_string(index=False))
    print("\\n=== EARLY SIGN OUTCOMES ===")
    print(esdf.to_string(index=False))
    print("\\n=== EARLY3 BACKTEST BASE VS EARLY EXIT ===")
    print(bdf.to_string(index=False))
    print("\\n=== DAY3 NONPOSITIVE: RECOVERED VS STAYED LOSS ===")
    print(d3cdf.to_string(index=False))
    print("\\n=== EARLY EXIT ROBUSTNESS GRID ===")
    print(gdf.sort_values(["checkpoint_days","threshold_pct"],ascending=[True,False]).to_string(index=False))
    print("\\n=== FULL YEARS: BASE + D2 ROBUSTNESS, BASKET/BTC/ETH ===")
    print(fydf.to_string(index=False))
    print("\\n=== LOSING YEAR EPISODES ===")
    print(e[e["year"].isin(y.loc[y["outcome"]=="LOSS","year"].tolist())].to_string(index=False))


def flat_periods_current_rule():
    """Consecutive zero-position periods for BASE and D2_0."""
    x=build()
    intr=(x["BTCUSDT_intraday"]+x["ETHUSDT_intraday"])/2
    sig=(x["BTCUSDT_ret30"].gt(0)&x["ETHUSDT_ret30"].gt(0)&x["er_avg"].ge(ER_T)).fillna(False).to_numpy()
    base=causal_pos(sig,None,None)
    d20=causal_pos(sig,2,0.0)
    rows=[]; summary=[]
    for name,pos in [("BASE",base),("D2_0",d20)]:
        flat=np.asarray(pos)==0
        starts=np.where(flat & np.r_[True,~flat[:-1]])[0]
        ends=np.where(flat & np.r_[~flat[1:],True])[0]
        lens=ends-starts+1
        for s,e,n in zip(starts,ends,lens):
            rows.append({"variant":name,"start":x.index[s],"end":x.index[e],"days":int(n)})
        summary.append({"variant":name,"total_days":len(pos),"flat_days":int(flat.sum()),"flat_pct":100*flat.mean(),"periods":len(lens),"avg_days":float(np.mean(lens)),"median_days":float(np.median(lens)),"max_days":int(np.max(lens))})
    p=pd.DataFrame(rows); sm=pd.DataFrame(summary)
    p.to_csv(OUT/"flat_periods.csv",index=False); sm.to_csv(OUT/"flat_summary.csv",index=False)
    print("\n=== FLAT SUMMARY ==="); print(sm.to_string(index=False))
    print("\n=== FLAT PERIODS >=30D ==="); print(p[p["days"]>=30].sort_values(["variant","start"]).to_string(index=False))
    print("\n=== TOP 15 LONGEST FLAT PERIODS ==="); print(p.sort_values("days",ascending=False).head(15).to_string(index=False))

if __name__=="__main__":
    main()

