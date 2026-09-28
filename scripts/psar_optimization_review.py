import ast
import json
import re
import time
from pathlib import Path

import numpy as np

FILES = [
    Path("scripts/psar_open_canonical_compare.py"),
    Path("scripts/psar_4h_canonical_compare.py"),
]
ROUNDS = 30
CASES_PER_ROUND = 1200


def load_first_exit(path):
    src = path.read_text()
    tree = ast.parse(src)
    keep = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            keep.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_first_exit":
            # Avoid numba disk-cache issues in dynamically compiled validation code.
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call):
                    for kw in dec.keywords:
                        if kw.arg == "cache":
                            kw.value = ast.Constant(False)
            keep.append(node)
    ns = {}
    module=ast.fix_missing_locations(ast.Module(body=keep, type_ignores=[]))
    exec(compile(module, str(path), "exec"), ns)
    return src, ns["_first_exit"]


def legacy_first_event(h, l, start, tp, sl, long):
    th = (h[start:] >= tp) if long else (l[start:] <= tp)
    sh = (l[start:] <= sl) if long else (h[start:] >= sl)
    ti = np.flatnonzero(th)
    si = np.flatnonzero(sh)
    it = int(ti[0]) if ti.size else 10**9
    is_ = int(si[0]) if si.size else 10**9
    if it == 10**9 and is_ == 10**9:
        return -1, False, False
    off = min(it, is_)
    return off, bool(th[off]), bool(sh[off])


def legacy_after_entry(h, l, fs, tp, sl, long, is_taker):
    # Old engine semantics: taker may exit on fs; maker fill-bar exits are
    # handled by authoritative 1m first, and if it continues, 15m scanning resumes fs+1.
    start = fs if is_taker else fs + 1
    return legacy_first_event(h, l, start, tp, sl, long)


def static_checks(src):
    checks = {
        "first_exit_present": "def _first_exit(" in src,
        "legacy_helper_absent": "_first_hits(" not in src,
        "suffix_ph_absent": "ph=h[fs:end]" not in src,
        "suffix_th_absent": "th=(ph>=tp)" not in src,
        "suffix_copy_absent": "th=th.copy()" not in src and "sh=sh.copy()" not in src,
        "maker_next_bar": src.count("scan_start=fs+1") == 2,
        "maker_fill_tp_direct": src.count("fill_tp=(h[fs]>=tp) if b else (l[fs]<=tp)") == 2,
        "maker_fill_sl_direct": src.count("fill_sl=(l[fs]<=sl) if b else (h[fs]>=sl)") == 2,
        "maker_1m_resolution": src.count("_resolve_1m(symbol,int(t[fs]),tp,sl,b,fill") == 2,
        "established_collision_1m": src.count("_resolve_1m(symbol,int(t[exit_i]),tp,sl,b,None") == 2,
        "data_gap_accounted": '"data_gap"]+q["exit_mismatch"]+q["unresolved_eod"]' in src,
        "same_open_timing": 'order_live":"same strategy-TF bar open"' in src,
        "no_horizon_cut": '"horizon_bars":None' in src,
        "no_period_split": "entry_year" not in src and "year_shard" not in src,
        "entry_mismatch_explicit": 'return "continue" if entry_seen else "entry_mismatch"' in src,
        "exit_mismatch_explicit": 'return "exit_mismatch"' in src,
        "entry_mismatch_excluded_from_fill": src.count('q["fills"]-=1; q["maker"]-=1; q["entry_mismatch"]+=1') == 2,
        "exit_mismatch_excluded_from_metrics": src.count('q["exit_mismatch"]+=1') == 2,
        "no_integrity_hard_fail": 'if rr=="data_error"' not in src,
    }
    bad = [k for k, v in checks.items() if not v]
    if bad:
        raise AssertionError(("static_checks", bad))
    return list(checks)


def deterministic_checks(fast):
    cases = []
    h = np.array([100., 101., 106., 103.])
    l = np.array([99., 98., 100., 94.])
    cases.append(("tp_first", fast(h, l, 0, 105., 95., True), (2, True, False)))
    cases.append(("sl_first", fast(h, l, 0, 110., 98.5, True), (1, False, True)))
    h2 = np.array([100., 106., 103.])
    l2 = np.array([99., 94., 100.])
    cases.append(("same_bar_both", fast(h2, l2, 0, 105., 95., True), (1, True, True)))
    cases.append(("no_hit", fast(h2, l2, 0, 120., 80., True), (-1, False, False)))
    cases.append(("start_last", fast(h2, l2, 2, 102., 99., True), (0, True, False)))
    hs = np.array([101., 100., 94., 96.])
    ls = np.array([100., 99., 90., 92.])
    cases.append(("short_tp", fast(hs, ls, 0, 95., 105., False), (2, True, False)))
    for name, got, expected in cases:
        got = (int(got[0]), bool(got[1]), bool(got[2]))
        if got != expected:
            raise AssertionError((name, got, expected))
    return [x[0] for x in cases]


