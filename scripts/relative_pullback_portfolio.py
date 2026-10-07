"""Account replay with MTM, Korea calendar days and frozen risk controls."""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd

from scripts.psar_1d_canonical_engine import load_raw
from scripts.relative_pullback_v1 import BAR, DAY, START, CUT, END, MAX_HOLD

RISK = .005
OPEN_RISK = .02
NOTIONAL_CAP = .30
GROSS_CAP = 2.0
MAX_POSITIONS = 6
DAY_STOP = .02
DD_HALF = .10
DD_HALT = .15
STOP_SLIP = .001
FUND_PER_DAY = .0002
KOREA_OFFSET = 9 * 3_600_000


def korea_day(ts):
    return (int(ts) + KOREA_OFFSET) // DAY


def pf(values):
    x = np.asarray(values, dtype=float)
    gain, loss = float(x[x > 0].sum()), float(-x[x < 0].sum())
    return gain / loss if loss > 0 else None


def losing_streak(values):
    longest = current = 0
    for x in values:
        current = current + 1 if x < 0 else 0
        longest = max(longest, current)
    return longest


class Market:
    def __init__(self, raw):
        self.raw = raw

    def price(self, symbol, ts):
        t, o, h, l, c = self.raw[symbol]
        j = int(np.searchsorted(t, ts))
        if j < len(t) and t[j] == ts:
            return float(o[j])
        if j > 0 and t[j - 1] + BAR == ts:
            return float(c[j - 1])
        raise ValueError(f"missing authoritative boundary mark {symbol} {ts}")


def liquidation_result(position, price, ts, fee, forced=False):
    tr = position["trade"]
    slip = STOP_SLIP if forced or tr["reason"] in {"SL", "SPLIT_END"} else 0.0
    fill = float(price) * (1 - tr["side"] * slip)
    gross = tr["side"] * position["qty"] * (fill - tr["entry"])
    exit_fee = position["qty"] * fill * fee
    funding = position["notional"] * FUND_PER_DAY * max(0, ts - tr["entry_time"]) / DAY
    return gross - exit_fee - funding, fill, funding, exit_fee


def stop_loss_fraction(tr, fee):
    """Budget actual adverse SL fill, both fees, and maximum-hold funding."""
    price_ratio = tr["sl"] / tr["entry"] * (1 - tr["side"] * STOP_SLIP)
    price_loss = tr["side"] * (1 - price_ratio)
    max_hold = int(tr.get('max_hold_bars', MAX_HOLD))
    if not 0 < max_hold <= 7 * DAY // BAR:
        raise ValueError('holding budget must be within seven days')
    return price_loss + fee * (1 + price_ratio) + FUND_PER_DAY * max_hold * BAR / DAY


