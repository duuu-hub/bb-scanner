"""Frozen V24 failed-resumption reversal research."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import path_efficiency_resumption_v23 as v23

BAR = v23.BAR
ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "research/failed-resumption-reversal-v24/FROZEN_CONTEXT.json"
HOLDS, EXITS = (8, 16), ("R10", "R15", "TRAIL")
COLUMNS = list(v23.COLUMNS) + ["attempt_time", "failure_time", "failure_confirmation"]


def configurations():
    out = [dict(
        key=f"FAIL_S{side:+d}_W{window:02d}_E{int(efficiency*100):02d}_{confirmation}",
        side=side, window=window, efficiency=efficiency, confirmation=confirmation,
    ) for side in (1, -1) for window in (8, 16) for efficiency in (.55, .70)
      for confirmation in ("REENTRY", "OPPOSITE_FLOW55")]
    assert len(out) == 16 and len({x["key"] for x in out}) == 16
    return out


def policies():
    out = [dict(**c, hold=h, exit_type=e, policy=f'{c["key"]}__H{h}__{e}')
           for c in configurations() for h in HOLDS for e in EXITS]
    assert len(out) == 96
    return out


def setup_at(i, cfg, raw, f):
    """Return a failed-resumption setup using information through failure close i."""
    t, o, h, l, c = raw
    q, formation_side, window = f["_quote"], cfg["side"], cfg["window"]
    attempt, pause, finish, start = i - 1, i - 2, i - 3, i - window - 3
    if start < 0 or i >= len(t) or np.any(np.diff(t[start:i + 1]) != BAR):
        return None
    closes = c[start:finish + 1]
    if len(closes) != window + 1 or np.any(~np.isfinite(closes)) or np.any(closes <= 0):
        return None
    returns = np.diff(np.log(closes)); signed = formation_side * returns
    move = float(formation_side * np.log(closes[-1] / closes[0]))
    gross = float(np.abs(returns).sum()); atr = float(f["prior_atr"][pause])
    if not (f["eligible"][i] and np.isfinite(atr) and atr > 0 and gross > 0):
        return None
    efficiency = move / gross
    directional_share = float(np.mean(signed > 0))
    max_bar_share = float(np.max(np.abs(returns)) / gross)
    minimum_move = max(.01, 1.5 * atr / closes[-1])
    if not (minimum_move <= move <= .08 and efficiency >= cfg["efficiency"]
            and directional_share >= .625 and max_bar_share <= .55):
        return None
    formation_median_quote = float(np.median(q[start:finish + 1]))
    if not np.isfinite(formation_median_quote) or formation_median_quote <= 0:
        return None
    pause_move = float(-formation_side * np.log(c[pause] / c[finish]))
    retrace = pause_move / move
    midpoint = float(closes[0] * np.exp(formation_side * .5 * move))
    midpoint_ok = l[pause] >= midpoint if formation_side == 1 else h[pause] <= midpoint
    pause_volume = float(q[pause] / formation_median_quote)
    attempt_volume = float(q[attempt] / formation_median_quote)
    attempt_price = (formation_side * (c[attempt] - o[attempt]) > 0
                     and formation_side * (c[attempt] - c[pause]) > 0)
    attempt_flow = f["buy_share"][attempt] >= .55 if formation_side == 1 else f["buy_share"][attempt] <= .45
    reentry = (formation_side * (c[i] - o[i]) < 0
               and formation_side * (c[i] - c[pause]) < 0
               and formation_side * (c[i] - c[finish]) < 0)
    opposite_flow = f["buy_share"][i] <= .45 if formation_side == 1 else f["buy_share"][i] >= .55
    if not (.05 <= retrace <= .35 and midpoint_ok and pause_volume <= .85
            and attempt_volume >= 1.25 and attempt_price and attempt_flow and reentry):
        return None
    if cfg["confirmation"] == "OPPOSITE_FLOW55" and not opposite_flow:
        return None
    return dict(formation_efficiency=efficiency, formation_move=move,
        directional_return_share=directional_share, max_bar_share=max_bar_share,
        formation_start_time=int(t[start]), formation_end_time=int(t[finish]),
        pause_time=int(t[pause]), pause_retrace=float(retrace),
        pause_volume_multiple=pause_volume,
        pause_extreme=float(l[pause] if formation_side == 1 else h[pause]),
        formation_midpoint=midpoint, confirmation_volume_multiple=attempt_volume,
        confirmation_time=int(t[attempt]), attempt_time=int(t[attempt]),
        failure_time=int(t[i]), prior_atr=atr)


def intents(symbol, cfg, raw, f, btc, start, end):
    t, o, h, l, c = raw
    rows, excluded, last = [], Counter(), -10000
    for i in range(cfg["window"] + 3, len(t) - 1):
        setup = setup_at(i, cfg, raw, f)
        if setup is None: continue
        j, side = i + 1, -cfg["side"]
        if i - last < 16:
            excluded["COOLDOWN"] += 1; continue
        last = i
        if j >= len(t) or not start <= t[j] < end: continue
        if t[j] != t[i] + BAR:
            excluded["ENTRY_PATH_GAP"] += 1; continue
        entry = float(o[j]); atr = setup["prior_atr"]
        if not np.isfinite(entry) or entry <= 0:
            excluded["INVALID_ENTRY"] += 1; continue
        formation_side = cfg["side"]
        structural = (max(h[i-1], h[i]) + .10*atr if formation_side == 1
                      else min(l[i-1], l[i]) - .10*atr)
        raw_distance = abs(entry - structural)
        distance = max(raw_distance, .60*atr)
        if distance > 2.50*atr:
            excluded["STOP_ABOVE_2P5_ATR"] += 1; continue
        sl = entry - side*distance
        if sl <= 0:
            excluded["NONPOSITIVE_LEVEL"] += 1; continue
        score = setup["formation_efficiency"]*setup["formation_move"]*setup["confirmation_volume_multiple"]
        rows.append(dict(symbol=symbol,key=cfg["key"],signal_time=int(t[i]),decision_time=int(t[i]+BAR),
            entry_time=int(t[j]),entry_index=int(j),entry=entry,sl=float(sl),side=int(side),
            risk_pct=float(distance/entry),score=float(score),atr_mult=2.0,prior_atr=atr,
            buy_share=float(f["buy_share"][i]),volume_multiple=float(f["volume_multiple"][i]),
            formation_bars=cfg["window"],efficiency_threshold=cfg["efficiency"],
            flow_confirmation=cfg["confirmation"],failure_confirmation=cfg["confirmation"],
            known_entry_gap=float(side*(entry/c[i]-1)),btc_r1=float(btc["r1"][i]),
            clv=float(f["clv"][i]),coin_r1=float(f["r1"][i])))
        rows[-1].update(setup)
    return rows, excluded


def policy_rows(symbol, chosen, raw, f, btc, start, end):
    rows, counts, bad, grouped = [], Counter(), [], {}
    for p in chosen: grouped.setdefault(p["key"], []).append(p)
    for cfgs in grouped.values():
        seeds, excluded = intents(symbol, cfgs[0], raw, f, btc, start, end)
        counts.update({cfgs[0]["key"]+"/"+k:n for k,n in excluded.items()})
        for p in cfgs:
            for seed in seeds:
                risk=abs(seed["entry"]-seed["sl"])
                multiple=1.0 if p["exit_type"]=="R10" else 1.5
                tr=dict(seed,tp=float(seed["entry"]+seed["side"]*multiple*risk),max_hold_bars=p["hold"])
                result=v23.engine.canonical.resolve(tr,raw,f,"TRAIL" if p["exit_type"]=="TRAIL" else "TP2",end)
                counts[p["policy"]+"/"+result["status"]]+=1
                if result["status"]!="RESOLVED":
                    bad.append(dict(symbol=symbol,policy=p["policy"],entry_time=tr["entry_time"],status=result["status"]));continue
                row={k:v for k,v in tr.items() if k!="entry_index"};row.update(result,variant=p["policy"],policy=p["policy"],exit_type=p["exit_type"])
                row["hold_min"]=(row["exit_time"]-row["entry_time"])/60000
                exit_price=row["exit"]*(1-row["side"]*.001) if row["reason"]=="SL" else row["exit"]
                ratio=exit_price/row["entry"];net=row["side"]*(ratio-1)-.002*(1+ratio)-.0002*row["hold_min"]/1440
                row["net40_fraction"]=float(net);row["net40_R"]=float(net/v23.account.stop_loss_fraction(row,.002));rows.append(row)
    return rows,counts,bad


def bind_engine():
    e=v23.engine;e.CONTEXT=CONTEXT;e.COLUMNS=COLUMNS;e.LOG_PREFIX="V24_FAILED_RESUMPTION"
    e.configurations=configurations;e.policies=policies;e.load=v23.load;e.features=v23.features;e.intents=intents
    e.BTC_LOAD=v23.SOURCE_LOAD;e.BTC_FEATURES=v23.SOURCE_FEATURES;e.policy_rows=policy_rows


def scan(*args, **kwargs):
    bind_engine();kwargs.setdefault("context_path",CONTEXT);return v23.engine.scan(*args,**kwargs)


def select(parts,out,context_path=CONTEXT):
    bind_engine();v23.engine.select(parts,out,context_path)
    path=out/"selection.json";decision=json.loads(path.read_text())
    decision["union_name"]="FAILED_RESUMPTION_REVERSAL_UNION"
    decision["note"]="Frozen V24 failed-resumption reversal. Diagnostics are not executable account growth."
    path.write_text(json.dumps(decision,indent=2,allow_nan=False))


def accounts(*args,**kwargs):
    bind_engine();return v23.engine.accounts(*args,**kwargs)


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("scan")
    for name in ("data","btc","out","minute-cache","source-check"):p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--stage",choices=("DEV","GATE"),required=True);p.add_argument("--selection",type=Path)
    p=sub.add_parser("select");p.add_argument("--parts",type=Path,required=True);p.add_argument("--out",type=Path,required=True)
    p=sub.add_parser("accounts")
    for name in ("data","parts","out","selection"):p.add_argument("--"+name,type=Path,required=True)
    a=parser.parse_args()
    if a.command=="scan":scan(a.data,a.btc,a.out,a.minute_cache,a.stage,a.source_check,a.selection)
    elif a.command=="select":select(a.parts,a.out)
    else:accounts(a.data,a.parts,a.out,a.selection)


if __name__=="__main__":
    main()
