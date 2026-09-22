import argparse, math, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd

from backtest import fetch_range, rows_to_df
from precision_backtest import calc_pf, FEE_PCT

MIN=60_000
DAY=24*60*MIN

def extend_group(g, end_ts):
    """Extend original TIME exits with 1m candles until TP/SL or dataset end.

    Non-TIME trades are unchanged. TIME variants sharing symbol/signal_ts are
    processed against the same downloaded future path.
    """
    g=g.copy()
    times=g[g["outcome"]=="TIME"].copy()
    if times.empty:
        return g, []

    symbol=str(g.iloc[0]["symbol"])
    start=int(times["exit_ts"].min())
    if start>=end_ts:
        unresolved=times.index.tolist()
        return g, unresolved

    # Pull in 1-day chunks so we can stop as soon as every variant resolves.
    active={idx:row for idx,row in times.iterrows()}
    cursor=start
    while active and cursor < end_ts:
        chunk_end=min(end_ts, cursor+DAY)
        try:
            rows=fetch_range(symbol,"1m",1,cursor,chunk_end)
            bars=rows_to_df(rows,1)
        except Exception as e:
            print(f"[WARN] {symbol} extension fetch {cursor}..{chunk_end}: {e}")
            cursor=chunk_end+MIN
            continue
        if bars.empty:
            cursor=chunk_end+MIN
            continue

        # Each original time-exit is exact through its own exit_ts; don't use
        # future bars earlier than that variant's original limit.
        resolved=[]
        for idx,row in list(active.items()):
            entry=float(row["entry_price"])
            tp=float(row["tp_pct"])
            sl=float(row["sl_pct"])
            direction=row["direction"]
            sub=bars[bars["ts"]>=int(row["exit_ts"])]
            if sub.empty:
                continue
            if direction=="LONG":
                tp_px=entry*(1+tp/100)
                sl_px=entry*(1-sl/100)
            else:
                tp_px=entry*(1-tp/100)
                sl_px=entry*(1+sl/100)

            for b in sub.itertuples(index=False):
                if direction=="LONG":
                    hit_tp=float(b.high)>=tp_px
                    hit_sl=float(b.low)<=sl_px
                else:
                    hit_tp=float(b.low)<=tp_px
                    hit_sl=float(b.high)>=sl_px
                if hit_tp and hit_sl:
                    outcome="SL"; exit_price=sl_px
                elif hit_sl:
                    outcome="SL"; exit_price=sl_px
                elif hit_tp:
                    outcome="TP"; exit_price=tp_px
                else:
                    continue
                gross=(exit_price/entry-1)*100 if direction=="LONG" else (1-exit_price/entry)*100
                g.loc[idx,"outcome"]=outcome
                g.loc[idx,"exit_price"]=exit_price
                g.loc[idx,"exit_ts"]=int(b.ts)+MIN
                g.loc[idx,"gross_pct"]=gross
                g.loc[idx,"net_pct"]=gross-FEE_PCT
                g.loc[idx,"extended_minutes"]=(int(b.ts)+MIN-int(row["exit_ts"]))//MIN
                resolved.append(idx)
                break
        for idx in resolved:
            active.pop(idx,None)
        cursor=chunk_end+MIN

    # For unresolved positions, keep them OPEN and mark to the last available
    # close only for end-of-window MTM reporting. Do not pretend they closed.
    unresolved=[]
    if active:
        try:
            rows=fetch_range(symbol,"1m",1,max(start,end_ts-DAY),end_ts)
            bars=rows_to_df(rows,1)
            end_px=float(bars.iloc[-1]["close"]) if not bars.empty else None
        except Exception:
            end_px=None
        for idx,row in active.items():
            unresolved.append(idx)
            g.loc[idx,"outcome"]="OPEN_END"
            g.loc[idx,"exit_ts"]=end_ts
            if end_px and end_px>0:
                entry=float(row["entry_price"])
                gross=(end_px/entry-1)*100 if row["direction"]=="LONG" else (1-end_px/entry)*100
                g.loc[idx,"exit_price"]=end_px
                g.loc[idx,"gross_pct"]=gross
                g.loc[idx,"net_pct"]=gross-FEE_PCT
    return g, unresolved


