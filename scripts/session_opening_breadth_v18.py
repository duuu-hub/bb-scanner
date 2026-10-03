"""Causal whole-universe breadth for the first two closed bars of each UTC 8h session."""
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
THRESHOLD_ATR=.25
ROOT=Path(__file__).resolve().parents[1]
CONTEXT=ROOT/'research/session-opening-impulse-v18/FROZEN_CONTEXT.json'
digest=helpers.digest
COUNT_COLUMNS=['n','down','up']
COLUMNS=['open_time']+COUNT_COLUMNS

def features(raw,q,buy):
    f=history.features(raw,q,buy);t=raw[0];n=len(t)
    f['eligible'] &= helpers.observed_days(t)>=30
    f['session_anchor']=np.full(n,-1,dtype=np.int64)
    f['session_bar']=np.full(n,-1,dtype=np.int64)
    for a,b in base.segments(t):
        k=a
        while k<b:
            anchor=(int(t[k])//(8*60*60*1000))*(8*60*60*1000)
            if t[k]!=anchor:k+=1;continue
            z=k
            while z<b and t[z]<anchor+8*60*60*1000 and t[z]==anchor+(z-k)*BAR:z+=1
            f['session_anchor'][k:z]=anchor;f['session_bar'][k:z]=np.arange(z-k)
            k=max(z,k+1)
    return f

def grid(stage):
    start,end=helpers.interval(stage)
    return np.arange(start-BAR,end,BAR,dtype=np.int64)

def counts(raw,f,times):
    t,o,_,_,c=raw;result=np.zeros((len(times),3),dtype=np.int64)
    ix=np.searchsorted(times,t);present=ix<len(times);present[present]&=times[ix[present]]==t[present]
    selected=np.flatnonzero(present&f['eligible']&(f['session_bar']==1)&(f['prior_atr']>0)&np.isfinite(f['prior_atr']))
    if not len(selected):return result
    anchors=selected-1
    contiguous=(anchors>=0)&(t[selected]==t[anchors]+BAR)&(f['session_bar'][anchors]==0)
    selected=selected[contiguous];anchors=anchors[contiguous]
    impulse=np.divide(c[selected]-o[anchors],f['prior_atr'][selected])
    loc=ix[selected]
    np.add.at(result[:,0],loc,1)
    np.add.at(result[:,1],loc,(impulse<=-THRESHOLD_ATR).astype(np.int64))
    np.add.at(result[:,2],loc,(impulse>=THRESHOLD_ATR).astype(np.int64))
    return result

def validate_counts(frame,times):
    if list(frame.columns)!=COLUMNS or len(frame)!=len(times):raise ValueError('opening breadth schema/grid mismatch')
    x=frame.to_numpy(float)
    if np.any(~np.isfinite(x)) or np.any(x!=np.floor(x)):raise ValueError('noninteger/nonfinite breadth counts')
    if not np.array_equal(frame.open_time.to_numpy(np.int64),times):raise ValueError('breadth time geometry mismatch')
    n,d,u=[frame[k].to_numpy(np.int64) for k in COUNT_COLUMNS]
    if np.any(n<0)|np.any(d<0)|np.any(u<0)|np.any(d+u>n):raise ValueError('impossible breadth counts')

def map_shard(data,btc_path,out,stage,source_check,context_path=CONTEXT):
    paths=sorted(data.rglob('*.csv.gz'));source,context,sources=verify_source(source_check,paths,btc_path,context_path)
    times=grid(stage);_,end=helpers.interval(stage);values=np.zeros((len(times),3),dtype=np.int64);coverage=[]
    out.mkdir(parents=True,exist_ok=True);shutil.copyfile(source_check,out/'verified_source.json')
    def checkpoint(complete):
        frame=pd.DataFrame(values,columns=COUNT_COLUMNS);frame.insert(0,'open_time',times)
        path=out/'breadth_map.csv.gz';tmp=out/'breadth_map.csv.gz.tmp'
        frame.to_csv(tmp,index=False,compression=dict(method='gzip',mtime=0));tmp.replace(path)
        meta=dict(complete=complete,stage=stage,shard=source['shards'][0],source_data_run=36095439671,
            market_hashes=sources,source_files=len(paths),baseline_sha256=context['baseline_sha256'],
            source_context_sha256=digest(context_path),btc_sha256=digest(btc_path),
            source_check_sha256=digest(out/'verified_source.json'),map_sha256=digest(path),rows=len(frame),
            threshold_atr=THRESHOLD_ATR,minimum_universe=MIN_UNIVERSE,coverage=coverage)
        tmp=out/'map_meta.json.tmp';tmp.write_text(json.dumps(meta,indent=2,allow_nan=False));tmp.replace(out/'map_meta.json')
    try:
        for i,path in enumerate(paths,1):
            symbol=path.name[:-7];raw,q,buy=load(path,end)
            if not len(raw[0]) or raw[0][0]>=base.DEV_END-30*DAY:
                coverage.append(dict(symbol=symbol,status='NO_DEVELOPMENT_HISTORY'));continue
            mapped=counts(raw,features(raw,q,buy),times);values+=mapped
            coverage.append(dict(symbol=symbol,status='COUNTED',eligible=int(mapped[:,0].sum())))
            if i%8==0 or i==len(paths):checkpoint(False);print('V18_OPENING_BREADTH_MAP',stage,i,'/',len(paths),flush=True)
    except BaseException:
        checkpoint(False);raise
    checkpoint(True);print('V18_OPENING_BREADTH_MAP_DONE',stage,'shard',source['shards'][0],flush=True)

def reduce_maps(parts,out,stage,context_path=CONTEXT):
    paths=sorted(parts.rglob('breadth_map.csv.gz'));context=json.loads(context_path.read_text())
    if len(paths)!=8:raise ValueError('exact eight opening breadth shards required')
    times=grid(stage);values=np.zeros((len(times),3),dtype=np.int64);sources,shards,inputs={},set(),[]
    for path in paths:
        meta_path=path.parent/'map_meta.json';m=json.loads(meta_path.read_text())
        if (not m['complete'] or m['stage']!=stage or m['map_sha256']!=digest(path)
            or m['source_context_sha256']!=digest(context_path) or m['baseline_sha256']!=context['baseline_sha256']
            or m['btc_sha256']!=context['btc_sha256'] or m['source_data_run']!=36095439671
            or m['threshold_atr']!=THRESHOLD_ATR or m['minimum_universe']!=MIN_UNIVERSE):
            raise ValueError('altered/incomplete opening breadth map')
        check=path.parent/'verified_source.json';s=json.loads(check.read_text())
        verified={x['symbol']:x['sha256'] for x in s['files']}
        if digest(check)!=m['source_check_sha256'] or s['status']!='VERIFIED' or verified!=m['market_hashes']:
            raise ValueError('opening breadth source verification mismatch')
        if m['shard'] not in range(8) or m['shard'] in shards:raise ValueError('duplicate/invalid breadth shard')
        shards.add(m['shard'])
        for symbol,sha in m['market_hashes'].items():
            if symbol in sources or context['expected_market_sha256'].get(symbol)!=sha:raise ValueError('source hash/duplicate')
            sources[symbol]=sha
        frame=pd.read_csv(path);validate_counts(frame,times);values+=frame[COUNT_COLUMNS].to_numpy(np.int64)
        inputs.append(dict(shard=m['shard'],map_sha256=digest(path),meta_sha256=digest(meta_path),source_check_sha256=digest(check)))
    if shards!=set(range(8)) or sources!=context['expected_market_sha256']:raise ValueError('incomplete full breadth universe')
    frame=pd.DataFrame(values,columns=COUNT_COLUMNS);frame.insert(0,'open_time',times);validate_counts(frame,times)
    out.mkdir(parents=True,exist_ok=True);path=out/'breadth.csv.gz'
    frame.to_csv(path,index=False,compression=dict(method='gzip',mtime=0))
    m=dict(complete=True,stage=stage,source_data_run=36095439671,source_context_sha256=digest(context_path),
        baseline_sha256=context['baseline_sha256'],btc_sha256=context['btc_sha256'],market_hashes=sources,
        map_inputs=inputs,data_sha256=digest(path),rows=len(times),threshold_atr=THRESHOLD_ATR,
        minimum_universe=MIN_UNIVERSE,low_universe_bars=int((frame.n<MIN_UNIVERSE).sum()),
        availability='Counts at opening bar 1 time use only bars 0 and 1 after bar 1 closes.')
    (out/'breadth_manifest.json').write_text(json.dumps(m,indent=2,allow_nan=False))
    print('V18_GLOBAL_OPENING_BREADTH_FROZEN',stage,'sources',len(sources),'sha256',m['data_sha256'],flush=True)
    return frame,m

def load_breadth(root,stage,context_path=CONTEXT):
    m=json.loads((root/'breadth_manifest.json').read_text());context=json.loads(context_path.read_text());path=root/'breadth.csv.gz'
    if (not m['complete'] or m['stage']!=stage or m['data_sha256']!=digest(path)
        or m['source_context_sha256']!=digest(context_path) or m['market_hashes']!=context['expected_market_sha256']
        or m['threshold_atr']!=THRESHOLD_ATR or m['minimum_universe']!=MIN_UNIVERSE
        or {x['shard'] for x in m['map_inputs']}!=set(range(8))):
        raise ValueError('frozen opening breadth artifact mismatch')
    frame=pd.read_csv(path);validate_counts(frame,grid(stage));return frame,m

def align(t,frame):
    times=frame.open_time.to_numpy(np.int64);ix=np.searchsorted(times,t)
    valid=ix<len(times);valid[valid]&=times[ix[valid]]==t[valid];out={}
    for col in COUNT_COLUMNS:
        value=np.full(len(t),np.nan);value[valid]=frame[col].to_numpy(float)[ix[valid]];out[col]=value
    out['opening_breadth_time']=np.where(valid,t,np.nan)
    ok=valid&(out['n']>=MIN_UNIVERSE)
    out['up_fraction']=np.divide(out['up'],out['n'],out=np.full(len(t),np.nan),where=ok)
    out['down_fraction']=np.divide(out['down'],out['n'],out=np.full(len(t),np.nan),where=ok)
    return out

def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('map')
    for name in ('data','btc','out','source-check'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--stage',choices=('DEV','GATE'),required=True)
    p=sub.add_parser('reduce')
    for name in ('parts','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--stage',choices=('DEV','GATE'),required=True)
    a=ap.parse_args()
    if a.command=='map':map_shard(a.data,a.btc,a.out,a.stage,a.source_check)
    else:reduce_maps(a.parts,a.out,a.stage)
if __name__=='__main__':main()
