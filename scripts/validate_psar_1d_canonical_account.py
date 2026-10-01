"""Frozen PSAR 1D SHORT account-capacity and mark-to-market validation.

This does not retune the signal.  It replays the frozen Age=3 / D0 / 7D+reflip
selected-trade ledger under account constraints, then reconstructs 15m MTM risk.
"""
import argparse
import glob
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

DAY = 86_400_000
STEP = 900_000
CUT = 1_735_689_600_000
POSITION_FRACTION = 0.30
MAX_POSITIONS = 6
MAX_GROSS = 2.00
COSTS_PCT = (0.20, 0.40, 0.80)


def symbol_from_csv(path):
    s = Path(path).name[:-7].upper()
    if not re.fullmatch(r"[A-Z0-9]+USDT", s):
        raise ValueError(path)
    return s


def find_one(root, name):
    xs = glob.glob(str(Path(root) / "**" / name), recursive=True)
    assert len(xs) == 1, (name, len(xs), xs[:5])
    return xs[0]


def load_selected(root):
    trades = pd.read_csv(find_one(root, "selected_trades.csv.gz"))
    freeze = json.loads(Path(find_one(root, "frozen_train_selection.json")).read_text())
    assert int(freeze["selection"]) == 7
    assert np.isclose(float(freeze["D0_threshold"]), 2.0546962455657463, rtol=0, atol=1e-14)
    need = {"symbol","ts","exit_ts","entry_open","gross_return_pct","hold_days","period","d0"}
    assert need.issubset(trades.columns), sorted(need - set(trades.columns))
    trades = trades.sort_values(["ts","symbol"], kind="mergesort").reset_index(drop=True)
    for c in ["ts","exit_ts","hold_days"]:
        trades[c] = pd.to_numeric(trades[c], errors="raise").astype("int64")
    for c in ["entry_open","gross_return_pct","d0"]:
        trades[c] = pd.to_numeric(trades[c], errors="raise").astype(float)
    assert not trades.duplicated(["symbol","ts"]).any()
    assert (trades.exit_ts > trades.ts).all()
    assert trades.hold_days.between(1,7).all()
    assert (trades.exit_ts == trades.ts + trades.hold_days * DAY).all()
    assert set(trades.period.unique()) <= {"TRAIN","HOLDOUT"}
    assert (trades.loc[trades.period.eq("TRAIN"), "ts"] < CUT).all()
    assert (trades.loc[trades.period.eq("HOLDOUT"), "ts"] >= CUT).all()
    return trades, freeze


def load_daily_marks(reconciliation_root, symbols):
    marks = {}
    parts = defaultdict(list)
    for path in glob.glob(str(Path(reconciliation_root) / "**" / "daily" / "*.npz"), recursive=True):
        sym = Path(path).name.split("_",1)[0].upper()
        if sym not in symbols:
            continue
        z = np.load(path)
        parts[sym].append(pd.DataFrame({
            "ts": z["t"].astype(np.int64),
            "open": z["o"].astype(float),
            "high": z["h"].astype(float),
            "low": z["l"].astype(float),
        }))
    assert set(parts) == set(symbols), (len(parts), len(symbols), sorted(set(symbols)-set(parts))[:20])
    for sym, ps in parts.items():
        d = pd.concat(ps, ignore_index=True).sort_values("ts", kind="mergesort")
        if d.duplicated("ts").any():
            g = d.groupby("ts")
            assert (g.open.nunique() == 1).all()
            assert (g.high.nunique() == 1).all()
            assert (g.low.nunique() == 1).all()
            d = d.drop_duplicates("ts", keep="first")
        assert (np.diff(d.ts.to_numpy(np.int64)) > 0).all()
        marks[sym] = {
            "open": dict(zip(d.ts.astype(np.int64), d.open.astype(float))),
            "high": dict(zip(d.ts.astype(np.int64), d.high.astype(float))),
            "low": dict(zip(d.ts.astype(np.int64), d.low.astype(float))),
        }
    return marks


def max_loss_streak(values):
    best = cur = 0
    for v in values:
        if v < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return int(best)


