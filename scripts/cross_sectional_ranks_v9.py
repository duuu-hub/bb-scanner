"""Point-in-time prior-day ranks for V9. No exit outcomes or orders here."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd

from scripts import day_edge_lab as base
from scripts.premium_absorption_v8 import features
from scripts.shock_confirmation_v3 import load

BAR, DAY = base.BAR, base.DAY
MIN_UNIVERSE = 30
MAP_COLUMNS = ["symbol", "rank_time", "last_bar_open", "reference_bar_open",
               "relative_return24", "coin_return24", "btc_return24", "turnover24",
               "observed_source_days"]
RANK_COLUMNS = MAP_COLUMNS + ["universe_n", "rank_from_low", "rank_from_high", "rank_fraction"]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def interval(stage):
    if stage == "DEV":
        return base.START, base.DEV_END
    if stage == "GATE":
        return base.DEV_END, base.GATE_END
    raise ValueError("unknown rank stage")


def observed_days(t):
    """Cumulative distinct observed UTC source dates, never future listings."""
    if not len(t):
        return np.array([], dtype=np.int64)
    return np.cumsum(np.r_[True, t[1:] // DAY != t[:-1] // DAY])


def snapshots(symbol, raw, f, btc, start, end):
    """At midnight, use only the 97 closes bounding the preceding 24h."""
    t = raw[0]
    decision = t + BAR
    days = observed_days(t)
    candidate = ((decision % DAY == 0) & (decision >= start - DAY)
                 & (decision < end) & f["eligible"] & (days >= 30))
    # r96 is defined only within exact contiguous 15m segments. Exact BTC
    # alignment likewise never interpolates a missing bar or missing return.
    known = np.isfinite(f["r96"]) & np.isfinite(btc["r96"])
    rows = []
    for i in np.flatnonzero(candidate & known):
        rows.append(dict(symbol=symbol, rank_time=int(decision[i]),
                         last_bar_open=int(t[i]), reference_bar_open=int(t[i] - DAY),
                         relative_return24=float(np.log1p(f["r96"][i]) - np.log1p(btc["r96"][i])),
                         coin_return24=float(f["r96"][i]), btc_return24=float(btc["r96"][i]),
                         turnover24=float(f["q96"][i]), observed_source_days=int(days[i])))
    counts = dict(eligible_midnights=int(candidate.sum()),
                  exact24h_midnights=int((candidate & known).sum()),
                  missing_coin_or_btc_path=int((candidate & ~known).sum()))
    return rows, counts


def map_shard(data, btc_path, out, stage, source_check_path):
    paths = sorted(data.rglob("*.csv.gz"))
    if not paths:
        raise ValueError("empty frozen source shard")
    source_check = json.loads(source_check_path.read_text())
    if source_check["status"] != "VERIFIED" or len(source_check["shards"]) != 1:
        raise ValueError("rank map requires verified original source shard")
    verified = {x["symbol"]: x["sha256"] for x in source_check["files"]}
    start, end = interval(stage)
    br, bq, bb = load(btc_path, end)
    if not len(br[0]):
        raise ValueError("empty BTC context")
    bf = features(br, bq, bb)
    out.mkdir(parents=True, exist_ok=True)
    rows, coverage, seen = [], [], set()
    sources = {}

    def checkpoint(complete):
        frame = pd.DataFrame(rows, columns=MAP_COLUMNS).sort_values(["rank_time", "symbol"])
        frame.to_csv(out / "daily_map.csv.gz", index=False, compression=dict(method="gzip", mtime=0))
        meta = dict(complete=complete, stage=stage, shard=source_check["shards"][0],
                    source_data_run=36095439671, source_files=len(paths), market_hashes=sources,
                    baseline_sha256=source_check["baseline_sha256"],
                    source_check_sha256=digest(source_check_path), btc_sha256=digest(btc_path),
                    daily_map_sha256=digest(out / "daily_map.csv.gz"), map_rows=len(rows),
                    coverage=coverage, start=start, end=end)
        (out / "map_meta.json").write_text(json.dumps(meta, indent=2, allow_nan=False))

    try:
        for n, path in enumerate(paths, 1):
            symbol = path.name[:-7]
            if symbol in seen:
                raise ValueError("duplicate market input " + symbol)
            seen.add(symbol)
            sha = digest(path)
            if verified.get(symbol) != sha:
                raise ValueError("rank map source differs from verified source " + symbol)
            sources[symbol] = sha
            raw, q, buy = load(path, end)
            if not len(raw[0]) or raw[0][0] >= base.DEV_END - 30 * DAY:
                coverage.append(dict(symbol=symbol, status="NO_DEVELOPMENT_HISTORY", rows=0))
                continue
            f = features(raw, q, buy)
            aligned_btc = base.align_btc(raw[0], br[0], bf)
            mapped, counts = snapshots(symbol, raw, f, aligned_btc, start, end)
            rows.extend(mapped)
            coverage.append(dict(symbol=symbol, status="MAPPED", rows=len(mapped), **counts))
            if n % 8 == 0 or n == len(paths):
                checkpoint(False)
                print("V9_RANK_MAP", stage, n, "/", len(paths), "rows", len(rows), flush=True)
        if set(sources) != set(verified):
            raise ValueError("rank map omitted a verified source file")
    except BaseException:
        checkpoint(False)
        raise
    checkpoint(True)
    print("V9_RANK_MAP_DONE", stage, len(rows), "source_files", len(paths), flush=True)


def validate_map(frame):
    if not set(MAP_COLUMNS).issubset(frame.columns):
        raise ValueError("daily map schema mismatch")
    if frame.empty:
        return
    for key in ("rank_time", "last_bar_open", "reference_bar_open", "observed_source_days"):
        x = frame[key].to_numpy(float)
        if np.any(~np.isfinite(x)) or np.any(x != np.floor(x)):
            raise ValueError("noninteger daily map " + key)
    if (frame.symbol.isna().any() or not frame.symbol.astype(str).str.fullmatch(r"[A-Z0-9]+USDT").all()
            or frame.duplicated(["symbol", "rank_time"]).any()):
        raise ValueError("invalid or duplicate symbol/rank timestamp")
    if (np.any(frame.rank_time.to_numpy(np.int64) % DAY)
            or np.any(frame.last_bar_open + BAR != frame.rank_time)
            or np.any(frame.reference_bar_open + DAY != frame.last_bar_open)
            or np.any(frame.observed_source_days < 30)):
        raise ValueError("daily map chronology mismatch")
    for key in ("relative_return24", "coin_return24", "btc_return24", "turnover24"):
        if not np.isfinite(frame[key]).all():
            raise ValueError("nonfinite daily map " + key)
    if (np.any(frame.coin_return24 <= -1) or np.any(frame.btc_return24 <= -1)
            or np.any(frame.turnover24 < 20_000_000)
            or not np.allclose(frame.relative_return24,
                               np.log1p(frame.coin_return24) - np.log1p(frame.btc_return24),
                               rtol=1e-10, atol=1e-12)):
        raise ValueError("invalid daily map economics")


def rank_frame(frame):
    """Deterministic global tails; no zero-imputation for absent symbols."""
    validate_map(frame)
    ordered = frame.sort_values(["rank_time", "relative_return24", "symbol"], kind="stable").copy()
    sizes = ordered.groupby("rank_time").size()
    low_universe = [dict(rank_time=int(ts), universe_n=int(n)) for ts, n in sizes.items() if n < MIN_UNIVERSE]
    ordered["universe_n"] = ordered.rank_time.map(sizes).astype(np.int64)
    ordered = ordered[ordered.universe_n >= MIN_UNIVERSE].copy()
    ordered["rank_from_low"] = ordered.groupby("rank_time").cumcount() + 1
    ordered["rank_from_high"] = ordered.universe_n - ordered.rank_from_low + 1
    ordered["rank_fraction"] = (ordered.rank_from_low - 1) / (ordered.universe_n - 1)
    return ordered.reindex(columns=RANK_COLUMNS).reset_index(drop=True), low_universe


def reduce_maps(parts, out, stage, baseline_path, expected_shards=8):
    paths = sorted(parts.rglob("daily_map.csv.gz"))
    if len(paths) != expected_shards:
        raise ValueError("incomplete daily map shards")
    baseline = json.loads(baseline_path.read_text())["expected_csv_sha256"]
    sources, shards, btc_hashes, inputs, frames = {}, set(), set(), [], []
    for path in paths:
        meta_path = path.parent / "map_meta.json"
        m = json.loads(meta_path.read_text())
        if not m["complete"] or m["stage"] != stage or m["daily_map_sha256"] != digest(path):
            raise ValueError("incomplete or altered daily map")
        if m["baseline_sha256"] != digest(baseline_path) or m["source_data_run"] != 36095439671:
            raise ValueError("rank map baseline changed")
        if m["shard"] in shards:
            raise ValueError("duplicate rank map shard")
        shards.add(m["shard"])
        btc_hashes.add(m["btc_sha256"])
        for symbol, sha in m["market_hashes"].items():
            if symbol in sources:
                raise ValueError("duplicate source symbol across rank shards " + symbol)
            sources[symbol] = sha
        frame = pd.read_csv(path)
        if len(frame) != m["map_rows"] or not set(frame.symbol).issubset(m["market_hashes"]):
            raise ValueError("daily map count/source mismatch")
        start, end = interval(stage)
        if len(frame) and not ((frame.rank_time >= start - DAY) & (frame.rank_time < end)).all():
            raise ValueError("rank map stage leakage")
        validate_map(frame)
        frames.append(frame)
        inputs.append(dict(shard=m["shard"], map_file=str(path), map_sha256=digest(path),
                           meta_sha256=digest(meta_path), rows=len(frame),
                           source_check_sha256=m["source_check_sha256"]))
    if len(btc_hashes) != 1:
        raise ValueError("inconsistent BTC context across rank shards")
    if expected_shards == 8 and shards != set(range(8)):
        raise ValueError("rank reducer missing a numbered source shard")
    if any(sources.get(symbol) != sha for symbol, sha in baseline.items()):
        raise ValueError("rank reducer missing or altered a frozen historical symbol")
    nonempty = [f for f in frames if len(f)]
    frame = pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame(columns=MAP_COLUMNS)
    ranked, low_universe = rank_frame(frame)
    out.mkdir(parents=True, exist_ok=True)
    rank_path = out / "daily_ranks.csv.gz"
    ranked.to_csv(rank_path, index=False, compression=dict(method="gzip", mtime=0))
    manifest = dict(complete=True, stage=stage, source_data_run=36095439671,
                    baseline_sha256=digest(baseline_path), btc_sha256=next(iter(btc_hashes)),
                    market_hashes=sources, map_inputs=inputs, rank_data_sha256=digest(rank_path),
                    map_rows=len(frame), rank_rows=len(ranked), ranked_dates=int(ranked.rank_time.nunique()),
                    minimum_universe=MIN_UNIVERSE, low_universe_dates=low_universe,
                    rank_rule="ascending(relative_return24,symbol); tail k=ceil(n*tail_pct/100)",
                    availability="Only the exact current prior-midnight rank; one closed next-day bar required.")
    (out / "rank_manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False))
    print("V9_RANK_REDUCE_DONE", stage, json.dumps({k: manifest[k] for k in
          ("map_rows", "rank_rows", "ranked_dates", "minimum_universe", "rank_data_sha256")}),
          "excluded_low_universe_dates", len(low_universe), flush=True)
    return ranked, manifest


def load_ranks(root, stage):
    m = json.loads((root / "rank_manifest.json").read_text())
    rank_path = root / "daily_ranks.csv.gz"
    if (not m["complete"] or m["stage"] != stage or m["minimum_universe"] != MIN_UNIVERSE
            or m["rank_data_sha256"] != digest(rank_path)):
        raise ValueError("frozen rank artifact mismatch")
    frame = pd.read_csv(rank_path)
    if (len(frame) != m["rank_rows"] or frame.rank_time.nunique() != m["ranked_dates"]
            or not set(RANK_COLUMNS).issubset(frame.columns)):
        raise ValueError("frozen rank count/schema mismatch")
    validate_map(frame)
    expected, _ = rank_frame(frame[MAP_COLUMNS])
    if len(frame):
        actual = frame.sort_values(["rank_time", "rank_from_low"], kind="stable").reset_index(drop=True)
        if not actual.symbol.equals(expected.symbol):
            raise ValueError("frozen rank ordering mismatch")
        for key in ("universe_n", "rank_from_low", "rank_from_high", "rank_fraction"):
            if not np.allclose(actual[key], expected[key], rtol=1e-12, atol=1e-12):
                raise ValueError("frozen rank values mismatch " + key)
    return frame, m


def align_ranks(symbol, t, frame):
    """Exact daily key lookup, with no nearest-time or future backfill."""
    f = frame[frame.symbol == symbol].sort_values("rank_time")
    rt = ((t + BAR) // DAY) * DAY
    times = f.rank_time.to_numpy(np.int64)
    current = np.searchsorted(times, rt)
    previous = np.searchsorted(times, rt - DAY)

    def exact(index, wanted):
        valid = index < len(times)
        valid[valid] &= times[index[valid]] == wanted[valid]
        return valid

    now_ok = exact(current, rt) & (t >= rt)
    old_ok = exact(previous, rt - DAY) & (t >= rt)
    out = dict(rank_time=rt, rank_available=now_ok, previous_rank_available=old_ok)
    for key in ("universe_n", "rank_from_low", "rank_from_high", "rank_fraction", "relative_return24"):
        values = f[key].to_numpy(float)
        for prefix, index, valid in (("", current, now_ok), ("previous_", previous, old_ok)):
            value = np.full(len(t), np.nan)
            value[valid] = values[index[valid]]
            out[prefix + key] = value
    return out


def smoke(out):
    """Synthetic full map/reduce/availability path, no market-alpha claim."""
    # Use the actual frozen DEV boundary, loaders, file hashes and 8 map shards.
    # Synthetic source checks are explicit fixtures, never a Binance source claim.
    t = base.START + np.arange(35 * 96, dtype=np.int64) * BAR
    c = 100 * np.exp(np.arange(len(t)) * .00005)
    raw = (t, c.copy(), c + 1, c - 1, c.copy())
    q = np.full(len(t), 1_000_000.)
    bf = features(raw, q, q * .5)
    rows, checks, hashes = [], {i: [] for i in range(8)}, {}
    out.mkdir(parents=True, exist_ok=True)
    def save_market(path, raw, buy):
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(dict(open_time=raw[0], open=raw[1], high=raw[2], low=raw[3],
                          close=raw[4], quote_volume=q, taker_buy_quote=buy)).to_csv(
                              path, index=False, compression=dict(method='gzip', mtime=0))
    btc_path = out / 'btc' / 'BTCUSDT.csv.gz'
    save_market(btc_path, raw, q * .5)
    for n in range(32):
        coin = 100 * np.exp(np.arange(len(t)) * (.00004 + n * .000001))
        r = (t, coin.copy(), coin + 1, coin - 1, coin.copy())
        ff = features(r, q, q * .6)
        symbol = f"X{n:02d}USDT"
        mapped, _ = snapshots(symbol, r, ff, base.align_btc(t, t, bf), base.START, base.DEV_END)
        rows.extend(mapped)
        path = out / 'sources' / str(n % 8) / (symbol + '.csv.gz')
        save_market(path, r, q * .6)
        hashes[symbol] = digest(path)
        checks[n % 8].append(dict(symbol=symbol, sha256=hashes[symbol]))
    rank, excluded = rank_frame(pd.DataFrame(rows, columns=MAP_COLUMNS))
    if rank.empty or excluded:
        raise AssertionError("rank smoke missing global daily ranks")
    available = align_ranks("X31USDT", base.START + np.array([31 * DAY - BAR, 31 * DAY, 31 * DAY + BAR]), rank)
    assert not available["rank_available"][0]
    assert available["rank_available"][1:].all()
    assert (available["rank_from_high"][1:] == 1).all()
    pd.testing.assert_frame_equal(rank, rank_frame(pd.DataFrame(rows[::-1], columns=MAP_COLUMNS))[0])
    baseline = out / 'synthetic_baseline.json'
    baseline.write_text(json.dumps(dict(synthetic_only=True, expected_csv_sha256=hashes)))
    for i in range(8):
        check = out / 'sources' / str(i) / 'synthetic_source_check.json'
        check.write_text(json.dumps(dict(status='VERIFIED', synthetic_only=True, shards=[i],
                                         files=checks[i], baseline_sha256=digest(baseline))))
        map_shard(out / 'sources' / str(i), btc_path, out / 'maps' / str(i), 'DEV', check)
    reduced, manifest = reduce_maps(out / 'maps', out / 'ranks', 'DEV', baseline)
    loaded, _ = load_ranks(out / 'ranks', 'DEV')
    pd.testing.assert_frame_equal(reduced, loaded, check_dtype=False)
    # CSV round trips use finite precision; do not demand bit equality to RAM rows.
    assert len(reduced) == len(rank)
    from scripts import cross_sectional_leader_v9 as leader
    leader.scan(out / 'sources' / '0', btc_path, out / 'ranks', out / 'scan',
                out / 'unused_minute_cache', 'DEV',
                out / 'sources' / '0' / 'synthetic_source_check.json')
    scan_meta = json.loads((out / 'scan' / 'scan_meta.json').read_text())
    assert scan_meta['complete'] and scan_meta['minute_months'] == 0
    rank.to_csv(out / "synthetic_ranks.csv", index=False)
    report = dict(status="PASS", synthetic_only=True, map_rows=len(rows), rank_rows=len(rank),
                  symbols=32, maps=8, scan_complete=scan_meta['complete'],
                  availability_checks=3, reverse_input_order="PASS",
                  market_profitability_claim=False)
    (out / "smoke.json").write_text(json.dumps(report, indent=2))
    print("V9_RANK_MAP_REDUCE_SMOKE_PASS", json.dumps(report), flush=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("map")
    for name in ("data", "btc", "out", "source-check"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--stage", choices=("DEV", "GATE"), required=True)
    p = sub.add_parser("reduce")
    for name in ("parts", "out", "baseline"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--stage", choices=("DEV", "GATE"), required=True)
    p = sub.add_parser("smoke")
    p.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.command == "map":
        map_shard(a.data, a.btc, a.out, a.stage, a.source_check)
    elif a.command == "reduce":
        reduce_maps(a.parts, a.out, a.stage, a.baseline)
    else:
        smoke(a.out)


if __name__ == "__main__":
    main()
