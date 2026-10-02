"""Preregistered prior-day global leadership and next-day resumption; research only."""
from __future__ import annotations
import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from scripts import day_edge_lab as base
from scripts import day_edge_canonical as canonical
from scripts import relative_pullback_v1 as chronology
from scripts import relative_pullback_portfolio as account
from scripts import official_minute_provenance as official
from scripts import premium_absorption_v8 as minute_audit
from scripts import cross_sectional_ranks_v9 as ranks
from scripts.shock_confirmation_v3 import load

BAR, DAY = base.BAR, base.DAY
HOLDS, EXITS = (24, 96), ('TP2', 'TP3', 'TRAIL')
COLUMNS = ['symbol', 'key', 'signal_time', 'decision_time', 'entry_time', 'entry', 'sl',
           'side', 'risk_pct', 'score', 'atr_mult', 'prior_atr', 'buy_share',
           'volume_multiple', 'rank_time', 'rank_fraction', 'rank_universe_n',
           'rank_from_tail', 'prior_relative_return24', 'previous_rank_fraction',
           'tail_pct', 'persistence', 'residual_r16', 'btc_r16', 'btc_filter',
           'known_entry_gap', 'tp', 'max_hold_bars', 'status', 'exit_time', 'exit',
           'reason', 'gross_return', 'variant', 'policy', 'exit_type', 'hold_min',
           'net40_fraction', 'net40_R', 'split']


def configurations():
    return [dict(key=f'LEADER_S{side:+d}_T{tail}_D{days}_{btc}', side=side,
                 tail=tail, persistence=days, btc=btc)
            for side in (1, -1) for tail in (5, 10) for days in (1, 2)
            for btc in ('ANY', 'ALIGN4H')]


def policies():
    return [dict(**cfg, hold=h, exit_type=e, policy=f'{cfg["key"]}__H{h}__{e}')
            for cfg in configurations() for h in HOLDS for e in EXITS]


def features(raw, q, buy):
    f = minute_audit.features(raw, q, buy)
    f['eligible'] &= ranks.observed_days(raw[0]) >= 30
    return f


def mask(cfg, raw, f, btc):
    t, o, h, l, c = raw
    side = cfg['side']
    tail_key = 'rank_from_high' if side == 1 else 'rank_from_low'
    k = np.ceil(f['universe_n'] * cfg['tail'] / 100)
    tail = f['rank_available'] & (f[tail_key] <= k)
    if cfg['persistence'] == 2:
        old_k = np.ceil(f['previous_universe_n'] * cfg['tail'] / 100)
        tail &= f['previous_rank_available'] & (f['previous_' + tail_key] <= old_k)
    elif cfg['persistence'] != 1:
        raise ValueError('unknown persistence')
    residual = side * (f['r16'] - btc['r16'])
    breakout = c > f['prevh4'] if side == 1 else c < f['prevl4']
    flow = f['buy_share'] >= .55 if side == 1 else f['buy_share'] <= .45
    result = (f['eligible'] & tail & (residual >= .005) & breakout & flow
              & (f['volume_multiple'] >= 1.5) & (side * (c - o) > 0)
              & (side * f['r1'] > 0) & (abs(f['r1']) <= .03))
    if cfg['btc'] == 'ALIGN4H':
        result &= side * btc['r16'] > 0
    elif cfg['btc'] != 'ANY':
        raise ValueError('unknown BTC filter')
    prior = np.r_[False, result[:-1]]
    contiguous = np.r_[False, np.diff(t) == BAR]
    return result & ~(prior & contiguous)


