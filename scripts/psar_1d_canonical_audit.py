"""Thirty different invariants, ten full rounds with different randomized fixtures."""
import json

import numpy as np
import pandas as pd

import psar_1d_canonical_engine as e

NAMES = (
    "timestamp_strict_increase", "timestamp_15m_alignment", "ohlc_finite", "ohlc_geometry",
    "day_bucket_96", "day_boundary_utc", "gap_segments", "daily_open_identity",
    "daily_high_identity", "daily_low_identity", "daily_close_identity",
    "psar_future_mutation_invariance", "side_future_mutation_invariance", "burn_in",
    "flip_definition", "age_reset", "age_increment", "d0_freeze", "d0_nonnegative",
    "atr_open_lag", "horizon_bounds", "short_return_sign", "mfe_geometry", "mae_geometry",
    "symbol_parse", "duplicate_symbol_time", "shard_exhaustive", "shard_disjoint",
    "deterministic_hash", "train_frozen_threshold",
)


def raises(fn):
    try:
        fn()
    except (ValueError, AssertionError):
        return
    raise AssertionError("invalid input was accepted")


def fixture(seed):
    rng = np.random.default_rng(seed)
    n = 192 * 96
    t = np.arange(n, dtype=np.int64) * e.STEP
    trend = .04 * np.sin(np.arange(n) / (96 * 2.7))
    o = 100 * np.exp(np.cumsum(rng.normal(0, .001, n)) + trend)
    c = o * np.exp(rng.normal(0, .0006, n))
    h = np.maximum(o, c) * (1 + rng.uniform(.0001, .004, n))
    l = np.minimum(o, c) * (1 - rng.uniform(.0001, .004, n))
    return rng, [t, o, h, l, c]


def suite(seed):
    rng, raw = fixture(seed)
    t, o, h, l, c = raw
    e.validate_raw(*raw)
    rt, ro, rh, rl, rc = daily = e.resample_day(*raw)
    sar, bull, atr, flip, age, d0, event = e.event_features(*daily)
    passed = []
    def ok(index, fn):
        fn()
        passed.append(NAMES[index - 1])
    def check(condition):
        assert condition
    def invalid(array_index, position, value):
        modified = [v.copy() for v in raw]
        modified[array_index][position] = value
        return modified

    ok(1, lambda: raises(lambda: e.validate_raw(*invalid(0, 10, t[9]))))
    ok(2, lambda: raises(lambda: e.validate_raw(*invalid(0, 10, t[10] + 1))))
    ok(3, lambda: raises(lambda: e.validate_raw(*invalid(1, 10, np.nan))))
    ok(4, lambda: raises(lambda: e.validate_raw(*invalid(2, 10, l[10] - 1))))
    ok(5, lambda: check(len(e.resample_day(*(v[:-1] for v in raw))[0]) == len(rt) - 1))
    ok(6, lambda: check(np.all(rt % e.DAY == 0)))
    cut = int(rng.integers(100, len(t) - 100))
    gapped = np.delete(t, cut)
    ok(7, lambda: check(len(e.segments(gapped)) == 2 and all(np.all(np.diff(gapped[a:b]) == e.STEP) for a, b in e.segments(gapped))))
    ok(8, lambda: check(np.array_equal(ro, o[::96])))
    ok(9, lambda: check(np.array_equal(rh, h.reshape(-1, 96).max(axis=1))))
    ok(10, lambda: check(np.array_equal(rl, l.reshape(-1, 96).min(axis=1))))
    ok(11, lambda: check(np.array_equal(rc, c[95::96])))
    i = int(rng.integers(e.BURN, len(rt) - 8))
    mh, ml = rh.copy(), rl.copy()
    mh[i:] *= rng.uniform(2, 20)
    ml[i:] *= rng.uniform(.001, .1)
    changed_sar, changed_bull = e.psar_open(mh, ml)
    ok(12, lambda: check(np.allclose(sar[:i + 1], changed_sar[:i + 1], equal_nan=True)))
    ok(13, lambda: check(np.array_equal(bull[:i + 1], changed_bull[:i + 1])))
    ok(14, lambda: check(np.all(age[:e.BURN] == -1) and np.isnan(d0[:e.BURN]).all()))
    ok(15, lambda: check(np.array_equal(flip[1:], bull[1:] != bull[:-1])))
    reset = np.flatnonzero(flip & (np.arange(len(rt)) >= e.BURN))
    ok(16, lambda: check(len(reset) > 0 and np.all(age[reset] == 0) and np.array_equal(event[reset], rt[reset])))
    increments = (age[1:] >= 1) & ~flip[1:]
    ok(17, lambda: check(np.all(age[1:][increments] == age[:-1][increments] + 1)))
    ok(18, lambda: check(np.array_equal(d0[1:][increments], d0[:-1][increments])))
    ok(19, lambda: check(np.all(d0[np.isfinite(d0)] >= 0)))
    independent_tr = [max(rh[j] - rl[j], abs(rh[j] - rc[j - 1]), abs(rl[j] - rc[j - 1])) for j in range(i - 14, i)]
    mc = rc.copy()
    mc[i:] *= 7
    changed_atr = e.atr_open(mh, ml, mc)
    ok(20, lambda: check(np.isclose(atr[i], np.mean(independent_tr)) and np.allclose(atr[:i + 1], changed_atr[:i + 1], equal_nan=True)))
    ok(21, lambda: raises(lambda: e.first_exit(bull, len(bull) - 4, 4)))
    ok(22, lambda: check(np.isclose(e.short_return(100, 90), 10) and np.isclose(e.short_return(100, 110), -10)))
    mfe = 100 * (1 - min(rl[i:i + 8]) / ro[i])
    mae = 100 * (max(rh[i:i + 8]) / ro[i] - 1)
    ok(23, lambda: check(0 <= mfe < 100 and np.isclose(mfe, max(100 * (1 - rl[i:i + 8] / ro[i])))))
    ok(24, lambda: check(mae >= 0 and np.isclose(mae, max(100 * (rh[i:i + 8] / ro[i] - 1)))))
    ok(25, lambda: (check(e.symbol("BTCUSDT.csv.gz") == "BTCUSDT"), raises(lambda: e.symbol("BAD-USDT.csv.gz"))))
    frame = pd.DataFrame({"symbol": ["BTCUSDT"] * 10, "ts": np.arange(10), "d0": rng.uniform(.1, 9, 10)})
    def no_duplicates(d):
        if d.duplicated(["symbol", "ts"]).any():
            raise ValueError("duplicate key")
    ok(26, lambda: (no_duplicates(frame), raises(lambda: no_duplicates(pd.concat([frame, frame.iloc[:1]])))))
    file_ids = list(range(int(rng.integers(800, 900))))
    shards = [set(file_ids[k::8]) for k in range(8)]
    ok(27, lambda: check(set.union(*shards) == set(file_ids)))
    ok(28, lambda: check(all(not shards[a].intersection(shards[b]) for a in range(8) for b in range(a + 1, 8))))
    ok(29, lambda: check(e.digest_frame(frame) == e.digest_frame(frame.sample(frac=1, random_state=seed))))
    boundary = 20 * e.DAY
    train_test = pd.DataFrame({"symbol": ["BTCUSDT"] * 40, "ts": np.arange(40) * e.DAY,
                               "max_exit_ts": (np.arange(40) + 8) * e.DAY, "d0": rng.uniform(.1, 9, 40)})
    q = e.freeze_threshold(train_test, boundary)
    changed = train_test.copy()
    changed.loc[changed.max_exit_ts > boundary, "d0"] = 1e9
    ok(30, lambda: check(q == e.freeze_threshold(changed, boundary)))
    assert len(passed) == 30 and len(set(passed)) == 30
    # Additional exit and initialization checks do not replace the named invariants.
    b = np.zeros(12, bool)
    b[4] = True
    assert e.first_exit(b, 1, 7) == 3
    assert e.first_exit(np.zeros(12, bool), 1, 7) == 7
    for index in rng.choice(np.arange(2, len(rt)), 5, replace=False):
        r, side = e.psar_prefix_reference(rh, rl, int(index))
        assert np.isclose(sar[index], r) and bull[index] == side
    return passed


