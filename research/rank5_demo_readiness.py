from __future__ import annotations
import argparse, math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd

from precision_backtest import candidate_rank, first_cross, fetch_range, rows_to_df, MIN, FEE_PCT

HORIZONS_H=[6,8,10,12,14,16,18,24]
TP=10.0
SL=4.0
DELAYS=[1,2,3]

def parse_args():
    p=argparse.ArgumentParser()
    p.add_argument("--source-old",required=True)
    p.add_argument("--source-new",required=True)
    p.add_argument("--regime",required=True)
    p.add_argument("--outdir",default="rank5_demo_readiness_results")
    p.add_argument("--workers",type=int,default=8)
    return p.parse_args()

def pf(vals, extra_cost=0.0):
    x=pd.to_numeric(pd.Series(vals),errors="coerce").dropna()-extra_cost
    pos=float(x[x>0].sum()); neg=float(-x[x<0].sum())
    if neg<=0:return float("inf") if pos>0 else float("nan")
    return pos/neg

def metrics(x, col="net_pct", extra_cost=0.0):
    z=pd.to_numeric(x[col],errors="coerce").dropna()-extra_cost
    return dict(n=int(len(z)),symbols=int(x.loc[z.index,"symbol"].nunique()) if len(z) else 0,
                avg=float(z.mean()) if len(z) else np.nan,
                median=float(z.median()) if len(z) else np.nan,
                sum=float(z.sum()) if len(z) else np.nan,
                pf=pf(x.loc[z.index,col],extra_cost) if len(z) else np.nan,
                win_pct=float((z>0).mean()*100) if len(z) else np.nan)

def build_signals(source, universe):
    df=pd.read_csv(source).sort_values(["symbol","ts"]).reset_index(drop=True)
    df["rank"]=candidate_rank(df)
    # Candidate frozen from prior research: rank5 + 4H lag, first-cross.
    cand=(df["rank"].to_numpy()==5)&(df["4H_above"].to_numpy()==0)
    # Predeclared near-miss negative control: same rank5 intensity but 4H is NOT the lagging TF.
    ctrl=(df["rank"].to_numpy()==5)&(df["4H_above"].to_numpy()==1)
    out=[]
    for label,mask in [("RANK5_4H_LAG",cand),("RANK5_OTHER_LAG_CONTROL",ctrl)]:
        trig=first_cross(pd.Series(mask,index=df.index),df["symbol"])
        cols=["symbol","ts","time_utc","price","rank","exact_count","within_3pct_count",
              "1W_above","1D_above","12H_above","4H_above","1H_above","30M_above","15M_above"]
        # Preserve any useful context columns already present.
        for c in ["1W_dist","1D_dist","12H_dist","4H_dist","1H_dist","30M_dist","15M_dist"]:
            if c in df.columns and c not in cols: cols.append(c)
        q=df.loc[trig.to_numpy(),cols].copy()
        q["signal_type"]=label; q["universe"]=universe
        out.append(q)
    return pd.concat(out,ignore_index=True).sort_values(["ts","symbol"]).reset_index(drop=True)

def add_bear(signals,breadth):
    b=breadth[["ts","regime_60_40"]].drop_duplicates("ts")
    x=signals.merge(b,on="ts",how="left")
    x=x[x.regime_60_40=="BEAR"].copy()
    return x.rename(columns={"ts":"signal_ts"}).sort_values(["signal_ts","symbol"]).reset_index(drop=True)

def merge_windows(sig,hours=24):
    out={}
    for sym,g in sig.groupby("symbol"):
        arr=sorted((int(t),int(t)+(hours*60+10)*MIN) for t in g.signal_ts)
        if not arr:continue
        merged=[];cs,ce=arr[0]
        for s,e in arr[1:]:
            if s<=ce+5*MIN:ce=max(ce,e)
            else:merged.append((cs,ce));cs,ce=s,e
        merged.append((cs,ce));out[sym]=merged
    return out

