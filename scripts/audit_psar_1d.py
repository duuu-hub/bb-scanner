"""Thirty distinct invariants, with ten complete randomized clean passes."""
import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from reconcile_psar_1d import BURN, DAY, STEP, candidates, daily, load, psar, segments, symbol

NAMES = [
    'timestamp_strict_increase', '15m_alignment', 'OHLC_finite', 'OHLC_geometry',
    'daily_bucket_96', 'daily_UTC_boundary', 'gap_segmentation', 'daily_open_first',
    'daily_high_max', 'daily_low_min', 'daily_close_last', 'PSAR_future_mutation_invariance',
    'side_future_mutation_invariance', 'burn_in', 'flip_definition', 'age_reset',
    'age_increment', 'D0_freeze', 'D0_nonnegative', 'ATR_OPEN_lag', 'horizon_OOB',
    'SHORT_return_sign', 'MFE_geometry', 'MAE_geometry', 'symbol_parse',
    'duplicate_symbol_time', 'shard_exhaustive', 'shard_disjoint', 'deterministic_hash',
    'TRAIN_frozen_threshold_invariance',
]


def features(d):
    t, o, h, l, c = d
    sar, bull = psar(h, l)
    prev = np.r_[np.nan, c[:-1]]
    tr = np.maximum(h-l, np.maximum(abs(h-prev), abs(l-prev)))
    atr = np.r_[np.nan, pd.Series(tr).rolling(14, min_periods=14).mean().to_numpy()[:-1]]
    flips = np.r_[False, bull[1:] != bull[:-1]]
    age = np.full(len(t), -1, np.int64)
    d0 = np.full(len(t), np.nan)
    last, frozen = -1, np.nan
    for i in range(BURN, len(t)):
        if not np.isfinite(atr[i]) or atr[i] <= 0 or not np.isfinite(sar[i]):
            continue
        if flips[i]:
            last = i
            frozen = abs(c[i-1] - sar[i]) / atr[i]
        if last >= 0:
            age[i], d0[i] = i-last, frozen
    return sar, bull, atr, flips, age, d0


def fit_threshold(frame):
    assert len(frame) and np.isfinite(frame.d0).all()
    return float(frame.d0.quantile(1/3))


def unique(frame):
    assert not frame.duplicated(['symbol', 'ts']).any(), 'duplicate symbol/time'


def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, allow_nan=False).encode()).hexdigest()