def summarize_closed(closed):
    if not closed:
        return {"pf":None,"win_pct":None,"max_consecutive_trade_losses":0,
                "max_consecutive_losing_exit_events":0}
    d = pd.DataFrame(closed).sort_values(["exit_ts","symbol"], kind="mergesort")
    pnl = d.net_pnl.to_numpy(float)
    gp = pnl[pnl > 0].sum()
    gl = -pnl[pnl < 0].sum()
    event = d.groupby("exit_ts", sort=True).net_pnl.sum().to_numpy(float)
    return {
        "pf": float(gp/gl) if gl > 0 else None,
        "win_pct": float((pnl > 0).mean()*100),
        "max_consecutive_trade_losses": max_loss_streak(pnl),
        "max_consecutive_losing_exit_events": max_loss_streak(event),
    }


def replay_daily(signals, marks, cost_pct):
    signals = signals.sort_values(["ts","symbol"], kind="mergesort")
    by_ts = {int(t):g for t,g in signals.groupby("ts", sort=True)}
    start = int(signals.ts.min())
    end = int(signals.exit_ts.max())
    realized = 1.0
    openp = {}
    executed = []
    closed = []
    skips = defaultdict(int)
    max_simultaneous_signals = 0
    signal_days_over_capacity = 0
    daily_equity = []
    bankrupt_daily = False
    bankrupt_daily_ts = None
    half_fee = (cost_pct/100.0)/2.0

    def marked_equity(ts):
        unreal = 0.0
        for p in openp.values():
            px = marks[p["symbol"]]["open"].get(ts)
            assert px is not None, ("missing daily mark", p["symbol"], ts, p["entry_ts"], p["exit_ts"])
            unreal += p["notional"] * (1.0 - float(px)/p["entry_open"])
        return realized + unreal

    for ts in range(start, end + DAY, DAY):
        # Mark before exits; exits occur at this OPEN.
        eq_pre = marked_equity(ts) if openp else realized
        due = sorted([p for p in openp.values() if p["exit_ts"] == ts],
                     key=lambda p:p["symbol"])
        for p in due:
            px = marks[p["symbol"]]["open"].get(ts)
            assert px is not None
            gross_pct = (1.0 - float(px)/p["entry_open"]) * 100.0
            assert np.isclose(gross_pct, p["gross_return_pct"], rtol=1e-9, atol=1e-8), (
                p["symbol"], ts, gross_pct, p["gross_return_pct"])
            gross_pnl = p["notional"] * gross_pct/100.0
            exit_fee = p["notional"] * half_fee
            realized += gross_pnl - exit_fee
            net_pnl = p["notional"] * (gross_pct - cost_pct)/100.0
            closed.append({"symbol":p["symbol"],"entry_ts":p["entry_ts"],"exit_ts":ts,
                           "notional":p["notional"],"gross_return_pct":gross_pct,
                           "net_pnl":net_pnl})
            del openp[p["symbol"]]

        eq = marked_equity(ts) if openp else realized
        if eq <= 0:
            bankrupt_daily = True
            bankrupt_daily_ts = ts
            break

        g = by_ts.get(ts)
        if g is not None:
            g = g.sort_values("symbol", kind="mergesort")
            max_simultaneous_signals = max(max_simultaneous_signals, len(g))
            if len(g) > max(0, MAX_POSITIONS-len(openp)):
                signal_days_over_capacity += 1
            snapshot = eq
            intended = POSITION_FRACTION * snapshot
            for r in g.itertuples(index=False):
                sym = str(r.symbol)
                if sym in openp:
                    skips["same_symbol"] += 1
                    continue
                if len(openp) >= MAX_POSITIONS:
                    skips["max_positions"] += 1
                    continue
                gross = sum(p["notional"] for p in openp.values())
                if gross + intended > MAX_GROSS * snapshot + 1e-12:
                    skips["gross_cap"] += 1
                    continue
                entry_px = marks[sym]["open"].get(ts)
                assert entry_px is not None
                assert np.isclose(float(entry_px), float(r.entry_open), rtol=1e-10, atol=1e-10)
                entry_fee = intended * half_fee
                realized -= entry_fee
                p = {
                    "symbol":sym, "entry_ts":ts, "exit_ts":int(r.exit_ts),
                    "entry_open":float(r.entry_open), "gross_return_pct":float(r.gross_return_pct),
                    "hold_days":int(r.hold_days), "d0":float(r.d0), "notional":float(intended),
                    "cost_pct":float(cost_pct), "period":str(r.period),
                }
                openp[sym] = p
                executed.append(dict(p))

        eq_after = marked_equity(ts) if openp else realized
        daily_equity.append((ts, eq_after, len(openp),
                             sum(p["notional"] for p in openp.values())))

    if not bankrupt_daily:
        assert not openp, ("positions remain", len(openp), sorted(openp)[:5])
    summary = summarize_closed(closed)
    summary.update({
        "cost_pct":cost_pct,
        "raw_signals":int(len(signals)),
        "actual_trade_count":int(len(executed)),
        "closed_trade_count":int(len(closed)),
        "skipped_same_symbol":int(skips["same_symbol"]),
        "skipped_max_positions":int(skips["max_positions"]),
        "skipped_gross_cap":int(skips["gross_cap"]),
        "max_simultaneous_signals":int(max_simultaneous_signals),
        "signal_days_over_available_slots":int(signal_days_over_capacity),
        "daily_accounting_final_equity":float(realized) if not bankrupt_daily else 0.0,
        "daily_accounting_return_pct":float((realized-1)*100) if not bankrupt_daily else -100.0,
        "daily_bankrupt":bool(bankrupt_daily),
        "daily_bankrupt_ts":bankrupt_daily_ts,
        "selection_rule":"exits first; simultaneous entries sort symbol ASC; one pre-entry MTM equity snapshot sizes every accepted signal at 30%",
    })
    return {"summary":summary,"executed":executed,"closed":closed,"daily_equity":daily_equity}