def simulate_cap(trades, delay, cap=2.0, frac=0.30):
    t=trades[trades.delay_min==delay].sort_values(["entry_ts","strategy","symbol"]).copy()
    eq=1.0
    reserved=0.0
    openp=[]
    peak=1.0
    maxdd=0.0
    maxopen=0
    skipped=0
    accepted=[]
    for ts,batch in t.groupby("entry_ts",sort=True):
        ts=int(ts)
        still=[]
        for p in openp:
            if p["exit_ts"]<=ts:
                eq += p["size"]*(p["net_pct"]/100)
                reserved -= p["size"]
                accepted.append(p)
            else:
                still.append(p)
        openp=still
        peak=max(peak,eq)
        maxdd=min(maxdd,eq/peak-1)
        for r in batch.itertuples(index=False):
            size=eq*frac
            if reserved+size > eq*cap+1e-12:
                skipped+=1
                continue
            p={"symbol":r.symbol,"strategy":r.strategy,"outcome":r.outcome,
               "entry_ts":int(r.entry_ts),"exit_ts":int(r.exit_ts),
               "net_pct":float(r.net_pct),"size":size}
            openp.append(p); reserved+=size
            maxopen=max(maxopen,len(openp))
    # At dataset end, OPEN_END rows are MTM values by construction; settle
    # every accepted position for comparable end-window equity.
    for p in sorted(openp,key=lambda x:x["exit_ts"]):
        eq += p["size"]*(p["net_pct"]/100)
        accepted.append(p)
        peak=max(peak,eq)
        maxdd=min(maxdd,eq/peak-1)
    a=pd.DataFrame(accepted)
    return {
        "delay_min":delay,"trades_taken":len(a),"skipped_cap":skipped,
        "return_pct":(eq-1)*100,"final_equity":eq,
        "win_rate_pct":(a.net_pct>0).mean()*100 if len(a) else math.nan,
        "profit_factor":calc_pf(a.net_pct) if len(a) else math.nan,
        "max_realized_dd_pct":maxdd*100,"max_open_positions":maxopen,
        "open_end_taken":int((a.outcome=="OPEN_END").sum()) if len(a) else 0,
    }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--trades",required=True)
    ap.add_argument("--outdir",default="no_time_results")
    ap.add_argument("--workers",type=int,default=6)
    args=ap.parse_args()
    out=Path(args.outdir); out.mkdir(parents=True,exist_ok=True)
    trades=pd.read_csv(args.trades)
    # Backtest data ends at the last signal boundary plus 15m.
    end_ts=int(trades["signal_ts"].max())+15*MIN

    groups=[]
    unresolved=[]
    keyed=list(trades.groupby(["symbol","signal_ts"],sort=False))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs={pool.submit(extend_group,g,end_ts):(sym,ts) for (sym,ts),g in keyed}
        done=0
        for f in as_completed(futs):
            key=futs[f]; done+=1
            try:
                gg,un=f.result()
                groups.append(gg)
                unresolved += [(key[0],key[1],int(i)) for i in un]
            except Exception as e:
                print(f"[ERROR] {key}: {e}")
            if done%25==0 or done==len(futs):
                print(f"[EXTEND] {done}/{len(futs)}")
    ext=pd.concat(groups).sort_index()
    ext.to_csv(out/"candidate1_no_time_trades.csv",index=False)

    rows=[]
    for d in (1,2,3):
        b=trades[trades.delay_min==d]
        n=ext[ext.delay_min==d]
        for mode,z in [("BASE_TIME_LIMIT",b),("TP_SL_ONLY",n)]:
            r=z.net_pct
            rows.append({
                "mode":mode,"delay_min":d,"n":len(z),
                "win_rate_pct":(r>0).mean()*100,
                "avg_net_pct":r.mean(),"profit_factor":calc_pf(r),
                "tp_rate_pct":(z.outcome=="TP").mean()*100,
                "sl_rate_pct":(z.outcome=="SL").mean()*100,
                "time_rate_pct":(z.outcome=="TIME").mean()*100,
                "open_end_pct":(z.outcome=="OPEN_END").mean()*100,
            })
    pd.DataFrame(rows).to_csv(out/"trade_summary.csv",index=False)

    port=[]
    for mode,z in [("BASE_TIME_LIMIT",trades),("TP_SL_ONLY",ext)]:
        for d in (1,2,3):
            r=simulate_cap(z,d,2.0,0.30)
            r["mode"]=mode
            port.append(r)
    pd.DataFrame(port).to_csv(out/"portfolio_200pct.csv",index=False)

    # What happened specifically to the trades that used to TIME out?
    changed=ext.merge(
        trades[["delay_min","symbol","strategy","signal_ts","outcome","net_pct"]],
        on=["delay_min","symbol","strategy","signal_ts"],suffixes=("_new","_old")
    )
    changed=changed[changed.outcome_old=="TIME"]
    cs=(changed.groupby(["delay_min","outcome_new"]).agg(
        n=("symbol","size"),avg_new_net=("net_pct_new","mean"),
        avg_old_time_net=("net_pct_old","mean")
    ).reset_index())
    cs.to_csv(out/"former_time_outcomes.csv",index=False)

    print("\n=== TRADE SUMMARY ===")
    print(pd.DataFrame(rows).to_string(index=False))
    print("\n=== 200% PORTFOLIO ===")
    print(pd.DataFrame(port).to_string(index=False))
    print("\n=== FORMER TIME OUTCOMES ===")
    print(cs.to_string(index=False))
    print(f"\nUnresolved at dataset end: {len(unresolved)} variants")

if __name__=="__main__":
    main()