def fetch_symbol(sym,windows):
    parts=[]
    for s,e in windows:
        z=rows_to_df(fetch_range(sym,"1m",1,s,e),1)[["ts","open","high","low","close"]]
        parts.append(z)
    if not parts:return sym,pd.DataFrame()
    z=pd.concat(parts,ignore_index=True).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return sym,z

def fetch_minutes(sig,workers):
    ws=merge_windows(sig,24); out={}; failures=[]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fut={ex.submit(fetch_symbol,s,w):s for s,w in ws.items()}
        for f in as_completed(fut):
            sym=fut[f]
            try:
                s,z=f.result();out[s]=z;print(f"[1M] {s} rows={len(z)}")
            except Exception as e:
                failures.append((sym,str(e)));out[sym]=pd.DataFrame();print(f"[1M-ERR] {sym}: {e}")
    return out,failures

def entry(row,md,delay):
    candle_ts=int(row.signal_ts)+(delay-1)*MIN
    q=md[md.ts==candle_ts]
    if q.empty:return None
    return float(q.iloc[-1].close),int(row.signal_ts)+delay*MIN

def scan(path,entry_price):
    tp=entry_price*(1-TP/100); sl=entry_price*(1+SL/100)
    for bar in path.itertuples(index=False):
        ht=float(bar.low)<=tp; hs=float(bar.high)>=sl
        if ht and hs:return "SL",sl,int(bar.ts)+MIN
        if hs:return "SL",sl,int(bar.ts)+MIN
        if ht:return "TP",tp,int(bar.ts)+MIN
    return None

def replay_one(row,md,delay,hours):
    ep=entry(row,md,delay)
    if ep is None:return None
    ent,ets=ep; end=ets+hours*60*MIN
    path=md[(md.ts>=ets)&(md.ts<end)]
    if path.empty:return None
    hit=scan(path,ent)
    if hit:o,px,xt=hit
    else:o="TIME";px=float(path.iloc[-1].close);xt=int(path.iloc[-1].ts)+MIN
    net=(1-px/ent)*100-FEE_PCT
    # Path anatomy independent of TP/SL.
    mfe=(1-float(path.low.min())/ent)*100
    mae=(float(path.high.max())/ent-1)*100
    close_ret=(1-float(path.iloc[-1].close)/ent)*100-FEE_PCT
    return dict(universe=row.universe,signal_type=row.signal_type,symbol=row.symbol,signal_ts=int(row.signal_ts),
                delay_min=delay,horizon_h=hours,outcome=o,entry_ts=ets,exit_ts=xt,
                entry_price=ent,exit_price=px,net_pct=net,mfe_short_pct=mfe,mae_short_pct=mae,
                fixed_close_short_pct=close_ret,hold_h=(xt-ets)/3600000)

def add_event_id(x,hours):
    z=x.sort_values(["signal_ts","symbol"]).copy()
    ids=[];cid=-1;prev=None
    for t in z.signal_ts.astype("int64"):
        if prev is None or int(t)-prev>hours*3600_000:cid+=1
        ids.append(cid);prev=int(t)
    z[f"event_{hours}h"]=ids
    return z

def bootstrap_ci(vals,nboot=20000,seed=260924):
    a=np.asarray(vals,dtype=float);a=a[np.isfinite(a)]
    if len(a)==0:return np.nan,np.nan,np.nan
    rng=np.random.default_rng(seed)
    means=np.empty(nboot)
    for i in range(nboot):means[i]=rng.choice(a,size=len(a),replace=True).mean()
    return float(a.mean()),float(np.quantile(means,.025)),float(np.quantile(means,.975))

def signflip_p(vals,nperm=50000,seed=260925):
    a=np.asarray(vals,dtype=float);a=a[np.isfinite(a)]
    if len(a)==0:return np.nan
    obs=float(a.mean());rng=np.random.default_rng(seed);ge=0
    for _ in range(nperm):
        signs=rng.choice(np.array([-1.0,1.0]),size=len(a))
        if float((a*signs).mean())>=obs:ge+=1
    return float((ge+1)/(nperm+1))

