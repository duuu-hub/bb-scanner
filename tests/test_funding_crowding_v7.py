"""Independent as-of oracle, original archive integrity and economic timing."""
import hashlib, io, json, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts import funding_rate_archive as a
from scripts import funding_crowding_v7 as s

MONTH = '2022-01'
T = int(pd.Timestamp(MONTH+'-01', tz='UTC').timestamp()*1000)

def zip_bytes(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z: z.writestr('rates.csv', text)
    return b.getvalue()

def official_bytes():
    raw = zip_bytes('calc_time,funding_interval_hours,last_funding_rate\n'
                    f'{T},8,-0.001\n{T+8*a.HOUR},4,0.0005\n')
    checksum = (hashlib.sha256(raw).hexdigest()+'  BTCUSDT-fundingRate-2022-01.zip\n').encode()
    return raw, checksum

class ArchiveTests(unittest.TestCase):
    def setUp(self): a.INPUTS.clear()

    def test_original_minute_archive_is_retained_and_frozen_hash_cannot_be_replaced(self):
        raw=b'original official minute ZIP';check=b'original official checksum'
        meta=dict(status='VALIDATED_OFFICIAL_CHECKSUM',symbol='BTCUSDT',month=MONTH,
            source_url='official',checksum_url='official.CHECKSUM',
            original_zip_sha256=hashlib.sha256(raw).hexdigest(),
            official_checksum_text_sha256=hashlib.sha256(check).hexdigest())
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cache=root/'cache';cache.mkdir()
            with patch.object(s.official,'_get',side_effect=[raw,check]):
                s.retain_minute_original(meta,cache,root/'evidence')
            self.assertEqual((root/'evidence'/f'BTCUSDT-{MONTH}.original.zip').read_bytes(),raw)
            with patch.object(s.official,'_get',side_effect=AssertionError('immutable cache')):
                s.retain_minute_original(meta,cache,root/'second')
            (cache/f'BTCUSDT-{MONTH}.original.zip').write_bytes(b'corrupt')
            with patch.object(s.official,'_get',side_effect=AssertionError('no replacement')),self.assertRaises(ValueError):
                s.retain_minute_original(meta,cache,root/'failed')

    def test_official_header_and_headerless_arrays_agree(self):
        header = 'calc_time,funding_interval_hours,last_funding_rate\n'
        text = f'{T},8,-0.001\n{T+8*a.HOUR},4,0.0005\n'
        x = a.parse_zip(zip_bytes(header+text), MONTH); y = a.parse_zip(zip_bytes(text), MONTH)
        for u, v in zip(x, y): np.testing.assert_array_equal(u, v)
        self.assertEqual(x[0].dtype, np.dtype('int64'))

    def test_invalid_timestamp_interval_and_rate_are_rejected(self):
        for text in (f'{T}.5,8,0\n', f'{T},0,0\n', f'{T},25,0\n', f'{T},8,NaN\n',
                     f'{T},8,1.01\n', f'{T-1},8,0\n', f'{T},8,0\n{T},8,0\n',
                     f'{T+1},8,0\n{T},8,0\n', 'wrong,header,names\n', ''):
            with self.subTest(text=text), self.assertRaises((ValueError, pd.errors.EmptyDataError)):
                a.parse_zip(zip_bytes(text), MONTH)

    def test_original_bytes_checksum_and_verified_cache_are_preserved(self):
        raw, checksum = official_bytes()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(a, '_get', side_effect=[raw, checksum]) as get:
                first = a.load_month('BTCUSDT', MONTH, root/'cache', root/'evidence')
                self.assertEqual(get.call_count, 2)
            with patch.object(a, '_get', side_effect=AssertionError('cache must be immutable')):
                again = a.load_month('BTCUSDT', MONTH, root/'cache', root/'evidence2')
            for u, v in zip(first, again): np.testing.assert_array_equal(u, v)
            paths = a.filenames('BTCUSDT', MONTH, root/'evidence')
            self.assertEqual(paths[0].read_bytes(), raw); self.assertEqual(paths[1].read_bytes(), checksum)
            m = json.loads(paths[3].read_text())
            self.assertTrue(m['checksum_verified']); self.assertEqual(m['intervals_hours'], [4., 8.])
            self.assertEqual(a.digest(paths[0]), m['official_checksum_sha256'])
            self.assertEqual(a.digest(paths[2]), m['npz_sha256'])

    def test_all_immutable_cache_components_fail_closed_without_redownload(self):
        for component in range(4):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as td:
                root = Path(td); raw, checksum = official_bytes()
                with patch.object(a, '_get', side_effect=[raw, checksum]):
                    a.load_month('BTCUSDT', MONTH, root/'cache', root/'evidence')
                paths = a.filenames('BTCUSDT', MONTH, root/'cache')
                if component == 3:
                    m = json.loads(paths[3].read_text()); m['source_url'] = 'wrong'
                    paths[3].write_text(json.dumps(m))
                else: paths[component].write_bytes(b'corrupt')
                with patch.object(a, '_get', side_effect=AssertionError('no replacement')), self.assertRaises(ValueError):
                    a.load_month('BTCUSDT', MONTH, root/'cache', root/'failed')
                self.assertEqual(a.INPUTS['BTCUSDT/'+MONTH]['status'], 'CACHE_INTEGRITY_ERROR')

    def test_missing_checksum_and_mismatch_preserve_raw_failure_not_zero(self):
        raw, _ = official_bytes()
        for checksum in (None, b'bad', ('0'*64).encode()):
            with self.subTest(checksum=checksum), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                with patch.object(a, '_get', side_effect=[raw, checksum]):
                    result = a.load_month('BTCUSDT', MONTH, root/'cache', root/'evidence')
                self.assertIsNone(result)
                self.assertEqual(a.filenames('BTCUSDT', MONTH, root/'evidence')[0].read_bytes(), raw)
                self.assertEqual(a.INPUTS['BTCUSDT/'+MONTH]['status'], 'FUNDING_DATA_GAP')

    def test_missing_archive_is_recorded_explicitly(self):
        with tempfile.TemporaryDirectory() as td, patch.object(a, '_get', return_value=None):
            result = a.load_month('BTCUSDT', MONTH, Path(td)/'cache', Path(td)/'evidence')
            self.assertIsNone(result); self.assertEqual(a.INPUTS['BTCUSDT/'+MONTH]['records'], 0)

    def test_transient_exhaustion_stops_and_keeps_downloaded_bytes(self):
        raw, _ = official_bytes()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(a, '_get', side_effect=[raw, RuntimeError('429 exhausted')]), self.assertRaises(RuntimeError):
                a.load_month('BTCUSDT', MONTH, root/'cache', root/'evidence')
            self.assertEqual(a.INPUTS['BTCUSDT/'+MONTH]['status'], 'TRANSIENT_SOURCE_ERROR')
            self.assertEqual(a.filenames('BTCUSDT', MONTH, root/'evidence')[0].read_bytes(), raw)

class AvailabilityTests(unittest.TestCase):
    def test_lagged_timestamp_and_variable_interval_normalization(self):
        data = (np.array([T, T+8*a.HOUR+1], np.int64), np.array([8., 4.]), np.array([-.001, .0005]))
        t = np.array([T-a.BAR, T, T+8*a.HOUR, T+8*a.HOUR+a.BAR], np.int64)
        f = a.asof(t, data, {MONTH}, T+a.DAY)
        np.testing.assert_array_equal(f['funding_available'], [False, True, True, True])
        self.assertEqual(f['funding_rate8h'][2], -.001)
        self.assertEqual(f['funding_rate8h'][3], .001)
        self.assertEqual(f['funding_calc_time'][3], T+8*a.HOUR+1)

    def test_asof_matches_independent_record_by_record_oracle(self):
        data = (np.array([T, T+4*a.HOUR+13, T+8*a.HOUR, T+12*a.HOUR], np.int64),
                np.array([4., 4., 4., 8.]), np.array([-.001, .0003, -.0002, .001]))
        t = np.arange(T-a.BAR, T+2*a.DAY, a.BAR, dtype=np.int64)
        f = a.asof(t, data, {MONTH}, T+a.DAY)
        for i, bar in enumerate(t):
            candidates = [j for j, ts in enumerate(data[0]) if ts < T+a.DAY and ts+a.BAR <= bar+a.BAR]
            j = candidates[-1] if candidates else None
            age = bar+a.BAR-data[0][j] if j is not None else None
            available = j is not None and age <= min(12*a.HOUR, data[1][j]*a.HOUR+a.BAR)
            self.assertEqual(bool(f['funding_available'][i]), available)
            if available:
                self.assertAlmostEqual(f['funding_rate8h'][i], data[2][j]*8/data[1][j])

    def test_future_funding_perturbation_cannot_change_earlier_decisions(self):
        t = np.arange(T, T+a.DAY, a.BAR, dtype=np.int64)
        data = (np.array([T, T+8*a.HOUR, T+16*a.HOUR], np.int64), np.array([8., 8., 8.]), np.array([-.001, .001, -.003]))
        changed = (data[0], data[1], np.array([-.001, .5, .9]))
        old = a.asof(t, data, {MONTH}, T+a.DAY); new = a.asof(t, changed, {MONTH}, T+a.DAY)
        for key in old: np.testing.assert_array_equal(old[key][t<T+8*a.HOUR], new[key][t<T+8*a.HOUR])

    def test_missing_month_cannot_carry_previous_rate_and_stale_interval_expires(self):
        feb = int(pd.Timestamp('2022-02-01', tz='UTC').timestamp()*1000)
        data = (np.array([feb-a.HOUR], np.int64), np.array([4.]), np.array([-.001]))
        t = np.array([feb, feb+3*a.HOUR, feb+4*a.HOUR], np.int64)
        self.assertFalse(a.asof(t, data, {MONTH}, feb+a.DAY)['funding_available'].any())
        np.testing.assert_array_equal(a.asof(t, data, {MONTH, '2022-02'}, feb+a.DAY)['funding_available'], [True, True, False])

    def test_twentyfour_hour_interval_still_expires_after_twelve_hours(self):
        data = (np.array([T], np.int64), np.array([24.]), np.array([-.003]))
        t = np.array([T+12*a.HOUR-a.BAR, T+12*a.HOUR], np.int64)
        f = a.asof(t, data, {MONTH}, T+a.DAY)
        np.testing.assert_array_equal(f['funding_available'], [True, False]); self.assertEqual(f['funding_rate8h'][0], -.001)

    def test_stage_end_is_physically_filtered_and_previous_month_loaded(self):
        feb = int(pd.Timestamp('2022-02-01', tz='UTC').timestamp()*1000)
        calls = []
        def get(symbol, month, cache, evidence):
            calls.append(month)
            ts = T if month == MONTH else feb
            return np.array([ts], np.int64), np.array([8.]), np.array([-.001])
        with patch.object(a, 'load_month', side_effect=get):
            data, valid = a.load_symbol('BTCUSDT', np.array([T, feb+2*a.BAR], np.int64), feb, feb+3*a.BAR, 'cache', 'evidence')
        self.assertEqual(set(calls), {MONTH, '2022-02'}); self.assertEqual(valid, {MONTH, '2022-02'})
        f = a.asof(np.array([feb, feb+a.BAR], np.int64), data, valid, feb)
        self.assertFalse(f['funding_available'].any())

def event(side=1, confirmation='FLOW', btc_filter='ANY', n=100):
    c = np.full(n, 100.); o = c.copy(); c[20] = 100.+2*side
    raw = [np.arange(n, dtype=np.int64)*s.BAR, o, c+3, c-3, c]
    f = dict(eligible=np.ones(n, bool), volume_multiple=np.full(n, 2.), prior_atr=np.full(n, 1.),
        buy_share=np.full(n, .6 if side==1 else .4), r1=np.full(n, side*.002),
        prevh4=np.full(n, 101.), prevl4=np.full(n, 99.), funding_available=np.ones(n, bool),
        funding_rate8h=np.full(n, -side*.001), funding_rate_original=np.full(n, -side*.001),
        funding_calc_time=np.zeros(n, np.int64), funding_interval_hours=np.full(n, 8.), funding_age_hours=np.full(n, 1.))
    btc = dict(r16=np.full(n, side*.03))
    cfg = next(p for p in s.configurations() if p['side']==side and p['threshold']==.0005
               and p['confirmation']==confirmation and p['btc']==btc_filter)
    return raw, f, btc, cfg

class EconomicTimingTests(unittest.TestCase):
    def test_sixteen_entries_and_ninety_six_unique_frozen_policies(self):
        self.assertEqual(len(s.configurations()), 16); self.assertEqual(len(s.policies()), 96)
        self.assertEqual(len({p['policy'] for p in s.policies()}), 96)

    def test_mirrored_long_negative_short_positive_with_each_confirmation(self):
        for side in (1, -1):
            for confirmation in ('RECLAIM', 'FLOW'):
                for btc_filter in ('ANY', 'ALIGN4H'):
                    raw, f, btc, cfg = event(side, confirmation, btc_filter)
                    self.assertEqual(np.flatnonzero(s.mask(cfg, raw, f, btc)).tolist(), [20])
                    f['funding_rate8h'] *= -1
                    self.assertFalse(s.mask(cfg, raw, f, btc).any())

    def test_missing_funding_is_rejected_even_if_price_and_flow_pass(self):
        raw, f, btc, cfg = event(); f['funding_available'][:] = False
        self.assertFalse(s.mask(cfg, raw, f, btc).any())

    def test_any_btc_can_be_missing_but_alignment_requires_exact_direction(self):
        raw, f, btc, cfg = event(); btc['r16'][:] = np.nan
        self.assertTrue(s.mask(cfg, raw, f, btc)[20])
        self.assertFalse(s.mask(dict(cfg, btc='ALIGN4H'), raw, f, btc).any())

    def test_flow_and_reclaim_reject_their_opposite_confirmations(self):
        for side in (1, -1):
            raw, f, btc, cfg = event(side); f['buy_share'][:] = .4 if side==1 else .6
            self.assertFalse(s.mask(cfg, raw, f, btc).any())
            raw, f, btc, cfg = event(side, 'RECLAIM')
            f['prevh4'][:] = 103.; f['prevl4'][:] = 97.
            self.assertFalse(s.mask(cfg, raw, f, btc).any())

    def test_actual_next_open_stop_and_priority_use_available_inputs(self):
        raw, f, btc, cfg = event(); raw[1][21] = 101.9
        rows, _ = s.intents('X', cfg, raw, f, btc, 0, 100*s.BAR); row = rows[0]
        self.assertEqual(row['entry'], 101.9); self.assertEqual(row['sl'], 99.9)
        self.assertAlmostEqual(row['score'], .001*np.sqrt(2)/ (1/102))
        self.assertEqual(row['entry_time'], row['decision_time']); self.assertEqual(row['entry_time'], 21*s.BAR)
        raw[2][21:] *= 100; raw[3][21:] *= .01; raw[4][21:] *= 20
        new, _ = s.intents('X', cfg, raw, f, btc, 0, 100*s.BAR)
        self.assertEqual(rows[0], new[0])

    def test_entry_gap_stop_floor_and_overwide_stop_are_explicit(self):
        for side in (1, -1):
            raw, f, btc, cfg = event(side); raw[1][21] = raw[4][20]*(1+side*.006)
            rows, ex = s.intents('X', cfg, raw, f, btc, 0, 100*s.BAR)
            self.assertFalse(rows); self.assertEqual(ex['ENTRY_CATCHUP_GAP'], 1)
            raw[1][21] = raw[4][20]*(1-side*.006); f['prior_atr'][:] = .001
            rows, _ = s.intents('X', cfg, raw, f, btc, 0, 100*s.BAR)
            self.assertAlmostEqual(rows[0]['risk_pct'], .005)
            f['prior_atr'][:] = 5
            rows, ex = s.intents('X', cfg, raw, f, btc, 0, 100*s.BAR)
            self.assertFalse(rows); self.assertEqual(ex['STOP_ABOVE_8PCT'], 1)

    def test_intent_cooldown_uses_fixed_history_not_future_exit(self):
        raw, f, btc, cfg = event()
        for i in (25, 35, 37): raw[4][i] = 102.
        rows, _ = s.intents('X', cfg, raw, f, btc, 0, 100*s.BAR)
        self.assertEqual([r['signal_time']//s.BAR for r in rows], [20, 37])

    def test_actual_risk_tp_and_shared_canonical_exclusion(self):
        raw, f, btc, cfg = event(); p = dict(cfg, hold=24, exit_type='TP3', policy='P')
        result = dict(status='RESOLVED', exit_time=23*s.BAR, exit=106., reason='TP', gross_return=.06)
        with patch.object(s.canonical, 'resolve', return_value=result) as resolver:
            rows, _, _ = s.policy_rows('X', [p], raw, f, btc, 0, 100*s.BAR)
        self.assertEqual(resolver.call_args.args[0]['tp'], 106.)
        self.assertAlmostEqual(rows[0]['net40_fraction'], .06-.002*2.06-.0002*2/96)
        with patch.object(s.canonical, 'resolve', return_value=dict(status='DATA_GAP')):
            rows, counts, bad = s.policy_rows('X', [p], raw, f, btc, 0, 100*s.BAR)
        self.assertFalse(rows); self.assertEqual(counts['P/DATA_GAP'], 1); self.assertEqual(bad[0]['status'], 'DATA_GAP')

    def test_prior_atr_range_and_volume_exclude_current_and_future_bars(self):
        n = 200; c = 100+np.arange(n)*.01; t = np.arange(n, dtype=np.int64)*s.BAR
        raw = [t, c.copy(), c+1, c-1, c.copy()]; q = np.full(n, 1e6)
        old = s.features(raw, q, q*.6)
        newraw = [x.copy() for x in raw]; newraw[2][150:] *= 2; newraw[3][150:] *= .5
        nq = q.copy(); nq[150:] *= 100
        new = s.features(newraw, nq, nq*.6)
        for key in ('prior_atr', 'prevh4', 'prevl4'): self.assertEqual(old[key][150], new[key][150])
        self.assertAlmostEqual(new['volume_multiple'][150], 100.)
        for key in old: np.testing.assert_allclose(old[key][:150], new[key][:150], equal_nan=True)

class SelectionCoverageTests(unittest.TestCase):
    def make_parts(self, parts, missing=False, negative_price=False, negative_r=False, clustered=False, empty=False):
        p = s.policies()[0]; rows = []
        for year in (2022, 2023):
            start = int(pd.Timestamp(f'{year}-01-01', tz='Asia/Seoul').timestamp()*1000)
            for i in range(160):
                ts = start+i*s.BAR if clustered else start+(i//2)*s.DAY+(i%2)*s.BAR
                row = {k: 0 for k in s.COLUMNS}
                row.update(symbol=f'X{i%10}', variant=p['policy'], policy=p['policy'], entry_time=ts,
                    net40_fraction=-.001 if negative_price and year==2022 else .02,
                    net40_R=-.1 if negative_r and year==2023 else .5, split='DEV')
                rows.append(row)
        for i in range(8):
            folder = parts/str(i); folder.mkdir(parents=True)
            pd.DataFrame(rows if i==0 and not empty else [], columns=s.COLUMNS).to_csv(folder/'independent_candidates.csv.gz', index=False)
            coverage = [dict(symbol=f'COIN{i}', funding_year_coverage={str(y):dict(eligible=100, known=94 if missing and y==2022 else 95) for y in (2022, 2023)})]
            (folder/'scan_meta.json').write_text(json.dumps(dict(complete=True, stage='DEV', counts={}, coverage=coverage)))

    def test_full96cells_and_kst_year_selection_after95percent_gate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.make_parts(root/'parts'); s.select(root/'parts', root/'out')
            table = pd.read_csv(root/'out/development_policy_cells.csv'); d = json.loads((root/'out/selection.json').read_text())
            self.assertEqual(len(table), 96); self.assertEqual((table.n==0).sum(), 95)
            self.assertEqual(table[table.n>0].iloc[0].year_2022_n, 160)
            self.assertEqual(len(d['policies']), 1); self.assertTrue(d['funding_coverage']['passed'])
            self.assertEqual(d['union_name'], 'FUNDING_UNION')

    def test_positive_results_cannot_hide_input_insufficiency(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); self.make_parts(root/'parts', missing=True); s.select(root/'parts', root/'out')
            d = json.loads((root/'out/selection.json').read_text())
            self.assertEqual(d['status'], 'SOURCE_COVERAGE_INSUFFICIENT'); self.assertFalse(d['policies'])

    def test_price_r_date_frequency_and_empty_cells_reject_independently(self):
        for flag in ('negative_price', 'negative_r', 'clustered', 'empty'):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as td:
                root = Path(td); self.make_parts(root/'parts', **{flag:True}); s.select(root/'parts', root/'out')
                self.assertFalse(json.loads((root/'out/selection.json').read_text())['policies'])
                self.assertEqual(len(pd.read_csv(root/'out/development_policy_cells.csv')), 96)

    def test_duplicate_universe_coverage_cannot_be_counted_twice(self):
        row = dict(symbol='X', funding_year_coverage={'2022':dict(eligible=100, known=95)})
        with self.assertRaises(ValueError): s.funding_coverage([dict(coverage=[row]), dict(coverage=[row])])

if __name__ == '__main__': unittest.main()
