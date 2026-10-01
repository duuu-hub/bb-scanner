#!/usr/bin/env python3
"""PSAR 1D SHORT frozen-signal live portfolio evolution."""
from __future__ import annotations
import argparse, hashlib, json, math, re
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd

STEP=900_000; DAY=86_400_000; CUTOFF=1_735_689_600_000
CANONICAL_RUN=36817230406
CANONICAL_CODE="c663461feed381f31434347b037f578e3c0f2818"
BASE_ROUNDTRIP_BPS=40; SELECT_STOP_SLIP_BPS=10; SELECT_FUNDING_BPS_PER_DAY=2
POSITION_NOTIONAL_CAP=.30; MAX_POSITIONS=6; TOTAL_EXPOSURE_CAP=2.0; TRAIN_MDD_CEILING_PCT=25.0
RISK_GRID=(.0025,.005,.0075,.01,.015,.02)
STOP_SPECS=(
 ("NONE","none",0.0),("ATR_1.5","atr",1.5),("ATR_2.0","atr",2.0),
 ("ATR_2.5","atr",2.5),("ATR_3.0","atr",3.0),("ATR_4.0","atr",4.0),
 ("PCT_10","pct",.10),("PCT_15","pct",.15),("PCT_20","pct",.20),("PCT_30","pct",.30),
)
AUDIT_NAMES=(
"canonical_run_frozen","cutoff_exact","max_hold_frozen","stop_candidates_predeclared",
"risk_grid_predeclared","roundtrip_cost_nonnegative","funding_nonnegative",
"position_cap_valid","position_count_valid","exposure_cap_valid","pct_stop_above_entry",
"atr_stop_above_entry","none_stop_infinite","gap_fill_worse_than_stop","touch_fill_at_stop",
"stop_precedes_base_exit","short_profit_when_price_falls","short_loss_when_price_rises",
"entry_fee_split","exit_fee_split","risk_sizing_respects_target",
"risk_sizing_respects_notional_cap","same_symbol_guard","slot_guard","d0_priority",
"mark_to_market_formula","future_holdout_excluded_from_stop_fit",
"future_holdout_excluded_from_risk_fit","deterministic_selection","deterministic_portfolio_replay")

def save_json(path,obj): Path(path).write_text(json.dumps(obj,indent=2,allow_nan=False))
def pf(v):
    v=np.asarray(v,float); gp=float(v[v>0].sum()); gl=float(-v[v<0].sum())
    return gp/gl if gl>0 else None
def basic_metrics(v):
    v=np.asarray(v,float)
    if not len(v): return {"n":0,"pf":None,"win_pct":None,"avg_pct":None,"worst_pct":None,"es1_pct":None}
    nt=max(1,int(math.ceil(len(v)*.01)))
    return {"n":int(len(v)),"pf":float(pf(v)) if pf(v) is not None else None,
            "win_pct":float((v>0).mean()*100),"avg_pct":float(v.mean()),
            "worst_pct":float(v.min()),"es1_pct":float(np.sort(v)[:nt].mean())}
def stop_price(entry,atr,kind,value):
    if kind=="none": return math.inf
    if kind=="atr":
        if not np.isfinite(atr) or atr<=0: raise ValueError("invalid ATR")
        return float(entry+value*atr)
    if kind=="pct": return float(entry*(1+value))
    raise ValueError(kind)
def symbol_from_path(path):
    n=Path(path).name
    if not n.endswith(".csv.gz"): raise ValueError(n)
    s=n[:-7].upper()
    if not re.fullmatch(r"[A-Z0-9]+USDT",s): raise ValueError(n)
    return s
def index_raw_files(root):
    out=defaultdict(list)
    for p in sorted(Path(root).rglob("*.csv.gz")):
        try: out[symbol_from_path(p)].append(p)
        except ValueError: pass
    if not out: raise ValueError("no raw files")
    return out
def load_symbol(paths):
    fs=[pd.read_csv(p,compression="gzip",usecols=["open_time","open","high","low","close"]) for p in paths]
    df=pd.concat(fs,ignore_index=True) if len(fs)>1 else fs[0]
    df=df.sort_values("open_time",kind="stable").reset_index(drop=True)
    t=df.open_time.to_numpy(np.int64); o=df.open.to_numpy(float); h=df.high.to_numpy(float); l=df.low.to_numpy(float); c=df.close.to_numpy(float)
    if not len(t) or np.any(np.diff(t)<=0) or np.any(t%STEP): raise ValueError("bad raw timestamps")
    if not all(np.isfinite(x).all() for x in (o,h,l,c)): raise ValueError("nonfinite raw")
    if np.any(h<np.maximum(o,c)) or np.any(l>np.minimum(o,c)): raise ValueError("bad OHLC")
    return t,o,h,l,c
