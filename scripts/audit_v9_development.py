"""Read original saved V9 cells and source/exclusion diagnostics; no market rerun."""
from __future__ import annotations
import argparse, collections, hashlib, json
from pathlib import Path
import numpy as np
import pandas as pd

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def audit(source, baseline, out):
    cells = pd.read_csv(source / 'development_policy_cells.csv')
    selection = json.loads((source / 'selection.json').read_text())
    provenance = json.loads((source / 'provenance.json').read_text())
    expected = json.loads(baseline.read_text())['expected_csv_sha256']
    assert len(cells) == 96 and cells.policy.nunique() == 96
    assert not cells.rejections.isna().any() and len(selection['policies']) == 0
    checked = {}
    for name, sha in provenance['sha256'].items():
        path = source / name
        if path.exists():
            assert digest(path) == sha, name
            checked[name] = sha
    counts, sources, coverage, coverage_n = collections.Counter(), {}, collections.Counter(), collections.Counter()
    metas, excluded = [], []
    for shard in range(8):
        m = json.loads((source / f'full_dev_shard_{shard}_scan_meta.json').read_text())
        check = json.loads((source / f'full_dev_shard_{shard}_source_check.json').read_text())
        e = pd.read_csv(source / f'full_dev_shard_{shard}_exclusions.csv')
        assert m['complete'] and m['shard'] == shard and m['stage'] == 'DEV'
        assert len(m['policies']) == 96 and check['status'] == 'VERIFIED'
        assert m['rank_data_sha256'] == selection['rank_data_sha256']
        assert m['rank_manifest_sha256'] == selection['rank_manifest_sha256']
        assert not set(sources).intersection(m['market_hashes'])
        assert {x['symbol']: x['sha256'] for x in check['files']} == m['market_hashes']
        assert digest(source / f'full_dev_shard_{shard}_source_check.json') == m['source_check_sha256']
        sources.update(m['market_hashes']); counts.update(m['counts']); excluded.append(e)
        for row in m['coverage']:
            coverage[row['status']] += 1
            for key in ('outcomes', 'eligible_bars', 'available_rank_bars', 'unavailable_rank_bars', 'missing_btc_4h_bars'):
                coverage_n[key] += row.get(key, 0)
        metas.append(dict(shard=shard, ledger_sha256=m['ledger_sha256'],
                          minute_months=m['minute_months'], verified=m['minute_official_checksums_verified'],
                          source_files=m['source_files'], baseline_sha256=m['baseline_sha256'], btc_sha256=m['btc_sha256']))
    assert all(sources.get(k) == v for k, v in expected.items()) and len(expected) == 256
    resolved = {k.split('/')[1]: v for k, v in counts.items() if k.endswith('/RESOLVED')}
    assert {r.policy: int(r.n) for r in cells.itertuples()} == resolved
    assert sum(resolved.values()) == coverage_n['outcomes'] == int(cells.n.sum())
    ex = pd.concat(excluded, ignore_index=True)
    assert len(ex) == sum(v for k, v in counts.items() if k.endswith('/DATA_GAP'))
    actual_rejections = dict(collections.Counter(x for text in cells.rejections for x in text.split('|')))
    assert actual_rejections == selection['rejection_counts']
    def records(frame):
        return json.loads(frame.to_json(orient='records', double_precision=15))
    report = dict(status='SAVED_96_CELL_SOURCE_COUNT_EXCLUSION_AUDIT_PASS',
        actual_run=36955250542, code_commit=provenance['code_commit'],
        development_branch='research-cross-sectional-leader-v9-dev-36955250542',
        development_commit='8ee2ea6255e914329032496bd14be07a70cece0e',
        audit_scope='Saved original 96-cell statistics, 8 source/scan metadata and all exclusions. Raw ledger means and full-archive byte audit remain a separate pending check; no market rerun.',
        cells=96, source_files=len(sources), prior_hashes_matched=len(expected),
        resolved_parameterized_outcomes=int(cells.n.sum()),
        positive_net40_price_cells=int((cells.net40_mean_bp > 0).sum()),
        positive_net40_R_cells=int((cells.net40_R_mean > 0).sum()),
        selected_policies=0, account_scenarios=0, strict_survivors=0,
        ranges={k:[float(cells[k].min()),float(cells[k].max())] for k in
                ('net40_mean_bp','net40_R_mean','net40_pf','win_pct')},
        positive_cells=records(cells[cells.net40_mean_bp > 0]),
        year_positive_cells={str(y):{m:int((cells[f'year_{y}_{m}']>0).sum())
            for m in ('net40_mean_bp','net40_R','day_R')} for y in (2021,2022,2023)},
        rejection_counts=actual_rejections,
        canonical_exclusions={k:int(v) for k,v in ex.status.value_counts().items()},
        unique_excluded_coin_entry_events=len(ex.drop_duplicates(['symbol','entry_time'])),
        intent_rejections={k:int(v) for k,v in counts.items() if not any('/'+p+'/' in k for p in resolved)},
        coverage=dict(coverage), coverage_counts=dict(coverage_n), shards=metas,
        official_minute_months_checksum_verified=sum(m['verified'] for m in metas),
        checked_saved_file_sha256=checked,
        diagnosis='94/96 net40 price and R means negative. The 2 small-positive LONG two-day top5% trail policies have only153/234 overlapping outcomes and2022 price/R losses. All96 have negative2022 R/equal-date R. No account replay or daily-target evidence. V9 is economically rejected; source/rank availability does not rescue annual fragility.',
        goal_daily_net_account_pct=[.7,2.], goal_met=False,
        history='V1-V9 were adaptively explored; not independent evidence. Transfer-only added newlines were removed after matching every original provenance hash; original git bytes unchanged.')
    out.mkdir(parents=True,exist_ok=True)
    (out/'FAILURE_AUDIT.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    (out/'FAILURE_AUDIT.md').write_text('''# V9 saved development failure audit

Actual run36955250542/code2e1bb92891c2c480028c075b30afe91275cc0c43;
232 tests/canonical checks, eight real canonical DEV shards and selection
completed.96 fixed cells/223,218 parameterized outcomes, zero survivor/account.
Saved 96-cell statistics and eight original source/count/exclusion products
audited without a market rerun;26 exact file SHA256s match saved provenance.
All856 sources and256 immutable prior hashes reconcile.496 official minute
months verified.54 DATA_GAP policy exclusions represent9 coin/entry events;
four wide-stop intent/config rejections. No missing result invented.

94/96 overall net40 price and R means are negative. Price means range
-63.6210 to+5.3485bp; R -.27166 to+.04636; PF .57707 to1.02713.
The two overall-positive policies are LONG top5%, two-day persistence,
24h TRAIL: ALIGN4H N153/+4.7837bp/PF1.0242 and ANY N234/+5.3485bp/PF1.0271.
2022 price means are respectively-26.1148/-85.3235bp; both R means negative.
They fail frozen sample/year screens. All96 cells have negative2022 R and
equal-active-date R. Positive2023 price/R cells11/96, positive equal-date R7/96.
These overlapping diagnostics are not executable account gains.

Rank availability7,401,146/7,910,008 eligible symbol-bars;508,862 unavailable
rank bars explicitly counted, no missing BTC4h bars. Exactly256 sources have
development history;600 later sources are not backfilled. Source coverage is
not the main failure: estimated economic edge is weak/unstable across years.
Full original ledger means/full archive byte audits remain separate pending
checks while the existing preservation job runs. Existing DEV results are
durable at8ee2ea6255e914329032496bd14be07a70cece0e. No unseen/2024 tuning.

No whole-account result or daily+0.7%-2% evidence exists. V10 changes the
economic observable to a prior-fitted relative-price residual and empirically
measured AR1 relaxation rather than retuning V9 ranks/momentum thresholds.
''')
    print('V9_SAVED_DEVELOPMENT_AUDIT_PASS',report['resolved_parameterized_outcomes'],report['canonical_exclusions'])

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--baseline',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args();audit(a.source,a.baseline,a.out)
