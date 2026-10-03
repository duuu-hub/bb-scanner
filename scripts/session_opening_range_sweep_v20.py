"""Preregistered V20 session opening-range liquidity sweep reversal."""
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
from scripts.cross_sectional_leader_v9 import development_rejections,strict_accounts
from scripts.shock_confirmation_v3 import load

BAR,DAY=base.BAR,base.DAY
HOLDS,EXITS=(16,32),('MID','OPP','R20')
ROOT=Path(__file__).resolve().parents[1]
CONTEXT=ROOT/'research/session-opening-range-sweep-v20/FROZEN_CONTEXT.json'
LOG_PREFIX='V20_OPENING_RANGE_SWEEP'
digest=source_helpers.digest
COLUMNS=['symbol','key','signal_time','decision_time','entry_time','entry','sl','side',
    'risk_pct','score','atr_mult','prior_atr','buy_share','volume_multiple',
    'width_cap_atr','balance_bars','volume_threshold','session_anchor_time',
    'session_bar','event_time','event_vwap','opening_high','opening_low','opening_width',
    'opening_width_atr','balance_start_time','balance_end_time','sweep_age_bars',
    'sweep_time','sweep_penetration_atr','sweep_reentry_atr','sweep_extreme',
    'midpoint_target','opposite_target','structural_stop',
    'coin_r1','btc_r1','clv','known_entry_gap',
    'tp','max_hold_bars','status','exit_time','exit','reason','gross_return',
    'variant','policy','exit_type','hold_min','net40_fraction','net40_R','split']
def configurations():
    return [dict(key=f'ORSWEEP_S{side:+d}_W{int(width*10):02d}_B{balance:02d}_V{int(volume*100):03d}',
                 side=side,width_cap=width,balance_bars=balance,volume=volume)
            for side in (1,-1) for width in (1.,1.5) for balance in (4,8) for volume in (1.25,1.75)]

def policies():
    return [dict(**cfg,hold=h,exit_type=e,policy=f'{cfg["key"]}__H{h}__{e}')
            for cfg in configurations() for h in HOLDS for e in EXITS]

def development_rejections(r):
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

def opening_range_sweeps(cfg,raw,f,btc):
    """Freeze bars 0-1, accept two-sided value, then require a rejected sweep."""
    t,o,h,l,c=raw;side=cfg['side'];events=[];counts=Counter();blocked=-1
    for e in np.flatnonzero(f['session_bar']==1):
        if e<=blocked or e<1:continue
        if t[e]!=t[e-1]+BAR or f['session_bar'][e-1]!=0:
            counts['OPENING_PATH_GAP']+=1;continue
        atr=float(f['prior_atr'][e])
        if not (f['eligible'][e] and np.isfinite(atr) and atr>0 and np.isfinite(btc['close'][e])):
            continue
        opening_high=float(max(h[e-1],h[e]));opening_low=float(min(l[e-1],l[e]))
        width=opening_high-opening_low
        if width<.25*atr:
            counts['OPENING_RANGE_TOO_NARROW']+=1;continue
        if width>cfg['width_cap']*atr:
            counts['OPENING_RANGE_TOO_WIDE']+=1;continue
        b0=e+1;b1=e+cfg['balance_bars']
        if b1>=len(t)-5:continue
        valid=True;above=False;below=False
        for k in range(b0,b1+1):
            if t[k]!=t[k-1]+BAR or f['session_anchor'][k]!=f['session_anchor'][e]:
                counts['BALANCE_PATH_GAP']+=1;valid=False;break
            if c[k]>opening_high or c[k]<opening_low:
                counts['BALANCE_CLOSE_OUTSIDE_RANGE']+=1;valid=False;break
            vwap=f['session_vwap'][k]
            if not np.isfinite(vwap):
                counts['BALANCE_VWAP_MISSING']+=1;valid=False;break
            above |= c[k]>vwap;below |= c[k]<vwap
        if not valid:continue
        if not (above and below):
            counts['BALANCE_NOT_TWO_SIDED']+=1;continue
        found=False
        for j in range(b1+1,min(b1+5,len(t)-1)):
            if t[j]!=t[j-1]+BAR or f['session_anchor'][j]!=f['session_anchor'][e]:
                counts['SWEEP_PATH_GAP']+=1;break
            if not (f['eligible'][j] and np.isfinite(btc['close'][j])):
                continue
            extreme=float(l[j] if side==1 else h[j])
            penetration=(opening_low-extreme) if side==1 else (extreme-opening_high)
            reentry=(c[j]-opening_low) if side==1 else (opening_high-c[j])
            flow=f['buy_share'][j]<=.45 if side==1 else f['buy_share'][j]>=.55
            inside=opening_low<=c[j]<=opening_high
            if (penetration<.10*atr or reentry<.05*atr or not inside
                    or f['volume_multiple'][j]<cfg['volume'] or not flow):
                continue
            events.append(dict(index=int(j),event_index=int(e),event_atr=atr,
                event_vwap=float(f['session_vwap'][j]),opening_high=opening_high,
                opening_low=opening_low,opening_width=width,
                opening_width_atr=float(width/atr),balance_start=int(b0),balance_end=int(b1),
                sweep_age_bars=int(j-b1),sweep_penetration_atr=float(penetration/atr),
                sweep_reentry_atr=float(reentry/atr),sweep_extreme=extreme))
            counts['QUALIFIED_SWEEP']+=1;blocked=j;found=True;break
        if not found:counts['NO_QUALIFIED_SWEEP']+=1
    return events,counts