def event_stats(trades):
    rows=[];loo=[];conc=[]
    q=trades[(trades.signal_type=="RANK5_4H_LAG")&(trades.horizon_h==12)&(trades.delay_min==1)].copy()
    for universe,g in q.groupby("universe"):
        g=add_event_id(g,24)
        e=g.groupby("event_24h").agg(pnl=("net_pct","sum"),signals=("symbol","size"),symbols=("symbol","nunique"),
                                    start_ts=("signal_ts","min"),end_ts=("signal_ts","max")).reset_index()
        mean,lo,hi=bootstrap_ci(e.pnl)
        rows.append(dict(universe=universe,trades=len(g),symbols=g.symbol.nunique(),events=len(e),
                         profitable_events=int((e.pnl>0).sum()),event_win_pct=float((e.pnl>0).mean()*100),
                         event_mean=mean,event_median=float(e.pnl.median()),event_sum=float(e.pnl.sum()),
                         bootstrap95_lo=lo,bootstrap95_hi=hi,signflip_p_one_sided=signflip_p(e.pnl)))
        for eid in e.event_24h:
            z=g[g.event_24h!=eid]
            loo.append(dict(universe=universe,dropped_event=int(eid),remaining_events=len(e)-1,
                            remaining_trades=len(z),pf=pf(z.net_pct),pf_cost050=pf(z.net_pct,.50),
                            avg=float((z.net_pct).mean()),sum=float(z.net_pct.sum())))
        abs_total=float(e.pnl.abs().sum())
        pos_total=float(e.loc[e.pnl>0,"pnl"].sum())
        es=e.sort_values("pnl",ascending=False)
        for k in [1,3,5]:
            kk=min(k,len(es));top=es.head(kk)
            conc.append(dict(universe=universe,kind="EVENT",top_n=kk,total_n=len(es),
                             top_share_count_pct=100*kk/len(es),
                             abs_pnl_share_pct=float(top.pnl.abs().sum()/abs_total*100) if abs_total else np.nan,
                             positive_pnl_share_pct=float(top.loc[top.pnl>0,"pnl"].sum()/pos_total*100) if pos_total else np.nan))
        # symbol concentration
        s=g.groupby("symbol").net_pct.sum().sort_values(ascending=False)
        abs_s=float(s.abs().sum());pos_s=float(s[s>0].sum())
        for k in [1,3,5]:
            kk=min(k,len(s));top=s.head(kk)
            conc.append(dict(universe=universe,kind="SYMBOL",top_n=kk,total_n=len(s),
                             top_share_count_pct=100*kk/len(s),
                             abs_pnl_share_pct=float(top.abs().sum()/abs_s*100) if abs_s else np.nan,
                             positive_pnl_share_pct=float(top[top>0].sum()/pos_s*100) if pos_s else np.nan))
    return pd.DataFrame(rows),pd.DataFrame(loo),pd.DataFrame(conc)

def repeat_stats(trades):
    # Work from unique signals, then attach 12h/+1m result.
    q=trades[(trades.signal_type=="RANK5_4H_LAG")&(trades.horizon_h==12)&(trades.delay_min==1)].copy()
    rows=[]
    for universe,g0 in q.groupby("universe"):
        g=g0.sort_values(["signal_ts","symbol"]).copy()
        g["symbol_ordinal"]=g.groupby("symbol").cumcount()+1
        g=add_event_id(g,24)
        g["event_ordinal"]=g.groupby("event_24h").cumcount()+1
        scopes=[
            ("SYMBOL_FIRST",g[g.symbol_ordinal==1]),
            ("SYMBOL_SECOND",g[g.symbol_ordinal==2]),
            ("SYMBOL_THIRD_PLUS",g[g.symbol_ordinal>=3]),
            ("EVENT_FIRST",g[g.event_ordinal==1]),
            ("EVENT_REPEAT",g[g.event_ordinal>=2]),
        ]
        for name,z in scopes:
            m=metrics(z)
            rows.append(dict(universe=universe,scope=name,total_trades=len(g),**m,
                             pf_cost025=pf(z.net_pct,.25) if len(z) else np.nan,
                             pf_cost050=pf(z.net_pct,.50) if len(z) else np.nan))
    return pd.DataFrame(rows)

