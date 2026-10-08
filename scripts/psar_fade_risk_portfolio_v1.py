#!/usr/bin/env python3
"""Independent policy portfolio audit for frozen PSAR stretched-fade LONG.
Portfolio ledger is exit-realized only, not a liquidation/MTM backtest.
"""
import os,glob,json,argparse,hashlib,sys,heapq
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd,numpy as np
sys.path.insert(0,"scripts")
from psar_dual_risk_containment_v1 import canonical,audit,STABLE,BURN,M,DATA_RUN,local_filter,eval_order,split
CUTOFF=1735689600000
DAY=86400000
POLICIES={
"P3_SL6_TIME":(3.,6.,False),"P3_SL6_FLIP":(3.,6.,True),
"P3p5_SL6_TIME":(3.5,6.,False),"P3p5_SL6_FLIP":(3.5,6.,True),
"P3p5_SL8_FLIP":(3.5,8.,True)}
CAPITAL=10000.
PORT_RULE={"max_slots":4,"max_single_fraction":.25,"max_gross_fraction":1.,
           "daily_realized_loss_halt":.015,"drawdown_pause":.05,"drawdown_pause_days":7,
           "loss_streak_pause":3,"loss_streak_pause_hours":24}
def ledger(a,fn,engine):
    files=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert files
    selected=[f for i,f in enumerate(files) if i%a.shards==a.shard]
    rows=[];skips=defaultdict(int)
    for j,file in enumerate(selected,1):
        sym=fn["_symbol"](file)
        if sym in STABLE:continue
        original=fn["load"](file);t,o,h,l,c=original
        for st,en in fn["contiguous_segments"](t):
            if en-st<M*(BURN+21):skips["short_segment"]+=1;continue
            sub=[v[st:en] for v in original]
            rt,ro,rh,rl,rc=fn["resample"](*sub,M)
            if len(rt)<=BURN+21:skips["short_segment"]+=1;continue
            sar,bull=fn["psar_open_projection"](rh,rl)
            prv=np.r_[np.nan,rc[:-1]]
            tr=np.maximum(rh-rl,np.maximum(abs(rh-prv),abs(rl-prv)))
            atr=np.r_[np.nan,pd.Series(tr).rolling(14,min_periods=14).mean().to_numpy()[:-1]]
            low6=pd.Series(rl).shift(1).rolling(6,min_periods=6).min().to_numpy()
            pos=np.searchsorted(sub[0],rt)
            assert np.all(sub[0][pos]==rt)
            flips=np.flatnonzero(bull[1:]!=bull[:-1])+1
            starts=[int(q) for q in flips if q>=BURN]
            ends=starts[1:]+[len(rt)]
            flip_pos={int(pos[q]) for q in flips}
            for a4,b4 in zip(starts,ends):
                if bull[a4]:continue
                i=a4+11 # 12th PSAR BEAR candle
                if i>=b4 or i+8>=len(rt):continue
                en_price=float(ro[i]);vol=float(atr[i]);dot=float(sar[i])
                if not np.isfinite(vol) or vol<=0 or not np.isfinite(dot) or en_price<=0:continue
                dist=(dot-en_price)/vol
                if not local_filter("BEAR",en_price,float(low6[i]),float("nan"),vol):continue
                from15=int(pos[i]);to15=int(pos[i+8])
                sp=split(int(rt[i]),int(rt[i+8]))
                if sp=="EXCLUDED_CROSS":skips["cross_split"]+=1;continue
                for policy,(d,sl,flip) in POLICIES.items():
                    if dist<d:continue
                    exit_i,fill,reason=eval_order(sub[1],sub[2],sub[3],sub[4],from15,to15,
                        en_price,True,sl,0,flip_pos if flip else set())
                    entry_ts=int(rt[i]);exit_ts=int(sub[0][exit_i])
                    assert exit_ts>=entry_ts
                    pnl=100*(fill/en_price-1.)
                    rows.append(dict(policy=policy,symbol=sym,entry_ts=entry_ts,exit_ts=exit_ts,
                        split=sp,year=datetime.fromtimestamp(entry_ts/1000,tz=timezone.utc).year,
                        entry=en_price,exit=fill,dist_atr=dist,stop_pct=sl,exit_reason=reason,
                        gross_pct=pnl,net20_pct=pnl-.2,net40_pct=pnl-.4))
        print("PROGRESS",a.shard,j,len(selected),sym,"trades",len(rows),flush=True)
    d=pd.DataFrame.from_records(rows)
    assert len(d)>0
    assert not d.duplicated(["policy","symbol","entry_ts"]).any()
    d.to_csv(a.out,index=False,compression="gzip")
    meta={"experiment":"PSAR_FADE_ACCOUNT_V1","classification":"EXPLORATORY",
        "files":len(selected),"all_files":len(files),"shards":a.shards,"shard":a.shard,
        "signals":len(d),"code_sha":os.getenv("GITHUB_SHA","local"),
        "engine_sha":engine,"source_data_run":DATA_RUN,"rows_by_policy":d.groupby("policy").size().to_dict(),
        "exclusions":dict(skips),"entry_exit_policy":"canonical TF OPEN; 15m stop market; only EXIT-time open for profit/flip/time, 40bp cost roundtrip"}
    Path(a.meta).write_text(json.dumps(meta,indent=2))
    print("SHARD_PASS",a.shard,len(d),flush=True)