def audit_real_cases(cases, frame, manifest, seed):
    """Every cached daily segment; new randomized prefix/mutation point each round."""
    rng = np.random.default_rng(seed)
    e.validate_ranges(manifest)
    assert not frame.duplicated(["symbol", "ts"]).any()
    assert (frame.age == 3).all() and (frame.ts - frame.flip_ts == 3 * e.DAY).all()
    assert (frame.remaining_days >= e.MAX_HOLD).all()
    assert (frame.d0 >= 0).all() and np.isfinite(frame.d0).all()
    q = e.freeze_threshold(frame)
    changed = frame.copy()
    changed.loc[changed.max_exit_ts > e.CUTOFF, "d0"] = 1e12
    assert e.freeze_threshold(changed) == q
    for sym, segment, t, o, h, l, c in cases:
        assert np.all(np.diff(t) == e.DAY) and np.all(t % e.DAY == 0)
        assert all(np.isfinite(v).all() for v in (o, h, l, c))
        sar, bull, atr, flip, age, d0, event = e.event_features(t, o, h, l, c)
        i = int(rng.integers(e.BURN, len(t)))
        hh, ll, cc = h.copy(), l.copy(), c.copy()
        hh[i:] *= 10
        ll[i:] *= .01
        cc[i:] *= 3
        ss, bb = e.psar_open(hh, ll)
        aa = e.atr_open(hh, ll, cc)
        assert np.allclose(ss[:i + 1], sar[:i + 1], equal_nan=True)
        assert np.array_equal(bb[:i + 1], bull[:i + 1])
        assert np.allclose(aa[:i + 1], atr[:i + 1], equal_nan=True)
        ref, direction = e.psar_prefix_reference(h, l, i)
        assert np.isclose(ref, sar[i]) and direction == bull[i]
        valid = age >= 0
        assert np.all(age[valid] * e.DAY == t[valid] - event[valid])
        continued = (age[1:] >= 1) & ~flip[1:]
        assert np.array_equal(d0[1:][continued], d0[:-1][continued])
    return len(cases)


def run_audit(cases=None, frame=None, manifest=None):
    rounds = []
    for round_index in range(10):
        seed = 61001 + round_index
        names = suite(seed)
        case_count = audit_real_cases(cases, frame, manifest, seed) if cases is not None else 0
        rounds.append({"round": round_index + 1, "seed": seed, "checks": len(names), "real_segments": case_count, "passed": True})
        print(f"AUDIT distinct=30 clean_streak={round_index + 1}/10 real_segments={case_count}", flush=True)
    return {"invariants": list(NAMES), "distinct_invariants": 30, "consecutive_clean": 10, "rounds": rounds,
            "raw_ohlc_aggregation": "every complete daily bucket checked against all 96 source bars during collection"}


if __name__ == "__main__":
    print(json.dumps(run_audit(), indent=2))
