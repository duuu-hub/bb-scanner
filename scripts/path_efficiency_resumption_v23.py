"""Frozen V23 path-efficiency pullback/resumption research."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import day_edge_lab as base
from scripts import relative_pullback_portfolio as account
from scripts import session_opening_range_sweep_v20 as engine
from scripts import btc_factor_lag_v6 as history
from scripts import cross_sectional_ranks_v9 as source_helpers

BAR, DAY = base.BAR, base.DAY
HOLDS, EXITS = (16, 32), ("R15", "R25", "TRAIL")
ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "research/path-efficiency-resumption-v23/FROZEN_CONTEXT.json"
digest = source_helpers.digest
SOURCE_LOAD = engine.load
SOURCE_FEATURES = engine.features
COLUMNS = [
    "symbol","key","signal_time","decision_time","entry_time","entry","sl","side",
    "risk_pct","score","atr_mult","prior_atr","buy_share","volume_multiple",
    "formation_bars","efficiency_threshold","formation_efficiency","formation_move",
    "directional_return_share","max_bar_share","formation_start_time","formation_end_time",
    "pause_time","pause_retrace","pause_volume_multiple","pause_extreme","formation_midpoint",
    "confirmation_time","confirmation_volume_multiple","flow_confirmation","known_entry_gap",
    "coin_r1","btc_r1","clv","tp","max_hold_bars","status","exit_time","exit",
    "reason","gross_return","variant","policy","exit_type","hold_min",
    "net40_fraction","net40_R","split",
]


def configurations():
    out = [
        dict(
            key=f"PATH_S{side:+d}_W{window:02d}_E{int(efficiency*100):02d}_{confirmation}",
            side=side, window=window, efficiency=efficiency, confirmation=confirmation,
        )
        for side in (1, -1)
        for window in (8, 16)
        for efficiency in (.55, .70)
        for confirmation in ("PRICE_ONLY", "FLOW55")
    ]
    assert len(out) == 16 and len({x["key"] for x in out}) == 16
    return out


def policies():
    out = [dict(**c, hold=h, exit_type=e, policy=f'{c["key"]}__H{h}__{e}')
           for c in configurations() for h in HOLDS for e in EXITS]
    assert len(out) == 96
    return out


def load(path, end=None):
    return SOURCE_LOAD(path, end)


def features(raw, q, buy):
    f = history.features(raw, q, buy)
    t, n = raw[0], len(q)
    for key in ("prior_quote_mean", "closed_quote_24h"):
        f[key] = np.full(n, np.nan)
    for a, b in base.segments(t):
        qq = pd.Series(q[a:b])
        f["prior_quote_mean"][a:b] = qq.rolling(96, min_periods=96).mean().shift(1).to_numpy()
        f["closed_quote_24h"][a:b] = qq.rolling(96, min_periods=96).sum().shift(1).to_numpy()
    f["volume_multiple"] = np.divide(q, f["prior_quote_mean"], out=np.full(n, np.nan),
        where=f["prior_quote_mean"] > 0)
    # The shared V20 scan records this compatibility diagnostic only; V23 never
    # uses it for signal generation or pricing.
    f["session_vwap"] = np.asarray(raw[4], dtype=float).copy()
    f["_quote"] = np.asarray(q, dtype=float)
    f["eligible"] &= source_helpers.observed_days(t) >= 30
    f["eligible"] &= f["closed_quote_24h"] >= 20_000_000
    return f


def setup_at(i, cfg, raw, f):
    """Return only information known at confirmation-bar close i."""
    t, o, h, l, c = raw
    q, side, window = f["_quote"], cfg["side"], cfg["window"]
    pause, start, finish = i - 1, i - window - 2, i - 2
    if start < 0 or i >= len(t) or np.any(np.diff(t[start:i + 1]) != BAR):
        return None
    closes = c[start:finish + 1]
    if len(closes) != window + 1 or np.any(~np.isfinite(closes)) or np.any(closes <= 0):
        return None
    returns = np.diff(np.log(closes))
    signed = side * returns
    move = float(side * np.log(closes[-1] / closes[0]))
    gross = float(np.abs(returns).sum())
    atr = float(f["prior_atr"][pause])
    if not (f["eligible"][i] and np.isfinite(atr) and atr > 0 and gross > 0):
        return None
    efficiency = move / gross
    directional_share = float(np.mean(signed > 0))
    max_bar_share = float(np.max(np.abs(returns)) / gross)
    minimum_move = max(.01, 1.5 * atr / closes[-1])
    if not (minimum_move <= move <= .08 and efficiency >= cfg["efficiency"]
            and directional_share >= .625 and max_bar_share <= .60):
        return None
    pause_move = float(-side * np.log(c[pause] / c[finish]))
    retrace = pause_move / move
    midpoint = float(closes[0] * np.exp(side * .5 * move))
    midpoint_ok = l[pause] >= midpoint if side == 1 else h[pause] <= midpoint
    pause_volume = float(q[pause] / f["prior_quote_mean"][pause])
    confirm_volume = float(q[i] / f["prior_quote_mean"][i])
    price_ok = side * (c[i] - o[i]) > 0 and side * (c[i] - c[pause]) > 0 and side * (c[i] - o[pause]) > 0
    flow_ok = f["buy_share"][i] >= .55 if side == 1 else f["buy_share"][i] <= .45
    if not (.05 <= retrace <= .35 and midpoint_ok and pause_volume <= 1.
            and confirm_volume >= 1.25 and price_ok):
        return None
    if cfg["confirmation"] == "FLOW55" and not flow_ok:
        return None
    return dict(formation_efficiency=efficiency, formation_move=move,
        directional_return_share=directional_share, max_bar_share=max_bar_share,
        formation_start_time=int(t[start]), formation_end_time=int(t[finish]),
        pause_time=int(t[pause]), pause_retrace=float(retrace),
        pause_volume_multiple=pause_volume, pause_extreme=float(l[pause] if side == 1 else h[pause]),
        formation_midpoint=midpoint, confirmation_volume_multiple=confirm_volume,
        prior_atr=atr)


def intents(symbol, cfg, raw, f, btc, start, end):
    t, o, h, l, c = raw
    rows, excluded, last = [], Counter(), -10000
    for i in range(cfg["window"] + 2, len(t) - 1):
        setup = setup_at(i, cfg, raw, f)
        if setup is None:
            continue
        j, side = i + 1, cfg["side"]
        if i - last < 16:
            excluded["COOLDOWN"] += 1
            continue
        last = i
        if j >= len(t) or not start <= t[j] < end:
            continue
        if t[j] != t[i] + BAR:
            excluded["ENTRY_PATH_GAP"] += 1
            continue
        entry = float(o[j])
        gap = side * (entry / c[i] - 1)
        if not np.isfinite(entry) or entry <= 0:
            excluded["INVALID_ENTRY"] += 1
            continue
        if gap > .005:
            excluded["ENTRY_CATCHUP_GAP"] += 1
            continue
        atr = setup["prior_atr"]
        structural = setup["pause_extreme"] - side * .10 * atr
        raw_distance = side * (entry - structural)
        if raw_distance <= 0:
            excluded["STRUCTURAL_STOP_WRONG_SIDE"] += 1
            continue
        distance = max(raw_distance, .005 * entry)
        if distance / entry > .06:
            excluded["STOP_ABOVE_6PCT"] += 1
            continue
        sl = entry - side * distance
        midpoint = float((h[i] + l[i]) / 2)
        if sl <= 0:
            excluded["NONPOSITIVE_LEVEL"] += 1
            continue
        score = setup["formation_efficiency"] * setup["formation_move"] * setup["confirmation_volume_multiple"]
        rows.append(dict(symbol=symbol,key=cfg["key"],signal_time=int(t[i]),decision_time=int(t[i]+BAR),
            entry_time=int(t[j]),entry_index=int(j),entry=entry,sl=float(sl),side=int(side),
            # Canonical TRAIL consumes atr_mult; the frozen contract is 2 ATR.
            risk_pct=float(distance/entry),score=float(score),atr_mult=2.0,prior_atr=atr,
            buy_share=float(f["buy_share"][i]),volume_multiple=float(f["volume_multiple"][i]),
            formation_bars=cfg["window"],efficiency_threshold=cfg["efficiency"],
            confirmation_time=int(t[i]),flow_confirmation=cfg["confirmation"],known_entry_gap=float(gap),
            btc_r1=float(btc["r1"][i]),clv=float(f["clv"][i])))
        rows[-1].update(setup, coin_r1=float(f["r1"][i]))
    return rows, excluded


def policy_rows(symbol, chosen, raw, f, btc, start, end):
    rows, counts, bad, grouped = [], Counter(), [], {}
    for p in chosen:
        grouped.setdefault(p["key"], []).append(p)
    for cfgs in grouped.values():
        seeds, excluded = intents(symbol, cfgs[0], raw, f, btc, start, end)
        counts.update({cfgs[0]["key"] + "/" + k: n for k, n in excluded.items()})
        for p in cfgs:
            for seed in seeds:
                risk = abs(seed["entry"] - seed["sl"])
                multiple = 1.5 if p["exit_type"] == "R15" else 2.5
                tp = seed["entry"] + seed["side"] * multiple * risk
                tr = dict(seed, tp=float(tp), max_hold_bars=p["hold"])
                result = engine.canonical.resolve(
                    tr, raw, f, "TRAIL" if p["exit_type"] == "TRAIL" else "TP2", end)
                counts[p["policy"] + "/" + result["status"]] += 1
                if result["status"] != "RESOLVED":
                    bad.append(dict(symbol=symbol,policy=p["policy"],entry_time=tr["entry_time"],status=result["status"]))
                    continue
                row = {k:v for k,v in tr.items() if k != "entry_index"}
                row.update(result,variant=p["policy"],policy=p["policy"],exit_type=p["exit_type"])
                row["hold_min"] = (row["exit_time"] - row["entry_time"]) / 60000
                exit_price = row["exit"] * (1-row["side"]*.001) if row["reason"] == "SL" else row["exit"]
                ratio = exit_price / row["entry"]
                net = row["side"]*(ratio-1)-.002*(1+ratio)-.0002*row["hold_min"]/1440
                row["net40_fraction"] = float(net)
                row["net40_R"] = float(net/account.stop_loss_fraction(row,.002))
                rows.append(row)
    return rows, counts, bad


def bind_engine():
    engine.CONTEXT=CONTEXT;engine.COLUMNS=COLUMNS;engine.LOG_PREFIX='V23_PATH_EFFICIENCY';engine.configurations=configurations
    engine.policies=policies;engine.load=load;engine.features=features;engine.intents=intents
    engine.BTC_LOAD=SOURCE_LOAD;engine.BTC_FEATURES=SOURCE_FEATURES
    engine.policy_rows=policy_rows


def scan(*args, **kwargs):
    bind_engine()
    kwargs.setdefault("context_path", CONTEXT)
    return engine.scan(*args, **kwargs)


def select(parts, out, context_path=CONTEXT):
    bind_engine(); engine.select(parts, out, context_path)
    path=out/"selection.json"; decision=json.loads(path.read_text())
    decision["union_name"]="PATH_EFFICIENCY_TREND_RESUMPTION_UNION"
    decision["note"]="Frozen V23 smooth-path, shallow-pause, high-volume resumption. Diagnostics are not executable account growth."
    path.write_text(json.dumps(decision,indent=2,allow_nan=False))
    selection_hash=digest(path)
    for meta in out.glob("dev-*/scan_meta.json"):
        m=json.loads(meta.read_text());m["selection_sha256"]=selection_hash
        meta.write_text(json.dumps(m,indent=2))


def accounts(*args, **kwargs):
    bind_engine(); return engine.accounts(*args, **kwargs)


def smoke(out):
    bind_engine();out.mkdir(parents=True,exist_ok=True);n=32*96
    t=base.START+np.arange(n,dtype=np.int64)*BAR;rng=np.random.default_rng(62202)
    x=np.cumsum(rng.normal(0,.001,n));btc=np.exp(10+x)
    def write(path, close, trades=1000):
        pd.DataFrame(dict(open_time=t,open=close,high=close*1.001,low=close*.999,close=close,
            quote_volume=np.full(n,1e6),trades=np.full(n,trades,dtype=int),
            taker_buy_quote=np.full(n,6e5))).to_csv(path,index=False,compression="gzip")
    # The frozen BTC context legitimately contains zero-count bars. It is loaded only
    # for market context and must not pass through V22's tradable-symbol validator.
    bp=out/"BTCUSDT.csv.gz";write(bp,btc,trades=0);hashes={}
    for i in range(8):
        d=out/"market"/str(i);d.mkdir(parents=True,exist_ok=True);p=d/f"X{i:02d}USDT.csv.gz"
        write(p,np.exp(1+1.2*x+rng.normal(0,.0001,n)));hashes[p.name[:-7]]=digest(p)
    cp=out/"synthetic-context.json";cp.write_text(json.dumps(dict(baseline_sha256="a"*64,btc_sha256=digest(bp),expected_market_sha256=hashes)))
    for i in range(8):
        symbol=f"X{i:02d}USDT";check=out/f"check-{i}.json"
        check.write_text(json.dumps(dict(status="VERIFIED",shards=[i],baseline_sha256="a"*64,files=[dict(symbol=symbol,sha256=hashes[symbol])])))
        scan(out/"market"/str(i),bp,out/"parts"/str(i),out/"minute-cache","DEV",check,context_path=cp)
    select(out/"parts",out/"selected",cp)
    cells=pd.read_csv(out/"selected/development_policy_cells.csv")
    assert len(cells)==96 and cells.n.sum()==0
    report=dict(synthetic_only=True,scans=8,all96_cells_preserved=True,market_profitability_claim=False)
    (out/"smoke.json").write_text(json.dumps(report,indent=2))
    print("V23_SYNTHETIC_PIPELINE_PASS",json.dumps(report),flush=True)


def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest="command",required=True)
    p=sub.add_parser("scan")
    for name in ("data","btc","out","minute-cache","source-check"):p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--stage",choices=("DEV","GATE"),required=True);p.add_argument("--selection",type=Path)
    p=sub.add_parser("select")
    for name in ("parts","out"):p.add_argument("--"+name,type=Path,required=True)
    p=sub.add_parser("accounts")
    for name in ("data","parts","out","selection"):p.add_argument("--"+name,type=Path,required=True)
    p=sub.add_parser("smoke");p.add_argument("--out",type=Path,required=True)
    a=ap.parse_args()
    if a.command=="scan":scan(a.data,a.btc,a.out,a.minute_cache,a.stage,a.source_check,a.selection)
    elif a.command=="select":select(a.parts,a.out)
    elif a.command=="accounts":accounts(a.data,a.parts,a.out,a.selection)
    else:smoke(a.out)


if __name__ == "__main__":
    main()
