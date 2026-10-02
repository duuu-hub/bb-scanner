"""Whole-universe closed-bar shock counts, with no exit outcomes or orders."""
from __future__ import annotations
import argparse,json,shutil,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from scripts import day_edge_lab as base
from scripts import btc_factor_lag_v6 as history
from scripts import cross_sectional_ranks_v9 as helpers
from scripts.breakout_level_retest_v13 import verify_source
from scripts.shock_confirmation_v3 import load

BAR,DAY=base.BAR,base.DAY
MIN_UNIVERSE=30
HORIZONS={16:.04,96:.08}
ROOT=Path(__file__).resolve().parents[1]
CONTEXT=ROOT/'research/market-breadth-recovery-v14/FROZEN_CONTEXT.json'
digest=helpers.digest
COUNT_COLUMNS=[f'{kind}{h}' for h in HORIZONS for kind in ('n','down','up')]
COLUMNS=['open_time']+COUNT_COLUMNS

def features(raw,q,buy):
    f=history.features(raw,q,buy);t=raw[0]
    f['eligible'] &= helpers.observed_days(t)>=30
    for name in ('structural_low','structural_high'):f[name]=np.full(len(t),np.nan)
    for a,b in base.segments(t):
        f['structural_low'][a:b]=pd.Series(raw[3][a:b]).rolling(5,min_periods=5).min().to_numpy()
        f['structural_high'][a:b]=pd.Series(raw[2][a:b]).rolling(5,min_periods=5).max().to_numpy()
    return f

def grid(stage):
    start,end=helpers.interval(stage)
    # Include the immediate prior closed bar for stage-boundary decisions.
    return np.arange(start-BAR,end,BAR,dtype=np.int64)

def counts(raw,f,times):
    t=raw[0];result=np.zeros((len(times),len(COUNT_COLUMNS)),dtype=np.int64)
    index=np.searchsorted(times,t);ok=index<len(times);ok[ok]&=times[index[ok]]==t[ok]
    for j,(h,shock) in enumerate(HORIZONS.items()):
        eligible=ok&f['eligible']&np.isfinite(f[f'r{h}'])
        selected=np.flatnonzero(eligible);loc=index[selected];r=f[f'r{h}'][selected]
        np.add.at(result[:,j*3],loc,1)
        np.add.at(result[:,j*3+1],loc,(r<=-shock).astype(np.int64))
        np.add.at(result[:,j*3+2],loc,(r>=shock).astype(np.int64))
    return result

def validate_counts(frame,times):
    if list(frame.columns)!=COLUMNS or len(frame)!=len(times):raise ValueError('breadth schema/grid size mismatch')
    x=frame.to_numpy(float)
    if np.any(~np.isfinite(x)) or np.any(x!=np.floor(x)):raise ValueError('noninteger/nonfinite breadth counts')
    if not np.array_equal(frame.open_time.to_numpy(np.int64),times):raise ValueError('breadth time geometry mismatch')
    for h in HORIZONS:
        n,d,u=[frame[k+str(h)].to_numpy(np.int64) for k in ('n','down','up')]
        if np.any(n<0)|np.any(d<0)|np.any(u<0)|np.any(d+u>n):raise ValueError('impossible breadth numerator/denominator')

