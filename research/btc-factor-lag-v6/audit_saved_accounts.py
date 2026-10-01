"""Read original V6 accounts and verify fills/cash/curves; no price rerun."""
import argparse,hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import numpy as np
import pandas as pd
from scripts.audit_saved_calendar import rebuild

def audit(source,out):
    results=json.loads((source/'summary.json').read_text());assert len(results)==8
    out.mkdir(parents=True,exist_ok=True);records=[]
    for r in results:
        name=f'{r["split"]}__{r["variant"]}__{r["cost_bps"]}bp__{"guarded" if r["guarded"] else "diagnostic"}'
        d=source/'details'/name;tr=pd.read_csv(d/'trades.csv.gz');day=pd.read_csv(d/'daily.csv');c=pd.read_csv(d/'curve.csv.gz')
        assert len(tr)==r['trades']
        fee=r['cost_bps']/20000;qty=tr.notional/tr.entry
        np.testing.assert_allclose(tr.entry_fee,tr.notional*fee,atol=1e-12)
        np.testing.assert_allclose(tr.exit_fee,qty*tr.exit*fee,atol=1e-12)
        np.testing.assert_allclose(tr.funding,tr.notional*.0002*tr.hold_min/1440,atol=1e-12)
        pnl=tr.side*qty*(tr.exit-tr.entry)-tr.entry_fee-tr.exit_fee-tr.funding
        np.testing.assert_allclose(tr.net_pnl,pnl,atol=1e-12)
        final=1+pnl.sum();assert np.isclose(final,c.equity.iloc[-1],atol=1e-10)
        assert np.isclose((final-1)*100,r['net_return_pct'],atol=1e-10)
        observed=np.r_[1.,np.column_stack([c.equity_pre_entry,c.equity]).ravel()]
        peak=np.maximum.accumulate(observed);assert np.isclose(((peak-observed)/peak).max()*100,r['mdd_15m_pct'])
        gain=pnl[pnl>0].sum();loss=-pnl[pnl<0].sum()
        assert np.isclose(gain/loss,r['pf'])
        assert (tr.notional/tr.entry_equity<=.300000001).all()
        assert (tr.reserved_risk/tr.entry_equity<=.005000001).all()
        assert tr.hold_min.max()<=1440
        assert c.positions.max()==r['max_concurrent'] and c.positions.max()<=6
        if r['halt_time'] is not None:assert (tr.entry_time<r['halt_time']).all()
        rebuilt=rebuild(c,tr,int(c.time.iloc[0]),int(c.time.iloc[-1]))
        pd.testing.assert_frame_equal(day,rebuilt,check_exact=False,atol=1e-10,rtol=1e-10)
        assert len(day)==(853 if r['split']=='DEV' else 367)
        assert day.partial_day.sum()==2
        assert np.isclose(np.prod(1+day.return_pct/100),final,atol=1e-10)
        ge07=int((day.return_pct>=.7).sum());ge2=int((day.return_pct>=2).sum())
        assert np.isclose(100*ge07/len(day),r['day_ge_0_7_pct'])
        assert np.isclose(100*ge2/len(day),r['day_ge_2_pct'])
        records.append(dict(scenario=name,all_independent_checks_passed=True,
            independent_candidates=r['independent_n'],executed=len(tr),execution_share_pct=100*len(tr)/r['independent_n'],
            rejections=r['rejections'],return_pct=r['net_return_pct'],cagr_pct=r['cagr_pct'],mdd_pct=r['mdd_15m_pct'],
            pf=r['pf'],calendar_dates=len(day),goal07_days=ge07,goal2_days=ge2,halt_time=r['halt_time'],
            gross_after_fill_slip_pnl=float((tr.side*qty*(tr.exit-tr.entry)).sum()),
            entry_exit_fees=float((tr.entry_fee+tr.exit_fee).sum()),funding_stress=float(tr.funding.sum()),
            curve_sha256=hashlib.sha256((d/'curve.csv.gz').read_bytes()).hexdigest(),
            daily_sha256=hashlib.sha256((d/'daily.csv').read_bytes()).hexdigest(),
            trades_sha256=hashlib.sha256((d/'trades.csv.gz').read_bytes()).hexdigest()))
    (out/'SAVED_ACCOUNT_AUDIT.json').write_text(json.dumps(dict(original_run=36906530309,
        executed_code='65095198c689b09a3028ec460db8198f819b0bcc',audit_kind='READ_SAVED_ORIGINAL_FILES_NO_MARKET_RERUN',
        scenarios=records),indent=2))
    print('V6_SAVED_ACCOUNT_AUDIT',json.dumps(records))

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for n in ('source','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();audit(a.source,a.out)