def time_split_stats(trades,src_ranges):
    q=trades[(trades.signal_type=="RANK5_4H_LAG")&(trades.horizon_h==12)&(trades.delay_min==1)].copy()
    rows=[]
    for universe,g in q.groupby("universe"):
        lo,hi=src_ranges[universe]; span=hi-lo
        g=g.copy()
        g["half"]=np.minimum(2,((g.signal_ts-lo)/(span/2)).astype(int)+1)
        g["quarter"]=np.minimum(4,((g.signal_ts-lo)/(span/4)).astype(int)+1)
        for kind,col,maxn in [("HALF","half",2),("QUARTER","quarter",4)]:
            for idx in range(1,maxn+1):
                z=g[g[col]==idx]
                m=metrics(z)
                # event count within slice
                ev=len(add_event_id(z,24)[["event_24h"]].drop_duplicates()) if len(z) else 0
                rows.append(dict(universe=universe,split_type=kind,split_index=idx,events=ev,**m,
                                 pf_cost050=pf(z.net_pct,.50) if len(z) else np.nan))
    return pd.DataFrame(rows)

def anatomy(trades):
    rows=[]
    q=trades[trades.signal_type=="RANK5_4H_LAG"].copy()
    for keys,g in q.groupby(["universe","delay_min","horizon_h"]):
        u,d,h=keys;m=metrics(g)
        rows.append(dict(universe=u,delay_min=d,horizon_h=h,**m,
                         pf_cost025=pf(g.net_pct,.25),pf_cost050=pf(g.net_pct,.50),
                         tp_rate=float((g.outcome=="TP").mean()*100),sl_rate=float((g.outcome=="SL").mean()*100),
                         time_rate=float((g.outcome=="TIME").mean()*100),
                         mfe_mean=float(g.mfe_short_pct.mean()),mfe_median=float(g.mfe_short_pct.median()),
                         mae_mean=float(g.mae_short_pct.mean()),mae_median=float(g.mae_short_pct.median()),
                         fixed_close_avg=float(g.fixed_close_short_pct.mean()),
                         fixed_close_pf=pf(g.fixed_close_short_pct)))
    return pd.DataFrame(rows)

def control_compare(trades):
    rows=[]
    q=trades[(trades.horizon_h==12)&(trades.delay_min==1)].copy()
    for universe,g0 in q.groupby("universe"):
        for st,g in g0.groupby("signal_type"):
            m=metrics(g)
            rows.append(dict(universe=universe,signal_type=st,**m,
                             pf_cost050=pf(g.net_pct,.50),events=len(add_event_id(g,24).event_24h.unique()) if len(g) else 0))
    return pd.DataFrame(rows)

def portfolio_sim(trades,extra_cost=0.0,risk_frac=.005,max_pos=6):
    # Scenario only: 0.5% equity risk to 4% stop => 12.5% equity notional per accepted trade.
    q=trades[(trades.signal_type=="RANK5_4H_LAG")&(trades.horizon_h==12)&(trades.delay_min==1)].copy()
    q=q.sort_values(["entry_ts","symbol"]).reset_index(drop=True)
    equity=100.0;peak=100.0;mdd=0.0;active=[];accepted=0;skip_busy=0;skip_cap=0
    curve=[]
    for r in q.itertuples(index=False):
        active=[a for a in active if a["exit_ts"]>int(r.entry_ts)]
        if any(a["symbol"]==r.symbol for a in active):
            skip_busy+=1;continue
        if len(active)>=max_pos:
            skip_cap+=1;continue
        notional=equity*risk_frac/(SL/100.0)
        pnl=notional*((float(r.net_pct)-extra_cost)/100.0)
        equity+=pnl;accepted+=1
        active.append({"symbol":r.symbol,"exit_ts":int(r.exit_ts)})
        peak=max(peak,equity)
        dd=(equity/peak-1)*100
        mdd=min(mdd,dd)
        curve.append((int(r.exit_ts),equity))
    return dict(extra_cost_pct=extra_cost,accepted=accepted,total_candidates=len(q),skip_same_symbol=skip_busy,
                skip_max_positions=skip_cap,final_equity=equity,return_pct=equity-100.0,max_drawdown_pct=mdd,
                risk_per_trade_pct=risk_frac*100,max_positions=max_pos)