def intents(symbol, cfg, raw, f, btc, start, end):
    t, o, h, l, c = raw
    rows, excluded, last = [], Counter(), -100
    for i in np.flatnonzero(mask(cfg, raw, f, btc)):
        j = i + 1
        if j >= len(t) or not start <= t[j] < end or i - last < 32:
            continue
        if t[j] != t[i] + BAR:
            excluded['ENTRY_PATH_GAP'] += 1
            continue
        side, entry, atr = cfg['side'], float(o[j]), f['prior_atr'][i]
        gap = side * (entry / c[i] - 1)
        if gap > .005:
            excluded['ENTRY_CATCHUP_GAP'] += 1
            continue
        if not np.isfinite(atr) or atr <= 0:
            excluded['INVALID_ATR'] += 1
            continue
        distance = max(2 * atr, .0075 * entry)
        if distance / entry > .08:
            excluded['STOP_ABOVE_8PCT'] += 1
            continue
        stop = entry - side * distance
        if stop <= 0:
            excluded['NONPOSITIVE_LEVEL'] += 1
            continue
        last = i
        fraction = f['rank_fraction'][i]
        extremity = fraction if side == 1 else 1 - fraction
        residual = side * (f['r16'][i] - btc['r16'][i])
        rows.append(dict(symbol=symbol, key=cfg['key'], signal_time=int(t[i]),
            decision_time=int(t[i] + BAR), entry_time=int(t[j]), entry_index=int(j),
            entry=entry, sl=float(stop), side=int(side), risk_pct=float(distance / entry),
            score=float(extremity * residual * np.sqrt(f['volume_multiple'][i]) / (atr / c[i])),
            atr_mult=3., prior_atr=float(atr), buy_share=float(f['buy_share'][i]),
            volume_multiple=float(f['volume_multiple'][i]), rank_time=int(f['rank_time'][i]),
            rank_fraction=float(fraction), rank_universe_n=int(f['universe_n'][i]),
            rank_from_tail=int(f['rank_from_high' if side == 1 else 'rank_from_low'][i]),
            prior_relative_return24=float(f['relative_return24'][i]),
            previous_rank_fraction=(float(f['previous_rank_fraction'][i])
                                    if f['previous_rank_available'][i] else None),
            tail_pct=cfg['tail'], persistence=cfg['persistence'], residual_r16=float(residual),
            btc_r16=float(btc['r16'][i]), btc_filter=cfg['btc'], known_entry_gap=float(gap)))
    return rows, excluded


def policy_rows(symbol, chosen, raw, f, btc, start, end):
    rows, counts, bad, grouped = [], Counter(), [], {}
    for p in chosen:
        grouped.setdefault(p['key'], []).append(p)
    for cfgs in grouped.values():
        entries, excluded = intents(symbol, cfgs[0], raw, f, btc, start, end)
        counts.update({cfgs[0]['key'] + '/' + k: n for k, n in excluded.items()})
        for p in cfgs:
            kind = p['exit_type']
            multiple = 3 if kind == 'TP3' else 2
            for seed in entries:
                tr = dict(seed, tp=float(seed['entry'] + seed['side'] * multiple
                                        * abs(seed['entry'] - seed['sl'])), max_hold_bars=p['hold'])
                result = canonical.resolve(tr, raw, f, 'TP2' if kind.startswith('TP') else 'TRAIL', end)
                counts[p['policy'] + '/' + result['status']] += 1
                if result['status'] != 'RESOLVED':
                    bad.append(dict(symbol=symbol, policy=p['policy'], entry_time=tr['entry_time'],
                                    status=result['status']))
                    continue
                row = {k: v for k, v in tr.items() if k != 'entry_index'}
                row.update(result, variant=p['policy'], policy=p['policy'], exit_type=kind)
                row['hold_min'] = (row['exit_time'] - row['entry_time']) / 60000
                exit_price = row['exit'] * (1 - row['side'] * .001) if row['reason'] == 'SL' else row['exit']
                ratio = exit_price / row['entry']
                net = row['side'] * (ratio - 1) - .002 * (1 + ratio) - .0002 * row['hold_min'] / 1440
                row['net40_fraction'] = float(net)
                row['net40_R'] = float(net / account.stop_loss_fraction(row, .002))
                rows.append(row)
    return rows, counts, bad


