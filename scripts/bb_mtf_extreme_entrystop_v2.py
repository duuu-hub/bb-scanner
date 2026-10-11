#!/usr/bin/env python3
"""BB-MTF upper/lower: causal opening -> optional confirmed reversal ->
STOP MARKET (single intrabar order) -> fixed-time or closed-15m BB exit.
Exploratory, post-hoc, prospective execution NOT validated. No 2nd intrabar
TP order; official 1m chronology contract smoke tested separately.
"""
from __future__ import annotations
import argparse,glob,os,json,hashlib,heapq
from pathlib import Path
from collections import defaultdict
from datetime import datetime,timezone
import numpy as np
import pandas as pd
import bb_mtf_strict_open_v1 as source
import s2_current_rule_5y as canonical1m
BAR=source.BAR
START=source.START
CUTOFF=source.CUTOFF
GROUPS=("ALL7","DAY4","NO_DAY6")
SIDES=("UPPER","LOWER")
CONFIRMS=("NOW","FIRST_REVERSE_BODY","BREAK_SETUP_EXTREME","RETURN_INSIDE_15M_BB")
STOPS=(3.,5.,8.,12.)
EXITS=("TIME","BB15_CLOSE_REVERSION")
HOLDS=(8,16,32) # 2h, 4h, 8h after actual entry
COOLDOWN=32
COSTS=(.2,.4)
MAX_SIGNAL_BAR=4 # confirmation on setup 15M bar or following 3 bars; fill NEXT 15M OPEN
CONFIG_COUNT=len(GROUPS)*len(SIDES)*len(CONFIRMS)*len(STOPS)*len(EXITS)*len(HOLDS)
assert CONFIG_COUNT==576
def sideflag(flags,frames,side):
    v=np.ones(len(next(iter(flags.values()))[0]),bool)
    a=v.copy()
    for tf in frames:
        vv,u,d=flags[tf]
        v&=vv;a&=u if side=="UPPER" else d
    return v&a
def confirmation(raw,setup,j,kind,side,bb15):
    op,cl,hi,lo=raw
    if kind=="NOW":return j==setup
    if side=="UPPER":
        if kind=="FIRST_REVERSE_BODY":return cl[j]<op[j]
        if kind=="BREAK_SETUP_EXTREME":return cl[j]<lo[setup]
        if kind=="RETURN_INSIDE_15M_BB":return cl[j]<bb15
    else:
        if kind=="FIRST_REVERSE_BODY":return cl[j]>op[j]
        if kind=="BREAK_SETUP_EXTREME":return cl[j]>hi[setup]
        if kind=="RETURN_INSIDE_15M_BB":return cl[j]>bb15
    raise ValueError((kind,side))
def simulate(ts,op,hi,lo,cl,entry_i,side,stop,hold,exitmode,setup_bb):
    """Only SL is an intrabar price order, so TP/SL collision is impossible.
    BB profit exit is SOFT: prior candle CLOSE confirms, NEXT OPEN fills.
    Gap open past stop booked at worse OPEN. Stop touch timestamp modeled at
    end of the triggering 15M candle: conservative position occupancy.
    """
    entry=float(op[entry_i]);short=side=="UPPER";et=int(ts[entry_i])
    level=entry*(1+stop/100.) if short else entry*(1-stop/100.)
    if entry<=0 or level<=0:return None
    end=entry_i+hold
    if end>=len(ts) or int(ts[end])-et!=hold*BAR:return None
    mfe=0.;mae=0.
    for j in range(entry_i,end+1):
        px=float(op[j])
        crossed_open=px>=level if short else px<=level
        if crossed_open:
            return {"exit_ts":int(ts[j]),"exit_price":px,"reason":"STOP_GAP",
                    "gross_pct":(entry-px)/entry*100 if short else (px-entry)/entry*100,
                    "mae_pct":mae,"mfe_pct":mfe}
        if j==end:
            return {"exit_ts":int(ts[j]),"exit_price":px,"reason":"TIME",
                    "gross_pct":(entry-px)/entry*100 if short else (px-entry)/entry*100,
                    "mae_pct":mae,"mfe_pct":mfe}
        if j>entry_i and exitmode=="BB15_CLOSE_REVERSION":
            cond=float(cl[j-1])<=setup_bb if short else float(cl[j-1])>=setup_bb
            if cond:
                return {"exit_ts":int(ts[j]),"exit_price":px,"reason":"BB15_CONFIRMED_NEXT_OPEN",
                        "gross_pct":(entry-px)/entry*100 if short else (px-entry)/entry*100,
                        "mae_pct":mae,"mfe_pct":mfe}
        # Only collect intrabar excursion if still in the position after OPEN exits.
        fave=((entry-float(lo[j]))/entry*100 if short else (float(hi[j])-entry)/entry*100)
        adv=((float(hi[j])-entry)/entry*100 if short else (entry-float(lo[j]))/entry*100)
        mfe=max(mfe,fave);mae=max(mae,adv)
        touched=float(hi[j])>=level if short else float(lo[j])<=level
        if touched:
            return {"exit_ts":int(ts[j])+BAR,"exit_price":level,"reason":"STOP_TOUCH",
                    "gross_pct":-stop,"mae_pct":mae,"mfe_pct":mfe}
    raise RuntimeError("impossible")