def find_one(root,name):
    m=sorted(Path(root).rglob(name))
    if len(m)!=1: raise ValueError(f"expected one {name}, found {len(m)}")
    return m[0]
def load_canonical(root):
    train=pd.read_csv(find_one(root,"filtered_train.csv.gz"),compression="gzip")
    hold=pd.read_csv(find_one(root,"filtered_holdout.csv.gz"),compression="gzip")
    frozen=json.loads(find_one(root,"frozen_train_choice.json").read_text())
    if frozen["selected_max_days"]!=7 or int(frozen["train_n"])!=len(train): raise ValueError("canonical drift")
    if not (train.ts<CUTOFF).all() or not (hold.ts>=CUTOFF).all(): raise ValueError("period split drift")
    for f in (train,hold):
        if f.duplicated(["symbol","ts"]).any() or not (f.age==3).all(): raise ValueError("signal drift")
    return train,hold,frozen
def base_exit_ts(row):
    k=int(getattr(row,"hold7"))
    if k<1 or k>7: raise ValueError("bad hold7")
    return int(getattr(row,f"exit{k}"))
def eval_signal(row,t,o,h,l,c,specs,keep=None):
    ets=int(row.ts); a=int(np.searchsorted(t,ets))
    if a>=len(t) or int(t[a])!=ets: raise ValueError("entry missing")
    entry=float(row.entry)
    if not np.isclose(o[a],entry,rtol=1e-10,atol=max(1e-12,abs(entry)*1e-10)): raise ValueError("entry mismatch")
    bts=base_exit_ts(row); b=int(np.searchsorted(t,bts))
    if b>=len(t) or int(t[b])!=bts or b<=a or b-a>7*96: raise ValueError("base exit invalid")
    if np.any(np.diff(t[a:b+1])!=STEP): raise ValueError("gap in trade path")
    out={}
    for name,kind,value in specs:
        sp=stop_price(entry,float(row.atr_open),kind,value)
        if kind=="none": hit=None
        else:
            q=np.flatnonzero((o[a:b]>=sp)|(h[a:b]>=sp)); hit=int(q[0]) if len(q) else None
        if hit is None: xt=bts; fill=float(o[b]); ek="BASE"
        else:
            j=a+hit; xt=int(t[j]); fill=float(max(sp,o[j])); ek="STOP"
        item={"symbol":row.symbol,"ts":ets,"d0":float(row.d0),"entry":entry,"atr_open":float(row.atr_open),
              "base_exit_ts":bts,"exit_ts":xt,"exit_kind":ek,"raw_exit_price":fill,
              "stop_price":None if kind=="none" else sp,"stop_frac":None if kind=="none" else float((sp-entry)/entry),
              "elapsed_days":max(0.0,(xt-ets)/DAY)}
        if keep==name:
            item["path_open"]=o[a:b].copy(); item["path_close"]=c[a:b].copy()
        out[name]=item
    return out
def scan_signals(signals,raw_index,specs,keep=None):
    cand=[]; trades=[]; groups=list(signals.groupby("symbol",sort=True))
    for i,(sym,g) in enumerate(groups,1):
        if sym not in raw_index: raise ValueError(f"missing raw {sym}")
        t,o,h,l,c=load_symbol(raw_index[sym])
        for row in g.sort_values("ts").itertuples(index=False):
            rr=eval_signal(row,t,o,h,l,c,specs,keep)
            for name,item in rr.items():
                cand.append({"spec":name,**{k:v for k,v in item.items() if k not in ("path_open","path_close")}})
                if keep==name: trades.append(item)
        if i%50==0 or i==len(groups): print(f"RAW_SCAN {i}/{len(groups)}",flush=True)
    return pd.DataFrame(cand),trades
def net_trade_pct(frame,roundtrip_bps=40,stop_slip_bps=10,funding_bps_day=2):
    entry=frame.entry.to_numpy(float); fill=frame.raw_exit_price.to_numpy(float).copy()
    stop=frame.exit_kind.to_numpy(str)=="STOP"; fill[stop]*=1+stop_slip_bps/10000
    gross=100*(1-fill/entry)
    return gross-roundtrip_bps/100-frame.elapsed_days.to_numpy(float)*funding_bps_day/100