def readiness(anat,ev,loo,conc,splits,ctrl,port):
    checks=[]
    # Predeclared factual gates for Demo forward, not live deployment.
    n=anat[(anat.universe=="NEW66_HOLDOUT")&(anat.delay_min==1)]
    adj=n[n.horizon_h.isin([10,12,14,16])]
    checks.append(("NEW66_10_16H_RAW", int((adj.pf>1).sum())>=3, f"{int((adj.pf>1).sum())}/4 horizons PF>1"))
    checks.append(("NEW66_10_16H_COST050", int((adj.pf_cost050>1).sum())>=2, f"{int((adj.pf_cost050>1).sum())}/4 horizons cost+0.50 PF>1"))
    er=ev[ev.universe=="NEW66_HOLDOUT"].iloc[0] if len(ev[ev.universe=="NEW66_HOLDOUT"]) else None
    checks.append(("EVENT_MEAN_POSITIVE", bool(er is not None and er.event_mean>0), f"event mean={er.event_mean:.3f}" if er is not None else "missing"))
    l=loo[loo.universe=="NEW66_HOLDOUT"]
    frac=float((l.pf>1).mean()) if len(l) else 0
    checks.append(("LOO_EVENT_MAJORITY", frac>=.70, f"{int((l.pf>1).sum())}/{len(l)} leave-one-event PF>1"))
    c=conc[(conc.universe=="NEW66_HOLDOUT")&(conc.kind=="EVENT")&(conc.top_n==1)]
    share=float(c.abs_pnl_share_pct.iloc[0]) if len(c) else np.nan
    checks.append(("NO_SINGLE_EVENT_DOMINANCE", bool(np.isfinite(share) and share<40), f"top1/total event abs PnL share={share:.1f}%"))
    s=splits[(splits.universe=="NEW66_HOLDOUT")&(splits.split_type=="QUARTER")]
    posq=int((s["sum"]>0).sum()) if len(s) else 0
    checks.append(("TIME_SPLIT",posq>=2,f"{posq}/4 quarters positive sum"))
    cc=ctrl[ctrl.universe=="NEW66_HOLDOUT"]
    cand=cc[cc.signal_type=="RANK5_4H_LAG"];con=cc[cc.signal_type=="RANK5_OTHER_LAG_CONTROL"]
    ok=bool(len(cand) and len(con) and cand.avg.iloc[0]>con.avg.iloc[0])
    desc=f"candidate avg={cand.avg.iloc[0]:.3f}, control avg={con.avg.iloc[0]:.3f}" if len(cand) and len(con) else "missing"
    checks.append(("BEATS_NEAR_MISS_CONTROL",ok,desc))
    p=port[(port.extra_cost_pct==.50)]
    okp=bool(len(p) and p.return_pct.iloc[0]>0 and p.max_drawdown_pct.iloc[0]>-15)
    descp=f"return={p.return_pct.iloc[0]:.2f}%, MDD={p.max_drawdown_pct.iloc[0]:.2f}%" if len(p) else "missing"
    checks.append(("PORTFOLIO_COST050",okp,descp))
    out=pd.DataFrame(checks,columns=["check","pass","detail"])
    passed=int(out["pass"].sum()); total=len(out)
    # Conservative label: 7-8 demo-ready, 5-6 shadow/demo-candidate, <=4 research-only.
    if passed>=7:label="DEMO_FORWARD_READY"
    elif passed>=5:label="CONDITIONAL_DEMO_OR_SHADOW"
    else:label="RESEARCH_ONLY"
    return out,label,passed,total