def intents(symbol,cfg,raw,f,btc,start,end):
    t,o,_,_,c=raw;rows,excluded=[],Counter()
    events,diagnostics=opening_range_sweeps(cfg,raw,f,btc);excluded.update(diagnostics)
    for event in events:
        i,e=event['index'],event['event_index'];j=i+1;side=cfg['side']
        if j>=len(t) or not start<=t[j]<end:continue
        if t[j]!=t[i]+BAR:excluded['ENTRY_PATH_GAP']+=1;continue
        if f['session_anchor'][j]!=f['session_anchor'][e]:excluded['ENTRY_SESSION_GAP']+=1;continue
        entry=float(o[j]);atr=event['event_atr']
        if not np.isfinite(entry) or entry<=0:excluded['INVALID_ENTRY']+=1;continue
        gap=side*(entry/c[i]-1)
        if gap>.005:excluded['ENTRY_CATCHUP_GAP']+=1;continue
        structural_stop=event['sweep_extreme']-side*.10*atr
        raw_distance=side*(entry-structural_stop)
        if raw_distance<=0:excluded['STRUCTURAL_STOP_WRONG_SIDE']+=1;continue
        distance=max(raw_distance,.005*entry);sl=entry-side*distance
        if distance/entry>.06:excluded['STOP_ABOVE_6PCT']+=1;continue
        if sl<=0:excluded['NONPOSITIVE_LEVEL']+=1;continue
        midpoint=(event['opening_high']+event['opening_low'])/2
        opposite=event['opening_high'] if side==1 else event['opening_low']
        risk=distance/entry
        row=dict(symbol=symbol,key=cfg['key'],signal_time=int(t[i]),decision_time=int(t[i]+BAR),
            entry_time=int(t[j]),entry_index=int(j),entry=entry,sl=float(sl),side=int(side),
            risk_pct=float(risk),score=float(event['sweep_penetration_atr']*np.sqrt(f['volume_multiple'][i])/risk),
            atr_mult=3.,prior_atr=float(atr),buy_share=float(f['buy_share'][i]),
            volume_multiple=float(f['volume_multiple'][i]),width_cap_atr=cfg['width_cap'],
            balance_bars=cfg['balance_bars'],volume_threshold=cfg['volume'],
            session_anchor_time=int(f['session_anchor'][e]),session_bar=int(f['session_bar'][e]),
            event_time=int(t[e]),event_vwap=event['event_vwap'],
            opening_high=event['opening_high'],opening_low=event['opening_low'],
            opening_width=event['opening_width'],opening_width_atr=event['opening_width_atr'],
            balance_start_time=int(t[event['balance_start']]),balance_end_time=int(t[event['balance_end']]),
            sweep_age_bars=event['sweep_age_bars'],sweep_time=int(t[i]),
            sweep_penetration_atr=event['sweep_penetration_atr'],sweep_reentry_atr=event['sweep_reentry_atr'],
            sweep_extreme=event['sweep_extreme'],midpoint_target=float(midpoint),opposite_target=float(opposite),
            structural_stop=float(structural_stop),coin_r1=float(f['r1'][i]),
            btc_r1=float(btc['r1'][i]),clv=float(f['clv'][i]),known_entry_gap=float(gap))
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
                if p['exit_type']=='MID':tp=seed['midpoint_target']
                elif p['exit_type']=='OPP':tp=seed['opposite_target']
                elif p['exit_type']=='R20':tp=seed['entry']+seed['side']*2.0*risk
                else:raise ValueError('unknown V20 exit')
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

def scan(data,btc_path,out,cache,stage,source_check,selection_path=None,context_path=CONTEXT):
    paths=sorted(data.rglob('*.csv.gz'));source,context,sources=verify_source(source_check,paths,btc_path,context_path)
    start,end=source_helpers.interval(stage)
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
            selection_sha256=digest(selection_path) if selection_path else None)
        (out/'scan_meta.json').write_text(json.dumps(meta,indent=2,allow_nan=False))
    try:
        for n,path in enumerate(paths,1):
            symbol=path.name[:-7];raw,q,buy=load(path,end)
            if not len(raw[0]) or raw[0][0]>=base.DEV_END-30*DAY:
                coverage.append(dict(symbol=symbol,status='NO_DEVELOPMENT_HISTORY',outcomes=0));continue
            f=features(raw,q,buy);btc=align_context(raw[0],br,bf)
            eligible=f['eligible']&(raw[0]+BAR>=start)&(raw[0]+BAR<end)
            r,c,b=policy_rows(symbol,chosen,raw,f,btc,start,end)
            for row in r:row['split']=stage
            rows.extend(r);bad.extend(b);counts.update({stage+'/'+k:v for k,v in c.items()})
            coverage.append(dict(symbol=symbol,status='SCANNED',bars=len(raw[0]),outcomes=len(r),eligible_bars=int(eligible.sum()),
                missing_btc_level_bars=int((eligible&~np.isfinite(btc['close'])).sum()),
                unavailable_session_vwap_bars=int((eligible&~np.isfinite(f['session_vwap'])).sum()),market_sha256=sources[symbol]))
            chronology.chronology.CACHE.clear()
            if n%8==0 or n==len(paths):
                checkpoint(False);print(LOG_PREFIX+'_SCAN',stage,n,'/',len(paths),'parameterized_outcomes',len(rows),flush=True)
    except BaseException:
        checkpoint(False);raise
    checkpoint(True);print(LOG_PREFIX+'_SCAN_DONE',stage,len(rows),'chronology_exclusions',len(bad),flush=True)

