import argparse
from pathlib import Path
import math
import numpy as np
import pandas as pd

from precision_backtest import (
    SIGNAL_COLS, load_with_extras, candidate_rank, first_cross,
    fetch_all_minutes, one_trade, calc_pf, STRATEGY_RULES, MIN, FEE_PCT
)

POSITION_FRACTION = 0.30
PRIORITY = {"L1": 1, "L2": 2, "L3": 3, "S1": 4}

# Candidate 2 (stress-tested version)
C2 = {
    "L1": {"direction":"LONG","tp":10.0,"sl":5.0,"horizon_min":720},
    "L2": {"direction":"LONG","tp":10.0,"sl":2.5,"horizon_min":60},
    "L3": {"direction":"LONG","tp":10.0,"sl":4.0,"horizon_min":720},
    "S1": {"direction":"SHORT","tp":10.0,"sl":4.0,"horizon_min":720},
}
TF_NAMES = ["1W","1D","12H","4H","1H","30M","15M"]


def build_candidate2_signals(df):
    df = df.sort_values(["symbol","ts"]).reset_index(drop=True).copy()
    df["rank"] = candidate_rank(df)
    g = df.groupby("symbol", sort=False)
    df["ret_1h"] = g["price"].pct_change(4) * 100.0
    df["ret_4h"] = g["price"].pct_change(16) * 100.0

    all_ts = np.sort(df["ts"].unique())
    cutoff = all_ts[max(0, int(len(all_ts)*0.70)-1)]
    df["split"] = np.where(df["ts"] <= cutoff, "train70", "test30")

    above = df[[f"{tf}_above" for tf in TF_NAMES]].to_numpy(dtype=int)
    only_4h_missing = (
        (df["exact_count"].to_numpy() == 6)
        & (above[:, TF_NAMES.index("4H")] == 0)
    )

    masks = {
        "L1": (df["rank"] >= 6) & (df["ret_1h"] >= 10.0),
        "L2": (df["rank"] >= 6) & (df["ret_4h"] >= 30.0),
        "L3": pd.Series(only_4h_missing, index=df.index),
        "S1": (df["rank"] == 7) & (df["ret_4h"] >= 10.0) & (df["ret_4h"] <= 35.0),
    }

    parts=[]
    for code,mask in masks.items():
        trig=first_cross(mask,df["symbol"])
        x=df.loc[trig.to_numpy(),[
            "symbol","ts","time_utc","price","split","rank","exact_count","ret_1h","ret_4h"
        ]].copy()
        cfg=C2[code]
        x["strategy"]=code
        x["direction"]=cfg["direction"]
        x["tp_pct"]=cfg["tp"]
        x["sl_pct"]=cfg["sl"]
        x["horizon_min"]=cfg["horizon_min"]
        x["priority"]=PRIORITY[code]
        parts.append(x)

    sig=pd.concat(parts,ignore_index=True).sort_values(
        ["ts","symbol","priority"]
    ).reset_index(drop=True)

    # Same symbol + same scan = one live entry; take highest-priority strategy.
    sig=sig.drop_duplicates(["symbol","ts"],keep="first").reset_index(drop=True)
    return sig


def price_at(minute_df, ts, fallback):
    row=minute_df[minute_df["ts"]==ts]
    if not row.empty:
        return float(row.iloc[-1]["close"])
    rr=minute_df[minute_df["ts"]<=ts]
    return float(rr.iloc[-1]["close"]) if not rr.empty else fallback