def map_shard(data,btc_path,out,stage,source_check,context_path=CONTEXT):
    paths=sorted(data.rglob('*.csv.gz'));source,context,sources=verify_source(source_check,paths,btc_path,context_path)
    times=grid(stage);_,end=helpers.interval(stage)
    values=np.zeros((len(times),len(COUNT_COLUMNS)),dtype=np.int64);coverage=[]
    out.mkdir(parents=True,exist_ok=True);shutil.copyfile(source_check,out/'verified_source.json')
    def checkpoint(complete):
        frame=pd.DataFrame(values,columns=COUNT_COLUMNS);frame.insert(0,'open_time',times)
        path=out/'breadth_map.csv.gz';temporary=out/'breadth_map.csv.gz.tmp'
        frame.to_csv(temporary,index=False,compression=dict(method='gzip',mtime=0));temporary.replace(path)
        m=dict(complete=complete,stage=stage,shard=source['shards'][0],source_data_run=36095439671,
            market_hashes=sources,source_files=len(paths),baseline_sha256=context['baseline_sha256'],
            source_context_sha256=digest(context_path),btc_sha256=digest(btc_path),
            source_check_sha256=digest(out/'verified_source.json'),map_sha256=digest(path),rows=len(frame),
            horizons={str(k):v for k,v in HORIZONS.items()},minimum_universe=MIN_UNIVERSE,coverage=coverage)
        temporary=out/'map_meta.json.tmp';temporary.write_text(json.dumps(m,indent=2,allow_nan=False));temporary.replace(out/'map_meta.json')
    try:
        for i,path in enumerate(paths,1):
            symbol=path.name[:-7];raw,q,buy=load(path,end)
            if not len(raw[0]) or raw[0][0]>=base.DEV_END-30*DAY:
                coverage.append(dict(symbol=symbol,status='NO_DEVELOPMENT_HISTORY'));continue
            f=features(raw,q,buy);mapped=counts(raw,f,times);values+=mapped
            coverage.append(dict(symbol=symbol,status='COUNTED',eligible16=int(mapped[:,0].sum()),eligible96=int(mapped[:,3].sum())))
            if i%8==0 or i==len(paths):
                checkpoint(False);print('V14_BREADTH_MAP',stage,i,'/',len(paths),flush=True)
    except BaseException:
        checkpoint(False);raise
    checkpoint(True);print('V14_BREADTH_MAP_DONE',stage,'shard',source['shards'][0],'sources',len(paths),'rows',len(times),flush=True)

def reduce_maps(parts,out,stage,context_path=CONTEXT):
    paths=sorted(parts.rglob('breadth_map.csv.gz'));context=json.loads(context_path.read_text())
    if len(paths)!=8:raise ValueError('exact eight global breadth shards required')
    times=grid(stage);values=np.zeros((len(times),len(COUNT_COLUMNS)),dtype=np.int64)
    sources,shards,inputs={},set(),[]
    for path in paths:
        meta_path=path.parent/'map_meta.json';m=json.loads(meta_path.read_text())
        if (not m['complete'] or m['stage']!=stage or m['map_sha256']!=digest(path)
            or m['source_context_sha256']!=digest(context_path) or m['baseline_sha256']!=context['baseline_sha256']
            or m['btc_sha256']!=context['btc_sha256'] or m['source_data_run']!=36095439671
            or m['horizons']!={str(k):v for k,v in HORIZONS.items()} or m['minimum_universe']!=MIN_UNIVERSE):
            raise ValueError('altered/incomplete breadth map')
        check=path.parent/'verified_source.json';s=json.loads(check.read_text())
        verified={x['symbol']:x['sha256'] for x in s['files']}
        if (digest(check)!=m['source_check_sha256'] or s['status']!='VERIFIED' or s['shards']!=[m['shard']]
            or s['baseline_sha256']!=m['baseline_sha256'] or verified!=m['market_hashes']
            or len(s['files'])!=len(verified) or m['source_files']!=len(verified)):
            raise ValueError('breadth source verification mismatch')
        if m['shard'] not in range(8) or m['shard'] in shards:raise ValueError('duplicate/invalid breadth shard')
        shards.add(m['shard'])
        for symbol,sha in m['market_hashes'].items():
            if symbol in sources or context['expected_market_sha256'].get(symbol)!=sha:raise ValueError('breadth source hash/duplicate')
            sources[symbol]=sha
        frame=pd.read_csv(path);validate_counts(frame,times)
        if len(frame)!=m['rows']:raise ValueError('breadth row count mismatch')
        if any((frame['n'+str(h)]>m['source_files']).any() for h in HORIZONS):raise ValueError('shard breadth universe count overflow')
        values+=frame[COUNT_COLUMNS].to_numpy(np.int64)
        inputs.append(dict(shard=m['shard'],map_sha256=digest(path),meta_sha256=digest(meta_path),source_check_sha256=digest(check)))
    if shards!=set(range(8)) or sources!=context['expected_market_sha256']:raise ValueError('incomplete full breadth universe')
    frame=pd.DataFrame(values,columns=COUNT_COLUMNS);frame.insert(0,'open_time',times);validate_counts(frame,times)
    if any((frame['n'+str(h)]>len(sources)).any() for h in HORIZONS):raise ValueError('breadth universe count overflow')
    out.mkdir(parents=True,exist_ok=True);path=out/'breadth.csv.gz'
    frame.to_csv(path,index=False,compression=dict(method='gzip',mtime=0))
    m=dict(complete=True,stage=stage,source_data_run=36095439671,source_context_sha256=digest(context_path),
        baseline_sha256=context['baseline_sha256'],btc_sha256=context['btc_sha256'],market_hashes=sources,
        map_inputs=inputs,data_sha256=digest(path),rows=len(times),horizons={str(k):v for k,v in HORIZONS.items()},
        minimum_universe=MIN_UNIVERSE,low_universe_bars={str(h):int((frame['n'+str(h)]<MIN_UNIVERSE).sum()) for h in HORIZONS},
        availability='Counts at open_time t use bar closed at t+BAR; exact previous t-BAR only; no future fill.')
    (out/'breadth_manifest.json').write_text(json.dumps(m,indent=2,allow_nan=False))
    print('V14_GLOBAL_BREADTH_FROZEN',stage,'sources',len(sources),'rows',len(times),'sha256',m['data_sha256'],flush=True)
    return frame,m

