"""Preregistered V25 complete-UTC-day channel breakout research."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import path_efficiency_resumption_v23 as prior
from scripts import aggressive_flow_cascade_v11 as btc_source

BAR, DAY = prior.BAR, prior.DAY
ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "research/daily-channel-breakout-v25/FROZEN_CONTEXT.json"
HOLDS, EXITS = (192, 672), ("R10", "R20", "R40")
SOURCE_LOAD = prior.SOURCE_LOAD
ENGINE = prior.engine
COLUMNS = [
    "symbol", "key", "signal_time", "decision_time", "entry_time", "entry", "sl", "side",
    "risk_pct", "score", "atr_mult", "prior_atr", "buy_share", "volume_multiple",
    "channel_days", "btc_regime", "daily_start_time", "daily_close_time", "daily_atr20",
    "channel_level", "channel_input_cutoff", "atr_input_cutoff", "btc_daily_r20",
    "known_entry_gap", "coin_r1", "btc_r1", "clv", "tp", "max_hold_bars",
    "status", "exit_time", "exit", "reason", "gross_return", "variant", "policy",
    "exit_type", "hold_min", "net40_fraction", "net40_R", "split",
]


def configurations():
    return [dict(key=f"DAILY_S{s:+d}_D{d:02d}_{regime}_A{int(mult*10):02d}",
                 side=s, channel_days=d, btc_regime=regime, stop_atr=mult)
            for s in (1, -1) for d in (5, 20)
            for regime in ("ANY", "ALIGN20") for mult in (1.5, 2.5)]


def policies():
    return [dict(**cfg, hold=h, exit_type=e, policy=f'{cfg["key"]}__H{h}__{e}')
            for cfg in configurations() for h in HOLDS for e in EXITS]


def complete_days(raw):
    """Keep invalid-day markers so a missing/partial day breaks all roll windows."""
    t, o, h, l, c = raw
    if not len(t):
        return pd.DataFrame(columns=["day", "open", "high", "low", "close", "bar_index", "valid"])
    if np.any(np.diff(t) <= 0):
        raise ValueError("daily input timestamps must be strictly increasing")
    days, starts, counts = np.unique(t // DAY, return_index=True, return_counts=True)
    records = []
    for day, start, count in zip(days, starts, counts):
        end = start + count
        ok = count == 96 and np.array_equal(t[start:end], day*DAY + np.arange(96)*BAR)
        if ok:
            prices = np.array([o[start:end], h[start:end], l[start:end], c[start:end]])
            ok = (np.isfinite(prices).all() and (prices > 0).all()
                  and (h[start:end] >= np.maximum(o[start:end], c[start:end])).all()
                  and (l[start:end] <= np.minimum(o[start:end], c[start:end])).all()
                  and (h[start:end] >= l[start:end]).all())
        records.append(dict(day=int(day), open=float(o[start]), high=float(h[start:end].max()),
                            low=float(l[start:end].min()), close=float(c[end-1]),
                            bar_index=int(end-1), valid=bool(ok)))
    frame = pd.DataFrame(records)
    for name in ("atr20", "upper5", "lower5", "upper20", "lower20", "r20"):
        frame[name] = np.nan
    begin = 0
    while begin < len(frame):
        if not frame.valid.iloc[begin]:
            begin += 1
            continue
        end = begin + 1
        while end < len(frame) and frame.valid.iloc[end] and frame.day.iloc[end] == frame.day.iloc[end-1]+1:
            end += 1
        block = frame.iloc[begin:end]
        previous = block.close.shift(1)
        tr = np.maximum(block.high-block.low,
                        np.maximum(abs(block.high-previous), abs(block.low-previous)))
        frame.loc[block.index, "atr20"] = tr.rolling(20, min_periods=20).mean().shift(1)
        frame.loc[block.index, "r20"] = block.close / block.close.shift(20) - 1
        for width in (5, 20):
            frame.loc[block.index, f"upper{width}"] = block.high.rolling(width, min_periods=width).max().shift(1)
            frame.loc[block.index, f"lower{width}"] = block.low.rolling(width, min_periods=width).min().shift(1)
        begin = end
    return frame


def features(raw, q, buy):
    f = prior.history.features(raw, q, buy)
    # Common eligibility is exactly 30 observed days and >=20m last-closed-24h quote.
    f["session_vwap"] = raw[4].copy()  # shared scan compatibility diagnostic only
    f["_daily"] = complete_days(raw)
    return f


def align_context(t, br, bf):
    out = btc_source.align_context(t, br)
    out["daily_r20"] = np.full(len(t), np.nan)
    for row in bf["_daily"].itertuples():
        if not row.valid:
            continue
        ts = br[0][row.bar_index]
        j = np.searchsorted(t, ts)
        if j < len(t) and t[j] == ts:
            out["daily_r20"][j] = row.r20
    return out


def intents(symbol, cfg, raw, f, btc, start, end):
    t, o, h, l, c = raw
    daily, side, width = f["_daily"], cfg["side"], cfg["channel_days"]
    seeds, excluded, last_entry = [], Counter(), -10**18
    level_name = f"upper{width}" if side == 1 else f"lower{width}"
    for d in range(1, len(daily)):
        row, previous = daily.iloc[d], daily.iloc[d-1]
        if not row.valid or not previous.valid or row.day != previous.day+1:
            continue
        i, level, atr = int(row.bar_index), float(row[level_name]), float(row.atr20)
        if not (np.isfinite(level) and np.isfinite(atr) and atr > 0 and f["eligible"][i]):
            continue
        if side*(row.close-level) <= 0:
            continue
        # Price onset only: a regime change during an existing breakout is not a new signal.
        if np.isfinite(previous[level_name]) and side*(previous.close-previous[level_name]) > 0:
            continue
        j = i+1
        if j >= len(t) or not start <= t[j] < end:
            continue
        if t[j] != t[i]+BAR or t[j] != (int(row.day)+1)*DAY:
            excluded["ENTRY_PATH_GAP"] += 1
            continue
        regime_return = float(btc["daily_r20"][i])
        if cfg["btc_regime"] == "ALIGN20":
            if not np.isfinite(regime_return):
                excluded["BTC_DAILY_HISTORY_GAP"] += 1
                continue
            if side*regime_return <= 0:
                excluded["BTC_REGIME_OPPOSED"] += 1
                continue
        if t[j]-last_entry < 2*DAY:
            excluded["COOLDOWN"] += 1
            continue
        entry, distance = float(o[j]), cfg["stop_atr"]*atr
        if not np.isfinite(entry) or entry <= 0:
            excluded["INVALID_ENTRY"] += 1
            continue
        if distance / entry > .25:
            excluded["STOP_ABOVE_25PCT"] += 1
            continue
        stop = entry-side*distance
        if stop <= 0:
            excluded["NONPOSITIVE_LEVEL"] += 1
            continue
        last_entry = int(t[j])
        seeds.append(dict(symbol=symbol, key=cfg["key"], signal_time=int(t[i]),
            decision_time=int(t[i]+BAR), entry_time=int(t[j]), entry_index=int(j), entry=entry,
            sl=float(stop), side=side, risk_pct=float(distance/entry),
            score=float(side*(row.close-level)/atr), atr_mult=cfg["stop_atr"], prior_atr=atr,
            buy_share=float(f["buy_share"][i]), volume_multiple=float(f["volume_multiple"][i]),
            channel_days=width, btc_regime=cfg["btc_regime"], daily_start_time=int(row.day*DAY),
            daily_close_time=int((row.day+1)*DAY), daily_atr20=atr, channel_level=level,
            channel_input_cutoff=int(row.day*DAY), atr_input_cutoff=int(row.day*DAY),
            btc_daily_r20=regime_return, known_entry_gap=float(side*(entry/c[i]-1)),
            coin_r1=float(f["r1"][i]), btc_r1=float(btc["r1"][i]), clv=float(f["clv"][i])))
    return seeds, excluded


def policy_rows(symbol, chosen, raw, f, btc, start, end):
    rows, counts, bad, grouped = [], Counter(), [], {}
    for policy in chosen:
        grouped.setdefault(policy["key"], []).append(policy)
    for group in grouped.values():
        seeds, excluded = intents(symbol, group[0], raw, f, btc, start, end)
        counts.update({group[0]["key"]+"/"+key:count for key,count in excluded.items()})
        for policy in group:
            multiple = {"R10":1., "R20":2., "R40":4.}[policy["exit_type"]]
            for seed in seeds:
                risk = abs(seed["entry"]-seed["sl"])
                target = seed["entry"]+seed["side"]*multiple*risk
                if target <= 0:
                    counts[policy["policy"]+"/NONPOSITIVE_TP"] += 1
                    continue
                trade = dict(seed, tp=float(target), max_hold_bars=policy["hold"])
                result = ENGINE.canonical.resolve(trade, raw, f, "TP2", end)
                counts[policy["policy"]+"/"+result["status"]] += 1
                if result["status"] != "RESOLVED":
                    bad.append(dict(symbol=symbol, policy=policy["policy"],
                                    entry_time=trade["entry_time"], status=result["status"]))
                    continue
                row = {key:value for key,value in trade.items() if key != "entry_index"}
                row.update(result, variant=policy["policy"], policy=policy["policy"], exit_type=policy["exit_type"])
                row["hold_min"] = (row["exit_time"]-row["entry_time"])/60000
                exit_price = row["exit"]*(1-row["side"]*.001) if row["reason"] in {"SL", "SPLIT_END"} else row["exit"]
                ratio = exit_price/row["entry"]
                net = row["side"]*(ratio-1)-.002*(1+ratio)-.0002*row["hold_min"]/1440
                row["net40_fraction"] = float(net)
                row["net40_R"] = float(net/prior.account.stop_loss_fraction(row, .002))
                rows.append(row)
    return rows, counts, bad


@contextmanager
def bound_engine():
    """Restore shared engine globals even when scan/selection fails."""
    settings = dict(CONTEXT=CONTEXT, COLUMNS=COLUMNS, LOG_PREFIX="V25_DAILY_CHANNEL",
                    configurations=configurations, policies=policies, load=SOURCE_LOAD,
                    features=features, intents=intents, policy_rows=policy_rows,
                    BTC_LOAD=SOURCE_LOAD, BTC_FEATURES=features, align_context=align_context)
    saved = {key:getattr(ENGINE,key) for key in settings}
    try:
        for key,value in settings.items():
            setattr(ENGINE,key,value)
        yield
    finally:
        for key,value in saved.items():
            setattr(ENGINE,key,value)


def scan(*args, **kwargs):
    kwargs.setdefault("context_path", CONTEXT)
    with bound_engine():
        return ENGINE.scan(*args, **kwargs)


def select(parts, out, context_path=CONTEXT):
    with bound_engine():
        ENGINE.select(parts, out, context_path)
    path = out/"selection.json"
    decision = json.loads(path.read_text())
    decision.update(union_name="DAILY_CHANNEL_BREAKOUT_UNION",
                    note="Preregistered V25 daily breakout; independent policy outcomes are not executable account trades.")
    path.write_text(json.dumps(decision, indent=2, allow_nan=False))
    selection_hash = prior.digest(path)
    for meta in out.glob("dev-*/scan_meta.json"):
        value=json.loads(meta.read_text());value["selection_sha256"]=selection_hash
        meta.write_text(json.dumps(value, indent=2))


def accounts(*args, **kwargs):
    with bound_engine():
        return ENGINE.accounts(*args, **kwargs)


def smoke(out):
    out.mkdir(parents=True, exist_ok=True)
    n=64*96;ts=prior.base.START+np.arange(n,dtype=np.int64)*BAR
    bp=out/"BTCUSDT.csv.gz"
    def write(path, price):
        close=np.full(n,price)
        pd.DataFrame(dict(open_time=ts,open=close,high=close*1.01,low=close*.99,
                          close=close,quote_volume=np.full(n,1e6),taker_buy_quote=np.full(n,5e5))).to_csv(path,index=False,compression="gzip")
    write(bp,10000.)
    hashes={}
    for shard in range(8):
        folder=out/"market"/str(shard);folder.mkdir(parents=True,exist_ok=True)
        symbol=f"X{shard:02d}USDT";path=folder/f"{symbol}.csv.gz"
        write(path,100.);hashes[symbol]=prior.digest(path)
    context_path=out/"context.json"
    context_path.write_text(json.dumps(dict(baseline_sha256="a"*64,btc_sha256=prior.digest(bp),expected_market_sha256=hashes)))
    for shard,(symbol,digest) in enumerate(hashes.items()):
        check=out/f"check-{shard}.json"
        check.write_text(json.dumps(dict(status="VERIFIED",shards=[shard],baseline_sha256="a"*64,
                                        files=[dict(symbol=symbol,sha256=digest)])))
        scan(out/"market"/str(shard),bp,out/"parts"/str(shard),out/"minute-cache","DEV",check,context_path=context_path)
    select(out/"parts",out/"selected",context_path)
    cells=pd.read_csv(out/"selected/development_policy_cells.csv")
    assert len(cells)==96 and cells.n.sum()==0
    report=dict(synthetic_only=True,scans=8,all96_cells_preserved=True,market_profitability_claim=False)
    (out/"smoke.json").write_text(json.dumps(report,indent=2))
    print("V25_SYNTHETIC_PIPELINE_PASS",json.dumps(report),flush=True)


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True)
    cmd=sub.add_parser("scan")
    for name in ("data","btc","out","minute-cache","source-check"):
        cmd.add_argument("--"+name,type=Path,required=True)
    cmd.add_argument("--stage",choices=("DEV","GATE"),required=True);cmd.add_argument("--selection",type=Path)
    cmd=sub.add_parser("select");cmd.add_argument("--parts",type=Path,required=True);cmd.add_argument("--out",type=Path,required=True)
    cmd=sub.add_parser("accounts")
    for name in ("data","parts","out","selection"):
        cmd.add_argument("--"+name,type=Path,required=True)
    cmd=sub.add_parser("smoke");cmd.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    if args.command=="scan":scan(args.data,args.btc,args.out,args.minute_cache,args.stage,args.source_check,args.selection)
    elif args.command=="select":select(args.parts,args.out)
    elif args.command=="accounts":accounts(args.data,args.parts,args.out,args.selection)
    else:smoke(args.out)


if __name__=="__main__":
    main()