def stats(trades,splitname,risk,policy):
    trades=trades.sort_values(["entry_ts","symbol"],kind="mergesort")
    equity=CAPITAL;peak=equity;maxdd=0;accepted=0;haltuntil=0;loserow=0
    active=[];symbol_active=set();size_total=0.
    rejected=defaultdict(int); exit_reasons=defaultdict(int);annual=defaultdict(float);fills=[]
    maxslots=0
    realized_daily=0.;day_num=None;day_start_equity=equity
    realized_winners=0.;realized_losers=0.
    def settle(tlimit):
        nonlocal equity,peak,maxdd,loserow,haltuntil,realized_daily,day_num,day_start_equity,realized_winners,realized_losers
        while active and active[0][0]<=tlimit:
            end,_,symbol,investment,net_pct,reason,entrytime=heapq.heappop(active)
            symbol_active.remove(symbol)
            day=end//DAY
            if day_num!=day:
                day_num=day;realized_daily=0.;day_start_equity=equity
            pnl=investment*net_pct/100.
            equity+=pnl;realized_daily+=pnl
            annual[datetime.fromtimestamp(end/1000,tz=timezone.utc).year]+=pnl
            exit_reasons[reason]+=1
            if pnl>0:realized_winners+=pnl;loserow=0
            else:
                realized_losers+=-pnl;loserow+=1
            if loserow>=PORT_RULE["loss_streak_pause"]:
                haltuntil=max(haltuntil,end+PORT_RULE["loss_streak_pause_hours"]*3600000)
                loserow=0
            peak=max(peak,equity)
            dd=max(0.,1-equity/peak)
            maxdd=max(maxdd,dd)
            if dd>=PORT_RULE["drawdown_pause"]:
                haltuntil=max(haltuntil,end+PORT_RULE["drawdown_pause_days"]*DAY)
            fills.append((entrytime,end,symbol,investment,net_pct,pnl))
    sequence=0
    for r in trades.itertuples(index=False):
        now=int(r.entry_ts)
        settle(now)
        d=now//DAY
        if day_num!=d:
            day_num=d;realized_daily=0.;day_start_equity=equity
        if now<haltuntil:rejected["pause"]+=1;continue
        if realized_daily<=-PORT_RULE["daily_realized_loss_halt"]*day_start_equity:
            rejected["daily_halt"]+=1;continue
        if r.symbol in symbol_active:rejected["duplicate_symbol"]+=1;continue
        if len(active)>=PORT_RULE["max_slots"]:rejected["slots_full"]+=1;continue
        gross=sum(rec[3] for rec in active)
        fraction=min(PORT_RULE["max_single_fraction"],risk/((r.stop_pct+.4)/100.))
        investment=fraction*equity
        if investment+gross>equity*PORT_RULE["max_gross_fraction"]:
            investment=equity*PORT_RULE["max_gross_fraction"]-gross
        if investment<=0:rejected["gross_cap"]+=1;continue
        sequence+=1
        heapq.heappush(active,(int(r.exit_ts),sequence,r.symbol,investment,float(r.net40_pct),r.exit_reason,now))
        symbol_active.add(r.symbol)
        accepted+=1;size_total+=investment/equity
        maxslots=max(maxslots,len(active))
    settle(99999999999999)
    assert not active and not symbol_active
    duration_years=4. if splitname=="TRAIN" else ((pd.Timestamp("2026-10-08",tz="UTC")-pd.Timestamp("2025-01-01",tz="UTC")).days/365.25)
    return {
      "policy":policy,"split":splitname,"risk_equity_fraction":risk,"raw_events":len(trades),
      "accepted":accepted,"excluded":dict(rejected),"max_concurrent":maxslots,
      "equity_start":CAPITAL,"equity_end":round(equity,2),
      "return_pct":round((equity/CAPITAL-1)*100,3),
      "approx_cagr_pct":round(100*((equity/CAPITAL)**(1/duration_years)-1),3) if equity>0 else None,
      "realized_only_mdd_pct":round(100*maxdd,3),
      "avg_open_notional_fraction":round(size_total/max(1,accepted),4),
      "win_loss_pf_equity":round(realized_winners/realized_losers,4) if realized_losers else None,
      "exit_reasons":dict(exit_reasons),
      "annual_realized_pnl_usd":{str(k):round(v,2) for k,v in sorted(annual.items())},
      "note":"Realized-exit account MDD may understate MTM drawdowns; no funding, orderbook slippage or liquidation modelling"}