def simulate(ledger, market, start, end, cost_bps=20, guarded=True, all_kst_days=False):
    if end <= start or (end - start) % BAR:
        raise ValueError('invalid account interval')
    fee = cost_bps / 20000
    entries = defaultdict(list)
    for row in ledger.to_dict("records"):
        if not start <= row["entry_time"] < end or row["exit_time"] > end:
            raise ValueError("split leakage")
        if row["exit_time"] <= row["entry_time"]:
            raise ValueError("nonpositive holding chronology")
        entries[int(row["entry_time"])].append(row)
    for rows in entries.values():
        rows.sort(key=lambda r: (-r["score"], r["symbol"]))
    cash, peak, mdd = 1.0, 1.0, 0.0
    active = {}
    trades, daily, curve = [], [], []
    rejections = Counter()
    day = korea_day(start)
    day_begin = start
    day_base = 1.0
    day_entries = 0
    day_active = False
    blocked, reduced, halted = False, False, False
    halt_time = half_time = None
    triggers = []
    concurrent_sum = exposure_sum = 0.0
    max_concurrent = 0
    max_exposure = max_open_risk_at_entry = 0.0

    def equity(ts):
        # Net liquidation value includes open P&L, liquidation fees/slip and funding.
        return cash + sum(liquidation_result(p, market.price(sym, ts), ts, fee, forced=True)[0]
                          for sym, p in active.items())

    def close(symbol, ts, price, reason, forced=False):
        nonlocal cash
        p = active.pop(symbol)
        tr = p["trade"]
        delta, fill, funding, exit_fee = liquidation_result(p, price, ts, fee, forced)
        cash += delta
        pnl = delta - p["entry_fee"]
        trades.append({"symbol": symbol, "side": tr["side"], "entry_time": tr["entry_time"],
                       "exit_time": int(ts), "entry": tr["entry"], "exit": fill, "reason": reason,
                       "notional": p["notional"], "entry_equity": p["entry_equity"],
                       "reserved_risk": p["reserved_risk"], "net_pnl": pnl,
                       "account_pct": 100 * pnl / p["entry_equity"],
                       "position_pct": 100 * pnl / p["notional"],
                       "hold_min": (ts - tr["entry_time"]) / 60000,
                       "entry_fee": p["entry_fee"], "exit_fee": exit_fee, "funding": funding})

    def append_day(until, end_equity):
        full = until - day_begin == DAY and (day_begin + KOREA_OFFSET) % DAY == 0
        if not all_kst_days and not full:
            return
        row = {"day": pd.Timestamp(day * DAY, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
               "start_equity": day_base, "end_equity": end_equity,
               "return_pct": 100 * (end_equity / day_base - 1),
               "entries": day_entries, "active": day_active}
        if all_kst_days:
            row.update(partial_day=not full, covered_hours=(until - day_begin) / 3_600_000)
        daily.append(row)

    for ts in range(start, end + BAR, BAR):
        # Earlier intrabar exits are credited only once; no early slot release.
        due = sorted((int(p["trade"]["exit_time"]), sym) for sym, p in active.items()
                     if p["trade"]["exit_time"] <= ts)
        for xt, sym in due:
            tr = active[sym]["trade"]
            close(sym, xt, tr["exit"], tr["reason"])
        eq = equity(ts)
        pre_entry_eq = eq
        today = korea_day(ts)
        if today != day:
            append_day(ts, eq)
            day, day_begin, day_base = today, ts, eq
            day_entries = 0
            day_active = bool(active)
            blocked = False
        peak = max(peak, eq)
        dd = (peak - eq) / peak
        mdd = max(mdd, dd)
        if guarded and not halted and dd >= DD_HALT:
            for sym in sorted(list(active)):
                close(sym, ts, market.price(sym, ts), "DD_HALT", forced=True)
            halted, halt_time = True, ts
            triggers.append({"kind": "DD_HALT", "time": ts, "observed_drawdown_pct": dd * 100})
            eq = cash
        elif guarded and not reduced and dd >= DD_HALF:
            reduced, half_time = True, ts
        day_return = eq / day_base - 1
        if guarded and not blocked and not halted and day_return <= -DAY_STOP:
            for sym in sorted(list(active)):
                close(sym, ts, market.price(sym, ts), "DAY_STOP", forced=True)
            blocked = True
            triggers.append({"kind": "DAY_STOP", "time": ts, "observed_day_return_pct": day_return * 100})
            eq = cash
        if ts < end:
            for tr in entries.get(ts, []):
                if halted or blocked:
                    rejections["halted" if halted else "day_blocked"] += 1
                    continue
                sym = tr["symbol"]
                if sym in active:
                    rejections["same_symbol"] += 1
                    continue
                if len(active) >= MAX_POSITIONS:
                    rejections["slots"] += 1
                    continue
                eq = equity(ts)
                if eq <= 0:
                    raise ValueError("account insolvency")
                per_risk = RISK / 2 if guarded and reduced else RISK
                total_risk = OPEN_RISK / 2 if guarded and reduced else OPEN_RISK
                sf = stop_loss_fraction(tr, fee)
                if not np.isfinite(sf) or sf <= 0:
                    raise ValueError("invalid reserved stop risk")
                reserved = sum(p["reserved_risk"] for p in active.values())
                gross = sum(p["qty"] * market.price(s, ts) for s, p in active.items())
                risk_room = max(0., total_risk * eq - reserved)
                gross_room = max(0., GROSS_CAP * eq - gross)
                # Adjust aggregate headroom for the new position's liquidation costs.
                close_ratio = 1 - tr["side"] * STOP_SLIP
                liquidation_drag = fee + STOP_SLIP + close_ratio * fee
                notional = min(NOTIONAL_CAP * eq, per_risk * eq / sf,
                               risk_room / (sf + total_risk * liquidation_drag),
                               gross_room / (1 + GROSS_CAP * liquidation_drag))
                if notional < 1e-8 * eq:
                    rejections["risk_or_exposure"] += 1
                    continue
                if not np.isclose(market.price(sym, ts), tr["entry"], rtol=1e-7, atol=1e-10):
                    raise ValueError("entry/market mismatch")
                entry_fee = notional * fee
                cash -= entry_fee
                active[sym] = {"trade": tr, "qty": notional / tr["entry"], "notional": notional,
                               "entry_fee": entry_fee, "entry_equity": eq, "reserved_risk": notional * sf}
                eq = equity(ts)
                max_open_risk_at_entry = max(max_open_risk_at_entry,
                                             sum(p["reserved_risk"] for p in active.values()) / eq)
                day_entries += 1
                day_active = True
        eq = equity(ts)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
        gross = sum(p["qty"] * market.price(s, ts) for s, p in active.items())
        exposure = gross / eq if eq > 0 else 0.0
        max_exposure = max(max_exposure, exposure)
        max_concurrent = max(max_concurrent, len(active))
        if ts < end:
            concurrent_sum += len(active)
            exposure_sum += exposure
        day_active |= bool(active)
        curve.append({"time": ts, "equity": eq, "equity_pre_entry": pre_entry_eq, "positions": len(active), "gross_pct": exposure * 100,
                      "reduced": reduced, "halted": halted})
    if active:
        raise ValueError("unclosed position at split end")
    if all_kst_days and end > day_begin:
        append_day(end, cash)
    rdf, ddf = pd.DataFrame(trades), pd.DataFrame(daily)
    ordered = sorted(trades, key=lambda r: (r["exit_time"], r["symbol"]))
    pnls = [r["net_pnl"] for r in ordered]
    years = (end - start) / (365.25 * DAY)
    durations = [r["hold_min"] for r in trades]
    ticks = (end - start) / BAR
    summary = {"cost_bps": cost_bps, "guarded": guarded, "net_return_pct": 100 * (cash - 1),
               "cagr_pct": 100 * (cash ** (1 / years) - 1) if cash > 0 else -100.,
               "mdd_15m_pct": 100 * mdd, "pf": pf(pnls), "trades": len(trades),
               "win_pct": 100 * float(np.mean(np.asarray(pnls) > 0)) if pnls else None,
               "mean_trade_account_pct": float(np.mean([r["account_pct"] for r in trades])) if trades else None,
               "max_loss_streak": losing_streak(pnls), "avg_hold_min": float(np.mean(durations)) if durations else None,
               "max_hold_min": float(max(durations)) if durations else None,
               "avg_concurrent": concurrent_sum / ticks, "max_concurrent": max_concurrent,
               "avg_gross_pct": 100 * exposure_sum / ticks, "max_gross_pct": 100 * max_exposure,
               "max_reserved_risk_at_entry_pct": max_open_risk_at_entry * 100,
               "risk_half_time": half_time, "halt_time": halt_time, "guard_triggers": triggers,
               "rejections": dict(rejections), "calendar_days": len(daily),
               "calendar_scope": "ALL_INTERSECTED_KST_DATES" if all_kst_days else "COMPLETE_KST_DATES_ONLY",
               "partial_calendar_days": sum(row.get('partial_day', False) for row in daily)}
    if len(ddf):
        returns = ddf.return_pct.to_numpy(float)
        summary.update({"daily_mean_pct": float(returns.mean()),
                        "daily_geometric_pct": 100 * (np.prod(1 + returns / 100) ** (1 / len(returns)) - 1),
                        "day_ge_0_7_pct": float(np.mean(returns >= .7) * 100),
                        "day_ge_2_pct": float(np.mean(returns >= 2.) * 100),
                        "loss_days_pct": float(np.mean(returns < 0) * 100),
                        "no_entry_days_pct": float(np.mean(ddf.entries == 0) * 100),
                        "flat_days_pct": float(np.mean(~ddf.active) * 100),
                        "worst_day_pct": float(returns.min()), "best_day_pct": float(returns.max())})
        if all_kst_days:
            assert len(ddf) == korea_day(end - 1) - korea_day(start) + 1
            assert np.isclose(np.prod(1 + returns / 100), cash, atol=1e-10)
    return summary, rdf, ddf, pd.DataFrame(curve)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("relative-pullback-results"))
    args = ap.parse_args()
    ledger = pd.read_csv(args.out / "independent_candidates.csv.gz")
    raw = {sym: load_raw(args.data / f"{sym}.csv.gz") for sym in ledger.symbol.unique()}
    market = Market(raw)
    summaries, periods = [], []
    for split, start, end in (("TRAIN", START, CUT), ("HOLDOUT", CUT, END)):
        for mode, side in (("BOTH", None), ("LONG", 1), ("SHORT", -1)):
            selected = ledger[ledger.split == split]
            if side is not None:
                selected = selected[selected.side == side]
            for cost in (20, 40):
                for guarded in (True, False):
                    key = f"{split}_{mode}_{cost}bp_{'guarded' if guarded else 'diagnostic'}"
                    result, trades, daily, curve = simulate(selected, market, start, end, cost, guarded)
                    result.update({"split": split, "side_mode": mode, "independent_n": len(selected)})
                    summaries.append(result)
                    trades.to_csv(args.out / f"{key}_trades.csv.gz", index=False, compression="gzip")
                    daily.to_csv(args.out / f"{key}_daily.csv", index=False)
                    curve.to_csv(args.out / f"{key}_curve.csv.gz", index=False, compression="gzip")
                    if len(daily):
                        dates = pd.to_datetime(daily.day)
                        for frequency in ("Y", "Q"):
                            for period, group in daily.groupby(dates.dt.to_period(frequency)):
                                periods.append({"scenario": key, "period": str(period),
                                                "net_return_pct": 100 * (np.prod(1 + group.return_pct / 100) - 1),
                                                "days": len(group)})
                    print("ACCOUNT", key, json.dumps({k: result.get(k) for k in
                          ("trades", "net_return_pct", "mdd_15m_pct", "pf", "day_ge_0_7_pct")}), flush=True)
    (args.out / "summary.json").write_text(json.dumps(summaries, indent=2, allow_nan=False))
    flat = [{k: v for k, v in row.items() if not isinstance(v, (dict, list))} for row in summaries]
    pd.DataFrame(flat).to_csv(args.out / "summary.csv", index=False)
    pd.DataFrame(periods).to_csv(args.out / "year_quarter.csv", index=False)
    lines = ["# Relative Trend Pullback V1 — account results", "",
             "One configuration frozen before outcomes; no holdout selection. All return targets use net account equity.",
             "20/40bp round-trip costs + 10bp adverse stop/forced-close slip + 2bp/day funding stress charge.",
             "Guarded: 0.5% risk/trade, 2% aggregate reserved risk, 30% notional/coin, 6 positions, 200% gross cap;",
             "Korea-day -2% flatten; high-water -10% halves future entry risk, -15% flattens and halts.",
             "MTM and account guards observed every 15m; intraminute losses can exceed limits.",
             "Days include inactive days; only complete Korea calendar days count in daily/quarterly statistics.",
             "Diagnostics retain the same portfolio constraints but disable daily/DD guards; they expose edge hidden by stopping early.",
             "Fixed 18-symbol long-history basket has selection/survivorship limitations. Historical actual funding is not reconstructed.",
             "",
             "| Split | Side | Cost bp | Guards | N | Return % | MDD % | PF | Daily mean % | Days >=0.7% | Days >=2% | Flat days | Halted |",
             "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    def fmt(x):
        return "—" if x is None else f"{x:.3f}"
    for r in summaries:
        lines.append(f"| {r['split']} | {r['side_mode']} | {r['cost_bps']} | {r['guarded']} | {r['trades']} | "
                     f"{fmt(r['net_return_pct'])} | {fmt(r['mdd_15m_pct'])} | {fmt(r['pf'])} | "
                     f"{fmt(r.get('daily_mean_pct'))} | {fmt(r.get('day_ge_0_7_pct'))}% | "
                     f"{fmt(r.get('day_ge_2_pct'))}% | {fmt(r.get('flat_days_pct'))}% | {r['halt_time'] is not None} |")
    lines += ["", "A completed workflow is not a strategy pass. Signal thresholds are unchanged across all rows.",
              "Use the independent ledger and scan_meta.json for explicit DATA_GAP / ENTRY_MISMATCH / EXIT_MISMATCH counts."]
    (args.out / "REPORT.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
