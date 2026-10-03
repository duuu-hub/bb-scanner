"""Verified official monthly Binance USD-M mark-price 15m bars for V21."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from scripts.official_minute_provenance import _get, array_digest, digest

BAR = 900_000
DAY = 86_400_000
INPUTS: dict[str, dict] = {}


def validate_arrays(data, month):
    t, o, h, l, c = data
    start = int(pd.Timestamp(month + "-01", tz="UTC").timestamp() * 1000)
    end = int((pd.Timestamp(month + "-01", tz="UTC") + pd.offsets.MonthBegin(1)).timestamp() * 1000)
    if (
        not len(t)
        or len({len(x) for x in data}) != 1
        or t.dtype != np.dtype("int64")
        or np.any(t % BAR)
        or np.any(np.diff(t) <= 0)
        or t[0] < start
        or t[-1] >= end
        or any(np.any(~np.isfinite(x)) or np.any(x <= 0) for x in (o, h, l, c))
        or np.any(h < np.maximum.reduce([o, l, c]))
        or np.any(l > np.minimum.reduce([o, h, c]))
    ):
        raise ValueError("invalid mark-price timestamp or positive OHLC geometry")


def parse_zip(raw, month):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError("mark ZIP must contain exactly one CSV")
        frame = pd.read_csv(archive.open(members[0]), header=None, dtype=str)
    if frame.shape[1] != 12 or not len(frame):
        raise ValueError("invalid official mark column/row contract")
    if not re.fullmatch(r"\d+", str(frame.iat[0, 0]).strip()):
        header = [str(v).strip().lstrip("\ufeff").lower().replace(" ", "_") for v in frame.iloc[0, :5]]
        if header != ["open_time", "open", "high", "low", "close"]:
            raise ValueError("unknown official mark header " + repr(header))
        frame = frame.iloc[1:].reset_index(drop=True)
    if not len(frame) or not frame.iloc[:, 0].str.fullmatch(r"\d+").all():
        raise ValueError("empty or noninteger mark timestamp")
    t = frame.iloc[:, 0].to_numpy(dtype=np.int64)
    o, h, l, c = [pd.to_numeric(frame.iloc[:, i], errors="raise").to_numpy(float) for i in range(1, 5)]
    data = (t, o, h, l, c)
    validate_arrays(data, month)
    return data


def filenames(symbol, month, root):
    stem = Path(root) / f"{symbol}-markPrice-15m-{month}"
    return [Path(str(stem) + suffix) for suffix in (".zip", ".zip.CHECKSUM", ".npz", ".input.json")]


def _load_month(symbol, month, cache):
    if not re.fullmatch(r"[A-Z0-9]+USDT", symbol) or not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("invalid mark symbol/month")
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    raw_path, checksum_path, npz, sidecar = filenames(symbol, month, cache)
    key = symbol + "/" + month
    url = (
        "https://data.binance.vision/data/futures/um/monthly/markPriceKlines/"
        f"{symbol}/15m/{symbol}-15m-{month}.zip"
    )
    if sidecar.exists():
        meta = json.loads(sidecar.read_text())
        INPUTS[key] = meta
        if meta.get("status") == "VALIDATED_OFFICIAL_CHECKSUM":
            if (
                meta.get("source_url") != url
                or not meta.get("checksum_verified")
                or not all(path.exists() for path in (raw_path, checksum_path, npz))
                or digest(raw_path) != meta.get("original_zip_sha256")
                or meta.get("original_zip_sha256") != meta.get("official_checksum_sha256")
                or digest(checksum_path) != meta.get("official_checksum_text_sha256")
                or digest(npz) != meta.get("npz_sha256")
            ):
                raise ValueError("frozen verified mark cache mismatch " + key)
            if checksum_path.read_text().strip().split()[0].lower() != meta["original_zip_sha256"]:
                raise ValueError("frozen mark checksum token mismatch " + key)
            with np.load(npz, allow_pickle=False) as arrays:
                data = tuple(arrays[name].copy() for name in ("t", "o", "h", "l", "c"))
            if array_digest(data) != meta.get("array_sha256"):
                raise ValueError("frozen mark array mismatch " + key)
            validate_arrays(data, month)
            return data
    meta = {
        "symbol": symbol,
        "month": month,
        "source_url": url,
        "checksum_url": url + ".CHECKSUM",
        "status": "MARK_DATA_GAP",
        "bars": 0,
        "checksum_verified": False,
        "original_zip_sha256": None,
        "npz_sha256": None,
        "array_sha256": None,
    }
    INPUTS[key] = meta

    def save():
        sidecar.write_text(json.dumps(meta, indent=2))

    def gap(message):
        meta["message"] = message
        save()
        return None

    try:
        raw = _get(url)
        if raw is None:
            for path in (raw_path, checksum_path, npz):
                path.unlink(missing_ok=True)
            return gap("official mark archive 404")
        raw_path.write_bytes(raw)
        meta["original_zip_sha256"] = hashlib.sha256(raw).hexdigest()
        checksum = _get(url + ".CHECKSUM")
        if checksum is None:
            checksum_path.unlink(missing_ok=True)
            npz.unlink(missing_ok=True)
            return gap("official mark checksum 404")
        checksum_path.write_bytes(checksum)
        meta["official_checksum_text_sha256"] = hashlib.sha256(checksum).hexdigest()
        try:
            text = checksum.decode().strip()
            token = text.split()[0]
        except (UnicodeDecodeError, IndexError):
            return gap("malformed official mark checksum")
        meta["official_checksum_text"] = text
        if not re.fullmatch(r"[A-Fa-f0-9]{64}", token):
            return gap("malformed official mark checksum")
        meta["official_checksum_sha256"] = token.lower()
        if meta["original_zip_sha256"] != token.lower():
            return gap("official mark ZIP checksum mismatch")
        meta["checksum_verified"] = True
        try:
            data = parse_zip(raw, month)
        except (
            ValueError,
            KeyError,
            IndexError,
            OverflowError,
            zipfile.BadZipFile,
            pd.errors.ParserError,
            pd.errors.EmptyDataError,
        ) as error:
            return gap(type(error).__name__ + ": " + str(error))
        tmp = npz.with_suffix(".npz.tmp")
        with tmp.open("wb") as stream:
            np.savez_compressed(stream, t=data[0], o=data[1], h=data[2], l=data[3], c=data[4])
        tmp.replace(npz)
        meta.update(
            status="VALIDATED_OFFICIAL_CHECKSUM",
            bars=len(data[0]),
            first_time=int(data[0][0]),
            last_time=int(data[0][-1]),
            npz_sha256=digest(npz),
            array_sha256=array_digest(data),
        )
        save()
        return data
    except RuntimeError as error:
        meta.update(status="TRANSIENT_SOURCE_ERROR", message=str(error))
        save()
        raise


def load_month(symbol, month, cache, evidence):
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    try:
        return _load_month(symbol, month, cache)
    except ValueError as error:
        frozen = INPUTS.get(symbol + "/" + month, {})
        record = {
            "symbol": symbol,
            "month": month,
            "status": "CACHE_INTEGRITY_ERROR",
            "message": str(error),
            "frozen_metadata": frozen,
        }
        INPUTS[symbol + "/" + month] = record
        (evidence / f"{symbol}-markPrice-15m-{month}.failure.json").write_text(json.dumps(record, indent=2))
        raise
    finally:
        for path in filenames(symbol, month, cache):
            if path.exists():
                shutil.copyfile(path, evidence / path.name)


def load_symbol(symbol, t, start, end, cache, evidence):
    lo = max(int(t[0]), start - DAY)
    hi = min(int(t[-1]) + BAR, end)
    if hi <= lo:
        return tuple(np.array([], np.int64 if i == 0 else float) for i in range(5)), set()
    first = pd.Period(pd.Timestamp(lo, unit="ms", tz="UTC").strftime("%Y-%m"), freq="M")
    last = pd.Period(pd.Timestamp(hi - 1, unit="ms", tz="UTC").strftime("%Y-%m"), freq="M")
    months = [str(value) for value in pd.period_range(first, last, freq="M")]

    def get(month):
        return month, load_month(symbol, month, cache, evidence)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(get, months))
    valid = {month for month, data in results if data is not None}
    arrays = [data for _, data in results if data is not None]
    if not arrays:
        return tuple(np.array([], np.int64 if i == 0 else float) for i in range(5)), valid
    data = tuple(np.concatenate([part[i] for part in arrays]) for i in range(5))
    if np.any(np.diff(data[0]) <= 0):
        raise ValueError("overlapping mark months")
    cut = data[0] < end
    return tuple(value[cut] for value in data), valid


def align(t, data, valid_months, end):
    out = {name: np.full(len(t), np.nan) for name in ("mark_open", "mark_high", "mark_low", "mark_close")}
    out["mark_available"] = np.zeros(len(t), bool)
    data = tuple(value[data[0] < end] for value in data)
    if not len(data[0]):
        return out
    positions = np.searchsorted(data[0], t)
    matched = positions < len(data[0])
    idx = np.flatnonzero(matched)
    matched[idx] &= data[0][positions[idx]] == t[idx]
    idx = np.flatnonzero(matched)
    positions = positions[idx]
    months = pd.to_datetime(t[idx], unit="ms", utc=True).strftime("%Y-%m")
    ok = np.asarray(months.isin(valid_months))
    idx = idx[ok]
    positions = positions[ok]
    out["mark_available"][idx] = True
    for name, values in zip(("mark_open", "mark_high", "mark_low", "mark_close"), data[1:]):
        out[name][idx] = values[positions]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-symbol", default="BTCUSDT")
    parser.add_argument("--probe-month", default="2022-01")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        data = load_month(args.probe_symbol, args.probe_month, args.cache, args.out / "mark_evidence")
        if data is None:
            raise ValueError("OFFICIAL_MARK_SOURCE_PROBE_FAILED")
        print(
            "OFFICIAL_MARK_SOURCE_PROBE_PASS",
            args.probe_symbol,
            args.probe_month,
            "bars",
            len(data[0]),
            "close_range",
            float(data[4].min()),
            float(data[4].max()),
            flush=True,
        )
    finally:
        (args.out / "mark_inputs.json").write_text(json.dumps(list(INPUTS.values()), indent=2))


if __name__ == "__main__":
    main()
