from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from backtest import fetch_range, rows_to_df

SYMBOLS = [
    "1MCHEEMSUSDT","AAVEUSDT","ADAUSDT","AKTUSDT","ALLOUSDT","ANKRUSDT",
    "ARKMUSDT","ARKUSDT","ASRUSDT","ATUSDT","B2USDT","BEUSDT","BNBUSDT",
    "BROCCOLIUSDT","BTCUSDT","BUSDT","CCUSDT","CHILLGUYUSDT","DEXEUSDT",
    "DIAUSDT","DOGEUSDT","EGLDUSDT","ENSOUSDT","ENSUSDT","ETHUSDT",
    "EWJUSDT","EWYUSDT","GMEUSDT","GUNUSDT","GUSDT","HOMEUSDT",
    "HYPERUSDT","HYPEUSDT","ICPUSDT","INITUSDT","JSTUSDT","KATUSDT",
    "KAVAUSDT","KITEUSDT","LAUSDT","LITEUSDT","METISUSDT","MRVLUSDT",
    "MSFTUSDT","MSTRUSDT","NAORISUSDT","NILUSDT","OPGUSDT","ORCLUSDT",
    "PARTIUSDT","PEPEUSDT","PLTRUSDT","ROBOUSDT","SKYUSDT","SOLUSDT",
    "SPELLUSDT","SQDUSDT","SQQQUSDT","SUIUSDT","SUSDT","TRUMPUSDT",
    "TRXUSDT","TSLAUSDT","TUSDT","VELODROMEUSDT","VELVETUSDT","WIFUSDT",
    "WMTUSDT","XAUTUSDT","XRPUSDT","XVSUSDT","YGGUSDT","ZILUSDT","龙虾USDT",
]

START = pd.Timestamp("2026-05-23T09:30:00Z")
END = pd.Timestamp("2026-09-20T09:30:00Z")
WARMUP_DAYS = 35
TARGET_MOM = 543
TARGET_ASL = 119
BB_N = 20
BB_K = 2.0
OUT = Path("research_output/asl1_mirror_calibration")


def fetch_15m(symbol: str) -> pd.DataFrame:
    s = int((START - pd.Timedelta(days=WARMUP_DAYS)).timestamp() * 1000)
    e = int((END + pd.Timedelta(hours=13)).timestamp() * 1000)
    rows = fetch_range(symbol, "15m", 15, s, e)
    x = rows_to_df(rows, 15)[["ts","open","high","low","close","close_ts"]].copy()
    x["dt"] = pd.to_datetime(x["ts"], unit="ms", utc=True)
    return x.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


def completed_tf(df: pd.DataFrame, minutes: int) -> tuple[np.ndarray,np.ndarray]:
    rule = f"{minutes}min"
    z = (
        df.set_index("dt")
        .resample(rule, label="left", closed="left")
        .agg(close=("close","last"))
        .dropna()
        .reset_index()
    )
    close_ts = (z["dt"] + pd.to_timedelta(minutes, unit="m")).astype("int64") // 1_000_000
    return close_ts.to_numpy(np.int64), z["close"].to_numpy(float)


def dyn_bb(live: np.ndarray, eval_ts: np.ndarray, close_ts: np.ndarray, closes: np.ndarray):
    n=len(eval_ts)
    basis=np.full(n,np.nan)
    lower=np.full(n,np.nan)
    width=np.full(n,np.nan)
    prefix=np.r_[0.0,np.cumsum(closes)]
    prefix2=np.r_[0.0,np.cumsum(closes*closes)]
    idx=np.searchsorted(close_ts, eval_ts, side="right")
    for i,j in enumerate(idx):
        if j < BB_N-1:
            continue
        a=j-(BB_N-1)
        s=prefix[j]-prefix[a]+live[i]
        s2=prefix2[j]-prefix2[a]+live[i]*live[i]
        m=s/BB_N
        var=max(0.0,s2/BB_N-m*m)
        sd=math.sqrt(var)
        basis[i]=m
        lower[i]=m-BB_K*sd
        width[i]=(4.0*sd/m*100.0) if m>0 else np.nan
    return basis,lower,width