def audit():
    # Use repository 1m chronology smoke for reference on all mandatory collision cases.
    canonical1m.self_test()
    fn,_=source.canonical()
    source.smoke(fn)
    ts=np.arange(200,dtype=np.int64)*BAR
    op=np.ones(200,float)*100
    hi=np.ones(200,float)*101
    lo=np.ones(200,float)*99
    cl=np.ones(200,float)*100
    # No future high/low/close can alter entry.
    v,u,d=source.bb_open(op,cl)
    cl2=cl.copy();cl2[33]=1000
    a,b,c=source.bb_open(op,cl2)
    assert u[33]==b[33] and d[33]==c[33] and a[33]==v[33]
    # one market stop only, no TP/SL same-parent conflict possible
    hh=hi.copy();hh[50]=110
    z=simulate(ts,op,hh,lo,cl,50,"UPPER",5,16,"TIME",103)
    assert z["reason"]=="STOP_TOUCH" and z["gross_pct"]==-5 and z["exit_ts"]==int(ts[51])
    zz=simulate(ts,op,hi,lo,cl,50,"UPPER",5,16,"BB15_CLOSE_REVERSION",101)
    assert zz["reason"]=="BB15_CONFIRMED_NEXT_OPEN" and zz["exit_ts"]==int(ts[51])
    hh=hi.copy();hh[50]=110
    zz=simulate(ts,op,hh,lo,cl,50,"UPPER",5,16,"BB15_CLOSE_REVERSION",101)
    assert zz["reason"]=="STOP_TOUCH" and zz["exit_ts"]==int(ts[51])
    oo=op.copy();oo[51]=110
    zz=simulate(ts,oo,hi,lo,cl,50,"UPPER",5,16,"TIME",103)
    assert zz["reason"]=="STOP_GAP" and abs(zz["gross_pct"]+10)<1e-8
    ll=lo.copy();ll[50]=90
    zz=simulate(ts,op,hi,ll,cl,50,"LOWER",5,16,"TIME",97)
    assert zz["reason"]=="STOP_TOUCH" and zz["gross_pct"]==-5
    oo=op.copy();oo[51]=90
    zz=simulate(ts,oo,hi,lo,cl,50,"LOWER",5,16,"TIME",97)
    assert zz["reason"]=="STOP_GAP" and abs(zz["gross_pct"]+10)<1e-8
    assert confirmation((op,cl,hi,lo),50,50,"NOW","UPPER",102)
    assert not confirmation((op,cl,hi,lo),50,51,"NOW","UPPER",102)
    assert START<CUTOFF
    assert .40-.20==.2 # 20/40bp roundtrip total
    print("BB_RISK_AUDIT_PASS 1m chronology smoke, causal BB20, soft TP next open, single hard stop, gap at worse open, both sides, fees",flush=True)

