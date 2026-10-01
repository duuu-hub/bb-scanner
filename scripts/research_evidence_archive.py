"""Preserve research evidence and verify the frozen full source catalogue."""
from __future__ import annotations
import argparse,hashlib,json,os,shutil
from pathlib import Path
import pandas as pd

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def verify_source(data,manifests,baseline_path,out,expected_shards=1):
    out.parent.mkdir(parents=True,exist_ok=True)
    report=dict(status='INCOMPLETE',source_data_run=36095439671,files=[],errors=[])
    try:
        paths=sorted(manifests.rglob('manifest_shard_*.json'))
        if len(paths)!=expected_shards:raise ValueError('missing original source manifests')
        records={};shards=set()
        for path in paths:
            m=json.loads(path.read_text());idx=int(m['summary']['shard_index'])
            if idx in shards:raise ValueError('duplicate original source shard')
            shards.add(idx)
            for row in m['symbols']:
                if row.get('rows',0)>0:
                    if row['file'] in records:raise ValueError('duplicate expected market file')
                    records[row['file']]=row
        observed={}
        for path in data.rglob('*.csv.gz'):
            if path.name in observed:raise ValueError('duplicate market file')
            observed[path.name]=path
        if not observed or set(observed)!=set(records):
            raise ValueError('source file catalogue mismatch: missing='+str(sorted(set(records)-set(observed)))+' extra='+str(sorted(set(observed)-set(records))))
        baseline=json.loads(baseline_path.read_text())['expected_csv_sha256']
        matched=0
        for name,path in sorted(observed.items()):
            row=records[name];t=pd.read_csv(path,usecols=['open_time']).open_time
            if len(t)!=row['rows'] or int(t.iloc[0])!=row['first'] or int(t.iloc[-1])!=row['last']:
                raise ValueError('source timestamp/statistics mismatch '+name)
            sha=digest(path);symbol=row['symbol']
            if symbol in baseline:
                if sha!=baseline[symbol]:raise ValueError('frozen source SHA256 mismatch '+symbol)
                matched+=1
            report['files'].append(dict(symbol=symbol,file=name,rows=len(t),first=int(t.iloc[0]),
                last=int(t.iloc[-1]),sha256=sha,matched_prior_hash=symbol in baseline,
                original_declared_gaps=row.get('gaps'),original_errors=row.get('errors',[]),
                official_source_url=f'https://data.binance.vision/?prefix=data/futures/um/monthly/klines/{symbol}/15m/'))
        if not matched:raise ValueError('no prior immutable source hashes matched')
        if expected_shards==8 and matched!=len(baseline):
            raise ValueError('incomplete previously observed historical universe')
        report.update(status='VERIFIED',shards=sorted(shards),count=len(observed),
            matched_prior_hashes=matched,baseline_sha256=digest(baseline_path),
            original_manifests={p.name:digest(p) for p in paths},
            hash_semantics='SHA256 of original frozen processed CSV; not an absent raw Binance ZIP checksum')
    except BaseException as e:
        report['errors'].append(type(e).__name__+': '+str(e))
        out.write_text(json.dumps(report,indent=2));raise
    out.write_text(json.dumps(report,indent=2))
    print('FROZEN_SOURCE_VERIFIED',len(observed),'files',matched,'prior SHA256 matches',flush=True)

def archive(source,out,kind):
    if not source.exists():raise ValueError('evidence source missing')
    out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for path in sorted(source.rglob('*')):
        if not path.is_file():continue
        if path.is_symlink():raise ValueError('evidence symlink is not an immutable input')
        rel=path.relative_to(source);target=out/'files'/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        size=path.stat().st_size;sha=digest(path)
        if size>90*1024*1024:
            chunks=[]
            with path.open('rb') as f:
                for i in range((size+64*1024*1024-1)//(64*1024*1024)):
                    part=target.with_name(target.name+f'.part{i:04d}')
                    part.write_bytes(f.read(64*1024*1024))
                    chunks.append(dict(file=str(part.relative_to(out)),sha256=digest(part),bytes=part.stat().st_size))
            rows.append(dict(original_path=str(rel),bytes=size,sha256=sha,chunks=chunks))
        else:
            shutil.copyfile(path,target)
            if digest(target)!=sha:raise ValueError('evidence copy mismatch '+str(rel))
            rows.append(dict(original_path=str(rel),file=str(target.relative_to(out)),bytes=size,sha256=sha))
    if not rows:raise ValueError('no evidence files available')
    manifest=dict(kind=kind,run=os.environ.get('GITHUB_RUN_ID'),code_commit=os.environ.get('GITHUB_SHA'),
        files=rows,note='Concatenate listed chunks in order if split; verify full original SHA256. No failed outcomes removed.')
    (out/'EVIDENCE_MANIFEST.json').write_text(json.dumps(manifest,indent=2))
    print('EVIDENCE_ARCHIVED',kind,len(rows),'files',sum(r['bytes'] for r in rows),'bytes',flush=True)

def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('verify-source')
    for n in ('data','manifests','baseline','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--expected-shards',type=int,default=1)
    p=sub.add_parser('archive')
    p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--kind',required=True)
    a=ap.parse_args()
    if a.command=='verify-source':verify_source(a.data,a.manifests,a.baseline,a.out,a.expected_shards)
    else:archive(a.source,a.out,a.kind)

if __name__=='__main__':main()

