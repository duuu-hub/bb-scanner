"""Frozen threshold, full ledger audits, and train-only sizing selection."""
import argparse
from collections import Counter
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

from psar_1d_short_timelimit import CUT, DAY, STEP, LIMITS, V, validate
from psar_1d_exec_helpers import _symbol

THRESHOLD = 10.174524905858567
COSTS = (20, 40)
SIZES = (.005, .01, .025, .05, .075, .10, .15, .20, .30, .40, .50)
MAX_POS = (3, 6, 12, 24)
GROSS_CAPS = (.5, 1., 2.)
TRAIN_START = 1609459200000


def stats(x, cost):
    p = x.pnl_pct.to_numpy(float)-cost/100
    pos, neg = p[p>0], p[p<0]
    dur = (x.exit_ts-x.fill_ts)/DAY
    tm = x[x.outcome == "time"]
    return dict(n=len(x), cost_bp=cost, win_pct=100*np.mean(p>0) if len(p) else None,
        pf=pos.sum()/-neg.sum() if len(neg) else None,
        avg_pct=float(np.mean(p)) if len(p) else None,
        median_pct=float(np.median(p)) if len(p) else None,
        positive_n=int((p>0).sum()), negative_n=int((p<0).sum()),
        tp_pct=100*x.outcome.eq("win").mean() if len(x) else None,
        sl_pct=100*x.outcome.eq("loss").mean() if len(x) else None,
        time_pct=100*x.outcome.eq("time").mean() if len(x) else None,
        time_positive_pct=100*tm.pnl_pct.sub(cost/100).gt(0).mean() if len(tm) else None,
        mean_hold_days=float(dur.mean()) if len(x) else None,
        median_hold_days=float(dur.median()) if len(x) else None)


def select(x, size, max_pos=6, gross=2.):
    slots = min(max_pos, int(np.floor(gross/size+1e-12)))
    opened, ids = {}, []
    for r in x.sort_values(["fill_ts","stop_pct","symbol"], kind="mergesort").itertuples():
        opened = {s:end for s,end in opened.items() if end > r.fill_ts}
        if r.symbol in opened or len(opened) >= slots:
            continue
        opened[r.symbol] = int(r.exit_ts)
        ids.append(r.Index)
    return ids, slots


def price_at(prices, symbol, timestamp):
    t, c = prices[symbol]
    k = int(np.searchsorted(t, timestamp-STEP, side="right")-1)
    assert k >= 0 and t[k]+STEP <= timestamp
    return float(c[k])


def replay(x, ids, size, cost, prices):
    """Use known 15m closes for current equity; charge half cost each side."""
    y = x.loc[ids]
    entries, exits = {}, {}
    for r in y.itertuples():
        entries.setdefault(int(r.fill_ts), []).append(r)
        exits.setdefault(int(r.exit_ts), []).append(r)
    cash, opened, book = 1., {}, []
    peak, mdd, gp, gl, streak, longest = 1., 0., 0., 0., 0, 0
    half_fee = cost/20000
    bankrupt = False
    for ts in sorted(set(entries) | set(exits)):
        for r in sorted(exits.get(ts, []), key=lambda r:(r.symbol,r.signal_ts)):
            if r.Index not in opened:
                continue
            rr,n = opened.pop(r.Index)
            net = n*(r.pnl_pct-cost/100)/100
            cash += n*r.pnl_pct/100-n*half_fee
            gp += max(net,0); gl += max(-net,0)
            streak = streak+1 if net < 0 else 0
            longest = max(longest, streak)
            book.append(dict(id=r.Index, symbol=r.symbol, signal_ts=int(r.signal_ts),
                fill_ts=int(r.fill_ts), exit_ts=int(r.exit_ts), fill=r.fill,
                exit_px=r.exit_px, pnl_pct=r.pnl_pct, notional=n, net_pnl=net))
        equity = cash + sum(n*(r.fill-price_at(prices,r.symbol,ts))/r.fill
                            for r,n in opened.values())
        peak = max(peak,equity)
        mdd = max(mdd,100*(peak-equity)/peak)
        if equity <= 0:
            bankrupt = True
            break
        # Entries at the same 15m bucket share the same pre-entry equity.
        for r in sorted(entries.get(ts, []), key=lambda r:(r.stop_pct,r.symbol)):
            n = equity*size
            opened[r.Index] = (r,n)
            cash -= n*half_fee
    b = pd.DataFrame(book)
    assert bankrupt or len(b) == len(y)
    net = b.net_pnl if len(b) else pd.Series(dtype=float)
    return dict(n=len(y), booked_n=len(b), total_return_pct=-100. if bankrupt else (cash-1)*100,
        event_mdd_pct=mdd, pf=gp/gl if gl else None,
        win_pct=100*net.gt(0).mean() if len(b) else None,
        avg_trade_pct=y.pnl_pct.sub(cost/100).mean(),
        longest_losing_streak=longest, bankrupt=bankrupt), b