def main():
    a=parse_args();out=Path(a.outdir);out.mkdir(parents=True,exist_ok=True)
    breadth=pd.read_csv(a.regime)
    src_old=pd.read_csv(a.source_old);src_new=pd.read_csv(a.source_new)
    src_ranges={"AUTO50_OVERLAP":(int(src_old.ts.min()),int(src_old.ts.max())),
                "NEW66_HOLDOUT":(int(src_new.ts.min()),int(src_new.ts.max()))}
    s1=add_bear(build_signals(a.source_old,"AUTO50_OVERLAP"),breadth)
    s2=add_bear(build_signals(a.source_new,"NEW66_HOLDOUT"),breadth)
    sig=pd.concat([s1,s2],ignore_index=True).drop_duplicates(["universe","signal_type","symbol","signal_ts"])
    print("=== SIGNAL COUNTS ===")
    print(sig.groupby(["universe","signal_type"]).agg(n=("signal_ts","size"),symbols=("symbol","nunique")).to_string())

    minute,fail=fetch_minutes(sig,a.workers)
    if fail:pd.DataFrame(fail,columns=["symbol","error"]).to_csv(out/"minute_failures.csv",index=False)

    rows=[]
    for r in sig.itertuples(index=False):
        md=minute.get(r.symbol,pd.DataFrame())
        if md.empty:continue
        for d in DELAYS:
            for h in HORIZONS_H:
                z=replay_one(r,md,d,h)
                if z is not None:rows.append(z)
    T=pd.DataFrame(rows)
    T.to_csv(out/"all_trades.csv.gz",index=False,compression="gzip")
    sig.to_csv(out/"bear_signals.csv",index=False)

    A=anatomy(T);E,L,C=event_stats(T);R=repeat_stats(T);S=time_split_stats(T,src_ranges);N=control_compare(T)
    ports=pd.DataFrame([portfolio_sim(T,c) for c in [0.0,.25,.50]])
    checks,label,passed,total=readiness(A,E,L,C,S,N,ports)

    A.to_csv(out/"time_anatomy.csv",index=False);E.to_csv(out/"event_stats.csv",index=False)
    L.to_csv(out/"leave_one_event_out.csv",index=False);C.to_csv(out/"concentration.csv",index=False)
    R.to_csv(out/"repeat_signal_stats.csv",index=False);S.to_csv(out/"time_splits.csv",index=False)
    N.to_csv(out/"near_miss_control.csv",index=False);ports.to_csv(out/"portfolio_scenario.csv",index=False)
    checks.to_csv(out/"demo_readiness_checks.csv",index=False)
    pd.DataFrame([{"label":label,"passed":passed,"total":total}]).to_csv(out/"verdict.csv",index=False)

    print("\n=== TIME ANATOMY ===");print(A.to_string(index=False))
    print("\n=== EVENT STATS ===");print(E.to_string(index=False))
    print("\n=== LEAVE ONE EVENT OUT ===");print(L.to_string(index=False))
    print("\n=== CONCENTRATION ===");print(C.to_string(index=False))
    print("\n=== REPEAT SIGNAL ===");print(R.to_string(index=False))
    print("\n=== TIME SPLITS ===");print(S.to_string(index=False))
    print("\n=== NEAR MISS CONTROL ===");print(N.to_string(index=False))
    print("\n=== PORTFOLIO SCENARIO ===");print(ports.to_string(index=False))
    print("\n=== DEMO READINESS ===");print(checks.to_string(index=False))
    print(f"VERDICT={label} PASSED={passed}/{total}")
    print("NOTE: readiness gates were predeclared for demo-forward data collection only; they do not establish live-trading profitability.")
    print("[DONE]")

if __name__=="__main__":main()