def randomized_round(fast, seed, cases):
    rng = np.random.default_rng(seed)
    for case in range(cases):
        n = int(rng.integers(1, 900))
        mid = np.cumsum(rng.normal(0, 1, n)) + 100
        spread = rng.uniform(.001, 4, n)
        h = mid + spread
        l = mid - spread
        start = int(rng.integers(0, n))
        long = bool(rng.integers(0, 2))
        base = float(mid[start])
        tp = base + rng.uniform(.001, 12) if long else base - rng.uniform(.001, 12)
        sl = base - rng.uniform(.001, 12) if long else base + rng.uniform(.001, 12)

        expected = legacy_first_event(h, l, start, tp, sl, long)
        got = fast(h, l, start, tp, sl, long)
        got = (int(got[0]), bool(got[1]), bool(got[2]))
        if got != expected:
            raise AssertionError(("first_event", seed, case, expected, got, start, tp, sl, long))

        fs = int(rng.integers(0, n))
        is_taker = bool(rng.integers(0, 2))
        if (not is_taker) and fs == n - 1:
            expected2 = (-1, False, False)
            got2 = fast(h, l, n, tp, sl, long)
        else:
            expected2 = legacy_after_entry(h, l, fs, tp, sl, long, is_taker)
            scan_start = fs if is_taker else fs + 1
            got2 = fast(h, l, scan_start, tp, sl, long)
        got2 = (int(got2[0]), bool(got2[1]), bool(got2[2]))
        if got2 != expected2:
            raise AssertionError(("entry_semantics", seed, case, expected2, got2, fs, is_taker))
    return cases * 2


def benchmark(fast):
    n = 180000
    rng = np.random.default_rng(7)
    mid = np.cumsum(rng.normal(0, .2, n)) + 100
    sp = rng.uniform(.01, 1, n)
    h = mid + sp
    l = mid - sp
    fast(h, l, 0, 105., 95., True)  # compile
    qs = []
    for i in range(6000):
        s = int(rng.integers(0, n - 1))
        base = float(mid[s])
        long = bool(i % 2)
        tp = base + rng.uniform(.01, 8) if long else base - rng.uniform(.01, 8)
        sl = base - rng.uniform(.01, 8) if long else base + rng.uniform(.01, 8)
        qs.append((s, tp, sl, long))

    t0 = time.perf_counter()
    for s, tp, sl, long in qs:
        fast(h, l, s, tp, sl, long)
    new_sec = time.perf_counter() - t0

    t0 = time.perf_counter()
    for s, tp, sl, long in qs:
        legacy_first_event(h, l, s, tp, sl, long)
    legacy_sec = time.perf_counter() - t0
    speedup = legacy_sec / max(new_sec, 1e-12)
    if speedup <= 1.0:
        raise AssertionError(("performance_regression", new_sec, legacy_sec, speedup))
    return {
        "calls": len(qs),
        "new_sec": round(new_sec, 6),
        "legacy_sec": round(legacy_sec, 6),
        "speedup_x": round(speedup, 3),
    }


def main():
    loaded = [load_first_exit(p) for p in FILES]
    src1, fast1 = loaded[0]
    src4, fast4 = loaded[1]

    # Research parameter grids may intentionally differ by timeframe (e.g. 4H R20 boundary extension).
    # Compare executable engine source after normalizing only the RS assignment.
    norm=lambda s: re.sub(r"^RS=.*$", "RS=<PARAM_GRID>", s, flags=re.MULTILINE)
    if norm(src1) != norm(src4):
        raise AssertionError("1H/4H canonical engine files diverged outside RS parameter grid")

    static = static_checks(src1)
    deterministic = deterministic_checks(fast1) + deterministic_checks(fast4)

    reviews = []
    consecutive_clean = 0
    for i in range(1, ROUNDS + 1):
        seed = 2026092800 + i
        try:
            pairs1 = randomized_round(fast1, seed, CASES_PER_ROUND)
            pairs4 = randomized_round(fast4, seed + 100000, CASES_PER_ROUND)
            reviews.append({
                "review": i,
                "status": "PASS",
                "seed_1h": seed,
                "seed_4h": seed + 100000,
                "randomized_comparisons": pairs1 + pairs4,
                "checks": [
                    "legacy first-event parity",
                    "maker/taker scan-start parity",
                    "collision flag parity",
                    "no-hit/EOD parity",
                    "1H/4H helper parity",
                ],
            })
            consecutive_clean += 1
        except Exception as exc:
            consecutive_clean = 0
            reviews.append({
                "review": i,
                "status": "FAIL",
                "error": repr(exc),
            })
            raise

    if len(reviews) < 30:
        raise AssertionError(("minimum_reviews_not_met", len(reviews)))
    if consecutive_clean < 10:
        raise AssertionError(("final_consecutive_clean_not_met", consecutive_clean))

    bench1 = benchmark(fast1)
    bench4 = benchmark(fast4)
    report = {
        "total_reviews": len(reviews),
        "final_consecutive_clean": consecutive_clean,
        "minimum_reviews_required": 30,
        "final_consecutive_clean_required": 10,
        "static_checks": static,
        "deterministic_checks": deterministic,
        "benchmark_1h": bench1,
        "benchmark_4h": bench4,
        "reviews": reviews,
    }
    Path("psar_optimization_review.json").write_text(json.dumps(report, indent=2))
    print("OPT_REVIEW_PASS", f"reviews={len(reviews)}", f"final_consecutive_clean={consecutive_clean}")
    print("BENCH_1H", json.dumps(bench1, sort_keys=True))
    print("BENCH_4H", json.dumps(bench4, sort_keys=True))


if __name__ == "__main__":
    main()
