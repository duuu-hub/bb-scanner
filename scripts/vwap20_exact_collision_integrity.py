"""Validate only existing VWAP TP/SL-collision outcomes; never rescan signals.

Original artifacts remain immutable. The original strategy thresholds, entry price,
time limit and 0/20/40bp costs remain frozen. Official daily Binance USD-M 1m
archives provide complete chronology windows. The repository's canonical
_resolve_1m function is loaded verbatim rather than a strategy-local resolver.
"""
import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

MINUTE = 60_000
BAR = 15 * MINUTE
HOUR = 4 * BAR
TP = 8.0
SL = 1.0
COSTS = (0.0, 0.2, 0.4)
SOURCE = Path("scripts/psar_open_canonical_compare.py")
ARCHIVE = "https://data.binance.vision/data/futures/um/daily/klines"


def canonical_namespace(provider):
    source = SOURCE.read_text()
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, ast.FunctionDef) and node.name == "_resolve_1m"]
    if len(nodes) != 1:
        raise RuntimeError("canonical resolver not uniquely found")
    ns = {"np": np, "_one_min": provider}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns


def validate_window(arrays, ts):
    if isinstance(arrays, tuple) and len(arrays) == 2 and arrays[0] == "data_gap":
        return arrays
    t, h, l = arrays
    a = np.searchsorted(t, ts)
    z = np.searchsorted(t, ts + BAR)
    tt, hh, ll = t[a:z], h[a:z], l[a:z]
    if len(tt) != 15 or not np.array_equal(tt, ts + np.arange(15) * MINUTE):
        return ("data_gap", "missing, duplicate or misaligned required minute")
    if not np.isfinite(hh).all() or not np.isfinite(ll).all():
        return ("data_gap", "nonfinite minute prices")
    if np.any(ll <= 0) or np.any(hh < ll):
        return ("data_gap", "invalid minute high/low")
    return arrays


def smoke():
    holder = {}
    def provider(symbol, ts):
        return validate_window(holder["data"], ts)
    ns = canonical_namespace(provider)
    resolve = ns["_resolve_1m"]
    checks = []
    def case(name, rows, long, entry, expected):
        rows = list(rows)
        neutral = (rows[-1][1] + rows[-1][2]) / 2 if rows else 100.0
        while len(rows) < 15:
            minute = len(rows) * MINUTE
            rows.append((minute, neutral, neutral))
        holder["data"] = tuple(np.array([r[i] for r in rows],
                                      dtype=np.int64 if i == 0 else float)
                               for i in range(3))
        tp, sl = (105.0, 95.0) if long else (95.0, 105.0)
        got = resolve("TEST", 0, tp, sl, long, entry)
        if got != expected:
            raise AssertionError(f"{name}: {got} != {expected}")
        checks.append({"case": name, "pass": True, "outcome": got})
    case("long_preentry_tp_ignored", [(0,111,109),(MINUTE,101,99),(2*MINUTE,106,102)], True,100,"win")
    case("long_entry_minute_tp_loss", [(0,106,99)],True,100,"loss")
    case("long_entry_minute_sl_loss", [(0,101,94)],True,100,"loss")
    case("long_entry_minute_both_loss", [(0,106,94)],True,100,"loss")
    case("long_entry_then_tp", [(0,101,99),(MINUTE,106,101)],True,100,"win")
    case("long_entry_then_sl", [(0,101,99),(MINUTE,101,94)],True,100,"loss")
    case("long_gap_entry_then_tp", [(0,99,98),(MINUTE,106,101)],True,100,"win")
    case("short_gap_entry_then_tp", [(0,102,101),(MINUTE,99,94)],False,100,"win")
    case("established_both_loss", [(0,106,94)],True,None,"loss")
    case("market_at_open_tp_known_after_entry", [(0,106,99)],True,None,"win")
    case("established_exit_mismatch", [(0,104,96)],True,None,"exit_mismatch")
    case("resting_entry_mismatch", [(0,110,106),(MINUTE,104,102)],True,100,"entry_mismatch")
    case("preentry_tp_then_entry_continue", [(0,106,102),(MINUTE,101,99)],True,100,"continue")
    case("short_entry_minute_tp_loss", [(0,101,94)],False,100,"loss")
    case("short_entry_minute_sl_loss", [(0,106,99)],False,100,"loss")
    case("short_entry_then_tp", [(0,101,99),(MINUTE,99,94)],False,100,"win")
    case("short_entry_then_sl", [(0,101,99),(MINUTE,106,99)],False,100,"loss")
    for name, data in [
        ("missing_minutes_excluded", (np.arange(1,15)*MINUTE, np.full(14,106.), np.full(14,99.))),
        ("duplicate_minute_excluded", (np.r_[0,0,np.arange(2,15)*MINUTE], np.full(15,106.), np.full(15,99.))),
        ("invalid_prices_excluded", (np.arange(15)*MINUTE, np.full(15,94.), np.full(15,99.))),
        ("missing_archive_excluded", ("data_gap", "archive absent")),
    ]:
        holder["data"] = data
        got = resolve("TEST",0,105.,95.,True,None)
        if got != "data_gap":
            raise AssertionError(f"{name}: {got}")
        checks.append({"case": name, "pass": True, "outcome": got})
    return checks


