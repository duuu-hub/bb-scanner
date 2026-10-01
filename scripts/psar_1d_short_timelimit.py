"""Frozen PSAR 1D SHORT study; independent signals, not account trades."""
import argparse
from collections import Counter
import glob
import hashlib
import json
import os
import re

import numpy as np
import pandas as pd
from psar_1d_exec_helpers import (
    _ONE_MIN_CACHE, _resolve_1m, contiguous_segments, load,
    psar_open_projection, resample,
)

V = (2.75, 1.2, .75)
LIMITS = (1, 3, 7, 14)
BURN = 100
STEP = 900000
DAY = 86400000
CUT = 1735689600000
THRESHOLD = 10.174524905858567
EXCLUDED = {"data_gap", "entry_mismatch", "exit_mismatch", "source_mismatch"}
COLUMNS = [
    "symbol", "signal_ts", "fill_ts", "exit_ts", "limit_days", "outcome",
    "pnl_pct", "atr_pct", "stop_pct", "order", "fill", "sl", "tp",
    "exit_price", "psar_ref", "atr_open", "signal_open", "entry_target",
    "input_cutoff_ts",
]


def sym(path):
    name = os.path.basename(path)
    if not name.endswith(".csv.gz"):
        raise ValueError("unexpected source suffix")
    symbol = name[:-7].upper()
    if not re.fullmatch(r"[A-Z0-9]+USDT", symbol):
        raise ValueError("invalid symbol")
    return symbol


def prior_atr(rh, rl, rc):
    prev = np.r_[np.nan, rc[:-1]]
    tr = np.maximum(rh - rl, np.maximum(np.abs(rh-prev), np.abs(rl-prev)))
    closed = pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()
    return np.r_[np.nan, closed[:-1]]


def resolve_limits(t, h, l, c, fs, fill, tp, sl, symbol, taker,
                   limits=LIMITS, diagnostics=None):
    """Entry timestamps follow the inherited 15m-bar-open convention.

    TP/SL exit timestamps use the containing 15m bar close. Full-horizon
    eligibility is checked separately for each predeclared time limit.
    A later data problem never removes an earlier completed time exit.
    """
    diagnostics = diagnostics if diagnostics is not None else Counter()
    pending = {}
    for days in limits:
        deadline = fs + days*96
        if deadline > len(t):
            diagnostics[f"partial_horizon_{days}d"] += 1
        else:
            pending[days] = deadline
    out = {}
    if not pending:
        return out
    for j in range(fs, max(pending.values())):
        ht, hs = l[j] <= tp, h[j] >= sl
        result = None
        if not taker and j == fs:
            if ht or hs:
                result = _resolve_1m(
                    symbol, int(t[j]), tp, sl, False, fill,
                    float(h[j]), float(l[j]))
        elif ht and hs:
            result = _resolve_1m(
                symbol, int(t[j]), tp, sl, False, None,
                float(h[j]), float(l[j]))
        elif hs:
            result = "loss"
        elif ht:
            result = "win"
        if result in EXCLUDED:
            for days in pending:
                diagnostics[f"excluded_{result}_{days}d"] += 1
            return out
        if result in ("win", "loss"):
            price = tp if result == "win" else sl
            for days in pending:
                out[days] = (result, float(price), int(t[j]+STEP))
            return out
        if result not in (None, "continue"):
            raise RuntimeError(f"unexpected chronology result: {result}")
        for days in list(pending):
            if j+1 == pending[days]:
                assert int(t[j]+STEP) == int(t[fs]+days*DAY)
                out[days] = ("time", float(c[j]), int(t[j]+STEP))
                del pending[days]
        if not pending:
            return out
    raise AssertionError("unresolved complete horizon")


