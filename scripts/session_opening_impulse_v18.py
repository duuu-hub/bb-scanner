"""Preregistered V18 session-opening impulse acceptance continuation."""
from __future__ import annotations
import argparse,json,shutil,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from scripts import day_edge_lab as base
from scripts import day_edge_canonical as canonical
from scripts import relative_pullback_v1 as chronology
from scripts import relative_pullback_portfolio as account
from scripts import official_minute_provenance as official
from scripts import premium_absorption_v8 as minute_audit
from scripts import btc_factor_lag_v6 as history
from scripts import aggressive_flow_cascade_v11 as prior_cascade
from scripts import cross_sectional_ranks_v9 as source_helpers
from scripts import session_opening_breadth_v18 as opening_breadth
from scripts.cross_sectional_leader_v9 import strict_accounts
from scripts.shock_confirmation_v3 import load

BAR,DAY=base.BAR,base.DAY
HOLDS,EXITS=(16,32),('OR1','R15','R25')
ROOT=Path(__file__).resolve().parents[1]
CONTEXT=ROOT/'research/session-opening-impulse-v18/FROZEN_CONTEXT.json'
digest=source_helpers.digest
COLUMNS=['symbol','key','signal_time','decision_time','entry_time','entry','sl','side',
    'risk_pct','score','atr_mult','prior_atr','buy_share','volume_multiple',
    'displacement_atr','breadth_threshold','retracement_limit','session_anchor_time',
    'session_bar','event_time','event_vwap','opening_extreme','opening_impulse',
    'opening_breadth_n','opening_breadth_fraction','pullback_time','pullback_retracement',
    'confirmation_age_bars','confirmation_time','projection_target',
    'structural_stop','coin_r1','btc_r1','clv','known_entry_gap',
    'tp','max_hold_bars','status','exit_time','exit','reason','gross_return',
    'variant','policy','exit_type','hold_min','net40_fraction','net40_R','split']

def configurations():
    return [dict(key=f'OPEN_S{side:+d}_D{int(d*10):02d}_B{int(b*100):02d}_R{int(r*1000):03d}',
                 side=side,displacement=d,breadth=b,retracement=r)
            for side in (1,-1) for d in (1.,1.5) for b in (.55,.65) for r in (.382,.618)]

def policies():
    return [dict(**cfg,hold=h,exit_type=e,policy=f'{cfg["key"]}__H{h}__{e}')
            for cfg in configurations() for h in HOLDS for e in EXITS]

def development_rejections(r):
    """Exact V18 preregistered DEV gates; do not inherit another study's sample gates."""
    reasons=[]
    if r['n']<300:reasons.append('N_LT_300')
    if r['symbols']<60:reasons.append('SYMBOLS_LT_60')
    if r['top_symbol_share_pct']>30:reasons.append('TOP_SYMBOL_GT_30PCT')
    for key in ('net40_mean_bp','net40_R_mean'):
        if r[key] is None or not np.isfinite(r[key]) or r[key]<=0:reasons.append(key.upper()+'_NONPOSITIVE')
    for year in (2021,2022,2023):
        if r.get(f'year_{year}_n',0)<30:reasons.append(f'{year}_N_LT_30')
        if r.get(f'year_{year}_days',0)<20:reasons.append(f'{year}_DATES_LT_20')
        for metric in ('net40_R','net40_mean_bp','day_R'):
            if r.get(f'year_{year}_{metric}',-999)<=0:reasons.append(f'{year}_{metric}_NONPOSITIVE')
    return reasons