def scan(data, btc_path, rank_root, out, cache, stage, source_check_path, selection_path=None):
    paths = sorted(data.rglob('*.csv.gz'))
    if not paths:
        raise ValueError('empty frozen source shard')
    source = json.loads(source_check_path.read_text())
    if source['status'] != 'VERIFIED' or len(source['shards']) != 1:
        raise ValueError('scan requires verified source')
    frame, manifest = ranks.load_ranks(rank_root, stage)
    verified = {x['symbol']: x['sha256'] for x in source['files']}
    if source['baseline_sha256'] != manifest['baseline_sha256'] or ranks.digest(btc_path) != manifest['btc_sha256']:
        raise ValueError('scan context differs from frozen rank inputs')
    start, end = ranks.interval(stage)
    if stage == 'DEV':
        chosen = policies()
    else:
        selected = json.loads(selection_path.read_text())
        chosen = selected['policies']
        if not chosen or selected['status'] != 'ACCOUNT_REPLAY_REQUIRED':
            raise ValueError('no frozen policies for GATE')
    br, bq, bb = load(btc_path, end)
    if not len(br[0]):
        raise ValueError('empty BTC context')
    bf = features(br, bq, bb)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(rank_root / 'rank_manifest.json', out / 'rank_manifest.json')
    chronology.MINUTE_CACHE_DIR = cache
    chronology.chronology.one_min = minute_audit.audited_minutes
    chronology.chronology.CACHE.clear()
    official.INPUTS.clear()
    minute_audit.MINUTE_INPUTS.clear()
    minute_audit.MINUTE_SLICES.clear()
    minute_audit.SLICE_DIR = out / 'minute_evidence'
    minute_audit.MINUTE_RAW_DIR = out / 'minute_original_archives'
    minute_audit.SLICE_DIR.mkdir(exist_ok=True)
    minute_audit.MINUTE_RAW_DIR.mkdir(exist_ok=True)
    rows, counts, bad, coverage, sources = [], Counter(), [], [], {}

    def checkpoint(complete):
        ledger = out / 'independent_candidates.csv.gz'
        pd.DataFrame(rows, columns=COLUMNS).to_csv(ledger, index=False, compression=dict(method='gzip', mtime=0))
        pd.DataFrame(bad, columns=['symbol', 'policy', 'entry_time', 'status']).to_csv(out / 'exclusions.csv', index=False)
        (out / 'minute_inputs.json').write_text(json.dumps(list(minute_audit.MINUTE_INPUTS.values()), indent=2))
        (out / 'minute_slices.json').write_text(json.dumps(list(minute_audit.MINUTE_SLICES.values()), indent=2))
        meta = dict(complete=complete, stage=stage, shard=source['shards'][0], counts=dict(counts),
            coverage=coverage, source_files=len(paths), market_hashes=sources, policies=chosen,
            ledger_sha256=ranks.digest(ledger), baseline_sha256=source['baseline_sha256'],
            source_check_sha256=ranks.digest(source_check_path),
            rank_manifest_sha256=ranks.digest(out / 'rank_manifest.json'),
            rank_data_sha256=manifest['rank_data_sha256'], btc_sha256=ranks.digest(btc_path),
            minute_months=len(minute_audit.MINUTE_INPUTS),
            minute_official_checksums_verified=sum(bool(x.get('checksum_verified')) for x in minute_audit.MINUTE_INPUTS.values()),
            selection_sha256=ranks.digest(selection_path) if selection_path else None)
        (out / 'scan_meta.json').write_text(json.dumps(meta, indent=2, allow_nan=False))

    try:
        for n, path in enumerate(paths, 1):
            symbol, sha = path.name[:-7], ranks.digest(path)
            if symbol in sources or verified.get(symbol) != sha or manifest['market_hashes'].get(symbol) != sha:
                raise ValueError('scan source mismatch/duplicate ' + symbol)
            sources[symbol] = sha
            raw, q, buy = load(path, end)
            if not len(raw[0]) or raw[0][0] >= base.DEV_END - 30 * DAY:
                coverage.append(dict(symbol=symbol, status='NO_DEVELOPMENT_HISTORY', outcomes=0))
                continue
            f = features(raw, q, buy)
            btc = base.align_btc(raw[0], br[0], bf)
            f.update(ranks.align_ranks(symbol, raw[0], frame))
            eligible = f['eligible'] & (raw[0] + BAR >= start) & (raw[0] + BAR < end)
            r, c, b = policy_rows(symbol, chosen, raw, f, btc, start, end)
            for row in r:
                row['split'] = stage
            rows.extend(r)
            counts.update({stage + '/' + k: v for k, v in c.items()})
            bad.extend(b)
            coverage.append(dict(symbol=symbol, status='SCANNED', bars=len(raw[0]), outcomes=len(r),
                eligible_bars=int(eligible.sum()), available_rank_bars=int((eligible & f['rank_available']).sum()),
                unavailable_rank_bars=int((eligible & ~f['rank_available']).sum()),
                missing_btc_4h_bars=int((eligible & ~np.isfinite(btc['r16'])).sum()), market_sha256=sha))
            chronology.chronology.CACHE.clear()
            if n % 8 == 0 or n == len(paths):
                checkpoint(False)
                print('V9_LEADER_SCAN', stage, n, '/', len(paths), 'parameterized_outcomes', len(rows), flush=True)
        if sources != verified:
            raise ValueError('scan omitted a verified source file')
    except BaseException:
        checkpoint(False)
        raise
    checkpoint(True)
    print('V9_LEADER_SCAN_DONE', stage, len(rows), 'chronology_exclusions', len(bad), flush=True)