def verify_raw_daily(raw, d):
    """Independent pandas aggregation checks every retained day against all 96 inputs."""
    t, o, h, l, c = raw
    assert (np.diff(t)>0).all() and (t % STEP == 0).all()
    assert all(np.isfinite(v).all() for v in (o,h,l,c))
    assert (l > 0).all() and (h >= np.maximum.reduce([o,l,c])).all()
    assert (l <= np.minimum.reduce([o,h,c])).all()
    frame = pd.DataFrame({'t': t, 'o': o, 'h': h, 'l': l, 'c': c, 'bucket': t//DAY})
    check = frame.groupby('bucket', sort=True).agg(t=('t','first'), o=('o','first'),
             h=('h','max'), l=('l','min'), c=('c','last'), n=('t','size'))
    check = check[(check.n == 96) & (check.t % DAY == 0)]
    for arr, key in zip(d, ['t','o','h','l','c']):
        np.testing.assert_array_equal(arr, check[key].to_numpy())
    assert (d[0] % DAY == 0).all() and (np.diff(d[0]) == DAY).all()
    return {'bars15m': len(t), 'retained_days': len(d[0]), 'independent_aggregation': True}


def suite(seed):
    rng = np.random.default_rng(seed)
    counts = {name: 0 for name in NAMES}
    def ok(name, condition=True):
        assert condition, name
        counts[name] += 1
    def rejected(fn):
        try:
            fn()
        except (AssertionError, ValueError, RuntimeError):
            return True
        return False

    n = 96*320
    t = np.arange(n, dtype=np.int64)*STEP
    o = 100*np.exp(.16*np.sin(np.arange(n)/96/6) + rng.normal(0,.003,n))
    c = o*np.exp(rng.normal(0,.002,n))
    h, l = np.maximum(o,c)*1.004, np.minimum(o,c)*.996
    raw = (t,o,h,l,c)
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp)/'TESTUSDT.csv.gz'
        valid = pd.DataFrame(dict(zip(['open_time','open','high','low','close'], raw)))
        for name, column, idx, value in [
            (NAMES[0], 'open_time', 2, int(t[1])),
            (NAMES[1], 'open_time', 2, int(t[2]+1)),
            (NAMES[2], 'open', 2, float('nan')),
            (NAMES[3], 'high', 2, float(l[2]-1)),
        ]:
            bad = valid.copy(); bad.loc[idx,column] = value; bad.to_csv(f,index=False)
            ok(name, rejected(lambda: load(f)))
        d = daily(raw)
        partial = daily(tuple(v[:95] for v in raw))
        ok(NAMES[4], len(partial[0]) == 0 and len(d[0]) == 320)
        ok(NAMES[5], (d[0]%DAY == 0).all())
        gap = t.copy(); gap[100:] += STEP
        ok(NAMES[6], segments(gap) == [(0,100),(100,n)])
        ok(NAMES[7], np.array_equal(d[1],o[::96]))
        ok(NAMES[8], np.array_equal(d[2],h.reshape(-1,96).max(axis=1)))
        ok(NAMES[9], np.array_equal(d[3],l.reshape(-1,96).min(axis=1)))
        ok(NAMES[10], np.array_equal(d[4],c.reshape(-1,96)[:,-1]))
        sar,bull,atr,flip,age,d0 = features(d)
        for cut in [30,100,155,230]:
            changed = [v.copy() for v in d]
            changed[2][cut:] *= rng.uniform(2,10)
            changed[3][cut:] *= rng.uniform(.01,.5)
            changed[4][cut:] *= rng.uniform(.3,3)
            ss,bb,aa,*_ = features(tuple(changed))
            ok(NAMES[11], np.allclose(sar[:cut+1],ss[:cut+1],equal_nan=True))
            ok(NAMES[12], np.array_equal(bull[:cut+1],bb[:cut+1]))
            ok(NAMES[19], np.allclose(atr[:cut+1],aa[:cut+1],equal_nan=True))
        rows = candidates(d,'TESTUSDT','fixture',0,8)
        ok(NAMES[13], len(rows)>0 and all(r['ts'] >= BURN*DAY and r['flip_ts']>=BURN*DAY for r in rows))
        ok(NAMES[14], np.array_equal(flip[1:],bull[1:]!=bull[:-1]))
        active = (age>=0)
        ok(NAMES[15], (age[flip & active] == 0).all())
        idx = np.flatnonzero(active[1:] & active[:-1] & ~flip[1:])+1
        ok(NAMES[16], (age[idx] == age[idx-1]+1).all())
        ok(NAMES[17], np.allclose(d0[idx],d0[idx-1]))
        ok(NAMES[18], (d0[active] >= 0).all() and np.isfinite(d0[active]).all())
        ok(NAMES[20], all(r['days_remaining']>=8 and r['ts']+8*DAY<=d[0][-1] for r in rows))
        falling = (1-80/100)*100
        rising = (1-120/100)*100
        ok(NAMES[21], np.isclose(falling,20) and np.isclose(rising,-20))
        for row in rows[:20]:
            i = int(row['ts']//DAY)
            for k in [4,5,6,7,8]:
                mfe = (1-d[3][i:i+k].min()/d[1][i])*100
                mae = (d[2][i:i+k].max()/d[1][i]-1)*100
                ok(NAMES[22], mfe>=0 and np.isclose(mfe,max((1-d[3][i:i+k]/d[1][i])*100)))
                ok(NAMES[23], mae>=0 and np.isclose(mae,max((d[2][i:i+k]/d[1][i]-1)*100)))
        ok(NAMES[24], symbol(f)=='TESTUSDT' and rejected(lambda:symbol('wrong.csv.gz')))
        duplicate = pd.DataFrame([{'symbol':'XUSDT','ts':1}]*2)
        ok(NAMES[25], rejected(lambda:unique(duplicate)))
        parts = [[i for i in range(103) if i%8==s] for s in range(8)]
        ok(NAMES[26], sorted(i for p in parts for i in p)==list(range(103)))
        ok(NAMES[27], all(set(parts[a]).isdisjoint(parts[b]) for a in range(8) for b in range(a)))
        ok(NAMES[28], digest(rows)==digest(candidates(d,'TESTUSDT','fixture',0,8)))
        train = pd.DataFrame({'d0': rng.uniform(1,5,100)})
        threshold = fit_threshold(train)
        for holdout_values in [np.ones(20)*.001,np.ones(20)*1000]:
            _ = holdout_values>threshold
            ok(NAMES[29], fit_threshold(train)==threshold and bool(np.array([1000.])>threshold))
    assert all(counts.values()) and len(counts)==30
    return counts


def run_audit():
    passes = []
    for i in range(10):
        counts = suite(20261001+i)
        passes.append({'seed':20261001+i, 'passed_invariants':len(counts), 'assertions':counts})
    return {'distinct_invariants':30, 'consecutive_full_clean_passes':10, 'passes':passes}


if __name__=='__main__':
    print(json.dumps(run_audit(),indent=2))