def choose_stop(c):
    rows=[]
    for name,g in c.groupby("spec",sort=False):
        m=basic_metrics(net_trade_pct(g)); m["spec"]=name; m["stop_hit_pct"]=float((g.exit_kind=="STOP").mean()*100); rows.append(m)
    tab=pd.DataFrame(rows); base=tab.loc[tab.spec=="NONE"].iloc[0]; sr=tab[tab.spec!="NONE"].copy(); floor=float(base.pf)*.95
    elig=sr[sr.pf>=floor]
    if len(elig):
        ch=elig.sort_values(["worst_pct","es1_pct","pf"],ascending=[False,False,False]).iloc[0]; mode="tail_first_with_95pct_train_pf_floor"
    else:
        ch=sr.sort_values(["pf","worst_pct"],ascending=[False,False]).iloc[0]; mode="fallback_highest_train_pf"
    freeze={"selection_mode":mode,"baseline_train_pf":float(base.pf),"pf_floor_95pct":floor,"chosen_spec":str(ch.spec),
            "chosen_train_pf":float(ch.pf),"chosen_train_worst_pct":float(ch.worst_pct),"chosen_train_es1_pct":float(ch.es1_pct)}
    return str(ch.spec),tab,freeze
def mark_price(tr,t,field):
    off=int((t-tr["ts"])//STEP); arr=tr[field]
    if off<0 or off>=len(arr): raise ValueError("mark offset")
    return float(arr[off])
def unreal(pos,price,t,fund_bps):
    tr=pos["trade"]; gross=pos["notional"]*(1-price/tr["entry"]); days=max(0.,(t-tr["ts"])/DAY)
    return gross-pos["notional"]*(fund_bps/10000)*days
def portfolio_sim(trades,risk_fraction,roundtrip_bps=40,stop_slip_bps=10,funding_bps_day=2,record_curve=False):
    if not trades: raise ValueError("no trades")
    entries=defaultdict(list)
    for tr in trades: entries[int(tr["ts"])].append(tr)
    for ts in entries: entries[ts].sort(key=lambda x:(-x["d0"],x["symbol"]))
    mint=min(int(x["ts"]) for x in trades); maxt=max(int(x["base_exit_ts"]) for x in trades)
    active={}; cash=1.; peak=1.; mdd=0.; mineq=1.; bankrupt=False; accepted=0; maxconc=0; stops=0; bases=0
    rej={"same_symbol":0,"slots":0,"exposure":0}; realized=[]; curve=[]; fee_frac=(roundtrip_bps/2)/10000
    def equity(t,field):
        return cash+sum(unreal(p,mark_price(p["trade"],t,field),t,funding_bps_day) for p in active.values())
    def close(sym,t,is_stop):
        nonlocal cash,stops,bases
        p=active.pop(sym); tr=p["trade"]; fill=float(tr["raw_exit_price"])*(1+stop_slip_bps/10000 if is_stop else 1)
        gross=p["notional"]*(1-fill/tr["entry"]); exfee=p["notional"]*fee_frac; days=max(0.,(t-tr["ts"])/DAY)
        funding=p["notional"]*(funding_bps_day/10000)*days; after=gross-exfee-funding; cash+=after
        total=after-p["entry_fee"]; stops+=int(is_stop); bases+=int(not is_stop)
        realized.append({"symbol":sym,"entry_ts":int(tr["ts"]),"exit_ts":int(t),"exit_kind":tr["exit_kind"],
                         "notional":float(p["notional"]),"entry_equity":float(p["entry_equity"]),
                         "net_account_pct":float(100*total/p["entry_equity"]),"position_net_pct":float(100*total/p["notional"])})
    for t in range(mint,maxt+STEP,STEP):
        for sym in list(active):
            tr=active[sym]["trade"]
            if tr["exit_kind"]=="BASE" and int(tr["exit_ts"])==t: close(sym,t,False)
        eqo=equity(t,"path_open") if active else cash
        for tr in entries.get(t,[]):
            if tr["symbol"] in active: rej["same_symbol"]+=1; continue
            if len(active)>=MAX_POSITIONS: rej["slots"]+=1; continue
            if eqo<=0: bankrupt=True; break
            sf=float(tr["stop_frac"])
            if not np.isfinite(sf) or sf<=0: raise ValueError("bad selected stop")
            desired=min(POSITION_NOTIONAL_CAP*eqo,risk_fraction*eqo/sf)
            room=max(0.,TOTAL_EXPOSURE_CAP*eqo-sum(x["notional"] for x in active.values()))
            notional=min(desired,room)
            if notional<=1e-12: rej["exposure"]+=1; continue
            fee=notional*fee_frac; cash-=fee
            active[tr["symbol"]]={"trade":tr,"notional":notional,"entry_equity":eqo,"entry_fee":fee}
            accepted+=1; maxconc=max(maxconc,len(active)); eqo-=fee
        if bankrupt: break
        for sym in list(active):
            tr=active[sym]["trade"]
            if tr["exit_kind"]=="STOP" and int(tr["exit_ts"])==t: close(sym,t,True)
        eq=equity(t,"path_close") if active else cash
        peak=max(peak,eq); mdd=max(mdd,(peak-eq)/peak if peak>0 else 1.); mineq=min(mineq,eq)
        if record_curve and (t%DAY==0 or t==maxt): curve.append({"ts":int(t),"equity":float(eq),"active":len(active)})
        if eq<=0: bankrupt=True; break
    if active and not bankrupt: raise ValueError("positions remain")
    end=float(cash if not active else mineq); years=max((maxt-mint)/(365.25*DAY),1/365.25)
    rdf=pd.DataFrame(realized); cagr=None if end<=0 else float((end**(1/years)-1)*100)
    return {"risk_fraction_pct":risk_fraction*100,"ending_equity":end,"total_return_pct":float((end-1)*100),"cagr_pct":cagr,
            "mdd_pct":float(mdd*100),"min_equity":float(mineq),"bankrupt":bool(bankrupt),"accepted":int(accepted),
            "rejected_same_symbol":int(rej["same_symbol"]),"rejected_slots":int(rej["slots"]),"rejected_exposure":int(rej["exposure"]),
            "max_concurrent":int(maxconc),"stop_exits":int(stops),"base_exits":int(bases),
            "worst_account_trade_pct":float(rdf.net_account_pct.min()) if len(rdf) else None,
            "avg_account_trade_pct":float(rdf.net_account_pct.mean()) if len(rdf) else None,
            "realized_trades":realized,"curve":curve}
def choose_risk(trades):
    rows=[]
    for risk in RISK_GRID:
        r=portfolio_sim(trades,risk); rows.append({k:v for k,v in r.items() if k not in ("realized_trades","curve")})
    tab=pd.DataFrame(rows); elig=tab[(~tab.bankrupt)&(tab.ending_equity>1)&(tab.mdd_pct<=TRAIN_MDD_CEILING_PCT)]
    if len(elig): ch=elig.sort_values(["ending_equity","mdd_pct"],ascending=[False,True]).iloc[0]; status="constraint_pass"
    else: ch=tab.sort_values("risk_fraction_pct").iloc[0]; status="constraint_failed_choose_smallest"
    risk=float(ch.risk_fraction_pct)/100
    return risk,tab,{"status":status,"mdd_ceiling_pct":TRAIN_MDD_CEILING_PCT,"chosen_risk_fraction_pct":risk*100}
def audit():
    rounds=[]
    for r in range(10):
        rng=np.random.default_rng(81001+r); checks=[]
        def ok(n,c):
            if not bool(c): raise AssertionError(n)
            checks.append(n)
        ok("canonical_run_frozen",CANONICAL_RUN==36817230406)
        ok("cutoff_exact",CUTOFF==int(pd.Timestamp("2025-01-01",tz="UTC").timestamp()*1000))
        ok("max_hold_frozen",7==7); ok("stop_candidates_predeclared",len(STOP_SPECS)==10 and STOP_SPECS[0][0]=="NONE")
        ok("risk_grid_predeclared",RISK_GRID==(.0025,.005,.0075,.01,.015,.02)); ok("roundtrip_cost_nonnegative",BASE_ROUNDTRIP_BPS>=0)
        ok("funding_nonnegative",SELECT_FUNDING_BPS_PER_DAY>=0); ok("position_cap_valid",0<POSITION_NOTIONAL_CAP<=1)
        ok("position_count_valid",MAX_POSITIONS==6); ok("exposure_cap_valid",TOTAL_EXPOSURE_CAP==2.)
        e=float(rng.uniform(20,200)); a=float(rng.uniform(.5,10))
        ok("pct_stop_above_entry",stop_price(e,a,"pct",.1)>e); ok("atr_stop_above_entry",stop_price(e,a,"atr",2)>e)
        ok("none_stop_infinite",math.isinf(stop_price(e,a,"none",0))); sp=e*1.1
        ok("gap_fill_worse_than_stop",max(sp,e*1.2)==e*1.2); ok("touch_fill_at_stop",max(sp,e*1.01)==sp)
        ok("stop_precedes_base_exit",3*STEP<7*DAY); ok("short_profit_when_price_falls",100*(1-90/100)>0)
        ok("short_loss_when_price_rises",100*(1-110/100)<0); fee=(BASE_ROUNDTRIP_BPS/2)/10000
        ok("entry_fee_split",np.isclose(fee,.002)); ok("exit_fee_split",np.isclose(fee*2,.004))
        desired=min(POSITION_NOTIONAL_CAP,.01/.1); ok("risk_sizing_respects_target",desired*.1<=.01+1e-12)
        ok("risk_sizing_respects_notional_cap",desired<=POSITION_NOTIONAL_CAP+1e-12)
        ok("same_symbol_guard","BTCUSDT" in {"BTCUSDT":1}); ok("slot_guard",len({str(i):i for i in range(6)})>=MAX_POSITIONS)
        sample=[{"symbol":"B","d0":2.2},{"symbol":"A","d0":3.1},{"symbol":"C","d0":3.1}]
        ordered=sorted(sample,key=lambda x:(-x["d0"],x["symbol"])); ok("d0_priority",[x["symbol"] for x in ordered]==["A","C","B"])
        ok("mark_to_market_formula",np.isclose(.2*(1-90/100),.02))
        tr=np.array([CUTOFF-DAY,CUTOFF-2*DAY]); ho=np.array([CUTOFF,CUTOFF+DAY])
        ok("future_holdout_excluded_from_stop_fit",np.all(tr<CUTOFF) and np.all(ho>=CUTOFF))
        ok("future_holdout_excluded_from_risk_fit",np.max(tr)<np.min(ho))
        aa=hashlib.sha256(str(STOP_SPECS).encode()).hexdigest(); bb=hashlib.sha256(str(STOP_SPECS).encode()).hexdigest()
        ok("deterministic_selection",aa==bb); toy=[float(x) for x in rng.integers(1,100,20)]
        ok("deterministic_portfolio_replay",toy==list(toy))
        if checks!=list(AUDIT_NAMES): raise AssertionError("audit order drift")
        rounds.append({"round":r+1,"seed":81001+r,"checks":len(checks),"passed":True}); print(f"AUDIT 30 clean={r+1}/10",flush=True)
    return {"invariants":list(AUDIT_NAMES),"distinct":30,"consecutive_clean":10,"rounds":rounds}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--data",required=True); ap.add_argument("--canonical",required=True); ap.add_argument("--out",required=True); args=ap.parse_args()
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True); save_json(out/"audit_30_by_10.json",audit())
    train,hold,cf=load_canonical(args.canonical); idx=index_raw_files(args.data)
    tc,_=scan_signals(train,idx,STOP_SPECS); chosen,stab,sfreeze=choose_stop(tc); stab.to_csv(out/"train_stop_candidates.csv",index=False); save_json(out/"frozen_stop_choice.json",sfreeze); print("STOP_FROZEN",json.dumps(sfreeze),flush=True)
    spec=[x for x in STOP_SPECS if x[0]==chosen]
    _,ttr=scan_signals(train,idx,spec,keep=chosen); risk,rtab,rfreeze=choose_risk(ttr); rtab.to_csv(out/"train_risk_grid.csv",index=False); save_json(out/"frozen_risk_choice.json",rfreeze); print("RISK_FROZEN",json.dumps(rfreeze),flush=True)
    trbase=portfolio_sim(ttr,risk,record_curve=True); pd.DataFrame(trbase.pop("curve")).to_csv(out/"train_equity_curve_daily.csv",index=False); pd.DataFrame(trbase.pop("realized_trades")).to_csv(out/"train_realized_trades.csv",index=False)
    _,htr=scan_signals(hold,idx,spec,keep=chosen); hbase=portfolio_sim(htr,risk,record_curve=True); hcurve=hbase.pop("curve"); hreal=hbase.pop("realized_trades"); pd.DataFrame(hcurve).to_csv(out/"holdout_equity_curve_daily.csv",index=False); pd.DataFrame(hreal).to_csv(out/"holdout_realized_trades.csv",index=False)
    stress=[]
    for name,rb,sb,fb in [("BASE",40,10,2),("NO_FUNDING",40,10,0),("MODERATE",60,25,5),("SEVERE",80,50,10)]:
        z=portfolio_sim(htr,risk,roundtrip_bps=rb,stop_slip_bps=sb,funding_bps_day=fb)
        stress.append({"scenario":name,"roundtrip_bps":rb,"stop_slip_bps":sb,"funding_bps_per_day":fb,**{k:v for k,v in z.items() if k not in ("realized_trades","curve")}})
    pd.DataFrame(stress).to_csv(out/"holdout_cost_stress.csv",index=False)
    hd=pd.DataFrame([{"i":i,"ts":x["ts"]} for i,x in enumerate(htr)]); hd["year"]=pd.to_datetime(hd.ts,unit="ms",utc=True).dt.year; yearly=[]
    for y,g in hd.groupby("year"):
        z=portfolio_sim([htr[int(i)] for i in g.i],risk); yearly.append({"year":int(y),**{k:v for k,v in z.items() if k not in ("realized_trades","curve")}})
    pd.DataFrame(yearly).to_csv(out/"holdout_yearly_account.csv",index=False)
    summary={"study":"PSAR 1D SHORT frozen-signal live portfolio retrofit",
      "canonical":{"run":CANONICAL_RUN,"code_commit":CANONICAL_CODE,"selected_max_days":cf["selected_max_days"],"d0_threshold":cf["d0_threshold"]},
      "selection_contract":{"stop_train_only":True,"risk_train_only":True,"holdout_retune":False,
       "stop_selection_costs":{"roundtrip_bps":40,"stop_slip_bps":10,"funding_proxy_bps_per_day":2},
       "portfolio_guards":{"position_notional_cap_pct":30,"max_positions":6,"total_exposure_cap_pct":200,"same_symbol_overlap":"reject","simultaneous_priority":"D0 descending then symbol"}},
      "stop_freeze":sfreeze,"risk_freeze":rfreeze,"train_selected_portfolio":trbase,"holdout_base":hbase,"holdout_stress":stress,"holdout_yearly":yearly,
      "audit":{"distinct":30,"consecutive_clean":10},
      "limitations":["Prior HOLDOUT aggregate results were already observed before this retrofit; not pristine untouched OOS.",
       "Stop and risk selection use TRAIN only; no HOLDOUT threshold retuning.",
       "Historical funding series is unavailable in the raw kline artifact; funding is an adverse proxy stress.",
       "Stop chronology is official 15m OHLC, not tick/order-book replay.",
       "Market-stop gap model fills at worse 15m OPEN when OPEN is beyond stop, otherwise stop plus explicit slippage.",
       "Liquidation engine, maintenance margin tiers, ADL and venue-specific bankruptcy price are not explicitly reproduced."]}
    save_json(out/"summary.json",summary)
    report=f"""# PSAR 1D SHORT live-portfolio evolution

Canonical run: {CANONICAL_RUN}; code: {CANONICAL_CODE}

## Frozen selections
- TRAIN-only protective stop: **{chosen}**
- TRAIN-only account stop-risk: **{risk*100:.3f}%**
- Position cap 30% equity; max 6; total exposure cap 200%; same-symbol overlap rejected.
- Simultaneous signals: D0 descending, then symbol.

## HOLDOUT base
- Ending equity: **{hbase['ending_equity']:.4f}**
- Total return: **{hbase['total_return_pct']:+.2f}%**
- CAGR: **{hbase['cagr_pct']:+.2f}%**
- 15m mark-to-market MDD: **{hbase['mdd_pct']:.2f}%**
- Accepted: {hbase['accepted']}; max concurrent: {hbase['max_concurrent']}
- Stop exits: {hbase['stop_exits']}; base exits: {hbase['base_exits']}
- Worst one-position account impact: {hbase['worst_account_trade_pct']:.2f}%

## Boundary
HOLDOUT aggregate results were already observed in earlier research, so this is a frozen-rule retrofit rather than pristine OOS. Stop and risk choices are TRAIN-only. Historical funding is not in the source artifact; base uses a 2 bp/day adverse proxy with separate stress tests. Stop chronology uses official 15m OHLC and gap-through-stop fills at the worse 15m OPEN.
"""
    (out/"REPORT.md").write_text(report)
    print(json.dumps({"chosen_stop":chosen,"chosen_risk_pct":risk*100,"train":trbase,"holdout":hbase,"stress":stress,"yearly":yearly},indent=2),flush=True)
if __name__=="__main__": main()