def mark_to_market(book, cost, prices, start, end):
    ts = np.arange(start//STEP*STEP, end//STEP*STEP+STEP, STEP, dtype=np.int64)
    diff = np.zeros(len(ts)+1)
    unreal = np.zeros(len(ts)); exposure = np.zeros(len(ts)); countdiff = np.zeros(len(ts)+1)
    half_fee = cost/20000
    for r in book.itertuples():
        a = int((r.fill_ts-ts[0])//STEP); b = int((r.exit_ts-ts[0])//STEP)
        assert 0 <= a < b < len(ts)
        n = r.notional
        diff[a] -= n*half_fee
        diff[b] += n*r.pnl_pct/100-n*half_fee
        countdiff[a] += 1; countdiff[b] -= 1
        exposure[a] += n
        if b > a+1:
            vt, vc = prices[r.symbol]
            pt = ts[a+1:b]-STEP
            k = np.searchsorted(vt,pt)
            assert (k<len(vt)).all() and np.array_equal(vt[k],pt)
            px = vc[k]
            unreal[a+1:b] += n*(r.fill-px)/r.fill
            exposure[a+1:b] += n*px/r.fill
    equity = 1.+np.cumsum(diff[:-1])+unreal
    peaks = np.maximum.accumulate(np.r_[1.,equity])[1:]
    mdd = 100*np.max((peaks-equity)/peaks)
    concurrent = np.cumsum(countdiff[:-1])
    assert concurrent.min() >= 0
    gross = np.divide(exposure,equity,out=np.full_like(exposure,np.inf),where=equity>0)
    duration_years = (end-start)/(DAY*365.25)
    final = float(equity[-1])
    result = dict(mtm_close_mdd_pct=float(mdd), max_concurrent=int(concurrent.max()),
        mean_concurrent=float(concurrent.mean()), mean_exposure_pct=float(np.mean(gross)*100),
        max_observed_gross_pct=float(np.max(gross)*100),
        cagr_pct=100*(final**(1/duration_years)-1) if final>0 and duration_years>=1 else None,
        mtm_bankrupt=bool(np.any(equity<=0)))
    assert np.isclose(final,1+book.net_pnl.sum(),atol=1e-10)
    return result, pd.DataFrame(dict(timestamp=ts,equity=equity,gross_pct=gross*100,positions=concurrent))


def period(x, name):
    if name == "train":
        # No training sizing uses returns after the 2025-01-01 boundary.
        return x[(x.signal_ts>=TRAIN_START)&(x.signal_ts<CUT)&(x.exit_ts<=CUT)]
    if name == "holdout":
        return x[x.signal_ts>=CUT]
    return x[x.signal_ts>=TRAIN_START]


def load_prices(data, symbols):
    out = {}
    for p in sorted(glob.glob(data+"/**/*.csv.gz",recursive=True)):
        symbol = _symbol(p)
        if symbol not in symbols:
            continue
        d = pd.read_csv(p,usecols=["open_time","close"]).sort_values("open_time")
        assert symbol not in out
        out[symbol] = (d.open_time.to_numpy(np.int64),d.close.to_numpy(float))
    assert symbols.issubset(set(out))
    return out


def ledger_audit(x, audits):
    assert {a["shard"] for a in audits} == set(range(8))
    assert all(a["shards"]==8 and tuple(a["variant"])==V for a in audits)
    assert len({a["workflow_commit_sha"] for a in audits})==1
    assert len({a["engine_sha256"] for a in audits})==1
    symbols = [s for a in audits for s in a["symbols"]]
    assert len(symbols)==len(set(symbols))
    assert len(x)==sum(a["rows"] for a in audits)
    validate(x[x.tp>0])
    checks = ["8 unique shards", "one commit", "one engine hash", "one shard per symbol",
              "row totals reconcile", "engine invariants"]
    for short, long in [(1,3),(3,7),(7,14)]:
        s=x[x.limit_days==short].set_index(["symbol","signal_ts"])
        l=x[x.limit_days==long].set_index(["symbol","signal_ts"])
        common=s.index.intersection(l.index)
        s,l=s.loc[common],l.loc[common]
        assert (s.fill_ts==l.fill_ts).all()
        for col in ("fill","sl","tp","atr_pct","stop_pct"):
            assert np.allclose(s[col],l[col],rtol=1e-12)
        resolved=s.outcome.ne("time")
        assert (s.loc[resolved,"outcome"]==l.loc[resolved,"outcome"]).all()
        assert (s.loc[resolved,"exit_ts"]==l.loc[resolved,"exit_ts"]).all()
        assert np.allclose(s.loc[resolved,"pnl_pct"],l.loc[resolved,"pnl_pct"],rtol=1e-12)
        checks.append(f"{short}/{long} common fills and earlier natural exits invariant")
    return checks


def baseline_reference(root, filtered, output):
    files = sorted(glob.glob(root+"/**/events_*.csv.gz",recursive=True))
    if not files:
        return {"available":False}
    assert len(files)==8
    chunks=[]
    use=["variant","side","symbol","signal_ts","fill_ts","exit_ts","outcome",
         "fill","tp","sl","atr_pct","stop_pct","pnl_pct"]
    for f in files:
        for d in pd.read_csv(f,usecols=use,chunksize=150000):
            q=d[(d.variant=="E2.75_SB1.2_R0.75")&(d.side=="SHORT")&
                (d.atr_pct>=THRESHOLD)&(d.tp>0)].copy()
            chunks.append(q)
    u=pd.concat(chunks,ignore_index=True)
    resolved=u[u.outcome.isin(["win","loss"])&u.exit_ts.notna()].copy()
    refs=[]
    for name in ("all","train","holdout"):
        g=period(resolved,name)
        for cost in (0,20,40):
            z=stats(g,cost);z.update(period=name,label="unlimited historical resolved reference")
            refs.append(z)
    pd.DataFrame(refs).to_csv(output/"unlimited_reference.csv",index=False)
    # Paired comparisons explicitly retain only common resolved reference trades.
    paired=filtered[filtered.limit_days==14].merge(resolved,on=["symbol","signal_ts"],
                                                   suffixes=("_limit","_unlimited"))
    assert np.allclose(paired.fill_limit,paired.fill_unlimited,rtol=1e-10)
    assert np.allclose(paired.tp_limit,paired.tp_unlimited,rtol=1e-10)
    assert np.allclose(paired.sl_limit,paired.sl_unlimited,rtol=1e-10)
    paired.to_csv(output/"unlimited_paired_14d.csv.gz",index=False,compression="gzip")
    return dict(available=True,reference_run=36710246395,resolved_n=len(resolved),
        excluded_unresolved_n=int((~u.outcome.isin(["win","loss"])).sum()),
        paired_14d_n=len(paired),
        warning="Historical reference uses its original snapshot mismatch policy and resolved-only scope; not a new unlimited portfolio backtest.")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",default="in")
    ap.add_argument("--data",default="data")
    ap.add_argument("--baseline",default="baseline")
    ap.add_argument("--out",default="summary")
    a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    files=sorted(glob.glob(a.input+"/**/tl_*.csv.gz",recursive=True))
    assert len(files)==8
    original_audits=[json.load(open(f+".meta.json")) for f in files]
    audits=[dict(shard=m["shard"],shards=m["shards"],variant=m["variant"],
                 workflow_commit_sha=m["commit"],engine_sha256=m["script_sha256"],
                 symbols=[s["symbol"] for s in m["sources"]],rows=m["events"],
                 counters=m["diagnostics"]) for m in original_audits]
    x=pd.concat([pd.read_csv(f) for f in files],ignore_index=True)
    checks=ledger_audit(x,audits)
    invalid_tp_events=int(x.tp.le(0).sum())
    invalid_tp_signals=x.loc[x.tp.le(0),["symbol","signal_ts"]].drop_duplicates().shape[0]
    x=x[(x.signal_ts>=TRAIN_START)&(x.atr_pct>=THRESHOLD)&(x.tp>0)].copy()
    x["exit_px"]=x.exit_price
    x.to_csv(out/"high_vol_ledger.csv.gz",index=False,compression="gzip")
    raw=[]
    cohort=set(map(tuple,x.loc[x.limit_days==14,["symbol","signal_ts"]].to_numpy()))
    common=x[[tuple(v) in cohort for v in x[["symbol","signal_ts"]].to_numpy()]]
    for scope,frame in [("available_horizon",x),("common_14d_cohort",common)]:
        for days,g in frame.groupby("limit_days"):
            for name in ("all","train","holdout"):
                sub=period(g,name)
                for cost in (0,20,40):
                    z=stats(sub,cost);z.update(limit_days=int(days),period=name,scope=scope)
                    raw.append(z)
    pd.DataFrame(raw).to_csv(out/"raw_summary.csv",index=False)
    prices=load_prices(a.data,set(x.symbol))
    portfolio=[]
    config_seen=set()
    # Baseline is diagnostic only: user size 30%, 6 positions, 200% entry allocation.
    for days,g in x.groupby("limit_days"):
        for name in ("all","train","holdout"):
            sub=period(g,name)
            ids,slots=select(sub,.3,6,2.)
            for cost in COSTS:
                z,b=replay(sub,ids,.3,cost,prices)
                if len(b) and not z["bankrupt"]:
                    m,_=mark_to_market(b,cost,prices,int(sub.signal_ts.min()),int(sub.exit_ts.max()))
                    z.update(m)
                z.update(limit_days=int(days),period=name,cost_bp=cost,size_pct=30.,
                         max_positions=6,gross_entry_cap_pct=200.,slots=slots,kind="baseline")
                portfolio.append(z)
    # The grid is frozen before this run. Select on TRAIN only, stress cost 40bp.
    # Search 14d because only that horizon survived the original complete raw cost screen.
    train_raw=pd.DataFrame(raw)
    candidates=train_raw[(train_raw.scope=="common_14d_cohort")&(train_raw.period=="train")&
                         (train_raw.cost_bp==40)&(train_raw.pf>1)&(train_raw.avg_pct>0)]
    candidate_days=candidates.limit_days.astype(int).tolist()
    g=x[x.limit_days==14]
    train=period(g,"train")
    sweep=[]
    for size in (SIZES if 14 in candidate_days else ()):
        for max_pos in MAX_POS:
            for gross in GROSS_CAPS:
                ids,slots=select(train,size,max_pos,gross)
                config=(size,slots)
                if config in config_seen:continue
                config_seen.add(config)
                for cost in COSTS:
                    z,b=replay(train,ids,size,cost,prices)
                    if len(b) and not z["bankrupt"]:
                        m,_=mark_to_market(b,cost,prices,int(train.signal_ts.min()),int(train.exit_ts.max()))
                        z.update(m)
                    z.update(limit_days=14,period="train",cost_bp=cost,size_pct=size*100,
                         max_positions=max_pos,gross_entry_cap_pct=gross*100,slots=slots)
                    sweep.append(z)
    sw=pd.DataFrame(sweep);sw.to_csv(out/"train_sizing_sweep.csv",index=False)
    # Research risk screen is disclosed; never reselect after reading holdout.
    eligible=sw[(sw.cost_bp==40)&(sw.total_return_pct>0)&(~sw.bankrupt)] if len(sw) else sw
    if "mtm_close_mdd_pct" in eligible:
        eligible=eligible[eligible.mtm_close_mdd_pct<=30]
        eligible=eligible[~eligible.mtm_bankrupt]
    chosen=None
    if len(eligible):
        r=eligible.sort_values(["total_return_pct","mtm_close_mdd_pct","size_pct"],
                              ascending=[False,True,True]).iloc[0]
        chosen={k:float(r[k]) for k in ["size_pct","max_positions","gross_entry_cap_pct","slots"]}
        size=chosen["size_pct"]/100
        for name in ("all","train","holdout"):
            sub=period(g,name)
            ids,slots=select(sub,size,int(chosen["max_positions"]),chosen["gross_entry_cap_pct"]/100)
            for cost in COSTS:
                z,b=replay(sub,ids,size,cost,prices)
                if len(b) and not z["bankrupt"]:
                    m,curve=mark_to_market(b,cost,prices,int(sub.signal_ts.min()),int(sub.exit_ts.max()))
                    z.update(m);curve.to_csv(out/f"selected_{name}_{cost}_equity.csv.gz",index=False,compression="gzip")
                z.update(limit_days=14,period=name,cost_bp=cost,kind="train_selected",**chosen)
                portfolio.append(z)
                b.to_csv(out/f"selected_{name}_{cost}_trades.csv",index=False)
    pd.DataFrame(portfolio).to_csv(out/"portfolio_summary.csv",index=False)
    counters=Counter()
    for j in audits:counters.update(j["counters"])
    report=dict(threshold=THRESHOLD,variant=V,limits=LIMITS,costs_bp=COSTS,
        workflow_commit_sha=audits[0]["workflow_commit_sha"],engine_sha256=audits[0]["engine_sha256"],
        audit_checks=checks,engine_behavior_tests=63,counters=dict(counters),
        original_input_run=36817548101,invalid_tp_events_excluded=invalid_tp_events,
        invalid_tp_signals_excluded=invalid_tp_signals,
        train_40bp_candidate_days=candidate_days,
        decision="NO_SIZING: all frozen limits fail train 40bp raw edge" if not candidate_days else "TRAIN_ONLY_SIZING",
        source_signal_start=int(x.signal_ts.min()),source_signal_end=int(x.signal_ts.max()),
        final_exit=int(x.exit_ts.max()),chosen_train_only=chosen,
        selection_rule="maximize train account return at 40bp subject to 15m-close MTM MDD<=30%; no holdout re-selection",
        optimization_status="best tested grid point; boundary points are not claimed as a global optimum",
        sizing="percent of current equity at known prior 15m close; half round-trip cost each side",
        gross_cap="entry-allocation slot budget; marked exposure can exceed it during price/equity moves and is reported",
        fill_timestamp="canonical 15m fill bucket start; intraminute timestamp not available",
        funding="not separately modeled; 20/40bp are constant fee plus slippage scenarios",
        training_boundary="training signals must exit by 2025-01-01 UTC; boundary-crossing trades excluded from training selection",
        baseline=baseline_reference(a.baseline,x,out))
    with open(out/"report.json","w") as f:json.dump(report,f,indent=2,allow_nan=False)
    print("RAW_HIGH_VOL_COST20")
    print(pd.DataFrame(raw).query("scope=='available_horizon' and period=='all' and cost_bp==20")[
        ["limit_days","n","win_pct","pf","avg_pct","time_pct","mean_hold_days"]].to_string(index=False))
    print("PORTFOLIO_RESULTS")
    print(pd.DataFrame(portfolio)[["kind","limit_days","period","cost_bp","size_pct","n",
                                   "total_return_pct","pf"]].to_string(index=False))
    print("SUMMARY_AUDIT_PASS",len(x),len(sweep),chosen,flush=True)


if __name__=="__main__":
    main()
