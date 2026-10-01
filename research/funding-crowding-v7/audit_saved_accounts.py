"""Audit saved V7 account ledgers/curves only; never replay market prices."""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np
import pandas as pd
from scripts.audit_saved_calendar import rebuild

RUN = 36922945306
CODE = '4db8b13a5ea64f3aef610609c994b2fe2cd0fc18'

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def portfolio_entry_checks(trades):
    """Rebuild open inventory at each saved fill; no price or outcome replay."""
    for _, row in trades.sort_values(['entry_time', 'symbol']).iterrows():
        prior = trades[(trades.entry_time <= row.entry_time) & (trades.exit_time > row.entry_time)]
        assert len(prior) <= 6
        assert prior.symbol.nunique() == len(prior)
        assert prior.notional.sum() <= 2.00000001 * row.entry_equity
        assert prior.reserved_risk.sum() <= .02000001 * row.entry_equity

def audit(source, out):
    summaries = json.loads((source/'summary.json').read_text())
    assert len(summaries) == 24
    out.mkdir(parents=True, exist_ok=True); records=[]
    for r in summaries:
        name=f'{r["split"]}__{r["variant"]}__{r["cost_bps"]}bp__{"guarded" if r["guarded"] else "diagnostic"}'
        path=source/'details'/name
        tr=pd.read_csv(path/'trades.csv.gz'); day=pd.read_csv(path/'daily.csv'); curve=pd.read_csv(path/'curve.csv.gz')
        assert len(tr)==r['trades']
        fee=r['cost_bps']/20000; qty=tr.notional/tr.entry
        np.testing.assert_allclose(tr.entry_fee,tr.notional*fee,atol=1e-12)
        np.testing.assert_allclose(tr.exit_fee,qty*tr.exit*fee,atol=1e-12)
        np.testing.assert_allclose(tr.funding,tr.notional*.0002*tr.hold_min/1440,atol=1e-12)
        pnl=tr.side*qty*(tr.exit-tr.entry)-tr.entry_fee-tr.exit_fee-tr.funding
        np.testing.assert_allclose(tr.net_pnl,pnl,atol=1e-12)
        final=1+pnl.sum()
        assert np.isclose(final,curve.equity.iloc[-1],atol=1e-10)
        assert np.isclose((final-1)*100,r['net_return_pct'],atol=1e-10)
        observed=np.r_[1.,np.column_stack([curve.equity_pre_entry,curve.equity]).ravel()]
        peak=np.maximum.accumulate(observed)
        assert np.isclose(((peak-observed)/peak).max()*100,r['mdd_15m_pct'])
        gain=pnl[pnl>0].sum(); loss=-pnl[pnl<0].sum()
        assert np.isclose(gain/loss,r['pf'])
        assert (tr.notional/tr.entry_equity<=.300000001).all()
        assert (tr.reserved_risk/tr.entry_equity<=.005000001).all()
        assert tr.hold_min.max()<=1440
        assert curve.positions.max()==r['max_concurrent'] and curve.positions.max()<=6
        portfolio_entry_checks(tr)
        if r['halt_time'] is not None: assert (tr.entry_time<r['halt_time']).all()
        rebuilt=rebuild(curve,tr,int(curve.time.iloc[0]),int(curve.time.iloc[-1]))
        pd.testing.assert_frame_equal(day,rebuilt,check_exact=False,atol=1e-10,rtol=1e-10)
        expected=853 if r['split']=='DEV' else 367
        assert len(day)==expected and day.partial_day.sum()==2
        assert np.isclose(np.prod(1+day.return_pct/100),final,atol=1e-10)
        ge07=int((day.return_pct>=.7).sum()); ge2=int((day.return_pct>=2).sum())
        assert np.isclose(100*ge07/len(day),r['day_ge_0_7_pct'])
        assert np.isclose(100*ge2/len(day),r['day_ge_2_pct'])
        records.append(dict(scenario=name,all_independent_checks_passed=True,
            independent_candidates=r['independent_n'],executed=len(tr),
            execution_share_pct=100*len(tr)/r['independent_n'],rejections=r['rejections'],
            return_pct=r['net_return_pct'],cagr_pct=r['cagr_pct'],mdd_pct=r['mdd_15m_pct'],pf=r['pf'],
            calendar_dates=len(day),goal07_days=ge07,goal2_days=ge2,halt_time=r['halt_time'],
            gross_after_fill_slip_pnl=float((tr.side*qty*(tr.exit-tr.entry)).sum()),
            entry_exit_fees=float((tr.entry_fee+tr.exit_fee).sum()),funding_stress=float(tr.funding.sum()),
            curve_sha256=sha(path/'curve.csv.gz'),daily_sha256=sha(path/'daily.csv'),
            trades_sha256=sha(path/'trades.csv.gz')))
    report=dict(original_run=RUN,executed_code=CODE,
        audit_kind='READ_SAVED_ORIGINAL_FILES_NO_MARKET_OR_STRATEGY_RERUN',scenarios=records)
    (out/'SAVED_ACCOUNT_AUDIT.json').write_text(json.dumps(report,indent=2))
    print('V7_SAVED_ACCOUNT_AUDIT_PASS',len(records),'scenarios')

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for n in ('source','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();audit(a.source,a.out)