def features(raw,q,buy):
    f=history.features(raw,q,buy);t=raw[0];n=len(t)
    f['quote']=q.copy();f['prior_quote_median']=np.full(n,np.nan)
    f['session_anchor']=np.full(n,-1,dtype=np.int64);f['session_bar']=np.full(n,-1,dtype=np.int64)
    f['session_vwap']=np.full(n,np.nan)
    for a,b in base.segments(t):
        qq=pd.Series(q[a:b]);f['prior_quote_median'][a:b]=qq.rolling(96,min_periods=96).median().shift(1).to_numpy()
        k=a
        while k<b:
            anchor=(int(t[k])//(8*60*60*1000))*(8*60*60*1000)
            if t[k]!=anchor:k+=1;continue
            z=k
            while z<b and t[z]<anchor+8*60*60*1000 and t[z]==anchor+(z-k)*BAR:z+=1
            typ=(raw[2][k:z]+raw[3][k:z]+raw[4][k:z])/3.;weights=q[k:z]
            base_proxy=np.divide(weights,typ,out=np.full(len(typ),np.nan),where=typ>0)
            denom=np.cumsum(base_proxy);numer=np.cumsum(weights)
            valid=np.logical_and.accumulate((weights>0)&np.isfinite(weights)&np.isfinite(base_proxy))&(denom>0)
            f['session_anchor'][k:z]=anchor;f['session_bar'][k:z]=np.arange(z-k)
            f['session_vwap'][k:z]=np.where(valid,numer/denom,np.nan)
            k=max(z,k+1)
    f['volume_multiple']=np.divide(q,f['prior_quote_median'],out=np.full(n,np.nan),where=f['prior_quote_median']>0)
    f['eligible'] &= source_helpers.observed_days(t)>=30
    return f

def align_context(t,br,_unused=None):
    return prior_cascade.align_context(t,br)

def opening_mask(cfg,raw,f,btc):
    t,o,_,_,c=raw;side=cfg['side'];atr=f['prior_atr'];vwap=f['session_vwap']
    prev=np.arange(len(t))-1;valid=prev>=0
    contiguous=np.zeros(len(t),bool);contiguous[valid]=t[valid]==t[prev[valid]]+BAR
    first_vwap=np.full(len(t),np.nan);first_vwap[valid]=vwap[prev[valid]]
    impulse=np.full(len(t),np.nan);impulse[valid]=side*(c[valid]-o[prev[valid]])
    breadth=f['up_fraction'] if side==1 else f['down_fraction']
    outer=f['clv']>=.75 if side==1 else f['clv']<=.25
    return (contiguous&f['eligible']&(f['session_bar']==1)&(atr>0)&np.isfinite(atr)
        &np.isfinite(vwap)&np.isfinite(first_vwap)&(side*(c-vwap)>0)
        &(side*(np.r_[np.nan,c[:-1]]-first_vwap)>0)&outer
        &(impulse>=cfg['displacement']*atr)&(f['n']>=opening_breadth.MIN_UNIVERSE)
        &(breadth>=cfg['breadth'])&(abs(f['r1'])<=.08)&(abs(btc['r1'])<=.02)
        &np.isfinite(btc['close']))

def intents(symbol,cfg,raw,f,btc,start,end):
    t,o,h,l,c=raw;rows,excluded,last=[],Counter(),-10000;side=cfg['side']
    breadth=f['up_fraction'] if side==1 else f['down_fraction']
    for e in np.flatnonzero(opening_mask(cfg,raw,f,btc)):
        if e<=last:continue
        atr=float(f['prior_atr'][e]);vwap=float(f['session_vwap'][e]);anchor=e-1
        impulse=float(side*(c[e]-o[anchor]));opening_extreme=float(max(h[anchor:e+1]) if side==1 else min(l[anchor:e+1]))
        pull=None;pull_retrace=None
        for k in range(e+1,min(e+5,len(t)-2)):
            if t[k]!=t[k-1]+BAR or f['session_anchor'][k]!=f['session_anchor'][e]:
                excluded['PULLBACK_PATH_GAP']+=1;break
            if side*(c[k]-vwap)<=0:excluded['VWAP_ACCEPTANCE_FAILED']+=1;break
            extreme=float(l[k] if side==1 else h[k]);retr=side*(opening_extreme-extreme)/impulse
            if retr>cfg['retracement']+1e-12:excluded['PULLBACK_TOO_DEEP']+=1;break
            if retr>=.10:pull=k;pull_retrace=float(retr);break
        if pull is None:continue
        confirm=None
        for i in range(pull+1,min(e+9,len(t)-1)):
            if t[i]!=t[i-1]+BAR or f['session_anchor'][i]!=f['session_anchor'][e]:
                excluded['CONFIRMATION_PATH_GAP']+=1;break
            if side*(c[i]-vwap)<=0:excluded['VWAP_ACCEPTANCE_FAILED']+=1;break
            extreme=float(l[i] if side==1 else h[i])
            retr=side*(opening_extreme-extreme)/impulse
            if retr>cfg['retracement']+1e-12:
                excluded['PULLBACK_TOO_DEEP']+=1;break
            directional=side*(c[i]-o[i])>0
            breaks=(c[i]>h[i-1]) if side==1 else (c[i]<l[i-1])
            if directional and breaks:confirm=i;break
        if confirm is None:continue
        j=confirm+1
        if j>=len(t) or not start<=t[j]<end:continue
        if t[j]!=t[confirm]+BAR:excluded['ENTRY_PATH_GAP']+=1;continue
        if f['session_anchor'][j]!=f['session_anchor'][e]:excluded['ENTRY_SESSION_GAP']+=1;continue
        entry=float(o[j])
        if not np.isfinite(entry) or entry<=0:excluded['INVALID_ENTRY']+=1;continue
        gap=side*(entry/c[confirm]-1)
        if gap>.005:excluded['ENTRY_CATCHUP_GAP']+=1;continue
        edge=float(np.min(l[pull:confirm+1]) if side==1 else np.max(h[pull:confirm+1]))
        structural_stop=edge-side*.25*atr
        distance=max(side*(entry-structural_stop),.005*entry);sl=entry-side*distance
        if side*(entry-structural_stop)<=0:excluded['STRUCTURAL_STOP_WRONG_SIDE']+=1;continue
        if distance/entry>.06:excluded['STOP_ABOVE_6PCT']+=1;continue
        if sl<=0:excluded['NONPOSITIVE_LEVEL']+=1;continue
        projection=opening_extreme+side*impulse
        if side*(projection-entry)<=0:excluded['PROJECTION_TARGET_WRONG_SIDE']+=1;continue
        last=confirm;risk=distance/entry
        row=dict(symbol=symbol,key=cfg['key'],signal_time=int(t[confirm]),decision_time=int(t[confirm]+BAR),
            entry_time=int(t[j]),entry_index=int(j),entry=entry,sl=float(sl),side=int(side),
            risk_pct=float(risk),score=float((impulse/atr)*breadth[e]/risk),
            atr_mult=3.,prior_atr=float(atr),buy_share=float(f['buy_share'][confirm]),
            volume_multiple=float(f['volume_multiple'][confirm]),displacement_atr=cfg['displacement'],
            breadth_threshold=cfg['breadth'],retracement_limit=cfg['retracement'],
            session_anchor_time=int(f['session_anchor'][e]),session_bar=int(f['session_bar'][e]),
            event_time=int(t[e]),event_vwap=vwap,opening_extreme=opening_extreme,opening_impulse=impulse,
            opening_breadth_n=int(f['n'][e]),opening_breadth_fraction=float(breadth[e]),
            pullback_time=int(t[pull]),pullback_retracement=pull_retrace,
            confirmation_age_bars=int(confirm-e),confirmation_time=int(t[confirm]),projection_target=float(projection),
            structural_stop=float(structural_stop),
            coin_r1=float(f['r1'][confirm]),btc_r1=float(btc['r1'][confirm]),clv=float(f['clv'][confirm]),known_entry_gap=float(gap))
        rows.append(row)
    return rows,excluded

def retain_failed_minute_original(meta,cache,evidence):
    """Preserve known original bytes even if canonical parsing excluded the month."""
    if meta.get('status')!='DATA_GAP' or not meta.get('original_zip_sha256'):return
    cache,evidence=Path(cache),Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
    cache.mkdir(parents=True,exist_ok=True);stem=meta['symbol']+'-'+meta['month']+'.original.zip'
    raw_path=cache/stem
    raw=raw_path.read_bytes() if raw_path.exists() else official._get(meta['source_url'])
    if raw is None:raise RuntimeError('known original error archive disappeared '+stem)
    target=evidence/stem;target.write_bytes(raw)
    if digest(target)!=meta['original_zip_sha256']:raise ValueError('immutable failed minute ZIP mismatch '+stem)
    raw_path.write_bytes(raw);meta['failed_raw_evidence_file']=stem
    if meta.get('official_checksum_text_sha256'):
        check_path=cache/(stem+'.CHECKSUM')
        check=check_path.read_bytes() if check_path.exists() else official._get(meta['checksum_url'])
        if check is None:raise RuntimeError('known error checksum disappeared '+stem)
        target=evidence/(stem+'.CHECKSUM');target.write_bytes(check)
        if digest(target)!=meta['official_checksum_text_sha256']:raise ValueError('immutable failed minute checksum mismatch '+stem)
        check_path.write_bytes(check);meta['failed_checksum_evidence_file']=stem+'.CHECKSUM'
    meta['failed_raw_preserved']=True

def audited_minutes(symbol,ts):
    data=minute_audit.audited_minutes(symbol,ts)
    key=symbol+'/'+pd.Timestamp(ts,unit='ms',tz='UTC').strftime('%Y-%m')
    meta=official.INPUTS[key]
    try:retain_failed_minute_original(meta,chronology.MINUTE_CACHE_DIR,minute_audit.MINUTE_RAW_DIR)
    except (ValueError,RuntimeError) as error:
        minute_audit.MINUTE_INPUTS[key]=dict(meta,preservation_error=str(error));raise
    minute_audit.MINUTE_INPUTS[key]=meta
    return data

def policy_rows(symbol,chosen,raw,f,btc,start,end):
    rows,counts,bad,grouped=[],Counter(),[],{}
    for p in chosen:grouped.setdefault(p['key'],[]).append(p)
    for cfgs in grouped.values():
        seeds,excluded=intents(symbol,cfgs[0],raw,f,btc,start,end)
        counts.update({cfgs[0]['key']+'/'+k:n for k,n in excluded.items()})
        for p in cfgs:
            for seed in seeds:
                risk=abs(seed['entry']-seed['sl'])
                if p['exit_type']=='OR1':tp=seed['projection_target']
                elif p['exit_type']=='R15':tp=seed['entry']+seed['side']*1.5*risk
                elif p['exit_type']=='R25':tp=seed['entry']+seed['side']*2.5*risk
                else:raise ValueError('unknown V18 exit')
                if seed['side']*(tp-seed['entry'])<=0:
                    counts[p['policy']+'/TARGET_WRONG_SIDE']+=1;continue
                tr=dict(seed,tp=float(tp),max_hold_bars=p['hold'])
                result=canonical.resolve(tr,raw,f,'TP2',end)
                counts[p['policy']+'/'+result['status']]+=1
                if result['status']!='RESOLVED':
                    bad.append(dict(symbol=symbol,policy=p['policy'],entry_time=tr['entry_time'],status=result['status']));continue
                row={k:v for k,v in tr.items() if k!='entry_index'}
                row.update(result,variant=p['policy'],policy=p['policy'],exit_type=p['exit_type'])
                row['hold_min']=(row['exit_time']-row['entry_time'])/60000
                exit_price=row['exit']*(1-row['side']*.001) if row['reason']=='SL' else row['exit']
                ratio=exit_price/row['entry'];net=row['side']*(ratio-1)-.002*(1+ratio)-.0002*row['hold_min']/1440
                row['net40_fraction']=float(net);row['net40_R']=float(net/account.stop_loss_fraction(row,.002));rows.append(row)
    return rows,counts,bad

def verify_source(source_check,paths,btc_path,context_path=CONTEXT):
    context=json.loads(context_path.read_text());source=json.loads(source_check.read_text())
    if source['status']!='VERIFIED' or len(source['shards'])!=1 or source['shards'][0] not in range(8):
        raise ValueError('one verified original shard required')
    if source['baseline_sha256']!=context['baseline_sha256'] or digest(btc_path)!=context['btc_sha256']:
        raise ValueError('frozen baseline/BTC mismatch')
    verified={x['symbol']:x['sha256'] for x in source['files']}
    if len(verified)!=len(source['files']) or len(paths)!=len(verified) or not paths:
        raise ValueError('source file missing/duplicate')
    actual={}
    for path in paths:
        symbol=path.name[:-7];sha=digest(path)
        if symbol in actual or verified.get(symbol)!=sha or context['expected_market_sha256'].get(symbol)!=sha:
            raise ValueError('source mismatch/duplicate '+symbol)
        actual[symbol]=sha
    if actual!=verified:raise ValueError('omitted verified source')
    return source,context,actual

def scan(data,btc_path,out,cache,stage,source_check,selection_path=None,context_path=CONTEXT,breadth_dir=None):
    paths=sorted(data.rglob('*.csv.gz'));source,context,sources=verify_source(source_check,paths,btc_path,context_path)
    start,end=source_helpers.interval(stage)
    global_frame,global_meta=opening_breadth.load_breadth(breadth_dir,stage,context_path)
    breadth_data_sha=global_meta['data_sha256'];breadth_meta_sha=digest(breadth_dir/'breadth_manifest.json')
    if stage=='DEV':chosen=policies()
    else:
        selected=json.loads(selection_path.read_text());chosen=selected['policies'];info={p['policy']:p for p in policies()}
        if selected['status']!='ACCOUNT_REPLAY_REQUIRED' or not chosen or selected['source_context_sha256']!=digest(context_path):
            raise ValueError('no consistent frozen selection')
        if any(p['policy'] not in info or any(p[k]!=v for k,v in info[p['policy']].items()) for p in chosen):
            raise ValueError('unregistered/altered selection')
    br,bq,bb=load(btc_path,end)
    if not len(br[0]):raise ValueError('empty BTC source')
    bf=features(br,bq,bb);out.mkdir(parents=True,exist_ok=True)
    chronology.MINUTE_CACHE_DIR=cache;chronology.chronology.one_min=audited_minutes
    chronology.chronology.CACHE.clear();official.INPUTS.clear();minute_audit.MINUTE_INPUTS.clear();minute_audit.MINUTE_SLICES.clear()
    minute_audit.SLICE_DIR=out/'minute_evidence';minute_audit.MINUTE_RAW_DIR=out/'minute_original_archives'
    minute_audit.SLICE_DIR.mkdir(exist_ok=True);minute_audit.MINUTE_RAW_DIR.mkdir(exist_ok=True)
    shutil.copyfile(source_check,out/'source_check.json');shutil.copyfile(context_path,out/'frozen_context.json')
    shutil.copyfile(breadth_dir/'breadth.csv.gz',out/'breadth.csv.gz')
    shutil.copyfile(breadth_dir/'breadth_manifest.json',out/'breadth_manifest.json')
    rows,counts,bad,coverage=[],Counter(),[],[]
    def checkpoint(complete):
        ledger=out/'independent_candidates.csv.gz'
        pd.DataFrame(rows,columns=COLUMNS).to_csv(ledger,index=False,compression=dict(method='gzip',mtime=0))
        pd.DataFrame(bad,columns=['symbol','policy','entry_time','status']).to_csv(out/'exclusions.csv',index=False)
        (out/'minute_inputs.json').write_text(json.dumps(list(minute_audit.MINUTE_INPUTS.values()),indent=2))
        (out/'minute_slices.json').write_text(json.dumps(list(minute_audit.MINUTE_SLICES.values()),indent=2))
        meta=dict(complete=complete,stage=stage,shard=source['shards'][0],counts=dict(counts),coverage=coverage,
            source_files=len(paths),market_hashes=sources,policies=chosen,ledger_rows=len(rows),ledger_sha256=digest(ledger),
            exclusions=len(bad),baseline_sha256=context['baseline_sha256'],source_check_sha256=digest(source_check),
            source_context_sha256=digest(context_path),btc_sha256=digest(btc_path),
            minute_months=len(minute_audit.MINUTE_INPUTS),minute_official_checksums_verified=sum(bool(x.get('checksum_verified')) for x in minute_audit.MINUTE_INPUTS.values()),
            breadth_data_sha256=breadth_data_sha,breadth_manifest_sha256=breadth_meta_sha,
            selection_sha256=digest(selection_path) if selection_path else None)
        (out/'scan_meta.json').write_text(json.dumps(meta,indent=2,allow_nan=False))
    try:
        for n,path in enumerate(paths,1):
            symbol=path.name[:-7];raw,q,buy=load(path,end)
            if not len(raw[0]) or raw[0][0]>=base.DEV_END-30*DAY:
                coverage.append(dict(symbol=symbol,status='NO_DEVELOPMENT_HISTORY',outcomes=0));continue
            f=features(raw,q,buy);f.update(opening_breadth.align(raw[0],global_frame));btc=align_context(raw[0],br,bf)
            eligible=f['eligible']&(raw[0]+BAR>=start)&(raw[0]+BAR<end)
            r,c,b=policy_rows(symbol,chosen,raw,f,btc,start,end)
            for row in r:row['split']=stage
            rows.extend(r);bad.extend(b);counts.update({stage+'/'+k:v for k,v in c.items()})
            coverage.append(dict(symbol=symbol,status='SCANNED',bars=len(raw[0]),outcomes=len(r),eligible_bars=int(eligible.sum()),
                missing_btc_level_bars=int((eligible&~np.isfinite(btc['close'])).sum()),
                unavailable_session_vwap_bars=int((eligible&~np.isfinite(f['session_vwap'])).sum()),
                unavailable_opening_breadth_bars=int((eligible&~np.isfinite(f['up_fraction'])).sum()),market_sha256=sources[symbol]))
            chronology.chronology.CACHE.clear()
            if n%8==0 or n==len(paths):
                checkpoint(False);print('V18_OPENING_IMPULSE_SCAN',stage,n,'/',len(paths),'parameterized_outcomes',len(rows),flush=True)
    except BaseException:
        checkpoint(False);raise
    checkpoint(True);print('V18_OPENING_IMPULSE_SCAN_DONE',stage,len(rows),'chronology_exclusions',len(bad),flush=True)

def select(parts,out,context_path=CONTEXT):
    out.mkdir(parents=True,exist_ok=True);paths=sorted(parts.rglob('independent_candidates.csv.gz'))
    if len(paths)!=8:raise ValueError('incomplete development shards')
    context=json.loads(context_path.read_text());source_context_sha=digest(context_path)
    originals,frames,sources,shards,breadth_hashes=[],[],{},set(),set()
    for path in paths:
        m=json.loads((path.parent/'scan_meta.json').read_text())
        global_frame,global_meta=opening_breadth.load_breadth(path.parent,'DEV',context_path)
        if (m['breadth_data_sha256']!=global_meta['data_sha256']
                or m['breadth_manifest_sha256']!=digest(path.parent/'breadth_manifest.json')
                or not m['complete'] or m['stage']!='DEV' or m['ledger_sha256']!=digest(path)
                or m['source_context_sha256']!=source_context_sha or m['btc_sha256']!=context['btc_sha256']
                or m['baseline_sha256']!=context['baseline_sha256'] or m['policies']!=policies()):
            raise ValueError('incomplete/altered development scan')
        breadth_hashes.add((m['breadth_data_sha256'],m['breadth_manifest_sha256']))
        if m['shard'] in shards:raise ValueError('duplicate development shard')
        shards.add(m['shard'])
        for symbol,sha in m['market_hashes'].items():
            if symbol in sources or context['expected_market_sha256'].get(symbol)!=sha:
                raise ValueError('source catalogue mismatch/duplicate')
            sources[symbol]=sha
        frame=pd.read_csv(path)
        if len(frame)!=m['ledger_rows']:raise ValueError('ledger count mismatch')
        originals.append(m);frames.append(frame)
    if shards!=set(range(8)) or sources!=context['expected_market_sha256'] or len(breadth_hashes)!=1:
        raise ValueError('incomplete global development universe')
    nonempty=[g for g in frames if len(g)];x=pd.concat(nonempty,ignore_index=True) if nonempty else frames[0].iloc[:0].copy()
    if x.duplicated(['symbol','variant','entry_time']).any():raise ValueError('duplicate development intent')
    if len(x) and (not x['split'].eq('DEV').all() or not x.entry_time.between(base.START,base.DEV_END-1).all()):
        raise ValueError('development stage leakage')
    info={p['policy']:p for p in policies()}
    if not set(x.variant).issubset(info):raise ValueError('unregistered policy')
    if not set(x.symbol).issubset(sources):raise ValueError('unknown development symbol')
    grouped={k:g for k,g in x.groupby('variant')};table=[]
    for name,p in info.items():
        g=grouped.get(name,x.iloc[:0]);years=pd.to_datetime(g.entry_time,unit='ms',utc=True).dt.tz_convert('Asia/Seoul').dt.year
        pn,rr=g.net40_fraction,g.net40_R;gain,loss=pn[pn>0].sum(),-pn[pn<0].sum()
        sym=g.groupby('symbol').net40_fraction.sum().clip(lower=0)
        r=dict(**p,n=len(g),symbols=g.symbol.nunique(),net40_mean_bp=float(pn.mean()*10000) if len(g) else None,
            net40_R_mean=float(rr.mean()) if len(g) else None,net40_pf=float(gain/loss) if loss else None,
            win_pct=float((pn>0).mean()*100) if len(g) else None,
            top_symbol_share_pct=float(sym.max()/sym.sum()*100) if sym.sum()>0 else 100.)
        for year,z in g.groupby(years):
            days=(z.entry_time.to_numpy(np.int64)+account.KOREA_OFFSET)//DAY;day_r=z.groupby(days).net40_R.mean()
            r.update({f'year_{year}_net40_R':float(z.net40_R.mean()),f'year_{year}_net40_mean_bp':float(z.net40_fraction.mean()*10000),
                f'year_{year}_day_R':float(day_r.mean()),f'year_{year}_days':len(day_r),f'year_{year}_n':len(z)})
        r['rejections']='|'.join(development_rejections(r));table.append(r)
    table.sort(key=lambda r:(-min(r.get(f'year_{y}_day_R',-999) for y in (2021,2022,2023)),r['policy']))
    chosen,used,side_count=[],set(),Counter()
    for r in table:
        if r['rejections'] or r['key'] in used or side_count[r['side']]>=3:continue
        chosen.append(dict(info[r['policy']],development_net40_R=r['net40_R_mean'],
            development_worst_year_day_R=min(r[f'year_{y}_day_R'] for y in (2021,2022,2023))))
        used.add(r['key']);side_count[r['side']]+=1
        if len(chosen)==6:break
    decision=dict(status='ACCOUNT_REPLAY_REQUIRED' if chosen else 'NO_DEVELOPMENT_POLICY_SURVIVOR',cells_examined=96,
        selected=chosen,policies=chosen,union_name='SESSION_OPENING_IMPULSE_ACCEPTANCE_UNION',source_context_sha256=source_context_sha,
        source_symbols=len(sources),economic_surviving_cells=sum(not r['rejections'] for r in table),
        rejection_counts=dict(Counter(reason for r in table for reason in r['rejections'].split('|') if reason)),
        note='Frozen session-opening impulse acceptance experiment. Overlapping diagnostics are not executable account growth.')
    pd.DataFrame(table).to_csv(out/'development_policy_cells.csv',index=False)
    (out/'selection.json').write_text(json.dumps(decision,indent=2,allow_nan=False));selection_hash=digest(out/'selection.json')
    selected_names={p['policy'] for p in chosen}
    for i,(path,m) in enumerate(zip(paths,originals)):
        d=pd.read_csv(path);d=d[d.variant.isin(selected_names)]
        kept={k:v for k,v in m['counts'].items() if any('/'+p+'/' in k for p in selected_names)}
        target=out/f'dev-{i}';target.mkdir(exist_ok=True);d.to_csv(target/'independent_candidates.csv.gz',index=False,compression='gzip')
        (target/'scan_meta.json').write_text(json.dumps(dict(complete=True,stage='DEV',shard=m['shard'],counts=kept,
            selection_sha256=selection_hash,source_development_ledger_sha256=m['ledger_sha256'],source_context_sha256=source_context_sha,
            coverage=m['coverage']),indent=2))
    print('V18_OPENING_IMPULSE_SELECT',json.dumps(decision),flush=True)

def accounts(data,parts,out,selection_path):
    canonical.accounts(data,parts,out,selection_path,('DEV','GATE'),expected_shards=16,all_kst_days=True)
    shutil.copyfile(out/'survivors.json',out/'shared_engine_decision.json')
    old=json.loads((out/'survivors.json').read_text())
    result=strict_accounts(pd.read_csv(out/'summary.csv'),pd.read_csv(out/'year_quarter.csv'),old['integrity'])
    (out/'survivors.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    print('V18_STRICT_ACCOUNT_DECISION',json.dumps(result),flush=True)

def smoke(out):
    """Synthetic source-file -> 8 scan shards -> 96-cell selection contract."""
    out.mkdir(parents=True,exist_ok=True);n=32*96;t=base.START+np.arange(n,dtype=np.int64)*BAR
    rng=np.random.default_rng(61202);x=np.cumsum(rng.normal(0,.001,n));btc_close=np.exp(10+x)
    def write(path,close):
        pd.DataFrame(dict(open_time=t,open=close,high=close*1.001,low=close*.999,close=close,
            quote_volume=np.full(n,1e6),taker_buy_quote=np.full(n,6e5))).to_csv(path,index=False,compression='gzip')
    bp=out/'BTCUSDT.csv.gz';write(bp,btc_close);hashes={}
    for i in range(8):
        d=out/'market'/str(i);d.mkdir(parents=True,exist_ok=True);path=d/f'X{i:02d}USDT.csv.gz'
        write(path,np.exp(1+1.2*x+rng.normal(0,.0001,n)));hashes[path.name[:-7]]=digest(path)
    cp=out/'synthetic-context.json';cp.write_text(json.dumps(dict(baseline_sha256='a'*64,btc_sha256=digest(bp),expected_market_sha256=hashes)))
    for i in range(8):
        symbol=f'X{i:02d}USDT';check=out/f'check-{i}.json'
        check.write_text(json.dumps(dict(status='VERIFIED',shards=[i],baseline_sha256='a'*64,files=[dict(symbol=symbol,sha256=hashes[symbol])])))
        opening_breadth.map_shard(out/'market'/str(i),bp,out/'breadth-parts'/str(i),'DEV',check,cp)
    opening_breadth.reduce_maps(out/'breadth-parts',out/'breadth','DEV',cp)
    for i in range(8):
        check=out/f'check-{i}.json'
        scan(out/'market'/str(i),bp,out/'parts'/str(i),out/'minute-cache','DEV',check,
            context_path=cp,breadth_dir=out/'breadth')
    select(out/'parts',out/'selected',cp)
    cells=pd.read_csv(out/'selected/development_policy_cells.csv');assert len(cells)==96 and cells.n.sum()==0
    report=dict(synthetic_only=True,scans=8,all96_cells_preserved=True,market_profitability_claim=False)
    (out/'smoke.json').write_text(json.dumps(report,indent=2));print('V18_SYNTHETIC_PIPELINE_PASS',json.dumps(report),flush=True)

def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('scan')
    for name in ('data','btc','out','minute-cache','source-check','breadth'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--stage',choices=('DEV','GATE'),required=True);p.add_argument('--selection',type=Path)
    p=sub.add_parser('select')
    for name in ('parts','out'):p.add_argument('--'+name,type=Path,required=True)
    p=sub.add_parser('accounts')
    for name in ('data','parts','out','selection'):p.add_argument('--'+name,type=Path,required=True)
    p=sub.add_parser('smoke');p.add_argument('--out',type=Path,required=True)
    a=ap.parse_args()
    if a.command=='scan':scan(a.data,a.btc,a.out,a.minute_cache,a.stage,a.source_check,a.selection,breadth_dir=a.breadth)
    elif a.command=='select':select(a.parts,a.out)
    elif a.command=='accounts':accounts(a.data,a.parts,a.out,a.selection)
    else:smoke(a.out)

if __name__=='__main__':main()