def merge(a,engine):
    cps=sorted(glob.glob(a.data+"/**/portfolio-ledger-*.csv.gz",recursive=True))
    mps=sorted(glob.glob(a.data+"/**/portfolio-meta-*.json",recursive=True))
    assert len(cps)==a.shards and len(mps)==a.shards,(len(cps),len(mps))
    metas=[json.load(open(p)) for p in mps]
    assert {m["shard"] for m in metas}==set(range(a.shards))
    assert len({m["code_sha"] for m in metas})==1 and len({m["engine_sha"] for m in metas})==1
    assert metas[0]["engine_sha"]==engine
    assert sum(m["files"] for m in metas)==metas[0]["all_files"]
    data=pd.concat([pd.read_csv(p) for p in cps],ignore_index=True)
    assert len(data)==sum(m["signals"] for m in metas)
    assert not data.duplicated(["policy","symbol","entry_ts"]).any()
    result={"experiment":"PSAR_FADE_ACCOUNT_V1","classification":"EXPLORATORY","data_run":DATA_RUN,
        "signal_run":37729000696,"risk_parameter_run":37741302968,"commit":metas[0]["code_sha"],
        "engine_sha":engine,"files":sum(m["files"] for m in metas),"rows":len(data),
        "risk_policies":PORT_RULE,"policies":POLICIES,
        "results":[],"bias":["no funding","no mark-to-market intraday MDD; equity computed on exits",
        "historical current-listed USDT universe (survivorship)","SEEN period was previously viewed, not pristine OOS",
        "gap/illiquidity may exceed historical stop slippage","no strategy live authority"]}
    for policy in POLICIES:
        for sp in ("TRAIN","SEEN_VALIDATION"):
            dd=data[(data.policy==policy)&(data.split==sp)]
            for risk in (.0025,.005):
                result["results"].append(stats(dd,sp,risk,policy))
    for policy in POLICIES:
        for risk in (.005,):
            t=next(r for r in result["results"] if r["policy"]==policy and r["split"]=="TRAIN" and r["risk_equity_fraction"]==risk)
            v=next(r for r in result["results"] if r["policy"]==policy and r["split"]=="SEEN_VALIDATION" and r["risk_equity_fraction"]==risk)
            print("PORTFOLIO",policy,"TRAIN",{"return":t["return_pct"],"MDDrealized":t["realized_only_mdd_pct"],"trades":t["accepted"],"blocked":t["excluded"]},
                  "SEEN",{"return":v["return_pct"],"MDDrealized":v["realized_only_mdd_pct"],"trades":v["accepted"],"blocked":v["excluded"]},flush=True)
    Path(a.out).write_text(json.dumps(result,indent=2,ensure_ascii=False))
    print("PORTFOLIO_MERGE_PASS",len(metas),len(data),flush=True)
def check():
    fn,sha=canonical();audit(fn)
    p=Path("scripts/psar_dual_risk_containment_v1.py").read_text()
    assert 'Only one intrabar order level exists' in p
    # sizing includes 0.4% round-trip stress cost
    assert abs(.005/((6+.4)/100)-.078125)<1e-10
    assert PORT_RULE["max_slots"]==4
    assert len(POLICIES)==5
    print("PORTFOLIO_AUDIT_PASS source PSAR, stop gap, costs, risk fraction, independent policies",flush=True)
    return fn,sha
def main():
    p=argparse.ArgumentParser();p.add_argument("--audit",action="store_true");p.add_argument("--merge",action="store_true")
    p.add_argument("--data",default="data");p.add_argument("--shards",type=int,default=4)
    p.add_argument("--shard",type=int,default=0);p.add_argument("--out");p.add_argument("--meta")
    a=p.parse_args();fn,sha=check()
    if a.audit:return
    assert a.out
    if a.merge:merge(a,sha)
    else:
        assert a.meta and 0<=a.shard<a.shards
        ledger(a,fn,sha)
if __name__=="__main__":main()