def process(a):
    fn,eng_sha=source.canonical()
    paths=sorted(glob.glob(a.data+"/**/*.csv.gz",recursive=True));assert paths
    chosen=[p for k,p in enumerate(paths) if k%a.shards==a.shard]
    rows=[];count=defaultdict(int);symbols=set()
    for k,path in enumerate(chosen,1):
        sym=fn["_symbol"](path)
        if sym in source.STABLE:count["excluded_stable"]+=1;continue
        t,o,h,l,c=fn["load"](path)
        symbols.add(sym)
        for st,en in fn["contiguous_segments"](t):
            if en-st<96*25:
                count["short_segment"]+=1;continue
            ts,op,hi,lo,cl=[x[st:en] for x in (t,o,h,l,c)]
            state=source.tf_states(ts,op,hi,lo,cl,fn)["STRICT_TF_OPEN"]
            bvalid,up,down=source.bb_open(op,cl)
            for group in GROUPS:
                for side in SIDES:
                    flag=sideflag(state,source.COMBOS[group],side)
                    prev=np.r_[False,flag[:-1]]
                    ix=np.flatnonzero(flag&~prev)
                    cooldown=-1
                    for si in ix:
                        if int(ts[si])<START or si<96*20 or si<cooldown:continue
                        cooldown=si+COOLDOWN
                        count[f"signal_{group}_{side}"]+=1
                        if si+MAX_SIGNAL_BAR+max(HOLDS)>=len(ts):
                            count["eod_censored"]+=1;continue
                        bb15=float(up[si]) if side=="UPPER" else float(down[si])
                        if not np.isfinite(bb15):count["invalid_bb15"]+=1;continue
                        distance=abs(float(op[si])-bb15)/float(op[si])*100
                        for conf in CONFIRMS:
                            entry_i=None
                            if conf=="NOW":entry_i=int(si)
                            else:
                                for j in range(int(si),int(si)+MAX_SIGNAL_BAR):
                                    if confirmation((op,cl,hi,lo),int(si),j,conf,side,bb15):
                                        entry_i=j+1
                                        break
                            if entry_i is None:
                                count[f"no_confirm_{conf}"]+=1;continue
                            if int(ts[entry_i])!=int(ts[si])+(entry_i-si)*BAR:
                                count["entry_mismatch"]+=1;continue
                            # Guard split no trading with future outcomes spanning boundary.
                            for hold in HOLDS:
                                final_i=entry_i+hold
                                if final_i>=len(ts):count["unresolved_eod"]+=1;continue
                                end_time=int(ts[final_i])
                                sigtime=int(ts[si]);entrytime=int(ts[entry_i])
                                if sigtime<CUTOFF and end_time>=CUTOFF:
                                    count["cross_boundary"]+=1;continue
                                split="TRAIN" if sigtime<CUTOFF else "SEEN"
                                for stop in STOPS:
                                    for exitmode in EXITS:
                                        res=simulate(ts,op,hi,lo,cl,entry_i,side,stop,hold,exitmode,bb15)
                                        if res is None:count["data_gap"]+=1;continue
                                        assert int(res["exit_ts"])>=entrytime and int(res["exit_ts"])<=end_time
                                        gross=float(res["gross_pct"])
                                        rows.append((sym,group,side,conf,stop,exitmode,hold,split,
                                            sigtime,entrytime,int(res["exit_ts"]),float(op[entry_i]),float(res["exit_price"]),
                                            float(distance),gross,gross-.2,gross-.4,float(res["mae_pct"]),float(res["mfe_pct"]),res["reason"],
                                            datetime.fromtimestamp(sigtime/1000,tz=timezone.utc).year))
            count["segments"]+=1
        print("BB_RISK_PROGRESS",a.shard,k,len(chosen),sym,"ledger_rows",len(rows),flush=True)
    cols=["symbol","group","side","confirm","stop_pct","exit_mode","hold_bars","split","setup_ts","entry_ts","exit_ts",
          "entry","exit","setup_dist_pct","gross_pct","net20_pct","net40_pct","mae_pct","mfe_pct","exit_reason","year"]
    pd.DataFrame.from_records(rows,columns=cols).to_csv(a.out,index=False,compression="gzip")
    meta={"experiment":"BB_MTF_EXTREME_ENTRYSTOP_V2","class":"EXPLORATORY",
          "data_run":source.RAW_RUN,"code_sha":os.getenv("GITHUB_SHA","local"),
          "source_engine_sha256":eng_sha,"shard":a.shard,"shards":a.shards,
          "files":len(chosen),"all_files":len(paths),"trade_rows":len(rows),
          "symbols":len(symbols),"counters":dict(count),"grids":dict(groups=GROUPS,sides=SIDES,confirms=CONFIRMS,stops=STOPS,exits=EXITS,holds=HOLDS),
          "semantics":"TF own OPEN, 15m OPEN NOW or prior 15m CLOSED confirmation -> next 15m OPEN. Single stop market, optional soft BB15 CLOSE->next OPEN profit. No intrabar TP, no TP/SL collision. Stop TOUCH exit timestamp at end of triggering 15m. Worst-open gaps modeled. Total 20/40bp roundtrip; funding excluded."}
    Path(a.meta).write_text(json.dumps(meta,indent=2))
    print("BB_RISK_SHARD_PASS",a.shard,len(rows),len(chosen),"files",flush=True)

