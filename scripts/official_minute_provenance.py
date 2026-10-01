"""Official Binance minute inputs with original ZIP checksum and cache provenance."""
from __future__ import annotations
import hashlib,io,json,re,time,urllib.error,urllib.request,zipfile
from pathlib import Path
import numpy as np
import pandas as pd

INPUTS={}
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def array_digest(data):
    h=hashlib.sha256()
    for a in data:
        h.update(str(a.dtype).encode());h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()
def _get(url):
    last=None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url,timeout=45) as response:return response.read()
        except urllib.error.HTTPError as error:
            if error.code==404:return None
            last=error
        except (urllib.error.URLError,TimeoutError,OSError) as error:last=error
        if attempt<3:time.sleep(2**attempt)
    raise RuntimeError(f'transient official minute download exhausted: {url}: {last}')
def parse_zip(raw,month):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        members=[n for n in z.namelist() if n.lower().endswith('.csv')]
        if len(members)!=1:raise ValueError('official minute ZIP must contain one CSV')
        d=pd.read_csv(z.open(members[0]),header=None,dtype=str)
    if d.shape[1]!=12 or not len(d):raise ValueError('invalid official 1m column/row contract')
    first=pd.to_numeric(pd.Series([d.iat[0,0]]),errors='coerce').iat[0]
    if not np.isfinite(first):
        header=[str(v).strip().lstrip('\ufeff').lower().replace(' ','_') for v in d.iloc[0,:5]]
        if header!=['open_time','open','high','low','close']:raise ValueError('unknown official minute header')
        d=d.iloc[1:].reset_index(drop=True)
    if not len(d):raise ValueError('empty official minute data')
    numeric_time=pd.to_numeric(d.iloc[:,0],errors='raise').to_numpy()
    t=numeric_time.astype(np.int64)
    if np.any(numeric_time!=t):raise ValueError('fractional official minute timestamp')
    o,h,l,c=[pd.to_numeric(d.iloc[:,i],errors='raise').to_numpy(float) for i in range(1,5)]
    start=int(pd.Timestamp(month+'-01',tz='UTC').timestamp()*1000)
    end=int((pd.Timestamp(month+'-01',tz='UTC')+pd.offsets.MonthBegin(1)).timestamp()*1000)
    if (len({len(t),len(o),len(h),len(l),len(c)})!=1 or np.any(t%60000)
        or np.any(np.diff(t)!=60000) or t[0]<start or t[-1]>=end
        or any(np.any(~np.isfinite(a)) or np.any(a<=0) for a in (o,h,l,c))
        or np.any(h<np.maximum.reduce([o,l,c])) or np.any(l>np.minimum.reduce([o,h,c]))):
        raise ValueError('invalid official minute geometry or timestamp continuity')
    return t,o,h,l

def load_month(symbol,ts,cache):
    if not re.fullmatch(r'[A-Z0-9]+USDT',symbol):raise ValueError('invalid minute symbol')
    month=pd.Timestamp(ts,unit='ms',tz='UTC').strftime('%Y-%m');key=symbol+'/'+month
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    npz=cache/f'{symbol}-{month}.npz';sidecar=cache/f'{symbol}-{month}.input.json'
    url=f'https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/{symbol}-1m-{month}.zip'
    # Only verified sidecars can establish an immutable cache hit.
    if sidecar.exists():
        meta=json.loads(sidecar.read_text())
        if meta.get('status')=='VALIDATED_OFFICIAL_CHECKSUM':
            if (meta.get('source_url')!=url or not meta.get('checksum_verified')
                or meta.get('original_zip_sha256')!=meta.get('official_checksum_sha256')
                or not npz.exists() or digest(npz)!=meta.get('npz_sha256')):
                raise ValueError('frozen verified minute cache mismatch '+key)
            with np.load(npz,allow_pickle=False) as z:data=tuple(z[k].copy() for k in ('t','o','h','l'))
            if array_digest(data)!=meta.get('array_sha256'):
                raise ValueError('frozen minute array mismatch '+key)
            INPUTS[key]=meta;return data
    raw=_get(url)
    meta=dict(symbol=symbol,month=month,source_url=url,checksum_url=url+'.CHECKSUM',
        original_zip_sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None,
        checksum_verified=False,status='DATA_GAP',bars=0,npz_sha256=None,array_sha256=None)
    def gap(message):
        meta['message']=message;INPUTS[key]=meta
        sidecar.write_text(json.dumps(meta,indent=2));return ('data_gap',message)
    if raw is None:return gap('official archive 404')
    checksum=_get(url+'.CHECKSUM')
    if checksum is None:return gap('official checksum 404')
    meta['official_checksum_text_sha256']=hashlib.sha256(checksum).hexdigest()
    try:
        text=checksum.decode('utf-8').strip();token=text.split()[0]
    except (UnicodeDecodeError,IndexError):return gap('malformed official checksum')
    meta['official_checksum_text']=text
    if not re.fullmatch(r'[A-Fa-f0-9]{64}',token):return gap('malformed official checksum')
    meta['official_checksum_sha256']=token.lower()
    if meta['original_zip_sha256']!=token.lower():return gap('official ZIP checksum mismatch')
    meta['checksum_verified']=True
    try:data=parse_zip(raw,month)
    except (ValueError,KeyError,IndexError,OverflowError,zipfile.BadZipFile,pd.errors.ParserError,pd.errors.EmptyDataError) as error:
        return gap(type(error).__name__+': '+str(error))
    temporary=npz.with_suffix('.npz.tmp')
    with temporary.open('wb') as f:np.savez_compressed(f,t=data[0],o=data[1],h=data[2],l=data[3])
    temporary.replace(npz)
    meta.update(status='VALIDATED_OFFICIAL_CHECKSUM',bars=len(data[0]),
                npz_sha256=digest(npz),array_sha256=array_digest(data))
    sidecar.write_text(json.dumps(meta,indent=2));INPUTS[key]=meta
    return data