def one(t, o, h, l, c, symbol, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else Counter()
    assert len(t) == len(o) == len(h) == len(l) == len(c) and len(t) > 0
    assert np.all(np.diff(t) == STEP), "non-contiguous segment"
    rt, ro, rh, rl, rc = resample(t, o, h, l, c, 96)
    sar, bull = psar_open_projection(rh, rl)
    atr = prior_atr(rh, rl, rc)
    pos = np.searchsorted(t, rt)
    out = []
    for i in range(max(BURN, 15), len(rt)):
        if bull[i]:
            continue
        a, ps, start = float(atr[i]), float(sar[i]), int(pos[i])
        if not np.isfinite(a) or not np.isfinite(ps) or a <= 0:
            continue
        diagnostics["eligible_short_signals"] += 1
        target = ps-V[0]*a
        taker = bool(ro[i] >= target)
        if taker:
            fs, fill, order = start, float(ro[i]), "TAKER"
        else:
            hit = np.flatnonzero(h[start:min(start+96, len(t))] >= target)
            if not hit.size:
                diagnostics["unfilled"] += 1
                continue
            fs, fill, order = start+int(hit[0]), float(target), "MAKER"
        sl = ps+V[1]*a
        risk = sl-fill
        if fill <= 0 or risk <= 1e-12*max(1., abs(fill), abs(sl)):
            diagnostics["invalid_risk"] += 1
            continue
        tp = fill-V[2]*risk
        if not np.isfinite(tp) or tp <= 0:
            diagnostics["nonpositive_tp"] += 1
            continue
        exits = resolve_limits(t, h, l, c, fs, fill, tp, sl,
                               symbol, taker, diagnostics=diagnostics)
        for days, (outcome, price, exit_ts) in exits.items():
            out.append(dict(
                symbol=symbol, signal_ts=int(rt[i]), fill_ts=int(t[fs]),
                exit_ts=exit_ts, limit_days=days, outcome=outcome,
                pnl_pct=(fill-price)/fill*100., atr_pct=100.*a/float(ro[i]),
                stop_pct=risk/fill*100., order=order, fill=fill, sl=sl,
                tp=tp, exit_price=price, psar_ref=ps, atr_open=a,
                signal_open=float(ro[i]), entry_target=target,
                input_cutoff_ts=int(rt[i]-1),
            ))
    return out


def validate(z, require_all_limits=True):
    assert len(z), "empty result"
    if require_all_limits:
        assert set(z.limit_days) == set(LIMITS)
    nums = [x for x in COLUMNS if x not in ("symbol", "outcome", "order")]
    assert np.isfinite(z[nums].to_numpy(dtype=float)).all()
    assert not z.duplicated(["symbol", "signal_ts", "limit_days"]).any()
    assert z.outcome.isin(["win", "loss", "time"]).all()
    assert z.order.isin(["MAKER", "TAKER"]).all()
    assert (z.input_cutoff_ts < z.signal_ts).all()
    assert (z.signal_ts <= z.fill_ts).all()
    assert (z.fill_ts < z.signal_ts+DAY).all()
    assert (z.exit_ts > z.fill_ts).all()
    assert (z.exit_ts-z.fill_ts <= z.limit_days*DAY).all()
    for name in ("signal_ts", "fill_ts", "exit_ts"):
        assert (z[name] % STEP == 0).all()
    assert (z.signal_ts % DAY == 0).all()
    time = z[z.outcome == "time"]
    assert (time.exit_ts-time.fill_ts == time.limit_days*DAY).all()
    assert (z[["fill", "sl", "exit_price", "atr_open", "signal_open"]]>0).all().all()
    assert (z.sl > z.fill).all()
    assert (z.tp > 0).all(), "non-orderable TP"
    assert np.allclose(z.sl, z.psar_ref+V[1]*z.atr_open)
    assert np.allclose(z.entry_target, z.psar_ref-V[0]*z.atr_open)
    assert np.allclose(z.tp, z.fill-V[2]*(z.sl-z.fill))
    assert np.allclose(z.pnl_pct, (z.fill-z.exit_price)/z.fill*100.)
    assert np.allclose(z.stop_pct, (z.sl-z.fill)/z.fill*100.)
    assert np.allclose(z.atr_pct, z.atr_open/z.signal_open*100.)
    for outcome, field in (("win", "tp"), ("loss", "sl")):
        g = z[z.outcome == outcome]
        assert np.allclose(g.exit_price, g[field])
    assert (z.loc[z.outcome == "win", "pnl_pct"] > 0).all()
    assert (z.loc[z.outcome == "loss", "pnl_pct"] < 0).all()
    maker = z[z.order == "MAKER"]
    taker = z[z.order == "TAKER"]
    assert np.allclose(maker["fill"], maker.entry_target)
    assert (maker.signal_open < maker.entry_target).all()
    assert np.allclose(taker["fill"], taker.signal_open)
    assert (taker.signal_open >= taker.entry_target).all()
    assert (taker.signal_ts == taker.fill_ts).all()
    # Identical signal inputs and fill across all eligible time limits.
    invariants = ["fill_ts", "fill", "sl", "tp", "atr_pct", "stop_pct"]
    assert (z.groupby(["symbol", "signal_ts"])[invariants].nunique() <= 1).all().all()
    # Early natural exits reproduce on every available longer horizon.
    for small, large in ((1,3),(1,7),(1,14),(3,7),(3,14),(7,14)):
        a=z[(z.limit_days==small)&(z.outcome!="time")]
        b=z[z.limit_days==large]
        m=a.merge(b,on=["symbol","signal_ts"],suffixes=("_a","_b"))
        assert (m.outcome_a == m.outcome_b).all()
        assert (m.exit_ts_a == m.exit_ts_b).all()
        assert np.allclose(m.pnl_pct_a,m.pnl_pct_b)


def source_files(data, shard, shards, max_files=0):
    assert shards>0 and 0<=shard<shards
    all_files=sorted(glob.glob(os.path.join(data,"**","*.csv.gz"),recursive=True))
    assert all_files, "no input files"
    symbols=[sym(p) for p in all_files]
    assert len(set(symbols))==len(symbols), "duplicate source symbol"
    files=[p for k,p in enumerate(all_files) if k%shards==shard]
    return all_files, files[:max_files] if max_files else files


def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument("--data",default="data")
    ap.add_argument("--shard",type=int,default=0)
    ap.add_argument("--shards",type=int,default=8)
    ap.add_argument("--max-files",type=int,default=0)
    ap.add_argument("--out",default="tl.csv.gz")
    args=ap.parse_args(argv)
    all_files,files=source_files(args.data,args.shard,args.shards,args.max_files)
    rows, diagnostics, sources = [], Counter(), []
    for n,path in enumerate(files,1):
        symbol=sym(path)
        values=load(path)
        t=values[0]
        sources.append(dict(symbol=symbol,rows=len(t),start_ts=int(t[0]),end_ts=int(t[-1]+STEP)))
        for lo,hi in contiguous_segments(t):
            if hi-lo < 96*(BURN+1):
                continue
            rows.extend(one(*(x[lo:hi] for x in values),symbol,diagnostics))
        _ONE_MIN_CACHE.clear()
        print("PROGRESS",args.shard,n,len(files),symbol,len(rows),flush=True)
    z=pd.DataFrame(rows,columns=COLUMNS)
    validate(z)
    z.to_csv(args.out,index=False,compression="gzip")
    manifest=dict(
        source_run=36095439671,variant=V,limits=LIMITS,atr_threshold=THRESHOLD,
        shard=args.shard,shards=args.shards,total_input_files=len(all_files),
        selected_files=len(files),sources=sources,events=len(z),
        diagnostics=dict(diagnostics),commit=os.environ.get("GITHUB_SHA","local"),
        script_sha256=hashlib.sha256(open(__file__,"rb").read()).hexdigest(),
        helper_sha256=hashlib.sha256(open(os.path.join(os.path.dirname(__file__),"psar_1d_exec_helpers.py"),"rb").read()).hexdigest(),
        fill_timestamp_policy="15m entry-bar open; not tick-accurate maker fill time",
        exit_timestamp_policy="15m exit-bar close",
        fees_policy="applied in summary; funding not included",
    )
    with open(args.out+".meta.json","w") as f:
        json.dump(manifest,f,indent=2)
    print("PASS",len(z),z.groupby(["limit_days","outcome"]).size().to_dict(),flush=True)


if __name__ == "__main__":
    main()
