"""Development-only fixed-time event scout. These are NOT account backtests.

No price-triggered orders/exits are simulated here: signal at a closed 15m bar,
entry at the following open, exit at a predeclared later open. Therefore there
is no unobserved intrabar TP/SL ordering. Finalists must use the canonical
minute-authority engine and the account risk rules before they are strategies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from scripts.psar_1d_canonical_engine import segments, validate_raw

BAR = 900_000
DAY = 86_400_000
START = int(pd.Timestamp('2021-09-01', tz='UTC').timestamp() * 1000)
DEV_END = int(pd.Timestamp('2024-01-01', tz='UTC').timestamp() * 1000)
GATE_END = int(pd.Timestamp('2025-01-01', tz='UTC').timestamp() * 1000)
COMPARISON_END = int(pd.Timestamp('2026-09-01', tz='UTC').timestamp() * 1000)
HOLDS = (4, 16, 48, 96)


def configurations():
    """36 economic entry hypotheses x four holding times; frozen before scout."""
    out = []
    specs = [('SHOCK_FOLLOW', (1, 4), (.03, .06)),
             ('SHOCK_FADE', (1, 4), (.03, .06)),
             ('ISOLATED_FADE', (4,), (.03, .06)),
             ('EXHAUSTION_FADE', (96,), (.15, .30)),
             ('REJECTION_FADE', (4,), (.04, .08)),
             ('TREND_IGNITION', (16,), (.04, .08)),
             ('SLOW_PULLBACK', (96,), (.10, .20))]
    for family, lookbacks, thresholds in specs:
        for lb in lookbacks:
            for threshold in thresholds:
                for move in (-1, 1):
                    side = -move if 'FADE' in family else move
                    key = f'{family}_L{lb}_T{round(threshold*100)}_M{move:+d}'
                    out.append(dict(key=key, family=family, lookback=lb,
                                    threshold=threshold, move=move, side=side))
    assert len(out) == 36
    return out


def load(path):
    df = pd.read_csv(path, usecols=['open_time', 'open', 'high', 'low', 'close', 'quote_volume'])
    ts = df.open_time.to_numpy()
    if not np.isfinite(ts).all() or np.any(ts != np.floor(ts)):
        raise ValueError(f'noninteger timestamp: {path}')
    raw = tuple([ts.astype(np.int64)] + [df[k].to_numpy(float) for k in ('open', 'high', 'low', 'close')])
    validate_raw(*raw)
    q = df.quote_volume.to_numpy(float)
    if np.any(~np.isfinite(q)) or np.any(q < 0):
        raise ValueError(f'bad quote turnover: {path}')
    return raw, q


def features(raw, quote):
    t, o, h, l, c = raw
    keys = ['r1', 'r4', 'r16', 'r96', 'atr', 'q96', 'qratio', 'prevh4', 'prevl4',
            'prevh16', 'prevl16', 'clv']
    f = {k: np.full(len(t), np.nan) for k in keys}
    for a, b in segments(t):
        cc, hh, ll, qq = c[a:b], h[a:b], l[a:b], quote[a:b]
        for n in (1, 4, 16, 96):
            if b-a > n:
                f[f'r{n}'][a+n:b] = cc[n:] / cc[:-n] - 1
        s = pd.Series(cc)
        prev = s.shift(1).to_numpy()
        tr = np.maximum(hh-ll, np.maximum(abs(hh-prev), abs(ll-prev)))
        f['atr'][a:b] = pd.Series(tr).ewm(alpha=1/14, adjust=False, min_periods=14).mean().to_numpy() / cc
        f['q96'][a:b] = pd.Series(qq).rolling(96, min_periods=96).sum().to_numpy()
        old = pd.Series(qq).rolling(96, min_periods=96).mean().shift(4).to_numpy() * 4
        now = pd.Series(qq).rolling(4, min_periods=4).sum().to_numpy()
        f['qratio'][a:b] = np.divide(now, old, out=np.full(len(cc), np.nan), where=old > 0)
        for n in (4, 16):
            f[f'prevh{n}'][a:b] = pd.Series(hh).rolling(n, min_periods=n).max().shift(1).to_numpy()
            f[f'prevl{n}'][a:b] = pd.Series(ll).rolling(n, min_periods=n).min().shift(1).to_numpy()
        f['clv'][a:b] = np.divide(cc-ll, hh-ll, out=np.full(len(cc), .5), where=hh>ll)
    # The source starts in 2021-09 for old coins. Never trade its first 30 days.
    f['eligible'] = (t >= t[0] + 30*DAY) & (f['q96'] >= 20_000_000)
    return f


def align_btc(t, bt, bf):
    k = np.searchsorted(bt, t)
    matched = k < len(bt)
    matched[matched] &= bt[k[matched]] == t[matched]
    out = {}
    for key in ('r1', 'r4', 'r16', 'r96'):
        value = np.full(len(t), np.nan)
        value[matched] = bf[key][k[matched]]
        out[key] = value
    return out


def signal_mask(cfg, raw, f, btc):
    t, o, h, l, c = raw
    move, threshold, lb = cfg['move'], cfg['threshold'], cfg['lookback']
    family = cfg['family']
    mask = (move*f[f'r{lb}'] >= threshold) & f['eligible']
    if family == 'ISOLATED_FADE':
        mask &= (abs(btc['r4']) <= .015) & (move*(f['r4']-btc['r4']) >= threshold)
    elif family == 'EXHAUSTION_FADE':
        mask &= (move*f['r4'] <= -.005) & (move*f['r16'] <= -.01)
        mask &= c < f['prevl4'] if move == 1 else c > f['prevh4']
    elif family == 'REJECTION_FADE':
        mask &= (f['qratio'] >= 2) & ((f['clv'] <= .35) if move == 1 else (f['clv'] >= .65))
    elif family == 'TREND_IGNITION':
        mask &= (move*f['r96'] > 0) & (f['qratio'] >= 2)
        mask &= c > f['prevh16'] if move == 1 else c < f['prevl16']
    elif family == 'SLOW_PULLBACK':
        mask &= (move*f['r16'] <= -.01) & (move*f['r4'] >= .005)
    elif family not in ('SHOCK_FOLLOW', 'SHOCK_FADE'):
        raise ValueError(family)
    # Trigger on onset of a qualifying state, using only closed bars.
    prior = np.r_[False, mask[:-1]]
    contiguous = np.r_[False, np.diff(t) == BAR]
    return mask & ~(prior & contiguous)


def fixed_events(cfg, raw, f, btc, hold, start, end):
    """Non-overlap per coin/cell, fixed OPEN exits. No account-profit claim."""
    t, o, h, l, c = raw
    idx = np.flatnonzero(signal_mask(cfg, raw, f, btc))
    blocked_until = -1
    rows = []
    for i in idx:
        j, z = i+1, i+1+hold
        if j < blocked_until or z >= len(t) or not start <= t[j] < end or t[z] >= end:
            continue
        if t[z]-t[i] != (hold+1)*BAR:
            continue
        ratio = float(o[z]/o[j])
        gross = cfg['side'] * (ratio-1)
        fund = .0002 * hold*BAR/DAY
        rows.append((int(t[j]), gross, gross-.001*(1+ratio)-fund,
                     gross-.002*(1+ratio)-fund))
        blocked_until = z
    return pd.DataFrame(rows, columns=['time', 'gross', 'net20', 'net40'])


def moments(values):
    x = np.asarray(values, dtype=float)
    return dict(n=len(x), sum=float(x.sum()), sumsq=float(x@x),
                positive_sum=float(x[x>0].sum()), negative_sum=float(-x[x<0].sum()),
                wins=int((x>0).sum()), median=float(np.median(x)) if len(x) else None)


def scout(data, btc_path, out, plan):
    out.mkdir(parents=True, exist_ok=True)
    br, bq = load(btc_path)
    # Gate restriction is applied BEFORE computing BTC indicators too.
    m = br[0] < GATE_END
    br, bq = tuple(a[m] for a in br), bq[m]
    bf = features(br, bq)
    rows, manifest = [], []
    files = sorted(data.rglob('*.csv.gz'))
    seen = set()
    configs = configurations()
    for n, path in enumerate(files, 1):
        symbol = path.name[:-7]
        if symbol in seen:
            raise ValueError(f'duplicate input {symbol}')
        seen.add(symbol)
        raw, quote = load(path)
        source_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        # All asset classes introduced after 2023 are ineligible in this study.
        # This removes later TradFi perpetuals without guessing ticker names.
        m = raw[0] < GATE_END
        raw, quote = tuple(a[m] for a in raw), quote[m]
        if not len(raw[0]) or raw[0][0] >= DEV_END-30*DAY:
            manifest.append(dict(symbol=symbol, status='NO_DEVELOPMENT_HISTORY', sha256=source_sha))
            continue
        f = features(raw, quote)
        btc = align_btc(raw[0], br[0], bf)
        emitted = 0
        for cfg in configs:
            for hold in HOLDS:
                for split, start, end in [('DEV', START, DEV_END), ('GATE', DEV_END, GATE_END)]:
                    events = fixed_events(cfg, raw, f, btc, hold, start, end)
                    if events.empty:
                        continue
                    years = pd.to_datetime(events.time, unit='ms', utc=True).dt.year
                    for year, ev in events.groupby(years):
                        row = dict(symbol=symbol, key=cfg['key'], family=cfg['family'], side=cfg['side'],
                                   hold=hold, split=split, year=int(year))
                        for col in ('gross', 'net20', 'net40'):
                            for k, value in moments(ev[col]).items():
                                row[f'{col}_{k}'] = value
                        rows.append(row)
                        emitted += len(ev)
        manifest.append(dict(symbol=symbol, status='SCOUTED', sha256=source_sha,
                             bars=len(raw[0]), first=int(raw[0][0]), last=int(raw[0][-1]),
                             gaps=int((np.diff(raw[0]) != BAR).sum()), cell_events=emitted))
        if n % 8 == 0 or n == len(files):
            print('SCOUT', n, '/', len(files), 'symbol', symbol, 'rows', len(rows), flush=True)
    pd.DataFrame(rows).to_csv(out/'cell_symbol_year.csv.gz', index=False, compression='gzip')
    meta = dict(kind='FIXED_TIME_EVENT_SCOUT_NOT_ACCOUNT', configurations=configs, holds=HOLDS,
                dev_end=DEV_END, gate_end=GATE_END, source_data_run=36095439671,
                input_files=len(files), manifest=manifest,
                plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest())
    (out/'meta.json').write_text(json.dumps(meta, indent=2))
    print('SCOUT_DONE', len(rows), flush=True)


def merge(parts, out):
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(parts.rglob('cell_symbol_year.csv.gz'))
    if not files:
        raise ValueError('no scout shards')
    z = pd.concat([pd.read_csv(p) for p in files], ignore_index=True)
    if z.duplicated(['symbol', 'key', 'hold', 'split', 'year']).any():
        raise ValueError('duplicate scout rows')
    results = []
    for (key, hold, split), g in z.groupby(['key', 'hold', 'split']):
        row = dict(key=key, hold=int(hold), split=split, family=g.family.iloc[0],
                   side=int(g.side.iloc[0]), symbols=int(g.symbol.nunique()))
        for col in ('gross', 'net20', 'net40'):
            nn, total = int(g[f'{col}_n'].sum()), float(g[f'{col}_sum'].sum())
            gain, loss = float(g[f'{col}_positive_sum'].sum()), float(g[f'{col}_negative_sum'].sum())
            row[f'{col}_n'] = nn
            row[f'{col}_mean_bp'] = total / nn * 10000
            row[f'{col}_pf'] = gain/loss if loss else None
            row[f'{col}_win_pct'] = float(g[f'{col}_wins'].sum()/nn*100)
            sums = g.groupby('symbol')[f'{col}_sum'].sum()
            row[f'{col}_positive_symbol_pct'] = float((sums > 0).mean()*100)
            row[f'{col}_top_positive_symbol_share_pct'] = float(sums.clip(lower=0).max()/sums.clip(lower=0).sum()*100) if (sums>0).any() else None
        for year, gy in g.groupby('year'):
            row[f'year_{year}_net40_bp'] = float(gy.net40_sum.sum()/gy.net40_n.sum()*10000)
            row[f'year_{year}_n'] = int(gy.net40_n.sum())
        results.append(row)
    frame = pd.DataFrame(results).sort_values(['split', 'net40_mean_bp'], ascending=[True, False])
    frame.to_csv(out/'scout_all_cells.csv', index=False)
    frame.to_json(out/'scout_all_cells.json', orient='records', indent=2)
    # Frozen gate: no post-2024 outcome is reachable from this executable.
    candidates = []
    dev = frame[frame.split == 'DEV']
    gate = frame[frame.split == 'GATE'].set_index(['key', 'hold'])
    for _, r in dev.iterrows():
        pair = (r['key'], r['hold'])
        if pair not in gate.index:
            continue
        v = gate.loc[pair]
        yearly = [r.get('year_2022_net40_bp', np.nan), r.get('year_2023_net40_bp', np.nan)]
        shares = [r.net40_top_positive_symbol_share_pct, v.net40_top_positive_symbol_share_pct]
        if (not np.all(np.isfinite(yearly + shares))
                or r.net40_n < 300 or v.net40_n < 100 or min(r.symbols,v.symbols) < 10
                or min(r.net40_mean_bp,v.net40_mean_bp) <= 0
                or min(yearly) <= 0 or max(shares) > 30):
            continue
        candidates.append(dict(key=r['key'], hold=int(r['hold']), family=r.family, side=int(r.side),
                               dev_net40_bp=float(r.net40_mean_bp), gate_net40_bp=float(v.net40_mean_bp),
                               rank_score=float(min(r.net40_mean_bp,v.net40_mean_bp))))
    candidates.sort(key=lambda r: (-r['rank_score'], r['key'], r['hold']))
    # One strongest hold per entry key, at most two per family, six total.
    selected, used, families = [], set(), {}
    for c in candidates:
        if c['key'] in used or families.get(c['family'], 0) >= 2:
            continue
        selected.append(c); used.add(c['key']); families[c['family']] = families.get(c['family'], 0)+1
        if len(selected) == 6:
            break
    selection = dict(status='CANONICAL_REPLAY_REQUIRED' if selected else 'NO_CELL_PASSED_FROZEN_GATE',
                     cells_examined=36*len(HOLDS), shard_count=len(files), eligible_cells=candidates,
                     selected=selected, observations='Overlapping across coins; NO account return inference.')
    (out/'selection.json').write_text(json.dumps(selection, indent=2))
    z.to_csv(out/'merged_cell_symbol_year.csv.gz', index=False, compression='gzip')
    print('MERGE_DONE', json.dumps(selection), flush=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='command', required=True)
    scout_args = sub.add_parser('scout')
    scout_args.add_argument('--data', type=Path, required=True)
    scout_args.add_argument('--btc', type=Path, required=True)
    scout_args.add_argument('--out', type=Path, required=True)
    scout_args.add_argument('--plan', type=Path, default=Path('research/day-edge-lab-v2/PLAN.md'))
    merge_args = sub.add_parser('merge')
    merge_args.add_argument('--parts', type=Path, required=True)
    merge_args.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    if a.command == 'scout':
        scout(a.data, a.btc, a.out, a.plan)
    else:
        merge(a.parts, a.out)


if __name__ == '__main__':
    main()
