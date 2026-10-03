"""Frozen V22 fragmented-chase exhaustion reversal research."""
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
HOLDS, EXITS = (16, 32), ("MID", "R15", "R25")
ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "research/fragmented-chase-exhaustion-v22/FROZEN_CONTEXT.json"
digest = source_helpers.digest
SOURCE_LOAD = engine.load
COLUMNS = [
    "symbol","key","signal_time","decision_time","entry_time","entry","sl","side",
    "risk_pct","score","atr_mult","prior_atr","buy_share","volume_multiple",
    "shock_atr","fragmentation_ratio","trade_count_multiple","event_trade_count",
    "event_average_trade_value","prior_average_trade_value","event_high","event_low",
    "event_midpoint","confirmation_time","confirmation_reclaim","known_entry_gap",
    "coin_r1","btc_r1","clv","tp","max_hold_bars","status","exit_time","exit",
    "reason","gross_return","variant","policy","exit_type","hold_min",
    "net40_fraction","net40_R","split",
]


def configurations():
    out = [
        dict(
            key=f"FRAG_S{side:+d}_A{int(shock*100):03d}_F{int(fragment*100):02d}_T{int(flow*100):02d}",
            side=side, shock_atr=shock, fragment_ratio=fragment, flow_threshold=flow,
        )
        for side in (1, -1)
        for shock in (1.50, 2.25)
        for fragment in (.65, .85)
        for flow in ((.40, .33) if side == 1 else (.60, .67))
    ]
    assert len(out) == 16 and len({x["key"] for x in out}) == 16
    return out


def policies():
    out = [dict(**c, hold=h, exit_type=e, policy=f'{c["key"]}__H{h}__{e}')
           for c in configurations() for h in HOLDS for e in EXITS]
    assert len(out) == 96
    return out


def load(path, end=None):
    raw, q, buy = SOURCE_LOAD(path, end)
    d = pd.read_csv(path, usecols=["open_time", "trades"])
    trades = d.trades.to_numpy(float)
    if end is not None:
        trades = trades[d.open_time.to_numpy(np.int64) < end]
    if len(trades) != len(q) or np.any(~np.isfinite(trades)) or np.any(trades <= 0) or np.any(trades != np.floor(trades)):
        raise ValueError(f"bad positive integer trade count {path}")
    return raw, q, np.column_stack((buy, trades))


def features(raw, q, aux):
    aux = np.asarray(aux)
    if aux.ndim != 2 or aux.shape != (len(q), 2):
        raise ValueError("expected taker-buy and trade-count columns")
    buy, trades = aux[:, 0], aux[:, 1]
    if np.any(~np.isfinite(trades)) or np.any(trades <= 0) or np.any(trades != np.floor(trades)):
        raise ValueError("bad positive integer trade count")
    f = history.features(raw, q, buy)
    t, n = raw[0], len(q)
    for key in ("trade_count", "average_trade_value", "prior_average_trade_value",
                "prior_trade_count_median", "prior_quote_mean", "closed_quote_24h",
                "fragmentation_ratio", "trade_count_multiple"):
        f[key] = np.full(n, np.nan)
    f["trade_count"] = trades.copy()
    f["average_trade_value"] = q / trades
    for a, b in base.segments(t):
        av = pd.Series(f["average_trade_value"][a:b])
        tc, qq = pd.Series(trades[a:b]), pd.Series(q[a:b])
        f["prior_average_trade_value"][a:b] = av.rolling(96, min_periods=96).median().shift(1).to_numpy()
        f["prior_trade_count_median"][a:b] = tc.rolling(96, min_periods=96).median().shift(1).to_numpy()
        f["prior_quote_mean"][a:b] = qq.rolling(96, min_periods=96).mean().shift(1).to_numpy()
        f["closed_quote_24h"][a:b] = qq.rolling(96, min_periods=96).sum().shift(1).to_numpy()
    f["fragmentation_ratio"] = np.divide(f["average_trade_value"], f["prior_average_trade_value"],
        out=np.full(n, np.nan), where=f["prior_average_trade_value"] > 0)
    f["trade_count_multiple"] = np.divide(trades, f["prior_trade_count_median"],
        out=np.full(n, np.nan), where=f["prior_trade_count_median"] > 0)
    f["volume_multiple"] = np.divide(q, f["prior_quote_mean"], out=np.full(n, np.nan),
        where=f["prior_quote_mean"] > 0)
    f["session_vwap"] = f["prior_average_trade_value"]  # diagnostics-only compatibility field
    f["eligible"] &= source_helpers.observed_days(t) >= 30
    f["eligible"] &= f["closed_quote_24h"] >= 20_000_000
    return f


def event_mask(cfg, raw, f):
    t, o, h, l, c = raw
    previous = np.r_[np.nan, c[:-1]]
    atr = f["prior_atr"]
    side = cfg["side"]
    shock_direction = -side
    move = shock_direction * (c - previous)
    outer = np.divide(c - l, h - l, out=np.full(len(c), np.nan), where=h > l)
    outer_ok = outer <= .25 if side == 1 else outer >= .75
    flow_ok = f["buy_share"] <= cfg["flow_threshold"] if side == 1 else f["buy_share"] >= cfg["flow_threshold"]
    return (f["eligible"] & np.isfinite(atr) & (atr > 0) & (move >= cfg["shock_atr"] * atr)
            & (shock_direction * (c - o) > 0) & outer_ok
            & (f["volume_multiple"] >= 1.5) & (f["trade_count_multiple"] >= 2.0)
            & (f["fragmentation_ratio"] <= cfg["fragment_ratio"]) & flow_ok)


