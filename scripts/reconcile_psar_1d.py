"""Reproduce legacy 24D/12D availability and export auditable daily inputs."""
import argparse
import glob
import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

DAY = 86400000
STEP = 900000
BURN = 100
CUT = 1735689600000


def symbol(path):
    name = Path(path).name
    if not name.endswith('.csv.gz'):
        raise ValueError(name)
    s = name[:-7].upper()
    if not re.fullmatch(r'[A-Z0-9]+USDT', s):
        raise ValueError(name)
    return s


def load(path):
    d = pd.read_csv(path, usecols=['open_time', 'open', 'high', 'low', 'close'])
    # Legacy sorted its inputs; also record original ordering independently.
    original_sorted = bool((np.diff(d.open_time.to_numpy(np.int64)) > 0).all())
    d = d.sort_values('open_time')
    t, o, h, l, c = [d[k].to_numpy(np.int64 if k == 'open_time' else float)
                      for k in ['open_time', 'open', 'high', 'low', 'close']]
    assert len(t) and (np.diff(t) > 0).all() and (t % STEP == 0).all()
    assert all(np.isfinite(v).all() for v in (o, h, l, c))
    assert (l > 0).all() and (h >= np.maximum.reduce([o, l, c])).all()
    assert (l <= np.minimum.reduce([o, h, c])).all()
    return (t, o, h, l, c), original_sorted


def segments(t, step=STEP):
    q = np.r_[0, np.flatnonzero(np.diff(t) != step) + 1, len(t)]
    return list(zip(q[:-1], q[1:]))


def daily(data):
    t, o, h, l, c = data
    b = t // DAY
    q = np.r_[0, np.flatnonzero(b[1:] != b[:-1]) + 1, len(t)]
    a, z = q[:-1], q[1:]
    g = (z - a == 96) & (t[a] % DAY == 0)
    a, z = a[g], z[g]
    return (t[a], o[a], np.maximum.reduceat(h, q[:-1])[g],
            np.minimum.reduceat(l, q[:-1])[g], c[z - 1])


def psar(h, l):
    n = len(h)
    sopen, bull = np.full(n, np.nan), np.ones(n, bool)
    if n < 3:
        return sopen, bull
    s, trend, ep, af = l[0], True, h[1], .02
    sopen[1] = s
    for i in range(2, n):
        z = s + af * (ep - s)
        z = min(z, l[i - 1], l[i - 2]) if trend else max(z, h[i - 1], h[i - 2])
        sopen[i], bull[i] = z, trend
        if trend:
            if l[i] < z:
                trend, s, ep, af = False, ep, l[i], .02
            else:
                s = z
                if h[i] > ep:
                    ep, af = h[i], min(af + .02, .2)
        else:
            if h[i] > z:
                trend, s, ep, af = True, ep, h[i], .02
            else:
                s = z
                if l[i] < ep:
                    ep, af = l[i], min(af + .02, .2)
    return sopen, bull


def candidates(data, s, source, segid, avail):
    t, o, h, l, c = data
    n = len(t)
    if n <= BURN + avail + 1:
        return []
    sar, bull = psar(h, l)
    prev = np.r_[np.nan, c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - prev), abs(l - prev)))
    atr = np.r_[np.nan, pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()[:-1]]
    flip = np.r_[False, bull[1:] != bull[:-1]]
    last, d0, out = -1, np.nan, []
    for i in range(BURN, n - avail):
        if not np.isfinite(sar[i]) or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if flip[i]:
            last = i
            d0 = abs(c[i - 1] - sar[i]) / atr[i]
        if last < 0 or bull[i] or i - last != 3:
            continue
        row = {'symbol': s, 'ts': int(t[i]), 'flip_ts': int(t[last]),
               'd0': float(d0), 'source': source, 'segment': int(segid),
               'availability': avail, 'days_remaining': n - 1 - i,
               'entry_open': float(o[i]), 'ret8': float((1 - o[i + 8] / o[i]) * 100)}
        for k in range(1, 9):
            row[f'ret{k}'] = float((1 - o[i + k] / o[i]) * 100)
            row[f'bull{k}'] = bool(bull[i + k])
        out.append(row)
    return out


def filtered(frame):
    q = frame.loc[frame.ts < CUT, 'd0'].quantile([0, 1/3, 2/3, 1]).to_numpy()
    labels = pd.cut(frame.d0, q, labels=['LOW', 'MID', 'HIGH'], include_lowest=True)
    return frame[labels.isin(['MID', 'HIGH'])].copy(), q