def add_features(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    x=df.copy()
    x["price"]=x["open"].astype(float)
    eval_ts=x["ts"].to_numpy(np.int64)
    live=x["price"].to_numpy(float)

    t15=x["close_ts"].to_numpy(np.int64)
    c15=x["close"].to_numpy(float)
    b15,l15,w15=dyn_bb(live,eval_ts,t15,c15)
    t1,c1=completed_tf(x,60)
    b1,l1,w1=dyn_bb(live,eval_ts,t1,c1)
    t4,c4=completed_tf(x,240)
    b4,l4,w4=dyn_bb(live,eval_ts,t4,c4)

    x["basis15"]=b15; x["lower15"]=l15; x["width15"]=w15
    x["basis1h"]=b1; x["width1h"]=w1
    x["basis4h"]=b4; x["width4h"]=w4
    x["below_lower15"]=x["price"]<x["lower15"]
    x["below_mid1h"]=x["price"]<x["basis1h"]
    x["below_mid4h"]=x["price"]<x["basis4h"]
    x["ret4h"]=x["price"].pct_change(16)*100.0
    x["ret24h"]=x["price"].pct_change(96)*100.0

    r=x["price"].pct_change()
    rv=(r.rolling(16,min_periods=12).std(ddof=1)*math.sqrt(16)*100.0)
    # Later repo research explicitly described pre-signal RV as shifted by one snapshot.
    x["rv4h_pre"]=rv.shift(1)
    x["rv4h_now"]=rv
    prev_close=x["close"].shift(1)
    tr=pd.concat([
        (x["high"]-x["low"]).abs(),
        (x["high"]-prev_close).abs(),
        (x["low"]-prev_close).abs(),
    ],axis=1).max(axis=1)
    x["atr14"]=tr.rolling(14,min_periods=14).mean()/x["price"]*100.0
    x["symbol"]=symbol
    return x


def first_cross(mask: pd.Series) -> pd.Series:
    return mask.fillna(False) & ~mask.fillna(False).shift(1,fill_value=False)


def vol_masks(x: pd.DataFrame) -> dict[str,pd.Series]:
    return {
        "NONE": pd.Series(True,index=x.index),
        "RV4_PRE_GT_PREV1": x["rv4h_pre"] > x["rv4h_pre"].shift(1),
        "RV4_PRE_GT_PREV4": x["rv4h_pre"] > x["rv4h_pre"].shift(4),
        "RV4_PRE_GT_PREV16": x["rv4h_pre"] > x["rv4h_pre"].shift(16),
        "RV4_NOW_GT_PREV": x["rv4h_now"] > x["rv4h_now"].shift(1),
        "WIDTH15_GT_PREV": x["width15"] > x["width15"].shift(1),
        "WIDTH1H_GT_PREV": x["width1h"] > x["width1h"].shift(1),
        "WIDTH4H_GT_PREV": x["width4h"] > x["width4h"].shift(1),
        "ATR14_GT_PREV": x["atr14"] > x["atr14"].shift(1),
        "RV4_PRE_GT_MED96": x["rv4h_pre"] > x["rv4h_pre"].shift(1).rolling(96,min_periods=48).median(),
    }


def btc_flat_masks(btc: pd.DataFrame) -> dict[str,pd.Series]:
    out={}
    for a in (0.5,1.0,1.5,2.0,2.5,3.0,4.0,5.0):
        out[f"ABS4H_LE_{a:g}"]=btc["ret4h"].abs()<=a
    for a in (1.0,2.0,3.0,4.0,5.0,7.0,10.0):
        out[f"ABS24H_LE_{a:g}"]=btc["ret24h"].abs()<=a
    for a,b in ((1,3),(1,5),(1.5,3),(1.5,5),(2,3),(2,5),(2,7),(2.5,5),(3,5),(3,7)):
        out[f"ABS4H_LE_{a:g}_AND_ABS24H_LE_{b:g}"]=(btc["ret4h"].abs()<=a)&(btc["ret24h"].abs()<=b)
    # Common simple directional deadbands, included only for fingerprint matching.
    for lo,hi in ((-1,1),(-2,2),(-3,3),(-5,5),(-2,3),(-3,5)):
        out[f"RET24_{lo:g}_{hi:g}"]=(btc["ret24h"]>=lo)&(btc["ret24h"]<=hi)
    return out


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    frames={}
    failures=[]
    for i,s in enumerate(SYMBOLS,1):
        try:
            raw=fetch_15m(s)
            frames[s]=add_features(s,raw)
            print(f"[DATA] {i}/{len(SYMBOLS)} {s} rows={len(raw)}")
        except Exception as e:
            failures.append((s,str(e)))
            print(f"[ERROR] {s}: {e}")

    if "BTCUSDT" not in frames:
        raise RuntimeError("BTCUSDT missing")
    btc=frames["BTCUSDT"].copy()
    btc_idx=btc.set_index("ts")
    flat_defs=btc_flat_masks(btc)

    signal_rows=[]
    counts=[]
    for vol_name in vol_masks(next(iter(frames.values()))).keys():
        for trigger_mode in ("LOWER_CROSS","FULL_CROSS"):
            total=0
            parts=[]
            for s,x in frames.items():
                if s=="BTCUSDT":
                    pass
                v=vol_masks(x)[vol_name].fillna(False)
                base=(
                    x["below_lower15"].fillna(False)
                    & x["below_mid1h"].fillna(False)
                    & x["below_mid4h"].fillna(False)
                    & (x["ret4h"]<=-2.0).fillna(False)
                    & v
                )
                trig=(first_cross(x["below_lower15"]) & base) if trigger_mode=="LOWER_CROSS" else first_cross(base)
                trig &= (x["dt"]>=START)&(x["dt"]<=END)
                q=x.loc[trig,["symbol","ts","dt","price","ret4h","ret24h","rv4h_pre","width15","width1h","width4h","atr14"]].copy()
                if not q.empty:
                    parts.append(q)
                    total+=len(q)
            mom=pd.concat(parts,ignore_index=True) if parts else pd.DataFrame()
            counts.append({
                "vol_def":vol_name,"trigger_mode":trigger_mode,"mom_n":total,
                "mom_target_abs_error":abs(total-TARGET_MOM),
            })
            if mom.empty:
                continue
            for flat_name,flat_series in flat_defs.items():
                flat_map=pd.Series(flat_series.to_numpy(bool),index=btc["ts"].to_numpy(np.int64))
                keep=mom["ts"].map(flat_map).fillna(False).astype(bool)
                n=int(keep.sum())
                counts[-1][f"asl__{flat_name}"]=n
                # Long-form rows only for close candidates to keep output compact.
                if abs(total-TARGET_MOM)<=80 and abs(n-TARGET_ASL)<=40:
                    signal_rows.append({
                        "vol_def":vol_name,"trigger_mode":trigger_mode,
                        "flat_def":flat_name,"mom_n":total,"asl_n":n,
                        "mom_err":abs(total-TARGET_MOM),"asl_err":abs(n-TARGET_ASL),
                        "joint_err":abs(total-TARGET_MOM)+abs(n-TARGET_ASL),
                    })

    wide=pd.DataFrame(counts)
    wide.to_csv(OUT/"fingerprint_counts_wide.csv",index=False)
    close=pd.DataFrame(signal_rows)
    if close.empty:
        # Build ranked long-form from all combinations if no close rows.
        rows=[]
        for rec in counts:
            for k,v in rec.items():
                if k.startswith("asl__"):
                    rows.append({
                        "vol_def":rec["vol_def"],"trigger_mode":rec["trigger_mode"],
                        "flat_def":k[5:],"mom_n":rec["mom_n"],"asl_n":v,
                        "mom_err":rec["mom_target_abs_error"],"asl_err":abs(v-TARGET_ASL),
                        "joint_err":rec["mom_target_abs_error"]+abs(v-TARGET_ASL),
                    })
        close=pd.DataFrame(rows)
    close=close.sort_values(["joint_err","mom_err","asl_err"]).reset_index(drop=True)
    close.to_csv(OUT/"fingerprint_ranked.csv",index=False)

    meta={
        "target_mom_count":TARGET_MOM,
        "target_asl_count":TARGET_ASL,
        "period_start":START.isoformat(),
        "period_end":END.isoformat(),
        "artifact_snapshot_symbol_count":len(SYMBOLS),
        "symbols_loaded":len(frames),
        "failures":failures,
        "purpose":"Recover lost ASL1 implementation by matching historical signal-count fingerprint only; no PnL is used for definition selection.",
        "warning":"A count match is reconstruction evidence, not proof of exact original code. Mirrored SHORT must not be called exact unless fingerprint is unique/strong and original LONG metrics also reproduce."
    }
    (OUT/"meta.json").write_text(json.dumps(meta,indent=2,ensure_ascii=False),encoding="utf-8")
    print("=== META ===")
    print(json.dumps(meta,indent=2,ensure_ascii=False))
    print("\n=== TOP FINGERPRINT MATCHES ===")
    print(close.head(40).to_string(index=False))


if __name__=="__main__":
    main()