def simulate(trades, minute_map, delay, cap_multiple):
    t=trades[trades["delay_min"]==delay].sort_values(
        ["entry_ts","strategy","symbol"]
    ).copy()
    if t.empty:
        return {}, pd.DataFrame(), pd.DataFrame()

    entries={int(ts):g for ts,g in t.groupby("entry_ts")}
    start=int(t["entry_ts"].min())
    end=int(t["exit_ts"].max())

    balance=1.0
    open_pos=[]
    accepted=[]
    curve=[]
    peak_equity=1.0
    max_dd=0.0
    worst_snapshot=None
    skipped=0
    max_open=0
    max_losing=0
    max_longs=0
    max_shorts=0
    max_gross_ratio=0.0
    worst_unreal_pct=0.0
    worst_unreal_snapshot=None

    def mark(ts):
        unreal=0.0
        details=[]
        for p in open_pos:
            m=minute_map[p["symbol"]]
            px=price_at(m,ts,p["entry_price"])
            ret=(px/p["entry_price"]-1.0) if p["direction"]=="LONG" else (1.0-px/p["entry_price"])
            pnl=p["size"]*ret
            unreal+=pnl
            details.append((p,px,ret,pnl))
        return balance+unreal, unreal, details

    for ts in range(start,end+MIN,MIN):
        # Realize positions whose TP/SL/time exit has occurred.
        still=[]
        for p in open_pos:
            if p["exit_ts"]<=ts:
                balance += p["size"]*(p["net_pct"]/100.0)
                accepted.append(p)
            else:
                still.append(p)
        open_pos=still

        # New entries at this minute; 30% of current MTM equity each.
        batch=entries.get(ts)
        if batch is not None:
            for r in batch.itertuples(index=False):
                eq,_,_=mark(ts)
                size=max(0.0,eq*POSITION_FRACTION)
                reserved=sum(p["size"] for p in open_pos)
                if eq<=0 or reserved+size > eq*cap_multiple+1e-12:
                    skipped += 1
                    continue
                p={
                    "symbol":r.symbol,"strategy":r.strategy,"direction":r.direction,
                    "split":r.split,"entry_ts":int(r.entry_ts),"exit_ts":int(r.exit_ts),
                    "entry_price":float(r.entry_price),"exit_price":float(r.exit_price),
                    "net_pct":float(r.net_pct),"outcome":r.outcome,"size":size,
                }
                open_pos.append(p)

        eq,unreal,details=mark(ts)
        peak_equity=max(peak_equity,eq)
        dd=(eq/peak_equity-1.0) if peak_equity>0 else -1.0
        reserved=sum(p["size"] for p in open_pos)
        gross_ratio=reserved/eq if eq>0 else math.inf
        losing=[d for d in details if d[2]<0]
        longs=sum(1 for p in open_pos if p["direction"]=="LONG")
        shorts=len(open_pos)-longs

        max_open=max(max_open,len(open_pos))
        max_losing=max(max_losing,len(losing))
        max_longs=max(max_longs,longs)
        max_shorts=max(max_shorts,shorts)
        max_gross_ratio=max(max_gross_ratio,gross_ratio if math.isfinite(gross_ratio) else 999.0)

        unreal_pct=(unreal/eq*100.0) if eq>0 else -999.0
        if unreal_pct < worst_unreal_pct:
            worst_unreal_pct=unreal_pct
            worst_unreal_snapshot={
                "ts":ts,"equity":eq,"unreal":unreal,"open":len(open_pos),
                "losing":len(losing),"longs":longs,"shorts":shorts,
                "gross_ratio":gross_ratio,"details":details.copy()
            }

        if dd < max_dd:
            max_dd=dd
            worst_snapshot={
                "ts":ts,"equity":eq,"peak":peak_equity,"open":len(open_pos),
                "losing":len(losing),"longs":longs,"shorts":shorts,
                "gross_ratio":gross_ratio,"details":details.copy()
            }

        curve.append({
            "ts":ts,"equity":eq,"balance":balance,"open_positions":len(open_pos),
            "losing_positions":len(losing),"longs":longs,"shorts":shorts,
            "gross_exposure_multiple":gross_ratio,
            "drawdown_pct":dd*100.0,
            "unrealized_pnl_equity_pct":unreal_pct,
        })

    # settle any residue
    for p in open_pos:
        balance += p["size"]*(p["net_pct"]/100.0)
        accepted.append(p)

    acc=pd.DataFrame(accepted)
    curve_df=pd.DataFrame(curve)

    def snapshot_rows(label,snap):
        rows=[]
        if not snap: return rows
        for p,px,ret,pnl in snap["details"]:
            rows.append({
                "snapshot":label,"delay_min":delay,"cap_multiple":cap_multiple,
                "ts":snap["ts"],"equity":snap["equity"],"open_positions":snap["open"],
                "losing_positions":snap["losing"],"longs":snap["longs"],"shorts":snap["shorts"],
                "gross_exposure_multiple":snap["gross_ratio"],
                "symbol":p["symbol"],"strategy":p["strategy"],"direction":p["direction"],
                "entry_price":p["entry_price"],"mark_price":px,
                "position_return_pct":ret*100.0,
                "position_size_equity_units":p["size"],
                "position_pnl_equity_units":pnl,
            })
        return rows

    snapshots=pd.DataFrame(
        snapshot_rows("MAX_DD",worst_snapshot)
        + snapshot_rows("WORST_UNREAL",worst_unreal_snapshot)
    )

    row={
        "delay_min":delay,"cap_multiple":cap_multiple,
        "slot_equivalent":int(math.floor(cap_multiple/POSITION_FRACTION+1e-9)),
        "signals_available":len(t),"trades_taken":len(acc),"skipped_cap":skipped,
        "win_rate_pct":(acc["net_pct"]>0).mean()*100.0 if not acc.empty else np.nan,
        "profit_factor":calc_pf(acc["net_pct"]) if not acc.empty else np.nan,
        "final_equity_multiple":balance,"return_pct":(balance-1.0)*100.0,
        "max_drawdown_pct":max_dd*100.0,
        "max_open_positions":max_open,"max_losing_positions":max_losing,
        "max_simultaneous_longs":max_longs,"max_simultaneous_shorts":max_shorts,
        "max_observed_gross_exposure_multiple":max_gross_ratio,
        "worst_unrealized_pnl_equity_pct":worst_unreal_pct,
        "ever_equity_le_zero":bool((curve_df["equity"]<=0).any()),
        "minutes_4plus_losing":int((curve_df["losing_positions"]>=4).sum()),
        "minutes_6plus_losing":int((curve_df["losing_positions"]>=6).sum()),
        "minutes_8plus_losing":int((curve_df["losing_positions"]>=8).sum()),
    }
    return row,snapshots,curve_df


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",required=True)
    ap.add_argument("--outdir",default="exposure_risk_results")
    ap.add_argument("--workers",type=int,default=6)
    ap.add_argument("--extra-symbols",default="LSKUSDT,TUTUSDT,LABUSDT,ALLOUSDT")
    args=ap.parse_args()

    out=Path(args.outdir); out.mkdir(parents=True,exist_ok=True)
    source,added,fail=load_with_extras(args.source,args.extra_symbols)
    signals=build_candidate2_signals(source)
    signals.to_csv(out/"candidate2_signals.csv",index=False)
    print(f"[C2] symbols={source['symbol'].nunique()} signals={len(signals)} added={added} extra_fail={fail}")

    minute_map,fetch_fail=fetch_all_minutes(signals,args.workers)
    print(f"[1M] fetch_failures={len(fetch_fail)}")

    rows=[]
    for sig in signals.itertuples(index=False):
        m=minute_map.get(sig.symbol)
        for delay in (1,2,3):
            tr=one_trade(sig,m,delay)
            if tr:
                rows.append(tr)
    trades=pd.DataFrame(rows)
    trades.to_csv(out/"candidate2_trades.csv",index=False)

    sums=[]; snaps=[]; curves=[]
    for delay in (1,2,3):
        for cap in (1.0,2.0,3.0):
            row,snap,curve=simulate(trades,minute_map,delay,cap)
            sums.append(row)
            if not snap.empty: snaps.append(snap)
            curve["delay_min"]=delay; curve["cap_multiple"]=cap
            curves.append(curve)
    summary=pd.DataFrame(sums)
    summary.to_csv(out/"exposure_summary.csv",index=False)
    if snaps: pd.concat(snaps,ignore_index=True).to_csv(out/"worst_snapshots.csv",index=False)
    pd.concat(curves,ignore_index=True).to_csv(out/"equity_curves.csv.gz",index=False,compression="gzip")

    print("\n=== EXPOSURE SUMMARY ===")
    print(summary.to_string(index=False))


if __name__=="__main__":
    main()