def intents(symbol, cfg, raw, f, btc, start, end):
    t, o, h, l, c = raw
    rows, excluded, last = [], Counter(), -10000
    for i in np.flatnonzero(event_mask(cfg, raw, f)):
        k, j, side = i + 1, i + 2, cfg["side"]
        if i - last < 16:
            excluded["COOLDOWN"] += 1
            continue
        if j >= len(t) or not start <= t[j] < end:
            continue
        if t[k] != t[i] + BAR or t[j] != t[k] + BAR:
            excluded["CONFIRMATION_OR_ENTRY_PATH_GAP"] += 1
            continue
        atr = float(f["prior_atr"][i])
        event_range = float(h[i] - l[i])
        if event_range <= 0:
            excluded["ZERO_EVENT_RANGE"] += 1
            continue
        reclaim = (c[k] - l[i]) / event_range if side == 1 else (h[i] - c[k]) / event_range
        opposite_body = side * (c[k] - o[k]) > 0
        adverse_extension = max(0.0, l[i] - l[k]) if side == 1 else max(0.0, h[k] - h[i])
        if not opposite_body or reclaim < .35 or adverse_extension > .10 * atr:
            excluded["CONFIRMATION_FAILED"] += 1
            continue
        entry = float(o[j])
        gap = side * (entry / c[k] - 1)
        if not np.isfinite(entry) or entry <= 0:
            excluded["INVALID_ENTRY"] += 1
            continue
        if gap > .005:
            excluded["ENTRY_CATCHUP_GAP"] += 1
            continue
        extreme = min(l[i], l[k]) if side == 1 else max(h[i], h[k])
        structural = extreme - side * .10 * atr
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
        last = i
        score = cfg["shock_atr"] * f["trade_count_multiple"][i] / max(f["fragmentation_ratio"][i], 1e-9)
        rows.append(dict(symbol=symbol,key=cfg["key"],signal_time=int(t[i]),decision_time=int(t[k]+BAR),
            entry_time=int(t[j]),entry_index=int(j),entry=entry,sl=float(sl),side=int(side),
            risk_pct=float(distance/entry),score=float(score),atr_mult=cfg["shock_atr"],prior_atr=atr,
            buy_share=float(f["buy_share"][i]),volume_multiple=float(f["volume_multiple"][i]),
            shock_atr=float(abs(c[i]-c[i-1])/atr),fragmentation_ratio=float(f["fragmentation_ratio"][i]),
            trade_count_multiple=float(f["trade_count_multiple"][i]),event_trade_count=int(f["trade_count"][i]),
            event_average_trade_value=float(f["average_trade_value"][i]),
            prior_average_trade_value=float(f["prior_average_trade_value"][i]),event_high=float(h[i]),
            event_low=float(l[i]),event_midpoint=midpoint,confirmation_time=int(t[k]),
            confirmation_reclaim=float(reclaim),known_entry_gap=float(gap),coin_r1=float(f["r1"][i]),
            btc_r1=float(btc["r1"][i]),clv=float(f["clv"][i])))
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
                tp = seed["event_midpoint"] if p["exit_type"] == "MID" else seed["entry"] + seed["side"] * ({"R15":1.5,"R25":2.5}[p["exit_type"]]) * risk
                if seed["side"] * (tp - seed["entry"]) <= 0:
                    counts[p["policy"] + "/TARGET_WRONG_SIDE"] += 1
                    continue
                tr = dict(seed, tp=float(tp), max_hold_bars=p["hold"])
                result = engine.canonical.resolve(tr, raw, f, "TP2", end)
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
    engine.CONTEXT=CONTEXT;engine.COLUMNS=COLUMNS;engine.LOG_PREFIX='V22_FRAGMENTED_CHASE';engine.configurations=configurations
    engine.policies=policies;engine.load=load;engine.features=features;engine.intents=intents
    engine.policy_rows=policy_rows


def scan(*args, **kwargs):
    bind_engine(); return engine.scan(*args, **kwargs)


def select(parts, out, context_path=CONTEXT):
    bind_engine(); engine.select(parts, out, context_path)
    path=out/"selection.json"; decision=json.loads(path.read_text())
    decision["union_name"]="FRAGMENTED_CHASE_EXHAUSTION_REVERSAL_UNION"
    decision["note"]="Frozen V22 fragmented small-ticket chase exhaustion reversal. Diagnostics are not executable account growth."
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
    def write(path, close):
        pd.DataFrame(dict(open_time=t,open=close,high=close*1.001,low=close*.999,close=close,
            quote_volume=np.full(n,1e6),trades=np.full(n,1000,dtype=int),
            taker_buy_quote=np.full(n,6e5))).to_csv(path,index=False,compression="gzip")
    bp=out/"BTCUSDT.csv.gz";write(bp,btc);hashes={}
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
    print("V22_SYNTHETIC_PIPELINE_PASS",json.dumps(report),flush=True)


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