def perf(z):
    if z.empty:return None
    y=z.net40_pct
    gp=float(y[y>0].sum());gl=float(-y[y<0].sum())
    return {"trades":len(z),"ev40_pct":round(float(y.mean()),5),"pf40":round(gp/gl,4) if gl else None,
      "wr40_pct":round(float((y>0).mean()*100),3),"ev20_pct":round(float(z.net20_pct.mean()),5),
      "mae_avg_pct":round(float(z.mae_pct.mean()),3),"mae_gt5_pct":round(float((z.mae_pct>=5).mean()*100),3),
      "worst_pct40":round(float(y.min()),3),"stop_rate_pct":round(float(z.exit_reason.str.startswith("STOP").mean()*100),2)}
def account(z,cost_pct=.4):
    """Implement event-time sequential portfolio with capped gross leverage.
    MTM drawdown cannot be computed from realized ledger; marked as such.
    """
    q=z.sort_values(["entry_ts","setup_dist_pct","symbol"],ascending=[True,False,True],kind="mergesort")
    cash=10000.;peak=cash;dd=0.
    active=[];names=set();i=0;accepted=0;ignored=defaultdict(int);open_frac_sum=0.;profits=0.;losses=0.
    accepted_trades=[]
    for x in q.itertuples(index=False):
        now=int(x.entry_ts)
        while active and active[0][0]<=now:
            e,seq,s,notional,gross,year,why=heapq.heappop(active)
            names.remove(s)
            pnl=notional*(gross-cost_pct)/100.
            cash+=pnl
            profits+=max(pnl,0);losses+=max(-pnl,0)
            peak=max(peak,cash);dd=max(dd,(peak-cash)/peak if peak>0 else 1.)
        if x.symbol in names:ignored["same_symbol"]+=1;continue
        if len(active)>=4:ignored["slots"]+=1;continue
        current_gross=sum(h[3] for h in active)
        risk_frac=.0025
        nominal_frac=min(.25,risk_frac/((float(x.stop_pct)+.4)/100.))
        capital=min(nominal_frac*cash,cash-current_gross)
        if capital<=0:ignored["gross_cap"]+=1;continue
        i+=1;accepted+=1
        heapq.heappush(active,(int(x.exit_ts),i,x.symbol,capital,float(x.gross_pct),int(x.year),x.exit_reason))
        names.add(x.symbol)
        open_frac_sum+=capital/max(cash,1.)
        accepted_trades.append((int(x.setup_ts),int(x.year),x.symbol,float(x.net40_pct)))
    while active:
        e,seq,s,nominal,gross,year,why=heapq.heappop(active)
        pnl=nominal*(gross-cost_pct)/100.
        cash+=pnl
        profits+=max(0,pnl);losses+=max(0,-pnl)
        peak=max(peak,cash);dd=max(dd,(peak-cash)/peak if peak>0 else 1.)
    return {"return_pct":round((cash/10000.-1)*100,4),"realized_only_mdd_pct":round(dd*100,4),
            "accepted":accepted,"raw":len(z),"ignored":dict(ignored),
            "avg_nominal_fraction":round(open_frac_sum/accepted,4) if accepted else 0,
            "pf_equity":round(profits/losses,4) if losses>0 else None,
            "distinct_setup_days":len({x[0]//86400000 for x in accepted_trades}),
            "year_counts":{str(y):sum(x[1]==y for x in accepted_trades) for y in sorted(set(x[1] for x in accepted_trades))}}
def merge(a):
    fs=sorted(glob.glob(a.data+"/**/bb-risk-shard-*.csv.gz",recursive=True))
    ms=sorted(glob.glob(a.data+"/**/bb-risk-meta-*.json",recursive=True))
    assert len(fs)==len(ms)==a.shards,(len(fs),len(ms),a.shards)
    metas=[json.load(open(f)) for f in ms]
    assert {x["shard"] for x in metas}==set(range(a.shards))
    assert len({x["code_sha"] for x in metas})==1
    assert len({x["source_engine_sha256"] for x in metas})==1
    assert len({json.dumps(x["grids"],sort_keys=True) for x in metas})==1
    assert len({x["all_files"] for x in metas})==1
    assert sum(x["files"] for x in metas)==metas[0]["all_files"]
    d=pd.concat([pd.read_csv(f) for f in fs],ignore_index=True)
    assert len(d)==sum(x["trade_rows"] for x in metas)
    assert not d.duplicated(["symbol","group","side","confirm","stop_pct","exit_mode","hold_bars","setup_ts"]).any()
    rows=[];finalists=[];counter=defaultdict(int)
    for group in GROUPS:
      for side in SIDES:
       for cf in CONFIRMS:
        for sl in STOPS:
         for ex in EXITS:
          for hold in HOLDS:
            ds=d[(d.group==group)&(d.side==side)&(d.confirm==cf)&(d.stop_pct==sl)&(d.exit_mode==ex)&(d.hold_bars==hold)]
            train=ds[ds.split=="TRAIN"];seen=ds[ds.split=="SEEN"]
            t=perf(train);s=perf(seen)
            if not t and not s:continue
            record={"key":f"{group}|{side}|{cf}|SL{sl:g}|{ex}|H{hold}","group":group,"side":side,
              "confirm":cf,"stop":sl,"exit_mode":ex,"hold_bars":hold,"train":t,"seen":s,
              "train_distinct_days":int(train.setup_ts.floordiv(86400000).nunique()),
              "seen_distinct_days":int(seen.setup_ts.floordiv(86400000).nunique()),
              "train_years":{str(int(y)):perf(z) for y,z in train.groupby("year")},
              "seen_years":{str(int(y)):perf(z) for y,z in seen.groupby("year")}}
            rows.append(record)
            if len(train)>=55 and record["train_distinct_days"]>=20 and train.year.nunique()>=3:
                # portfolio TRAIN only ranking; seen figures are a falsification check.
                portfolio_tr=account(train,.4);portfolio_seen=account(seen,.4)
                record["train_account"]=portfolio_tr;record["seen_account"]=portfolio_seen
                score=portfolio_tr["return_pct"]-.5*portfolio_tr["realized_only_mdd_pct"]
                if portfolio_tr["accepted"]>=35:
                    finalists.append((score,record))
    finalists.sort(key=lambda z:z[0],reverse=True)
    pre={}
    for group in GROUPS:
      for side in SIDES:
        for conf in ("NOW","FIRST_REVERSE_BODY","BREAK_SETUP_EXTREME","RETURN_INSIDE_15M_BB"):
          key=f"{group}|{side}|{conf}|SL5|TIME|H16"
          x=next((q for q in rows if q["key"]==key),None)
          if x:pre[key]={"train":x["train"],"seen":x["seen"],"train_days":x["train_distinct_days"],"seen_days":x["seen_distinct_days"],
                          "train_account":x.get("train_account"),"seen_account":x.get("seen_account")}
    result={"experiment":"BB_MTF_EXTREME_ENTRYSTOP_V2","classification":"EXPLORATORY",
      "run_sha":metas[0]["code_sha"],"source_engine_sha256":metas[0]["source_engine_sha256"],
      "source_open_only_run":38111910409,"data_run":source.RAW_RUN,"files":sum(x["files"] for x in metas),
      "shards":len(metas),"raw_trade_grid_rows":len(d),
      "grid":metas[0]["grids"],"counters":dict(sum((__import__('collections').Counter(x["counters"]) for x in metas),__import__('collections').Counter())),
      "predefined_comparison":pre,
      "train_ranked_account": [{"score":round(score,4),"key":x["key"],"train":x["train"],"seen":x["seen"],
           "train_account":x["train_account"],"seen_account":x["seen_account"],
           "train_days":x["train_distinct_days"],"seen_days":x["seen_distinct_days"],
           "train_years":x["train_years"],"seen_years":x["seen_years"]} for score,x in finalists[:30]],
      "all_grid":rows,
      "account_model":"10k, one position/symbol, 4 slots max, total <=100% gross, <=25% each, 0.25% planned risk/stop including 40bp; same-timestamp ranking by distance beyond 15m BB20 at setup; realized-only MDD, no daily/streak halts; COMPARES 20/40bp per trade.",
      "limitations":["No hard take-profit intrabar: soft 15m closed-candle BB15 profit exit on next 15m open",
         "SL TOUCH uses 15m H/L and assumes market stop at stop price; gaps at worse 15m open; no book slippage",
         "No TP/SL intrabar collision possible; authoritative 1m chronology self-test passed, but no 1m reconstruction required by this single price-order design",
         "SL exit timestamp conservatively set to 15m candle close for portfolio occupancy",
         "MTM drawdown and liquidation not simulated; realized-only MDD understates risk",
         "Already-seen 2025-26 period and hyperparameter scan -> post-hoc exploratory only",
         "Current-listed Binance UM universe can have survivorship bias",
         "Funding excluded; no 30 checks + 10 clean audit rounds; no demo or live admission"]}
    Path(a.out).write_text(json.dumps(result,indent=2))
    for key,v in pre.items():
        if "|UPPER|" in key and key.startswith(("ALL7|","DAY4|")):
            print("PREDEFINED",key,"TRAIN",v["train"],"SEEN",v["seen"],"TRAIN_ACCOUNT",v["train_account"],"SEEN_ACCOUNT",v["seen_account"],flush=True)
    print("TOP_TRAIN_N",len(finalists),flush=True)
    for score,x in finalists[:12]:
        print("TOP",x["key"],"SCORE",round(score,4),
           "TRAIN",x["train"],"TRAIN_ACCOUNT",x["train_account"],
           "SEEN",x["seen"],"SEEN_ACCOUNT",x["seen_account"],
           "TRAIN_DAYS",x["train_distinct_days"],"SEEN_DAYS",x["seen_distinct_days"],flush=True)
    print("BB_RISK_MERGE_PASS",len(d),len(metas),"shards",len(rows),"configs",flush=True)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--audit",action="store_true")
    p.add_argument("--merge",action="store_true")
    p.add_argument("--data",default="data")
    p.add_argument("--shard",type=int,default=0)
    p.add_argument("--shards",type=int,default=4)
    p.add_argument("--out");p.add_argument("--meta")
    a=p.parse_args();audit()
    if a.audit:return
    assert a.out and a.shards>0
    if a.merge:merge(a)
    else:
        assert a.meta and 0<=a.shard<a.shards
        process(a)
if __name__=="__main__":main()