def day_of(ts):
    return pd.to_datetime(int(ts), unit="ms", utc=True).strftime("%Y-%m-%d")


def download_day(symbol, day):
    url = f"{ARCHIVE}/{symbol}/1m/{symbol}-1m-{day}.zip"
    payload = None
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bb-scanner-integrity-audit"})
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = response.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return ("data_gap", f"official archive absent: {symbol} {day}")
            last = repr(exc)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = repr(exc)
        if attempt < 2:
            time.sleep(1 + attempt)
    if payload is None:
        raise RuntimeError(f"official archive download failed {symbol} {day}: {last}")
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        members = [n for n in z.namelist() if n.endswith(".csv")]
        if len(members) != 1:
            raise RuntimeError(f"unexpected archive members: {symbol} {day}")
        raw = z.read(members[0])
    d = pd.read_csv(io.BytesIO(raw), header=None, dtype=str)
    if d.shape[1] < 5:
        raise RuntimeError(f"invalid 1m schema: {symbol} {day}")
    if len(d) and not pd.notna(pd.to_numeric(d.iloc[0,0], errors="coerce")):
        d = d.iloc[1:]
    ts = pd.to_numeric(d.iloc[:,0], errors="coerce")
    if ts.median() > 1e14:
        ts = ts / 1000.0
    ohlc = d.iloc[:,1:5].apply(pd.to_numeric, errors="coerce")
    if ts.isna().any() or not np.isfinite(ohlc.to_numpy()).all():
        return ("data_gap", f"malformed numeric archive: {symbol} {day}")
    t = ts.to_numpy(dtype=np.int64)
    o,h,l,c = [ohlc.iloc[:,i].to_numpy(dtype=float) for i in range(4)]
    if np.any(t % MINUTE) or np.any(np.diff(t) <= 0):
        return ("data_gap", f"misaligned or duplicate archive timestamps: {symbol} {day}")
    if len(t) == 0 or any(day_of(v) != day for v in (t[0],t[-1])):
        return ("data_gap", f"archive date mismatch: {symbol} {day}")
    if np.any(l <= 0) or np.any(h < l) or np.any(o < l) or np.any(o > h) or np.any(c < l) or np.any(c > h):
        return ("data_gap", f"invalid OHLC geometry: {symbol} {day}")
    return (t,h,l)


def read_events(indir):
    files = sorted(Path(indir).glob("events_[0-7].csv"))
    if len(files) != 8 or {f.stem for f in files} != {f"events_{i}" for i in range(8)}:
        raise RuntimeError(f"expected all eight BEAR shard files: {files}")
    bear = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    bull = pd.read_csv(Path(indir) / "bull_events.csv")
    frames = []
    for regime,d in (("BEAR",bear),("BULL",bull)):
        if not (d.ts.astype("int64") % BAR == 0).all():
            raise RuntimeError(f"misaligned entry timestamps: {regime}")
        if d.duplicated(["symbol","ts"]).any():
            raise RuntimeError(f"duplicate event key: {regime}")
        if not (d.dist_pct <= -20 + 1e-12).all() or not (d.ret7d_pct <= -5 + 1e-12).all():
            raise RuntimeError(f"frozen signal thresholds violated: {regime}")
        d["btc_regime"] = regime
        frames.append(d)
    combined = pd.concat(frames,ignore_index=True)
    if combined.duplicated(["symbol","ts"]).any():
        raise RuntimeError("same event exists in both regimes")
    return combined


def summary(d):
    rows = []
    groups = []
    for regime,g in d.groupby("btc_regime"):
        groups.append((regime,"ALL",g))
        groups += [(regime,str(k),v) for k,v in g.groupby("year")]
        groups += [(regime,str(k),v) for k,v in g.groupby("split")]
    for regime,scope,g in groups:
        valid = g.audit_gross_pct.notna()
        for cost in COSTS:
            y = g.loc[valid,"audit_gross_pct"].astype(float)-cost
            neg = -y[y<0].sum()
            pf = y[y>0].sum()/neg if neg else np.nan
            rows.append([regime,scope,cost,len(g),len(y),int((~valid).sum()),
                         100*(y>0).mean(),y.mean(),pf])
    return pd.DataFrame(rows,columns=["btc_regime","scope","cost_pct","source_n","n",
                                     "excluded_n","wr_pct","mean_pct","pf"])


