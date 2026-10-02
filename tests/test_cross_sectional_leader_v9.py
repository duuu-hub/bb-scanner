"""Causal global ranking, resumption, immutable sources and strict V9 gates."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import cross_sectional_ranks_v9 as r
from scripts import cross_sectional_leader_v9 as s


def daily_rows(n=32, days=(31, 32), tied=False):
    out = []
    for day in days:
        for i in range(n):
            ret = .01 if tied else (i - n / 2) / 1000
            out.append(dict(symbol=f'X{i:02d}USDT', rank_time=day*r.DAY,
                last_bar_open=day*r.DAY-r.BAR, reference_bar_open=(day-1)*r.DAY-r.BAR,
                relative_return24=np.log1p(ret), coin_return24=ret, btc_return24=0.,
                turnover24=96_000_000., observed_source_days=day))
    return pd.DataFrame(out, columns=r.MAP_COLUMNS)


def event(side=1, persistence=1, btc_filter='ANY', n=100):
    t = 32*r.DAY+np.arange(n, dtype=np.int64)*r.BAR
    c = np.full(n, 100.); o = c.copy(); c[20] += 2*side
    raw = [t, o, np.maximum(c, o)+1, np.minimum(c, o)-1, c]
    frame, _ = r.rank_frame(daily_rows())
    symbol = 'X31USDT' if side == 1 else 'X00USDT'
    f = dict(eligible=np.ones(n, bool), prior_atr=np.ones(n), volume_multiple=np.full(n, 2.),
             buy_share=np.full(n, .6 if side == 1 else .4), r1=np.full(n, side*.002),
             r16=np.full(n, side*.025), prevh4=np.full(n, 101.), prevl4=np.full(n, 99.),
             atr=np.full(n, .01))
    f.update(r.align_ranks(symbol, t, frame))
    btc = dict(r16=np.full(n, side*.01))
    cfg = next(x for x in s.configurations() if x['side'] == side and x['tail'] == 5
               and x['persistence'] == persistence and x['btc'] == btc_filter)
    return raw, f, btc, cfg, symbol


class RankTests(unittest.TestCase):
    def test_global_rank_is_independent_of_shard_order_and_ties(self):
        f = daily_rows(tied=True); a, _ = r.rank_frame(f); b, _ = r.rank_frame(f.iloc[::-1])
        pd.testing.assert_frame_equal(a, b)
        self.assertEqual(a.iloc[0].symbol, 'X00USDT'); self.assertEqual(a.iloc[31].rank_from_high, 1)
        self.assertEqual(a.iloc[0].rank_fraction, 0); self.assertEqual(a.iloc[31].rank_fraction, 1)

    def test_minimum_universe_excluded_and_recorded(self):
        a, low = r.rank_frame(daily_rows(29)); self.assertTrue(a.empty)
        self.assertEqual(len(low), 2); self.assertEqual(low[0]['universe_n'], 29)
        self.assertEqual(len(r.rank_frame(daily_rows(30))[0]), 60)

    def test_exact_midnight_and_one_closed_next_day_bar(self):
        frame, _ = r.rank_frame(daily_rows())
        t = np.array([32*r.DAY-r.BAR, 32*r.DAY, 32*r.DAY+r.BAR, 33*r.DAY])
        f = r.align_ranks('X31USDT', t, frame)
        np.testing.assert_array_equal(f['rank_available'], [False, True, True, False])
        self.assertTrue(np.isnan(f['rank_fraction'][[0, 3]]).all())
        self.assertTrue(f['previous_rank_available'][1]); self.assertEqual(f['rank_time'][1], 32*r.DAY)

    def test_absent_symbol_previous_date_or_stale_rank_never_backfilled(self):
        frame, _ = r.rank_frame(daily_rows(days=(30, 32)))
        t = np.array([31*r.DAY, 32*r.DAY, 33*r.DAY])
        f = r.align_ranks('X31USDT', t, frame)
        np.testing.assert_array_equal(f['rank_available'], [False, True, False])
        self.assertFalse(f['previous_rank_available'][1])
        self.assertFalse(r.align_ranks('NEWUSDT', t, frame)['rank_available'].any())

    def test_invalid_duplicate_nonfinite_path_or_low_turnover_fails(self):
        for key, value in [('rank_time', 1), ('last_bar_open', 1), ('reference_bar_open', 1),
                           ('observed_source_days', 29), ('relative_return24', float('nan')),
                           ('turnover24', 19_999_999), ('coin_return24', -1), ('symbol', 'bad')]:
            f = daily_rows(); f.loc[0, key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): r.rank_frame(f)
        f = daily_rows(); f = pd.concat([f, f.iloc[:1]])
        with self.assertRaises(ValueError): r.rank_frame(f)

    def test_observed_days_counts_source_dates_not_future_or_missing_dates(self):
        t = np.array([0, r.BAR, 10*r.DAY, 10*r.DAY+r.BAR, 20*r.DAY])
        np.testing.assert_array_equal(r.observed_days(t), [1, 1, 2, 2, 3])
        self.assertEqual(len(r.observed_days(np.array([], dtype=np.int64))), 0)

    def test_full24h_snapshots_are_causal_and_missing_btc_is_excluded(self):
        t = np.arange(34*96, dtype=np.int64)*r.BAR
        c = 100*np.exp(np.arange(len(t))*.00005); q = np.full(len(t), 1e6)
        raw = [t, c.copy(), c+1, c-1, c.copy()]
        f = s.features(raw, q, q*.6); btc = s.base.align_btc(t, t, f)
        a, _ = r.snapshots('XUSDT', raw, f, btc, 0, 34*r.DAY)
        new = [x.copy() for x in raw]; new[-1][32*96:] *= 1.1
        nf = s.features(new, q, q*.6)
        b, _ = r.snapshots('XUSDT', new, nf, btc, 0, 34*r.DAY)
        self.assertEqual([z for z in a if z['rank_time'] <= 32*r.DAY], [z for z in b if z['rank_time'] <= 32*r.DAY])
        btc['r96'][31*96-1] = np.nan
        d, counts = r.snapshots('XUSDT', raw, f, btc, 0, 34*r.DAY)
        self.assertFalse(any(z['rank_time'] == 31*r.DAY for z in d))
        self.assertEqual(counts['missing_coin_or_btc_path'], 1)

    def test_coin_gap_breaks_exact24h_snapshot(self):
        t = np.arange(34*96, dtype=np.int64)*r.BAR; t[30*96+20:] += r.BAR
        c = np.full(len(t), 100.); q = np.full(len(t), 1e6)
        raw = [t, c.copy(), c+1, c-1, c.copy()]; f = s.features(raw, q, q*.6)
        btc = {k: np.zeros(len(t)) for k in ('r96', 'r16')}
        rows, counts = r.snapshots('XUSDT', raw, f, btc, 0, 34*r.DAY)
        self.assertFalse(any(z['rank_time'] == 31*r.DAY for z in rows))
        # At the missing-boundary path even turnover/eligibility may be unknown;
        # either way, no synthetic daily observation is manufactured.
        self.assertTrue(all(z['rank_time'] % r.DAY == 0 for z in rows))

    def make_maps(self, root, alter=None):
        frame = daily_rows(); hashes = {f'X{i:02d}USDT': f'{i:064x}' for i in range(32)}
        for key in ('rank_time', 'last_bar_open', 'reference_bar_open'):
            frame[key] += s.base.START
        baseline = root/'baseline.json'; baseline.write_text(json.dumps(dict(expected_csv_sha256=hashes)))
        for shard in range(8):
            d = root/'parts'/str(shard); d.mkdir(parents=True)
            symbols = {k: v for i, (k, v) in enumerate(hashes.items()) if i % 8 == shard}
            part = frame[frame.symbol.isin(symbols)]
            part.to_csv(d/'daily_map.csv.gz', index=False, compression='gzip')
            m = dict(complete=True, stage='DEV', shard=shard, source_data_run=36095439671,
                     market_hashes=symbols, btc_sha256='b'*64, baseline_sha256=r.digest(baseline),
                     daily_map_sha256=r.digest(d/'daily_map.csv.gz'), map_rows=len(part), source_check_sha256='c'*64)
            if alter is not None and shard == 0: alter(m)
            (d/'map_meta.json').write_text(json.dumps(m))
        return baseline

    def test_eight_shard_reducer_and_verified_loader_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); baseline = self.make_maps(root)
            frame, m = r.reduce_maps(root/'parts', root/'ranks', 'DEV', baseline)
            got, gm = r.load_ranks(root/'ranks', 'DEV')
            pd.testing.assert_frame_equal(frame, got, check_dtype=False)
            self.assertEqual(gm, m); self.assertEqual(m['ranked_dates'], 2); self.assertEqual(m['rank_rows'], 64)

    def test_missing_shard_altered_hash_or_context_fails_reducer(self):
        for key, value in [('complete', False), ('btc_sha256', 'wrong'), ('baseline_sha256', 'wrong'),
                           ('daily_map_sha256', 'wrong'), ('stage', 'GATE'), ('shard', 1)]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as td:
                root = Path(td); baseline = self.make_maps(root, lambda m: m.update({key: value}))
                with self.assertRaises(ValueError): r.reduce_maps(root/'parts', root/'ranks', 'DEV', baseline)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); baseline = self.make_maps(root); (root/'parts/0/daily_map.csv.gz').unlink()
            with self.assertRaises(ValueError): r.reduce_maps(root/'parts', root/'ranks', 'DEV', baseline)

    def test_rank_hash_and_rank_geometry_corruption_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); baseline = self.make_maps(root); r.reduce_maps(root/'parts', root/'ranks', 'DEV', baseline)
            path = root/'ranks/daily_ranks.csv.gz'; f = pd.read_csv(path); f.loc[0, 'rank_from_low'] = 2
            f.to_csv(path, index=False, compression='gzip')
            with self.assertRaises(ValueError): r.load_ranks(root/'ranks', 'DEV')
            mp = root/'ranks/rank_manifest.json'; m = json.loads(mp.read_text()); m['rank_data_sha256'] = r.digest(path)
            mp.write_text(json.dumps(m))
            with self.assertRaises(ValueError): r.load_ranks(root/'ranks', 'DEV')

    def test_actual_file_map_reduce_load_and_scan_synthetic_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); r.smoke(root)
            report = json.loads((root/'smoke.json').read_text())
            self.assertTrue(report['synthetic_only']); self.assertEqual(report['maps'], 8)
            self.assertEqual(report['scan_complete'], True)
            self.assertFalse(report['market_profitability_claim'])


class EconomicTests(unittest.TestCase):
    def test_sixteen_entries_and_ninety_six_policies(self):
        self.assertEqual(len(s.configurations()), 16); self.assertEqual(len(s.policies()), 96)
        self.assertEqual(len({p['policy'] for p in s.policies()}), 96)

    def test_mirrored_tail_and_resumption(self):
        for side in (1, -1):
            for days in (1, 2):
                raw, f, btc, cfg, _ = event(side, days)
                self.assertEqual(np.flatnonzero(s.mask(cfg, raw, f, btc)).tolist(), [20])

    def test_second_day_requires_consecutive_previous_tail(self):
        raw, f, btc, cfg, _ = event(persistence=2)
        for key, value in [('previous_rank_available', False), ('previous_rank_from_high', 5.)]:
            ff = {k: v.copy() for k, v in f.items()}; ff[key][:] = value
            self.assertFalse(s.mask(cfg, raw, ff, btc).any())
        cfg['persistence'] = 1; self.assertTrue(s.mask(cfg, raw, ff, btc)[20])

    def test_ceiling_tail_count_is_predeclared(self):
        raw, f, btc, cfg, _ = event(); f['rank_from_high'][:] = 2
        self.assertTrue(s.mask(cfg, raw, f, btc)[20]) # ceil(32*5%) = 2
        f['rank_from_high'][:] = 3; self.assertFalse(s.mask(cfg, raw, f, btc).any())

    def test_residual_volume_flow_return_eligibility_and_breakout_filters(self):
        for key, value in [('r16', .0149), ('volume_multiple', 1.49), ('buy_share', .549),
                           ('r1', .03001), ('r1', -.001), ('eligible', False), ('prevh4', 102.),
                           ('rank_available', False)]:
            raw, f, btc, cfg, _ = event(); f[key][:] = value
            self.assertFalse(s.mask(cfg, raw, f, btc).any(), key)
        raw, f, btc, cfg, _ = event(side=-1); f['buy_share'][:] = .451
        self.assertFalse(s.mask(cfg, raw, f, btc).any())

    def test_any_never_zero_imputes_missing_btc_and_align_requires_sign(self):
        raw, f, btc, cfg, _ = event(); btc['r16'][:] = np.nan
        self.assertFalse(s.mask(cfg, raw, f, btc).any())
        raw, f, btc, cfg, _ = event(); btc['r16'][:] = -.01
        self.assertTrue(s.mask(cfg, raw, f, btc)[20]); cfg['btc'] = 'ALIGN4H'
        self.assertFalse(s.mask(cfg, raw, f, btc).any())

    def test_onset_is_contiguous_and_cooldown_ignores_future_exits(self):
        raw, f, btc, cfg, symbol = event()
        for i in (21, 25, 51, 52, 53, 60, 84): raw[4][i] = 102.
        rows, _ = s.intents(symbol, cfg, raw, f, btc, raw[0][0], raw[0][-1]+r.BAR)
        self.assertEqual([(z['signal_time']-raw[0][0])//r.BAR for z in rows], [20, 60])

    def test_actual_next_open_risk_floor_gap_and_wide_stop(self):
        raw, f, btc, cfg, symbol = event(); raw[1][21] = 101.9
        rows, _ = s.intents(symbol, cfg, raw, f, btc, raw[0][0], raw[0][-1]+r.BAR)
        a = rows[0]; self.assertEqual(a['entry'], 101.9); self.assertEqual(a['sl'], 99.9)
        self.assertAlmostEqual(a['score'], .015*np.sqrt(2)/(1/102)); self.assertEqual(a['entry_time'], a['decision_time'])
        f['prior_atr'][:] = .001; rows, _ = s.intents(symbol, cfg, raw, f, btc, 0, raw[0][-1]+r.BAR)
        self.assertAlmostEqual(rows[0]['risk_pct'], .0075)
        f['prior_atr'][:] = 5; rows, counts = s.intents(symbol, cfg, raw, f, btc, 0, raw[0][-1]+r.BAR)
        self.assertFalse(rows); self.assertEqual(counts['STOP_ABOVE_8PCT'], 1)
        f['prior_atr'][:] = 1; raw[1][21] = raw[4][20]*1.006
        rows, counts = s.intents(symbol, cfg, raw, f, btc, 0, raw[0][-1]+r.BAR)
        self.assertFalse(rows); self.assertEqual(counts['ENTRY_CATCHUP_GAP'], 1)

    def test_adverse_gap_retained_and_entry_path_gap_excluded(self):
        for side in (1, -1):
            raw, f, btc, cfg, symbol = event(side); raw[1][21] = raw[4][20]*(1-side*.006)
            rows, _ = s.intents(symbol, cfg, raw, f, btc, 0, raw[0][-1]+r.BAR)
            self.assertEqual(len(rows), 1); self.assertLess(rows[0]['known_entry_gap'], 0)
        raw[0][21:] += r.BAR
        rows, counts = s.intents(symbol, cfg, raw, f, btc, 0, raw[0][-1]+r.BAR)
        self.assertFalse(rows); self.assertEqual(counts['ENTRY_PATH_GAP'], 1)

    def test_future_ohlc_does_not_change_intents_or_priority(self):
        raw, f, btc, cfg, symbol = event(); old, _ = s.intents(symbol, cfg, raw, f, btc, 0, raw[0][-1]+r.BAR)
        raw[2][21:] *= 100; raw[3][21:] *= .01; raw[4][21:] *= .5
        new, _ = s.intents(symbol, cfg, raw, f, btc, 0, raw[0][-1]+r.BAR)
        self.assertEqual(old[0], new[0])

    def test_closed_baseline_does_not_include_current_or_future_bars(self):
        n = 200; t = np.arange(n, dtype=np.int64)*r.BAR; c = 100+np.arange(n)*.01; q = np.full(n, 1e6)
        raw = [t, c.copy(), c+1, c-1, c.copy()]; old = s.features(raw, q, q*.6)
        raw[2][150:] *= 2; raw[3][150:] *= .5; q[150:] *= 100; new = s.features(raw, q, q*.6)
        self.assertEqual(old['prior_atr'][150], new['prior_atr'][150]); self.assertEqual(new['volume_multiple'][150], 100)
        for k in old: np.testing.assert_allclose(old[k][:150], new[k][:150], equal_nan=True)

    def test_canonical_tp3_and_minute_exclusion_and_costs(self):
        raw, f, btc, cfg, symbol = event(); p = dict(cfg, hold=24, exit_type='TP3', policy='P')
        result = dict(status='RESOLVED', exit_time=raw[0][23], exit=106., reason='TP', gross_return=.06)
        with patch.object(s.canonical, 'resolve', return_value=result) as mock:
            rows, _, _ = s.policy_rows(symbol, [p], raw, f, btc, 0, raw[0][-1]+r.BAR)
        self.assertEqual(mock.call_args.args[0]['tp'], 106.)
        self.assertEqual(mock.call_args.args[3], 'TP2'); self.assertAlmostEqual(rows[0]['net40_fraction'], .06-.002*2.06-.0002*2/96)
        with patch.object(s.canonical, 'resolve', return_value=dict(status='ENTRY_MISMATCH')):
            rows, counts, bad = s.policy_rows(symbol, [p], raw, f, btc, 0, raw[0][-1]+r.BAR)
        self.assertFalse(rows); self.assertEqual(counts['P/ENTRY_MISMATCH'], 1); self.assertEqual(bad[0]['status'], 'ENTRY_MISMATCH')

    def test_sl_applies_adverse_slip_and_full_cost_inclusive_r(self):
        raw, f, btc, cfg, symbol = event(); p = dict(cfg, hold=96, exit_type='TP2', policy='P')
        result = dict(status='RESOLVED', exit_time=raw[0][23], exit=98., reason='SL', gross_return=-.02)
        with patch.object(s.canonical, 'resolve', return_value=result):
            rows, _, _ = s.policy_rows(symbol, [p], raw, f, btc, 0, raw[0][-1]+r.BAR)
        expected = .97902-1-.002*(1+.97902)-.0002*2/96
        self.assertAlmostEqual(rows[0]['net40_fraction'], expected)
        self.assertAlmostEqual(rows[0]['net40_R'], expected/s.account.stop_loss_fraction(rows[0], .002))


class SelectionTests(unittest.TestCase):
    def make(self, root, bad_year=None, kind=None, clustered=False, empty=False):
        p = s.policies()[0]; rows = []
        for year, n in ((2021, 80), (2022, 160), (2023, 160)):
            start = int(pd.Timestamp(f'{year}-10-01' if year == 2021 else f'{year}-01-01', tz='Asia/Seoul').timestamp()*1000)
            for i in range(n):
                ts = start+i*r.BAR if clustered else start+(i//2)*r.DAY+(i%2)*r.BAR
                row = {k: 0 for k in s.COLUMNS}; value = -.02 if bad_year == year and kind == 'price' else .02
                risk = -.5 if bad_year == year and kind == 'r' else .5
                row.update(symbol=f'X{i%10}USDT', variant=p['policy'], policy=p['policy'], entry_time=ts,
                           net40_fraction=value, net40_R=risk, split='DEV'); rows.append(row)
        sources = {f'C{i}USDT': str(i)*64 for i in range(8)}
        manifest = dict(market_hashes=sources, rank_data_sha256='d'*64)
        for i in range(8):
            d = root/str(i); d.mkdir(parents=True)
            pd.DataFrame(rows if i == 0 and not empty else [], columns=s.COLUMNS).to_csv(d/'independent_candidates.csv.gz', index=False, compression='gzip')
            (d/'rank_manifest.json').write_text(json.dumps(manifest))
            m = dict(complete=True, stage='DEV', shard=i, counts={}, coverage=[],
                ledger_sha256=r.digest(d/'independent_candidates.csv.gz'), rank_manifest_sha256=r.digest(d/'rank_manifest.json'),
                rank_data_sha256='d'*64, baseline_sha256='e'*64, btc_sha256='b'*64, market_hashes={f'C{i}USDT': sources[f'C{i}USDT']})
            (d/'scan_meta.json').write_text(json.dumps(m))

    def test_all96_cells_selection_and_union_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.make(root/'p'); s.select(root/'p', root/'out')
            d = json.loads((root/'out/selection.json').read_text()); table = pd.read_csv(root/'out/development_policy_cells.csv')
            self.assertEqual(len(d['policies']), 1); self.assertEqual(len(table), 96)
            self.assertEqual(d['union_name'], 'CROSS_SECTIONAL_LEADER_UNION')
            self.assertEqual(len(list((root/'out').rglob('scan_meta.json'))), 8)

    def test_each_year_price_r_and_active_date_frequency_are_hard_gates(self):
        for year in (2021, 2022, 2023):
            for kind in ('price', 'r'):
                with self.subTest(year=year, kind=kind), tempfile.TemporaryDirectory() as td:
                    root = Path(td); self.make(root/'p', year, kind); s.select(root/'p', root/'out')
                    self.assertFalse(json.loads((root/'out/selection.json').read_text())['policies'])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.make(root/'p', clustered=True); s.select(root/'p', root/'out')
            self.assertFalse(json.loads((root/'out/selection.json').read_text())['policies'])

    def test_empty_cells_still_count96_and_do_not_select(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.make(root/'p', empty=True); s.select(root/'p', root/'out')
            table = pd.read_csv(root/'out/development_policy_cells.csv'); self.assertEqual(len(table), 96)
            self.assertTrue(table.n.eq(0).all())

    def test_incomplete_duplicate_shard_or_modified_ledger_fails(self):
        for corruption in ('missing', 'duplicate', 'ledger'):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as td:
                root = Path(td); self.make(root/'p'); d = root/'p/0'
                if corruption == 'missing': (d/'independent_candidates.csv.gz').unlink()
                elif corruption == 'ledger': (d/'independent_candidates.csv.gz').write_bytes(b'changed')
                else:
                    mp = d/'scan_meta.json'; m = json.loads(mp.read_text()); m['shard'] = 1; mp.write_text(json.dumps(m))
                with self.assertRaises(ValueError): s.select(root/'p', root/'out')


class AccountGateTests(unittest.TestCase):
    def data(self):
        a = pd.DataFrame([dict(variant='P', split=split, cost_bps=cost, guarded=True, trades=100,
                              mdd_15m_pct=8., net_return_pct=30., cagr_pct=25., pf=1.3,
                              halt_time=np.nan, daily_mean_pct=.1)
                          for split in ('DEV', 'GATE') for cost in (20, 40)])
        y = pd.DataFrame([dict(variant='P', split=split, cost_bps=20, guarded=True, period=year, net_return_pct=2.)
                          for split, year in (('DEV', '2021'), ('DEV', '2022'), ('DEV', '2023'), ('GATE', '2024'))])
        return a, y

    def test_partial2021_is_required_and_small_return_is_not_daily_goal(self):
        a, y = self.data(); d = s.strict_accounts(a, y, dict(chronology_exclusions=0))
        self.assertEqual(len(d['survivors']), 1); self.assertFalse(d['survivors'][0]['goal_daily_mean_met'])
        y.loc[y.period == '2021', 'net_return_pct'] = -1
        self.assertFalse(s.strict_accounts(a, y, dict(chronology_exclusions=0))['survivors'])

    def test_dev2024_partial_boundary_never_substitutes_gate2024(self):
        a, y = self.data(); y.loc[y.period == '2024', 'split'] = 'DEV'
        self.assertFalse(s.strict_accounts(a, y, dict(chronology_exclusions=0))['survivors'])

    def test_each_account_threshold_halt_and_missing_values_fail(self):
        for key, value in [('trades', 79), ('mdd_15m_pct', 15), ('net_return_pct', 0),
                           ('cagr_pct', 19.9), ('pf', 1.04), ('pf', np.nan), ('halt_time', 123)]:
            a, y = self.data(); a.loc[0, key] = value
            with self.subTest(key=key): self.assertFalse(s.strict_accounts(a, y, dict(chronology_exclusions=0))['survivors'])

    def test_chronology_exclusions_require_review_even_if_growth_passes(self):
        a, y = self.data(); d = s.strict_accounts(a, y, dict(chronology_exclusions=1))
        self.assertIn('DATA_COVERAGE_REVIEW_REQUIRED', d['status'])


if __name__ == '__main__': unittest.main()
