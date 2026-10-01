"""Historical official paid funding; verified original bytes and lagged as-of use."""
from __future__ import annotations
import argparse, hashlib, io, json, re, shutil, sys, zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from scripts.official_minute_provenance import _get, digest, array_digest

BAR = 900_000
HOUR = 3_600_000
DAY = 24 * HOUR
INPUTS = {}
FIELDS = ('calc_time', 'funding_interval_hours', 'last_funding_rate')

def validate_arrays(data, month):
    t, interval, rate = data
    start = int(pd.Timestamp(month+'-01', tz='UTC').timestamp()*1000)
    end = int((pd.Timestamp(month+'-01', tz='UTC')+pd.offsets.MonthBegin(1)).timestamp()*1000)
    if (not len(t) or len({len(t), len(interval), len(rate)}) != 1
        or t.dtype != np.dtype('int64') or np.any(np.diff(t) <= 0)
        or t[0] < start or t[-1] >= end
        or np.any(~np.isfinite(interval)) or np.any(interval <= 0) or np.any(interval > 24)
        or np.any(~np.isfinite(rate)) or np.any(abs(rate) > 1)):
        raise ValueError('invalid historical funding timestamps, intervals or rates')

def parse_zip(raw, month):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        members = [n for n in z.namelist() if n.lower().endswith('.csv')]
        if len(members) != 1:
            raise ValueError('funding ZIP must contain exactly one CSV')
        d = pd.read_csv(z.open(members[0]), header=None, dtype=str)
    if d.shape[1] != 3 or not len(d):
        raise ValueError('invalid official funding column/row contract')
    if not re.fullmatch(r'\d+', str(d.iat[0, 0]).strip()):
        header = tuple(str(v).strip().lstrip('\ufeff').lower() for v in d.iloc[0])
        if header != FIELDS:
            raise ValueError('unknown official funding header '+repr(header))
        d = d.iloc[1:]
    if not len(d) or not d.iloc[:, 0].str.fullmatch(r'\d+').all():
        raise ValueError('empty or noninteger funding timestamp')
    t = d.iloc[:, 0].to_numpy(dtype=np.int64)
    interval, rate = [pd.to_numeric(d.iloc[:, i], errors='raise').to_numpy(float) for i in (1, 2)]
    data = t, interval, rate
    validate_arrays(data, month)
    return data

def filenames(symbol, month, root):
    stem = Path(root)/f'{symbol}-fundingRate-{month}'
    return [Path(str(stem)+suffix) for suffix in ('.zip', '.zip.CHECKSUM', '.npz', '.input.json')]

def _load_month(symbol, month, cache):
    if not re.fullmatch(r'[A-Z0-9]+USDT', symbol) or not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError('invalid historical funding symbol/month')
    cache = Path(cache); cache.mkdir(parents=True, exist_ok=True)
    raw_path, checksum_path, npz, sidecar = filenames(symbol, month, cache)
    key = symbol+'/'+month
    url = f'https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip'
    if sidecar.exists():
        meta = json.loads(sidecar.read_text())
        if meta.get('status') == 'VALIDATED_OFFICIAL_CHECKSUM':
            INPUTS[key] = meta
            if (meta.get('source_url') != url or not meta.get('checksum_verified')
                or not all(p.exists() for p in (raw_path, checksum_path, npz))
                or digest(raw_path) != meta.get('original_zip_sha256')
                or meta.get('original_zip_sha256') != meta.get('official_checksum_sha256')
                or digest(checksum_path) != meta.get('official_checksum_text_sha256')
                or digest(npz) != meta.get('npz_sha256')):
                raise ValueError('frozen verified funding cache mismatch '+key)
            text = checksum_path.read_text().strip()
            if not text or text.split()[0].lower() != meta['original_zip_sha256']:
                raise ValueError('frozen funding checksum token mismatch '+key)
            with np.load(npz, allow_pickle=False) as z:
                data = tuple(z[k].copy() for k in ('t', 'interval', 'rate'))
            if array_digest(data) != meta.get('array_sha256'):
                raise ValueError('frozen funding array mismatch '+key)
            validate_arrays(data, month)
            return data
    # Unverified/failed legacy inputs are never adopted as immutable cache hits.
    meta = dict(symbol=symbol, month=month, source_url=url, checksum_url=url+'.CHECKSUM',
        status='FUNDING_DATA_GAP', records=0, checksum_verified=False,
        original_zip_sha256=None, npz_sha256=None, array_sha256=None)
    INPUTS[key] = meta
    def save(): sidecar.write_text(json.dumps(meta, indent=2))
    def gap(message):
        meta['message'] = message; save(); return None
    try:
        raw = _get(url)
        if raw is None:
            # Never present an older failed download as this attempt's raw bytes.
            for p in (raw_path, checksum_path, npz): p.unlink(missing_ok=True)
            return gap('official funding archive 404')
        raw_path.write_bytes(raw)
        meta['original_zip_sha256'] = hashlib.sha256(raw).hexdigest()
        checksum = _get(url+'.CHECKSUM')
        if checksum is None:
            checksum_path.unlink(missing_ok=True); npz.unlink(missing_ok=True)
            return gap('official funding checksum 404')
        checksum_path.write_bytes(checksum)
        meta['official_checksum_text_sha256'] = hashlib.sha256(checksum).hexdigest()
        try:
            text = checksum.decode('utf-8').strip(); token = text.split()[0]
        except (UnicodeDecodeError, IndexError): return gap('malformed official funding checksum')
        meta['official_checksum_text'] = text
        if not re.fullmatch(r'[A-Fa-f0-9]{64}', token): return gap('malformed official funding checksum')
        meta['official_checksum_sha256'] = token.lower()
        if meta['original_zip_sha256'] != token.lower(): return gap('official funding ZIP checksum mismatch')
        meta['checksum_verified'] = True
        try: data = parse_zip(raw, month)
        except (ValueError, KeyError, IndexError, OverflowError, zipfile.BadZipFile,
                pd.errors.ParserError, pd.errors.EmptyDataError) as error:
            return gap(type(error).__name__+': '+str(error))
        temporary = npz.with_suffix('.npz.tmp')
        with temporary.open('wb') as f:
            np.savez_compressed(f, t=data[0], interval=data[1], rate=data[2])
        temporary.replace(npz)
        meta.update(status='VALIDATED_OFFICIAL_CHECKSUM', records=len(data[0]),
            intervals_hours=sorted(set(data[1].tolist())), first_time=int(data[0][0]),
            last_time=int(data[0][-1]), npz_sha256=digest(npz), array_sha256=array_digest(data))
        save(); return data
    except RuntimeError as error:
        meta.update(status='TRANSIENT_SOURCE_ERROR', message=str(error)); save(); raise