def select(parts,out,context_path=CONTEXT):
    out.mkdir(parents=True,exist_ok=True);paths=sorted(parts.rglob('independent_candidates.csv.gz'))
    if len(paths)!=8:raise ValueError('incomplete development shards')
    context=json.loads(context_path.read_text());source_context_sha=digest(context_path)
    originals,frames,sources,shards=[],[],{},set()
    for path in paths:
        m=json.loads((path.parent/'scan_meta.json').read_text())
        if (not m['complete'] or m['stage']!='DEV' or m['ledger_sha256']!=digest(path)
                or m['source_context_sha256']!=source_context_sha or m['btc_sha256']!=context['btc_sha256']
                or m['baseline_sha256']!=context['baseline_sha256'] or m['policies']!=policies()):
            raise ValueError('incomplete/altered development scan')
        if m['shard'] in shards:raise ValueError('duplicate development shard')
        shards.add(m['shard'])
        for symbol,sha in m['market_hashes'].items():
            if symbol in sources or context['expected_market_sha256'].get(symbol)!=sha:
                raise ValueError('source catalogue mismatch/duplicate')
            sources[symbol]=sha
        frame=pd.read_csv(path)
        if len(frame)!=m['ledger_rows']:raise ValueError('ledger count mismatch')
        originals.append(m);frames.append(frame)
    if shards!=set(range(8)) or sources!=context['expected_market_sha256']:
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
        selected=chosen,policies=chosen,union_name='SESSION_OPENING_RANGE_LIQUIDITY_SWEEP_UNION',source_context_sha256=source_context_sha,
        source_symbols=len(sources),economic_surviving_cells=sum(not r['rejections'] for r in table),
        rejection_counts=dict(Counter(reason for r in table for reason in r['rejections'].split('|') if reason)),
        note='Frozen session opening-range liquidity sweep reversal experiment. Overlapping diagnostics are not executable account growth.')
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
    print(LOG_PREFIX+'_SELECT',json.dumps(decision),flush=True)

def accounts(data,parts,out,selection_path):
    canonical.accounts(data,parts,out,selection_path,('DEV','GATE'),expected_shards=16,all_kst_days=True)
    shutil.copyfile(out/'survivors.json',out/'shared_engine_decision.json')
    old=json.loads((out/'survivors.json').read_text())
    result=strict_accounts(pd.read_csv(out/'summary.csv'),pd.read_csv(out/'year_quarter.csv'),old['integrity'])
    (out/'survivors.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    print('V20_STRICT_ACCOUNT_DECISION',json.dumps(result),flush=True)

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
        scan(out/'market'/str(i),bp,out/'parts'/str(i),out/'minute-cache','DEV',check,context_path=cp)
    select(out/'parts',out/'selected',cp)
    cells=pd.read_csv(out/'selected/development_policy_cells.csv');assert len(cells)==96 and cells.n.sum()==0
    report=dict(synthetic_only=True,scans=8,all96_cells_preserved=True,market_profitability_claim=False)
    (out/'smoke.json').write_text(json.dumps(report,indent=2));print('V20_SYNTHETIC_PIPELINE_PASS',json.dumps(report),flush=True)

def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('scan')
    for name in ('data','btc','out','minute-cache','source-check'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--stage',choices=('DEV','GATE'),required=True);p.add_argument('--selection',type=Path)
    p=sub.add_parser('select')
    for name in ('parts','out'):p.add_argument('--'+name,type=Path,required=True)
    p=sub.add_parser('accounts')
    for name in ('data','parts','out','selection'):p.add_argument('--'+name,type=Path,required=True)
    p=sub.add_parser('smoke');p.add_argument('--out',type=Path,required=True)
    a=ap.parse_args()
    if a.command=='scan':scan(a.data,a.btc,a.out,a.minute_cache,a.stage,a.source_check,a.selection)
    elif a.command=='select':select(a.parts,a.out)
    elif a.command=='accounts':accounts(a.data,a.parts,a.out,a.selection)
    else:smoke(a.out)

if __name__=='__main__':main()