def metrics(v):
    v = np.asarray(v, float) - .4
    loss = -v[v < 0].sum()
    return {'n': len(v), 'pf': float(v[v > 0].sum() / loss) if loss else None,
            'win': float((v > 0).mean() * 100) if len(v) else None,
            'avg': float(v.mean()) if len(v) else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--old-artifacts', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    out = Path(args.out)
    (out / 'daily').mkdir(parents=True, exist_ok=True)
    files = sorted(glob.glob(args.data + '/**/*.csv.gz', recursive=True))
    assert files
    rows24, rows12, rows8, manifest, seen = [], [], [], [], {}
    for fidx, path in enumerate(files):
        s = symbol(path)
        raw, original_sorted = load(path)
        assert original_sorted, f'input not increasing: {path}'
        t = raw[0]
        lo, hi = int(t[0]), int(t[-1])
        for a, b in seen.get(s, []):
            assert max(lo, a) > min(hi, b), f'duplicate range {s}'
        seen.setdefault(s, []).append((lo, hi))
        parts = segments(t)
        meta = {'symbol': s, 'file': path, 'first': lo, 'last': hi,
                'bars15m': len(t), 'segments': len(parts), 'daily_segments': []}
        for segid, (a, b) in enumerate(parts):
            d = daily(tuple(v[a:b] for v in raw))
            if not len(d[0]):
                continue
            assert (d[0] % DAY == 0).all() and (np.diff(d[0]) == DAY).all()
            name = f'{s}_{fidx:04d}_{segid:04d}.npz'
            np.savez_compressed(out / 'daily' / name, t=d[0], o=d[1], h=d[2], l=d[3], c=d[4])
            meta['daily_segments'].append({'file': name, 'days': len(d[0])})
            # Reproduce both legacy prefilters exactly, including short segments.
            for avail, target in [(24, rows24), (12, rows12), (8, rows8)]:
                if b - a >= 96 * (BURN + avail + 2):
                    target.extend(candidates(d, s, path, segid, avail))
        manifest.append(meta)
        if fidx % 50 == 0:
            print(f'PROGRESS files={fidx+1}/{len(files)} n24={len(rows24)} n12={len(rows12)}', flush=True)
    old_rows = []
    for path in sorted(glob.glob(args.old_artifacts + '/**/*.json', recursive=True)):
        if 'psar-distance-age-1d-' not in path:
            continue
        with open(path) as stream:
            d = json.load(stream)
        old_rows.extend({k: r[k] for k in ('symbol', 'ts', 'd0', 'ret8')}
                        for r in d['rows'] if r['side'] == 'BEAR' and r['age'] == 3)
    old = pd.DataFrame(old_rows)
    raw24, raw12, raw8 = (pd.DataFrame(r) for r in (rows24, rows12, rows8))
    for f in (old, raw24, raw12, raw8):
        assert not f.duplicated(['symbol', 'ts']).any()
    joined = old.merge(raw24, on=['symbol', 'ts'], how='outer', suffixes=('_old', '_raw'), indicator=True)
    unmatched = joined[joined._merge != 'both']
    joined.to_csv(out / 'artifact_vs_raw24.csv.gz', index=False)
    assert not len(unmatched), f'artifact/raw24 keys differ: {len(unmatched)}'
    assert np.allclose(joined.d0_old, joined.d0_raw, rtol=1e-12, atol=1e-12)
    assert np.allclose(joined.ret8_old, joined.ret8_raw, rtol=1e-12, atol=1e-12)
    f24, q24 = filtered(raw24)
    f12, q12 = filtered(raw12)
    diff = f24.merge(f12, on=['symbol', 'ts'], how='outer', suffixes=('_24', '_12'), indicator=True)
    diff = diff[diff._merge != 'both'].copy()
    all24 = set(zip(raw24.symbol, raw24.ts))
    diff['reason'] = ['availability_24_to_12' if (s, t) not in all24 else 'train_quantile_shift'
                      for s, t in zip(diff.symbol, diff.ts)]
    diff['period'] = np.where(diff.ts < CUT, 'TRAIN', 'HOLDOUT')
    diff['date'] = pd.to_datetime(diff.ts, unit='ms', utc=True).dt.strftime('%Y-%m-%d')
    diff.to_csv(out / 'n_diff_exact.csv', index=False)
    raw8.to_csv(out / 'candidates_raw8.csv.gz', index=False)
    raw12.to_csv(out / 'candidates_raw12.csv.gz', index=False)
    raw24.to_csv(out / 'candidates_raw24.csv.gz', index=False)
    result = {'files': len(files), 'symbols': len(seen), 'artifact_raw24_exact_match': True,
              'legacy_d0_edges_24': q24.tolist(), 'recent_d0_edges_12': q12.tolist(),
              'periods': {}, 'diff_groups': diff.groupby(['period', '_merge', 'reason'], observed=True).size().reset_index(name='n').astype({'_merge': str}).to_dict('records'),
              'diff_symbols': diff.groupby(['period', 'symbol'], observed=True).size().reset_index(name='n').to_dict('records'),
              'diff_dates': diff.groupby(['period', 'date'], observed=True).size().reset_index(name='n').to_dict('records')}
    for p, mask in [('TRAIN', lambda f: f.ts < CUT), ('HOLDOUT', lambda f: f.ts >= CUT)]:
        result['periods'][p] = {'raw24_base': metrics(raw24.loc[mask(raw24), 'ret8']),
                               'raw24_filtered': metrics(f24.loc[mask(f24), 'ret8']),
                               'raw12_filtered': metrics(f12.loc[mask(f12), 'ret8'])}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    (out / 'reconciliation.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ('diff_symbols', 'diff_dates')}, indent=2), flush=True)


if __name__ == '__main__':
    main()