def run(indir,outdir):
    checks = smoke()
    d = read_events(indir)
    candidates = d.exit_reason.str.contains("1m|unresolved", regex=True, na=False)
    keys = set()
    for r in d.loc[candidates].itertuples(index=False):
        keys.add((r.symbol,day_of(r.ts)))
        keys.add((r.symbol,day_of(r.ts+HOUR-1)))
    cache = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(download_day,*key):key for key in sorted(keys)}
        for n,future in enumerate(as_completed(futures),1):
            cache[futures[future]] = future.result()
            if n % 25 == 0:
                print(f"ARCHIVES {n}/{len(keys)}",flush=True)

    def provider(symbol,ts):
        data = cache[(symbol,day_of(ts))]
        return validate_window(data,ts)
    resolve = canonical_namespace(provider)["_resolve_1m"]
    d["audit_gross_pct"] = d.gross_pct.astype(float)
    d["audit_status"] = "UNCHANGED_NONCOLLISION"
    d["audit_bar_ts"] = pd.Series(pd.NA,index=d.index,dtype="Int64")
    for idx,r in d.loc[candidates].iterrows():
        outcome = "exit_mismatch"
        for bar_ts in range(int(r.ts),int(r.ts)+HOUR,BAR):
            data = provider(r.symbol,bar_ts)
            if isinstance(data,tuple) and len(data)==2 and data[0]=="data_gap":
                outcome = "data_gap"
                break
            t,h,l = data
            a,z = np.searchsorted(t,[bar_ts,bar_ts+BAR])
            tp_price = float(r.entry)*(1+TP/100)
            sl_price = float(r.entry)*(1-SL/100)
            if (h[a:z]>=tp_price).any() or (l[a:z]<=sl_price).any():
                # Entry is a market fill at the known 15m OPEN, so there is no
                # unknown resting-limit entry interval. Established-position path.
                outcome = resolve(r.symbol,bar_ts,tp_price,sl_price,True,None)
                d.at[idx,"audit_bar_ts"] = bar_ts
                break
        d.at[idx,"audit_status"] = outcome.upper()
        d.at[idx,"audit_gross_pct"] = TP if outcome=="win" else (-SL if outcome=="loss" else np.nan)
    valid = d.audit_gross_pct.notna()
    changed = valid & ~np.isclose(d.audit_gross_pct,d.gross_pct,atol=1e-10,rtol=0)
    out = Path(outdir)
    out.mkdir(parents=True,exist_ok=True)
    d.to_csv(out/"events_audited.csv",index=False)
    d.loc[candidates].to_csv(out/"collision_audit.csv",index=False)
    s = summary(d)
    s.to_csv(out/"summary_audited.csv",index=False)
    d.groupby(["btc_regime","audit_status"]).size().rename("n").reset_index().to_csv(out/"audit_counts.csv",index=False)
    metadata = {
        "source_bear_run":36814221893,"source_bull_run":36735084022,
        "source_data_run":36095439671,"workflow_commit_sha":os.environ.get("GITHUB_SHA","local"),
        "canonical_source_sha256":hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "strategy_thresholds_unchanged":True,"source_n":len(d),
        "candidate_n":int(candidates.sum()),"archive_days":len(keys),
        "changed_outcome_n":int(changed.sum()),"excluded_n":int((~valid).sum()),
        "smoke_checks":checks,
        "scope":"existing collision outcomes only; noncollision legacy outcomes preserved",
        "cost_note":"0/20/40bp are fixed round-trip deductions; no separate stop-gap/funding simulation",
    }
    (out/"audit_metadata.json").write_text(json.dumps(metadata,indent=2))
    print(json.dumps({k:v for k,v in metadata.items() if k!="smoke_checks"},indent=2),flush=True)
    print(s.to_string(index=False),flush=True)


if __name__=="__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--in",dest="indir")
    p.add_argument("--out",dest="outdir")
    p.add_argument("--smoke",action="store_true")
    a = p.parse_args()
    if a.smoke:
        checks = smoke()
        print(f"CANONICAL_CHRONOLOGY_SMOKE {len(checks)}/{len(checks)} PASS")
    else:
        run(a.indir,a.outdir)