def load_month(symbol, month, cache, evidence):
    evidence = Path(evidence); evidence.mkdir(parents=True, exist_ok=True)
    try: return _load_month(symbol, month, cache)
    except ValueError as error:
        frozen = INPUTS.get(symbol+'/'+month, {})
        record = dict(symbol=symbol, month=month, status='CACHE_INTEGRITY_ERROR',
                      message=str(error), frozen_metadata=frozen)
        INPUTS[symbol+'/'+month] = record
        (evidence/f'{symbol}-fundingRate-{month}.failure.json').write_text(json.dumps(record, indent=2))
        raise
    finally:
        # Original successful and failed bytes survive even if a download later fails.
        for p in filenames(symbol, month, cache):
            if p.exists(): shutil.copyfile(p, evidence/p.name)

def load_symbol(symbol, t, start, end, cache, evidence):
    lo = max(int(t[0]), start-DAY)
    hi = min(int(t[-1])+BAR, end)
    if hi <= lo:
        return (np.array([], np.int64), np.array([], float), np.array([], float)), set()
    first = pd.Period(pd.Timestamp(lo, unit='ms', tz='UTC').strftime('%Y-%m'), freq='M')
    last = pd.Period(pd.Timestamp(hi-1, unit='ms', tz='UTC').strftime('%Y-%m'), freq='M')
    months = [str(m) for m in pd.period_range(first, last, freq='M')]
    def get(month): return month, load_month(symbol, month, cache, evidence)
    # Different monthly archive keys are independent; never parallelize mutations to one key.
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(get, months))
    valid = {month for month, data in results if data is not None}
    arrays = [data for month, data in results if data is not None]
    if not arrays:
        return (np.array([], np.int64), np.array([], float), np.array([], float)), valid
    data = tuple(np.concatenate([a[i] for a in arrays]) for i in range(3))
    if np.any(np.diff(data[0]) <= 0): raise ValueError('overlapping funding months')
    cut = data[0] < end
    return tuple(a[cut] for a in data), valid

def asof(t, data, valid_months, end):
    decision = t+BAR
    out = {name: np.full(len(t), np.nan) for name in
        ('funding_rate8h', 'funding_rate_original', 'funding_calc_time', 'funding_interval_hours', 'funding_age_hours')}
    out['funding_available'] = np.zeros(len(t), bool)
    data = tuple(a[data[0] < end] for a in data)  # physically cut before any lookup
    if not len(data[0]): return out
    k = np.searchsorted(data[0]+BAR, decision, side='right')-1
    matched = k >= 0; idx = np.flatnonzero(matched); kk = k[matched]
    age = decision[idx]-data[0][kk]
    monthly = pd.to_datetime(decision[idx], unit='ms', utc=True).strftime('%Y-%m').isin(valid_months)
    ok = monthly & (age >= BAR) & (age <= np.minimum(12*HOUR, data[1][kk]*HOUR+BAR))
    idx, kk, age = idx[ok], kk[ok], age[ok]
    out['funding_available'][idx] = True
    for name, values in (
        ('funding_rate8h', data[2][kk]*8/data[1][kk]),
        ('funding_rate_original', data[2][kk]), ('funding_calc_time', data[0][kk]),
        ('funding_interval_hours', data[1][kk]), ('funding_age_hours', age/HOUR)):
        out[name][idx] = values
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe-symbol', default='BTCUSDT'); ap.add_argument('--probe-month', default='2022-01')
    ap.add_argument('--cache', type=Path, required=True); ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args(); a.out.mkdir(parents=True, exist_ok=True)
    try:
        data = load_month(a.probe_symbol, a.probe_month, a.cache, a.out/'funding_evidence')
        if data is None: raise ValueError('OFFICIAL_FUNDING_SOURCE_PROBE_FAILED')
        print('OFFICIAL_FUNDING_SOURCE_PROBE_PASS', a.probe_symbol, a.probe_month,
              'records', len(data[0]), 'intervals', sorted(set(data[1].tolist())), flush=True)
    finally: (a.out/'funding_inputs.json').write_text(json.dumps(list(INPUTS.values()), indent=2))

if __name__ == '__main__': main()