def development_rejections(r):
    reasons = []
    if r['n'] < 300: reasons.append('N_LT_300')
    if r['symbols'] < 10: reasons.append('SYMBOLS_LT_10')
    if r['top_symbol_share_pct'] > 30: reasons.append('TOP_SYMBOL_GT_30PCT')
    for key in ('net40_mean_bp', 'net40_R_mean'):
        if r[key] is None or not np.isfinite(r[key]) or r[key] <= 0: reasons.append(key.upper() + '_NONPOSITIVE')
    for year, min_n, min_days in ((2021, 40, 20), (2022, 80, 60), (2023, 80, 60)):
        if r.get(f'year_{year}_n', 0) < min_n: reasons.append(f'{year}_N')
        if r.get(f'year_{year}_days', 0) < min_days: reasons.append(f'{year}_DATES')
        for metric in ('net40_R', 'net40_mean_bp', 'day_R'):
            if r.get(f'year_{year}_{metric}', -999) <= 0: reasons.append(f'{year}_{metric}_NONPOSITIVE')
    return reasons


def select(parts, out):
    out.mkdir(parents=True, exist_ok=True)
    paths = sorted(parts.rglob('independent_candidates.csv.gz'))
    if len(paths) != 8:
        raise ValueError('incomplete development shards')
    originals, sources, shards, rank_hashes, contexts = [], {}, set(), set(), set()
    for path in paths:
        m = json.loads((path.parent / 'scan_meta.json').read_text())
        manifest_path = path.parent / 'rank_manifest.json'
        manifest = json.loads(manifest_path.read_text())
        if (not m['complete'] or m['stage'] != 'DEV' or m['ledger_sha256'] != ranks.digest(path)
                or m['rank_manifest_sha256'] != ranks.digest(manifest_path)
                or m['rank_data_sha256'] != manifest['rank_data_sha256']):
            raise ValueError('incomplete or altered development scan')
        if m['shard'] in shards:
            raise ValueError('duplicate development shard')
        shards.add(m['shard'])
        rank_hashes.add(m['rank_manifest_sha256'])
        contexts.add((m['baseline_sha256'], m['btc_sha256']))
        for symbol, sha in m['market_hashes'].items():
            if symbol in sources or manifest['market_hashes'].get(symbol) != sha:
                raise ValueError('development source differs from frozen ranks')
            sources[symbol] = sha
        originals.append(m)
    if (shards != set(range(8)) or len(rank_hashes) != 1 or len(contexts) != 1
            or sources != manifest['market_hashes']):
        raise ValueError('incomplete/inconsistent global development universe')
    frames = [pd.read_csv(p) for p in paths]
    nonempty = [g for g in frames if len(g)]
    x = pd.concat(nonempty, ignore_index=True) if nonempty else frames[0].iloc[:0].copy()
    if x.duplicated(['symbol', 'variant', 'entry_time']).any():
        raise ValueError('duplicate development intents')
    if len(x) and (not x['split'].eq('DEV').all() or not x.entry_time.between(base.START, base.DEV_END - 1).all()):
        raise ValueError('development ledger stage leakage')
    info = {p['policy']: p for p in policies()}
    if not set(x.variant).issubset(info):
        raise ValueError('unregistered policy')
    grouped, table = {k: g for k, g in x.groupby('variant')}, []
    for name, p in info.items():
        g = grouped.get(name, x.iloc[:0])
        years = pd.to_datetime(g.entry_time, unit='ms', utc=True).dt.tz_convert('Asia/Seoul').dt.year
        pn, rr = g.net40_fraction, g.net40_R
        gain, loss = pn[pn > 0].sum(), -pn[pn < 0].sum()
        sym = g.groupby('symbol').net40_fraction.sum().clip(lower=0)
        r = dict(**p, n=len(g), symbols=g.symbol.nunique(),
            net40_mean_bp=float(pn.mean() * 10000) if len(g) else None,
            net40_R_mean=float(rr.mean()) if len(g) else None,
            net40_pf=float(gain / loss) if loss else None,
            win_pct=float((pn > 0).mean() * 100) if len(g) else None,
            top_symbol_share_pct=float(sym.max() / sym.sum() * 100) if sym.sum() > 0 else 100.)
        for year, z in g.groupby(years):
            days = (z.entry_time.to_numpy(np.int64) + account.KOREA_OFFSET) // DAY
            day_r = z.groupby(days).net40_R.mean()
            r.update({f'year_{year}_net40_R': float(z.net40_R.mean()),
                      f'year_{year}_net40_mean_bp': float(z.net40_fraction.mean() * 10000),
                      f'year_{year}_day_R': float(day_r.mean()),
                      f'year_{year}_days': len(day_r), f'year_{year}_n': len(z)})
        r['rejections'] = '|'.join(development_rejections(r))
        table.append(r)
    table.sort(key=lambda r: (-min(r.get(f'year_{y}_day_R', -999) for y in (2021, 2022, 2023)), r['policy']))
    chosen, used, side_count = [], set(), Counter()
    for r in table:
        if r['rejections'] or r['key'] in used or side_count[r['side']] >= 3:
            continue
        chosen.append(dict(info[r['policy']], development_net40_R=r['net40_R_mean'],
                           development_worst_year_day_R=min(r[f'year_{y}_day_R'] for y in (2021, 2022, 2023))))
        used.add(r['key']); side_count[r['side']] += 1
        if len(chosen) == 6: break
    decision = dict(status='ACCOUNT_REPLAY_REQUIRED' if chosen else 'NO_DEVELOPMENT_POLICY_SURVIVOR',
        cells_examined=96, selected=chosen, policies=chosen, union_name='CROSS_SECTIONAL_LEADER_UNION',
        rank_manifest_sha256=next(iter(rank_hashes)), rank_data_sha256=manifest['rank_data_sha256'],
        source_symbols=len(sources), economic_surviving_cells=sum(not r['rejections'] for r in table),
        rejection_counts=dict(Counter(reason for r in table for reason in r['rejections'].split('|') if reason)),
        note='Overlapping parameterized outcomes and active-date means are diagnostics, not executable account profits.')
    pd.DataFrame(table).to_csv(out / 'development_policy_cells.csv', index=False)
    (out / 'selection.json').write_text(json.dumps(decision, indent=2, allow_nan=False))
    selection_hash = ranks.digest(out / 'selection.json')
    selected_names = {p['policy'] for p in chosen}
    for i, (path, m) in enumerate(zip(paths, originals)):
        d = pd.read_csv(path); d = d[d.variant.isin(selected_names)]
        kept = {k: v for k, v in m['counts'].items() if any('/' + p + '/' in k for p in selected_names)}
        target = out / f'dev-{i}'; target.mkdir(exist_ok=True)
        d.to_csv(target / 'independent_candidates.csv.gz', index=False, compression='gzip')
        (target / 'scan_meta.json').write_text(json.dumps(dict(complete=True, stage='DEV', counts=kept,
            selection_sha256=selection_hash, source_development_ledger_sha256=ranks.digest(path),
            rank_manifest_sha256=m['rank_manifest_sha256'], coverage=m['coverage']), indent=2))
    print('V9_LEADER_SELECT', json.dumps(decision), flush=True)