def build_raw_file_map(data_root):
    m = defaultdict(list)
    for path in glob.glob(str(Path(data_root) / "**" / "*.csv.gz"), recursive=True):
        try:
            s = symbol_from_csv(path)
        except ValueError:
            continue
        m[s].append(path)
    return m


def drawdown(equity):
    equity = np.asarray(equity, float)
    if len(equity) == 0:
        return None, None
    bad = np.flatnonzero(equity <= 0)
    if len(bad):
        return 100.0, int(bad[0])
    peak = np.maximum.accumulate(equity)
    dd = (peak-equity)/peak*100.0
    i = int(np.argmax(dd))
    return float(dd[i]), i


def mark_15m(portfolios, data_root):
    # One common 15m grid lets us load every raw symbol only once for all period/cost portfolios.
    active = [(k,p) for k,p in portfolios.items() if p["executed"]]
    assert active
    t0 = min(p["executed"][0]["entry_ts"] for _,p in active)
    t1 = max(max(x["exit_ts"] for x in p["executed"]) for _,p in active)
    grid = np.arange(t0, t1 + STEP, STEP, dtype=np.int64)
    n = len(grid)
    states = {}
    by_symbol = defaultdict(list)

    for key,p in active:
        cashflow = np.zeros(n, float)
        gross_diff = np.zeros(n+1, float)
        count_diff = np.zeros(n+1, float)
        unreal_open = np.zeros(n, float)
        unreal_high = np.zeros(n, float)
        half_fee = (float(p["summary"]["cost_pct"])/100.0)/2.0
        for j,tr in enumerate(p["executed"]):
            ei = int((tr["entry_ts"]-t0)//STEP)
            xi = int((tr["exit_ts"]-t0)//STEP)
            assert grid[ei] == tr["entry_ts"] and grid[xi] == tr["exit_ts"]
            cashflow[ei] -= tr["notional"]*half_fee
            cashflow[xi] += tr["notional"]*tr["gross_return_pct"]/100.0 - tr["notional"]*half_fee
            gross_diff[ei] += tr["notional"]; gross_diff[xi] -= tr["notional"]
            count_diff[ei] += 1; count_diff[xi] -= 1
            by_symbol[tr["symbol"]].append((key,j))
        states[key] = {
            "cashflow":cashflow,"gross_diff":gross_diff,"count_diff":count_diff,
            "unreal_open":unreal_open,"unreal_high":unreal_high,
            "max_high_ratio":np.ones(len(p["executed"]), float),
            "bars_seen":np.zeros(len(p["executed"]), np.int64),
        }

    fmap = build_raw_file_map(data_root)
    missing = sorted(set(by_symbol)-set(fmap))
    assert not missing, missing[:20]

    for si,sym in enumerate(sorted(by_symbol)):
        chunks=[]
        for path in fmap[sym]:
            d=pd.read_csv(path,usecols=["open_time","open","high"])
            d["open_time"]=pd.to_numeric(d.open_time,errors="raise").astype("int64")
            d["open"]=pd.to_numeric(d.open,errors="raise").astype(float)
            d["high"]=pd.to_numeric(d.high,errors="raise").astype(float)
            chunks.append(d)
        d=pd.concat(chunks,ignore_index=True).sort_values("open_time",kind="mergesort")
        assert not d.duplicated("open_time").any(), sym
        tt=d.open_time.to_numpy(np.int64); oo=d.open.to_numpy(float); hh=d.high.to_numpy(float)
        assert (np.diff(tt)>0).all()
        for key,j in by_symbol[sym]:
            tr=portfolios[key]["executed"][j]
            a=np.searchsorted(tt,tr["entry_ts"]); b=np.searchsorted(tt,tr["exit_ts"])
            assert a<len(tt) and tt[a]==tr["entry_ts"], (sym,tr["entry_ts"])
            assert b<len(tt) and tt[b]==tr["exit_ts"], (sym,tr["exit_ts"])
            assert b>a
            ex=(1.0-float(oo[b])/tr["entry_open"])*100.0
            assert np.isclose(ex,tr["gross_return_pct"],rtol=1e-9,atol=1e-8),(sym,ex,tr["gross_return_pct"])
            q=tt[a:b]
            gi=((q-t0)//STEP).astype(np.int64)
            assert (grid[gi]==q).all()
            st=states[key]
            st["unreal_open"][gi] += tr["notional"]*(1.0-oo[a:b]/tr["entry_open"])
            st["unreal_high"][gi] += tr["notional"]*(1.0-hh[a:b]/tr["entry_open"])
            st["max_high_ratio"][j] = float(np.max(hh[a:b]/tr["entry_open"]))
            st["bars_seen"][j] = len(q)
        if si % 100 == 0:
            print(f"MARK progress={si+1}/{len(by_symbol)}", flush=True)

    out={}
    for key,p in active:
        st=states[key]
        assert (st["bars_seen"]>0).all(), key
        cash=1.0+np.cumsum(st["cashflow"])
        gross=np.cumsum(st["gross_diff"][:-1])
        count=np.cumsum(st["count_diff"][:-1])
        eq=cash+st["unreal_open"]
        stress_eq=cash+st["unreal_high"]
        first=int((min(x["entry_ts"] for x in p["executed"])-t0)//STEP)
        last=int((max(x["exit_ts"] for x in p["executed"])-t0)//STEP)
        sl=slice(first,last+1)
        e=eq[sl]; se=stress_eq[sl]; gg=gross[sl]; cc=count[sl]; tg=grid[sl]
        mdd,mi=drawdown(e); smdd,smi=drawdown(se)
        bad=np.flatnonzero(e<=0); sbad=np.flatnonzero(se<=0)
        alive=e>0
        active_mask=(gg>0)&alive
        exposure=np.full(len(e),np.nan)
        exposure[alive]=gg[alive]/e[alive]*100.0
        cross=st["max_high_ratio"]>=2.0
        worst_loss=(1.0-st["max_high_ratio"])*100.0
        years=(tg[-1]-tg[0])/1000/86400/365.25 if len(tg)>1 else np.nan
        final=float(e[-1])
        # When primary synchronous 15m OPEN equity reaches <=0, bankruptcy is the terminal account outcome.
        bankrupt=bool(len(bad))
        if bankrupt:
            final_report=0.0; total_return=-100.0; cagr=None
        else:
            final_report=final; total_return=(final-1.0)*100.0
            cagr=float((final**(1/years)-1)*100.0) if years>0 and final>0 else None
        closed=pd.DataFrame(p["closed"]).sort_values(["exit_ts","symbol"],kind="mergesort")
        if bankrupt:
            bt=int(tg[bad[0]])
            closed_pre=closed[closed.exit_ts<=bt]
            accepted_pre=sum(int(x["entry_ts"]<=bt) for x in p["executed"])
        else:
            bt=None; closed_pre=closed; accepted_pre=len(p["executed"])
        pnl=closed_pre.net_pnl.to_numpy(float) if len(closed_pre) else np.array([],float)
        gp=pnl[pnl>0].sum() if len(pnl) else 0.0
        gl=-pnl[pnl<0].sum() if len(pnl) else 0.0
        out[key]={
            "final_equity_multiple":final_report,
            "total_return_pct":float(total_return),
            "cagr_pct":cagr,
            "mdd_15m_open_pct":float(mdd),
            "mdd_15m_open_ts":int(tg[mi]) if mi is not None else None,
            "bankrupt_15m_open":bankrupt,
            "bankrupt_15m_open_ts":bt,
            "accepted_before_bankruptcy":int(accepted_pre),
            "closed_before_bankruptcy":int(len(closed_pre)),
            "pf_account_currency_before_bankruptcy":float(gp/gl) if gl>0 else None,
            "win_pct_before_bankruptcy":float((pnl>0).mean()*100) if len(pnl) else None,
            "stress_mdd_15m_synchronized_high_pct":float(smdd),
            "stress_bankrupt_synchronized_high":bool(len(sbad)),
            "stress_bankrupt_first_ts":int(tg[sbad[0]]) if len(sbad) else None,
            "positions_with_adverse_move_gt_100pct_notional":int(cross.sum()),
            "pct_positions_with_adverse_move_gt_100pct_notional":float(cross.mean()*100),
            "worst_position_mark_loss_pct_notional":float(worst_loss.min()),
            "avg_gross_exposure_pct_equity_while_active":float(np.nanmean(exposure[active_mask])) if active_mask.any() else 0.0,
            "p95_gross_exposure_pct_equity_while_active":float(np.nanquantile(exposure[active_mask],.95)) if active_mask.any() else 0.0,
            "max_gross_exposure_pct_equity_before_bankruptcy":float(np.nanmax(exposure[active_mask])) if active_mask.any() else 0.0,
            "avg_cap_utilization_pct_of_200_limit":float(np.nanmean(exposure[active_mask])/200*100) if active_mask.any() else 0.0,
            "time_with_any_position_pct":float((gg>0).mean()*100),
            "time_at_6_positions_pct":float((cc>=6).mean()*100),
            "max_positions_observed":int(np.nanmax(cc)) if len(cc) else 0,
            "period_years":float(years),
            "mtm_definition":"synchronous 15m OPEN marks; entry/exit fees charged half at each side",
            "stress_definition":"within each 15m bar, every open short marked at that symbol bar HIGH simultaneously; conservative bound because exact intrabar high timestamps differ",
            "liquidation_definition":"no Binance maintenance-margin model; individual >100% notional losses remain uncapped. price>=2x entry is reported only as a 1x-isolated liquidation-risk proxy. Primary hard stop is account MTM equity<=0.",
        }
        if not bankrupt:
            # Reconstructed 15m terminal equity must match the daily accounting ledger.
            assert np.isclose(final,p["summary"]["daily_accounting_final_equity"],rtol=1e-8,atol=1e-8), (
                key,final,p["summary"]["daily_accounting_final_equity"])
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--canonical",required=True)
    ap.add_argument("--reconciliation",required=True)
    ap.add_argument("--data",required=True)
    ap.add_argument("--out",required=True)
    a=ap.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)

    trades,freeze=load_selected(a.canonical)
    marks=load_daily_marks(a.reconciliation,set(trades.symbol.unique()))
    periods={
        "FULL":trades,
        "TRAIN":trades[trades.period.eq("TRAIN")].copy(),
        "HOLDOUT":trades[trades.period.eq("HOLDOUT")].copy(),
    }
    portfolios={}
    for period,d in periods.items():
        for cost in COSTS_PCT:
            key=f"{period}_{int(cost*100):02d}bp"
            portfolios[key]=replay_daily(d,marks,cost)
            print("REPLAY",key,json.dumps(portfolios[key]["summary"],sort_keys=True),flush=True)

    mtm=mark_15m(portfolios,a.data)
    rows=[]
    for key,p in portfolios.items():
        p["summary"]["period"]=key.split("_")[0]
        p["summary"]["mtm_15m"]=mtm[key]
        rows.append({"key":key,**p["summary"],**{f"mtm_{k}":v for k,v in mtm[key].items()
                    if isinstance(v,(int,float,bool)) or v is None}})
    result={
        "protocol":{
            "frozen_signal":{"horizon":int(freeze["selection"]),"D0_threshold":float(freeze["D0_threshold"]),
                             "age":3,"side":"SHORT","exit":"first BULL reflip OPEN else max 7D OPEN"},
            "account":{"position_size":"30% of pre-entry synchronous MTM equity snapshot",
                       "max_positions":6,"max_gross_entry_pct":200,
                       "same_symbol_overlap":False,
                       "simultaneous_signal_rule":"symbol ascending; no outcome/performance rank",
                       "timestamp_order":"mark at OPEN -> close due positions -> recompute MTM equity -> size/accept new entries",
                       "costs_round_trip_pct":list(COSTS_PCT)},
            "risk":{"primary_mdd":"15m synchronous OPEN mark-to-market",
                    "intrabar_stress":"15m synchronized-HIGH conservative stress",
                    "bankruptcy":"primary account equity <=0 => terminal -100% account return",
                    "individual_short_loss":"uncapped linear short PnL; losses beyond 100% entry notional are retained",
                    "exchange_liquidation":"not modeled exactly without Binance margin mode/leverage/maintenance tiers; 2x-entry crossing is only a 1x-isolated risk proxy"},
            "no_retuning":True,
            "holdout_status":"2025-2026 already observed historically; not pristine OOS",
            "funding":"not included",
        },
        "source_counts":{"selected_total":int(len(trades)),
                         "TRAIN":int((trades.period=="TRAIN").sum()),
                         "HOLDOUT":int((trades.period=="HOLDOUT").sum())},
        "results":{k:{"account":p["summary"],"mtm_15m":mtm[k]} for k,p in portfolios.items()},
    }
    (out/"account_validation.json").write_text(json.dumps(result,indent=2))
    pd.DataFrame(rows).to_csv(out/"account_summary.csv",index=False)
    ex=[]
    for key,p in portfolios.items():
        for r in p["executed"]:
            ex.append({"portfolio":key,**r})
    pd.DataFrame(ex).to_csv(out/"executed_trades.csv.gz",index=False,compression="gzip")
    print("ACCOUNT_VALIDATION_PASS",json.dumps({"portfolios":len(portfolios),"rows":len(rows)},sort_keys=True),flush=True)
    for key in sorted(portfolios):
        s=portfolios[key]["summary"];m=mtm[key]
        print("RESULT",key,
              "N",s["actual_trade_count"],
              "RET",round(m["total_return_pct"],3),
              "CAGR",None if m["cagr_pct"] is None else round(m["cagr_pct"],3),
              "MDD",round(m["mdd_15m_open_pct"],3),
              "PF",None if m["pf_account_currency_before_bankruptcy"] is None else round(m["pf_account_currency_before_bankruptcy"],4),
              "WIN",None if m["win_pct_before_bankruptcy"] is None else round(m["win_pct_before_bankruptcy"],2),
              "STRESS_MDD",round(m["stress_mdd_15m_synchronized_high_pct"],3),
              "GT100",m["positions_with_adverse_move_gt_100pct_notional"],
              "BANKRUPT",m["bankrupt_15m_open"],flush=True)


if __name__=="__main__":
    main()