def load_breadth(root,stage,context_path=CONTEXT):
    m=json.loads((root/'breadth_manifest.json').read_text());context=json.loads(context_path.read_text());path=root/'breadth.csv.gz'
    if (not m['complete'] or m['stage']!=stage or m['data_sha256']!=digest(path) or m['source_data_run']!=36095439671
        or m['source_context_sha256']!=digest(context_path) or m['market_hashes']!=context['expected_market_sha256']
        or m['baseline_sha256']!=context['baseline_sha256'] or m['btc_sha256']!=context['btc_sha256']
        or m['horizons']!={str(k):v for k,v in HORIZONS.items()} or m['minimum_universe']!=MIN_UNIVERSE
        or {x['shard'] for x in m['map_inputs']}!=set(range(8)) or len(m['map_inputs'])!=8):
        raise ValueError('frozen global breadth artifact mismatch')
    f=pd.read_csv(path);validate_counts(f,grid(stage))
    if len(f)!=m['rows'] or any((f['n'+str(h)]>len(m['market_hashes'])).any() for h in HORIZONS):raise ValueError('frozen breadth count mismatch')
    return f,m

def align(t,frame):
    times=frame.open_time.to_numpy(np.int64);out={}
    for prefix,wanted in (('',t),('previous_',t-BAR)):
        ix=np.searchsorted(times,wanted);valid=ix<len(times);valid[valid]&=times[ix[valid]]==wanted[valid]
        for col in COUNT_COLUMNS:
            value=np.full(len(t),np.nan);value[valid]=frame[col].to_numpy(float)[ix[valid]];out[prefix+col]=value
        out[prefix+'open_time']=np.where(valid,wanted,np.nan)
        for h in HORIZONS:
            n=out[prefix+'n'+str(h)];ok=valid&(n>=MIN_UNIVERSE)
            for kind in ('down','up'):
                out[prefix+kind+'_fraction'+str(h)]=np.divide(out[prefix+kind+str(h)],n,out=np.full(len(t),np.nan),where=ok)
    return out

def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('map')
    for n in ('data','btc','out','source-check'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--stage',choices=('DEV','GATE'),required=True)
    p=sub.add_parser('reduce')
    for n in ('parts','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--stage',choices=('DEV','GATE'),required=True)
    a=ap.parse_args()
    if a.command=='map':map_shard(a.data,a.btc,a.out,a.stage,a.source_check)
    else:reduce_maps(a.parts,a.out,a.stage)
if __name__=='__main__':main()