def strict_accounts(summary, yearly, integrity):
    found, rejected = [], []
    for name, g in summary.groupby('variant'):
        a = g[g.guarded]
        why = []
        required = {(s, c) for s in ('DEV', 'GATE') for c in (20, 40)}
        if len(a) != 4 or set(zip(a.split, a.cost_bps)) != required:
            why.append('MISSING_ACCOUNT_SCENARIO')
        for key in ('trades', 'mdd_15m_pct', 'net_return_pct', 'cagr_pct', 'pf', 'daily_mean_pct'):
            if not np.isfinite(a[key]).all(): why.append('NONFINITE_' + key)
        if a.halt_time.notna().any(): why.append('DD15_HALT')
        if a.trades.min() < 80: why.append('TRADES_LT_80')
        if a.mdd_15m_pct.max() >= 15: why.append('MDD_GE_15')
        if a.net_return_pct.min() <= 0: why.append('ACCOUNT_NONPOSITIVE')
        b, stress = a[a.cost_bps == 20], a[a.cost_bps == 40]
        if b.cagr_pct.min() < 20: why.append('CAGR20_LT_20PCT')
        if b.pf.min() < 1.15: why.append('PF20_LT_1_15')
        if stress.pf.min() < 1.05: why.append('PF40_LT_1_05')
        yy = yearly[(yearly.variant == name) & yearly.guarded & (yearly.cost_bps == 20)]
        for split, year in (('DEV', '2021'), ('DEV', '2022'), ('DEV', '2023'), ('GATE', '2024')):
            z = yy[(yy.split == split) & (yy.period.astype(str) == year)]
            if len(z) != 1 or not np.isfinite(z.net_return_pct).all() or z.net_return_pct.min() <= 0:
                why.append(split + '_' + year + '_ACCOUNT_NONPOSITIVE_OR_MISSING')
        if why:
            rejected.append(dict(variant=name, reasons=why)); continue
        found.append(dict(variant=name, rank_score=float(b.cagr_pct.min()),
            worst_40bp_mdd_pct=float(stress.mdd_15m_pct.max()), min_40bp_pf=float(stress.pf.min()),
            goal_daily_mean_met=bool(b.daily_mean_pct.min() >= .7)))
    found.sort(key=lambda x: (-x['rank_score'], x['worst_40bp_mdd_pct'], x['variant']))
    status = 'PROVISIONAL_RESEARCH_SURVIVOR' if found else 'NO_CANONICAL_ACCOUNT_SURVIVOR'
    if integrity['chronology_exclusions']: status += '__DATA_COVERAGE_REVIEW_REQUIRED'
    return dict(status=status, survivors=found, selected=found[:1], rejected=rejected, integrity=integrity,
                annual_contract=[['DEV', 2021], ['DEV', 2022], ['DEV', 2023], ['GATE', 2024]],
                note='Seen historical research gate only. Daily target, robustness and fresh forward evidence remain separate.')


