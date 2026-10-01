"""Audit saved original V5 trials; no strategy rerun or threshold selection."""
import argparse, hashlib, json
from pathlib import Path
import numpy as np
import pandas as pd

def audit(parts, table, out):
    paths = sorted(parts.rglob('independent_candidates.csv.gz'))
    assert len(paths) == 8
    x = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    assert not x.duplicated(['symbol', 'variant', 'entry_time']).any()
    assert x.status.eq('RESOLVED').all()
    assert set(x.reason) <= {'TP', 'SL', 'TIME', 'SPLIT_END'}
    assert (x.exit_time > x.entry_time).all()
    assert (x.exit_time - x.entry_time <= x.max_hold_bars * 900000).all()
    assert (x.decision_time == x.signal_time + 900000).all()
    assert (x.entry_time == x.decision_time).all()
    gross = x.side * (x.exit / x.entry - 1)
    np.testing.assert_allclose(gross, x.gross_return, atol=1e-12)
    fill = x.exit * np.where(x.reason.eq('SL'), 1 - x.side * .001, 1.)
    ratio = fill / x.entry
    net = x.side * (ratio - 1) - .002 * (1 + ratio) - .0002 * (x.exit_time - x.entry_time) / 86400000
    np.testing.assert_allclose(net, x.net40_fraction, atol=1e-12)
    stopratio = x.sl / x.entry * (1 - x.side * .001)
    sf = x.side * (1 - stopratio) + .002 * (1 + stopratio) + .0002 * x.max_hold_bars / 96
    np.testing.assert_allclose(net / sf, x.net40_R, atol=1e-10)
    rows = []
    expected = pd.read_csv(table).set_index('policy')
    for name, g in x.groupby('policy'):
        r = dict(policy=name, n=len(g), gross_mean_bp=g.gross_return.mean()*10000,
                 net40_mean_bp=g.net40_fraction.mean()*10000,
                 net40_R_mean=g.net40_R.mean(),
                 cost_drag_mean_bp=(g.gross_return-g.net40_fraction).mean()*10000,
                 tp_pct=g.reason.eq('TP').mean()*100,
                 sl_pct=g.reason.eq('SL').mean()*100,
                 time_or_split_end_pct=g.reason.isin(['TIME','SPLIT_END']).mean()*100,
                 mean_hold_min=g.hold_min.mean())
        assert r['n'] == expected.loc[name, 'n']
        assert np.isclose(r['net40_mean_bp'], expected.loc[name, 'net40_mean_bp'], atol=1e-10)
        assert np.isclose(r['net40_R_mean'], expected.loc[name, 'net40_R_mean'], atol=1e-10)
        rows.append(r)
    d = pd.DataFrame(rows)
    assert len(d) == len(expected) == 96
    out.mkdir(parents=True, exist_ok=True)
    d.to_csv(out/'DIAGNOSTIC_FAILURE_DECOMPOSITION.csv', index=False)
    report = dict(all_original_ledger_checks_passed=True, original_outcomes=len(x),
                  all_96_cell_counts_and_means_reconciled=True,
                  positive_gross_mean_cells=int((d.gross_mean_bp>0).sum()),
                  positive_net40_mean_cells=int((d.net40_mean_bp>0).sum()),
                  gross_mean_bp_range=[float(d.gross_mean_bp.min()),float(d.gross_mean_bp.max())],
                  average_cost_drag_bp_range=[float(d.cost_drag_mean_bp.min()),float(d.cost_drag_mean_bp.max())],
                  original_inputs={str(p.relative_to(parts)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                  note='Saved original overlapping parameterized outcomes, NOT account profit or a new trial.')
    (out/'ORIGINAL_LEDGER_AUDIT.json').write_text(json.dumps(report, indent=2))
    print('V5_ORIGINAL_LEDGER_AUDIT',json.dumps({k:v for k,v in report.items() if k!='original_inputs'}))

if __name__ == '__main__':
    ap=argparse.ArgumentParser()
    for k in ('parts','table','out'): ap.add_argument('--'+k,type=Path,required=True)
    args=ap.parse_args();audit(args.parts,args.table,args.out)