def accounts(data, parts, out, selection_path):
    canonical.accounts(data, parts, out, selection_path, ('DEV', 'GATE'), expected_shards=16, all_kst_days=True)
    shutil.copyfile(out / 'survivors.json', out / 'shared_engine_decision.json')
    old = json.loads((out / 'survivors.json').read_text())
    result = strict_accounts(pd.read_csv(out / 'summary.csv'), pd.read_csv(out / 'year_quarter.csv'), old['integrity'])
    (out / 'survivors.json').write_text(json.dumps(result, indent=2, allow_nan=False))
    print('V9_STRICT_ACCOUNT_DECISION', json.dumps(result), flush=True)


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('scan')
    for n in ('data', 'btc', 'ranks', 'out', 'minute-cache', 'source-check'):
        p.add_argument('--' + n, type=Path, required=True)
    p.add_argument('--stage', choices=('DEV', 'GATE'), required=True)
    p.add_argument('--selection', type=Path)
    p = sub.add_parser('select')
    for n in ('parts', 'out'): p.add_argument('--' + n, type=Path, required=True)
    p = sub.add_parser('accounts')
    for n in ('data', 'parts', 'out', 'selection'): p.add_argument('--' + n, type=Path, required=True)
    a = ap.parse_args()
    if a.command == 'scan':
        scan(a.data, a.btc, a.ranks, a.out, a.minute_cache, a.stage, a.source_check, a.selection)
    elif a.command == 'select': select(a.parts, a.out)
    else: accounts(a.data, a.parts, a.out, a.selection)


if __name__ == '__main__': main()
