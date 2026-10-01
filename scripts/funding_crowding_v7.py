"""Preregistered paid-funding crowding/unwind research; no orders."""
from __future__ import annotations
import argparse,hashlib,json,shutil,sys
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
from scripts import funding_rate_archive as funding
from scripts.shock_confirmation_v3 import load

BAR,DAY=base.BAR,base.DAY
HOLDS=(24,96)
EXITS=('TP2','TP3','TRAIL')
MINUTE_INPUTS={}
MINUTE_SLICES={}
SLICE_DIR=None
MINUTE_RAW_DIR=None
COLUMNS=['symbol','key','signal_time','decision_time','entry_time','entry','sl','side',
    'risk_pct','score','atr_mult','prior_atr','buy_share','volume_multiple',
    'funding_rate8h','funding_rate_original','funding_calc_time',
    'funding_interval_hours','funding_age_hours','btc_r16',
    'confirmation','btc_filter','known_entry_gap','tp','max_hold_bars','status',
    'exit_time','exit','reason','gross_return','variant','policy','exit_type',
    'hold_min','net40_fraction','net40_R','split']

def configurations():
    return [dict(key=f'FUNDING_S{side:+d}_F{int(threshold*10000)}_{confirmation}_{btc}',
                 side=side,threshold=threshold,confirmation=confirmation,btc=btc)
            for side in (1,-1) for threshold in (.0005,.001)
            for confirmation in ('RECLAIM','FLOW') for btc in ('ANY','ALIGN4H')]

def policies():
    return [dict(**cfg,hold=h,exit_type=e,policy=f'{cfg["key"]}__H{h}__{e}')
            for cfg in configurations() for h in HOLDS for e in EXITS]

def features(raw,q,buy):
    f=base.features(raw,q)
    t,o,h,l,c=raw
    for key in ('prior_atr','volume_multiple'):
        f[key]=np.full(len(t),np.nan)
    f['buy_share']=np.divide(buy,q,out=np.full(len(q),np.nan),where=q>0)
    for a,b in base.segments(t):
        cc,hh,ll,qq=c[a:b],h[a:b],l[a:b],q[a:b]
        prev=pd.Series(cc).shift(1).to_numpy()
        tr=pd.Series(np.maximum(hh-ll,np.maximum(abs(hh-prev),abs(ll-prev))))
        f['prior_atr'][a:b]=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean().shift(1).to_numpy()
        oldq=pd.Series(qq).rolling(96,min_periods=96).mean().shift(1).to_numpy()
        f['volume_multiple'][a:b]=np.divide(qq,oldq,out=np.full(len(cc),np.nan),where=oldq>0)
    return f

def mask(cfg,raw,f,btc):
    t,o,h,l,c=raw;side=cfg['side']
    result=(f['eligible']&f['funding_available']&(f['volume_multiple']>=1.25)
            &(side*(c-o)>0)&(side*f['r1']>0)
            &(-side*f['funding_rate8h']>=cfg['threshold']))
    if cfg['confirmation']=='RECLAIM':
        result &= c>f['prevh4'] if side==1 else c<f['prevl4']
    elif cfg['confirmation']=='FLOW':
        result &= f['buy_share']>=.55 if side==1 else f['buy_share']<=.45
    else: raise ValueError('unknown funding confirmation')
    if cfg['btc']=='ALIGN4H': result &= side*btc['r16']>0
    elif cfg['btc']!='ANY': raise ValueError('unknown BTC filter')
    prior=np.r_[False,result[:-1]];contiguous=np.r_[False,np.diff(t)==BAR]
    return result&~(prior&contiguous)

def intents(symbol,cfg,raw,f,btc,start,end):
    t,o,h,l,c=raw;out,excluded=[],Counter();last=-100
    for i in np.flatnonzero(mask(cfg,raw,f,btc)):
        j=i+1
        if j>=len(t) or not start<=t[j]<end or i-last<16:continue
        if t[j]!=t[i]+BAR:
            excluded['ENTRY_PATH_GAP']+=1;continue
        side=cfg['side'];entry=float(o[j]);atr=f['prior_atr'][i]
        entry_gap=side*(entry/c[i]-1)
        if entry_gap>.005:
            excluded['ENTRY_CATCHUP_GAP']+=1;continue
        if not np.isfinite(atr) or atr<=0:
            excluded['INVALID_ATR']+=1;continue
        distance=max(2*atr,.005*entry)
        if distance/entry>.08:
            excluded['STOP_ABOVE_8PCT']+=1;continue
        stop=entry-side*distance
        if stop<=0:
            excluded['NONPOSITIVE_LEVEL']+=1;continue
        crowding=-side*f['funding_rate8h'][i];last=i
        out.append(dict(symbol=symbol,key=cfg['key'],signal_time=int(t[i]),
            decision_time=int(t[i]+BAR),entry_time=int(t[j]),entry_index=int(j),
            entry=entry,sl=float(stop),side=int(side),risk_pct=float(distance/entry),
            score=float(crowding*np.sqrt(f['volume_multiple'][i])/(atr/c[i])),atr_mult=3.,prior_atr=float(atr),
            buy_share=float(f['buy_share'][i]),
            volume_multiple=float(f['volume_multiple'][i]),
            funding_rate8h=float(f['funding_rate8h'][i]),
            funding_rate_original=float(f['funding_rate_original'][i]),
            funding_calc_time=int(f['funding_calc_time'][i]),
            funding_interval_hours=float(f['funding_interval_hours'][i]),
            funding_age_hours=float(f['funding_age_hours'][i]),btc_r16=float(btc['r16'][i]),
            confirmation=cfg['confirmation'],btc_filter=cfg['btc'],known_entry_gap=float(entry_gap)))
    return out,excluded

def retain_minute_original(meta,cache,evidence):
    """Retain original monthly bytes, including when a verified parsed cache was used."""
    if meta['status']!='VALIDATED_OFFICIAL_CHECKSUM': return
    cache=Path(cache);evidence=Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
    stem=meta['symbol']+'-'+meta['month']+'.original.zip'
    raw_path=cache/stem;check_path=cache/(stem+'.CHECKSUM')
    if raw_path.exists() and check_path.exists():
        raw,check=raw_path.read_bytes(),check_path.read_bytes()
    else:
        raw=official._get(meta['source_url']);check=official._get(meta['checksum_url'])
        if raw is None or check is None: raise RuntimeError('original verified minute archive unavailable '+stem)
    for name,content in ((stem,raw),(stem+'.CHECKSUM',check)):
        (evidence/name).write_bytes(content)
    if (hashlib.sha256(raw).hexdigest()!=meta['original_zip_sha256']
        or hashlib.sha256(check).hexdigest()!=meta['official_checksum_text_sha256']):
        raise ValueError('immutable original minute archive mismatch '+stem)
    raw_path.write_bytes(raw);check_path.write_bytes(check)

def audited_minutes(symbol,ts):
    ym=pd.Timestamp(ts,unit='ms',tz='UTC').strftime('%Y-%m');key=(symbol,ym)
    if len(chronology.chronology.CACHE)>=6:chronology.chronology.CACHE.clear()
    if key in chronology.chronology.CACHE:data=chronology.chronology.CACHE[key]
    else:
        data=official.load_month(symbol,ts,chronology.MINUTE_CACHE_DIR)
        meta=official.INPUTS[symbol+'/'+ym]
        if MINUTE_RAW_DIR is not None:
            try:retain_minute_original(meta,chronology.MINUTE_CACHE_DIR,MINUTE_RAW_DIR)
            except (ValueError,RuntimeError) as error:
                MINUTE_INPUTS[symbol+'/'+ym]=dict(meta,preservation_error=str(error))
                raise
        chronology.chronology.CACHE[key]=data
    MINUTE_INPUTS[symbol+'/'+ym]=official.INPUTS[symbol+'/'+ym]
    request=symbol+'/'+str(ts)
    if request not in MINUTE_SLICES:
        rec=dict(symbol=symbol,parent_open_time=int(ts),month_key=symbol+'/'+ym)
        if len(data)==2 and isinstance(data[0],str):
            rec.update(status='DATA_GAP',message=str(data[1]),bars=0)
        else:
            a=np.searchsorted(data[0],ts);z=np.searchsorted(data[0],ts+BAR)
            arrays=tuple(x[a:z].copy() for x in data)
            complete=len(arrays[0])==15 and arrays[0][0]==ts and arrays[0][-1]==ts+14*60000
            rec.update(status='VALIDATED_SLICE' if complete else 'DATA_GAP',bars=len(arrays[0]),
                array_sha256=official.array_digest(arrays))
            if SLICE_DIR is not None:
                path=SLICE_DIR/f'{symbol}-{ts}.npz'
                np.savez_compressed(path,t=arrays[0],o=arrays[1],h=arrays[2],l=arrays[3])
                rec.update(file=path.name,npz_sha256=official.digest(path))
        MINUTE_SLICES[request]=rec
    return data

def policy_rows(symbol,chosen,raw,f,btc,start,end):
    rows,counts,bad=[],Counter(),[]
    grouped={}
    for p in chosen: grouped.setdefault(p['key'],[]).append(p)
    for cfgs in grouped.values():
        cfg=cfgs[0];entries,ex=intents(symbol,cfg,raw,f,btc,start,end)
        for name,n in ex.items(): counts[f'{cfg["key"]}/{name}']+=n
        for policy in cfgs:
            kind=policy['exit_type'];r_mult=3 if kind=='TP3' else 2
            for tr0 in entries:
                tr={**tr0,'tp':float(tr0['entry']+tr0['side']*r_mult*abs(tr0['entry']-tr0['sl'])),
                    'max_hold_bars':policy['hold']}
                result=canonical.resolve(tr,raw,f,'TP2' if kind.startswith('TP') else 'TRAIL',end)
                counts[f'{policy["policy"]}/{result["status"]}']+=1
                if result['status']!='RESOLVED':
                    bad.append(dict(symbol=symbol,policy=policy['policy'],
                                    entry_time=tr['entry_time'],status=result['status']))
                    continue
                row={k:v for k,v in tr.items() if k!='entry_index'}
                row.update(result,variant=policy['policy'],policy=policy['policy'],exit_type=kind)
                row['hold_min']=(row['exit_time']-row['entry_time'])/60000
                actual_exit=row['exit']*(1-row['side']*.001) if row['reason']=='SL' else row['exit']
                ratio=actual_exit/row['entry']
                net40=row['side']*(ratio-1)-.002*(1+ratio)-.0002*(row['exit_time']-row['entry_time'])/DAY
                row['net40_fraction']=float(net40)
                row['net40_R']=float(net40/account.stop_loss_fraction(row,.002))
                rows.append(row)
    return rows,counts,bad

def scan(data,btc_path,out,cache,funding_cache,stage,selection_path=None):
    paths=sorted(data.rglob('*.csv.gz'))
    if not paths: raise ValueError('empty frozen source shard')
    out.mkdir(parents=True,exist_ok=True)
    if stage=='DEV':
        chosen=policies();start,end=base.START,base.DEV_END
    else:
        chosen=json.loads(selection_path.read_text())['policies']
        if not chosen: raise ValueError('no frozen policies for GATE')
        start,end=base.DEV_END,base.GATE_END
    br,bq,bb=load(btc_path,end)
    if not len(br[0]): raise ValueError('empty BTC context')
    bf=features(br,bq,bb)
    chronology.MINUTE_CACHE_DIR=cache;chronology.chronology.one_min=audited_minutes
    MINUTE_INPUTS.clear();MINUTE_SLICES.clear();official.INPUTS.clear();funding.INPUTS.clear()
    global SLICE_DIR,MINUTE_RAW_DIR
    SLICE_DIR=out/'minute_evidence';SLICE_DIR.mkdir(parents=True,exist_ok=True)
    MINUTE_RAW_DIR=out/'minute_original_archives';MINUTE_RAW_DIR.mkdir(parents=True,exist_ok=True)
    rows,counts,bad,coverage=[],Counter(),[],[]
    seen=set()
    def checkpoint(complete):
        pd.DataFrame(rows,columns=COLUMNS).to_csv(out/'independent_candidates.csv.gz',index=False,compression='gzip')
        pd.DataFrame(bad,columns=['symbol','policy','entry_time','status']).to_csv(out/'exclusions.csv',index=False)
        (out/'minute_inputs.json').write_text(json.dumps(list(MINUTE_INPUTS.values()),indent=2))
        (out/'minute_slices.json').write_text(json.dumps(list(MINUTE_SLICES.values()),indent=2))
        (out/'funding_inputs.json').write_text(json.dumps(list(funding.INPUTS.values()),indent=2))
        meta=dict(complete=complete,stage=stage,counts=dict(counts),coverage=coverage,
            source_files=len(paths),policies=chosen,minute_months=len(MINUTE_INPUTS),
            funding_months=len(funding.INPUTS),
            funding_validated_months=sum(m['status']=='VALIDATED_OFFICIAL_CHECKSUM' for m in funding.INPUTS.values()),
            funding_gap_months=sum(m['status']=='FUNDING_DATA_GAP' for m in funding.INPUTS.values()),
            minute_original_zip_hashes_retained=sum(bool(m.get('original_zip_sha256')) for m in MINUTE_INPUTS.values()),
            minute_official_checksums_verified=sum(bool(m.get('checksum_verified')) for m in MINUTE_INPUTS.values()),
            selection_sha256=hashlib.sha256(selection_path.read_bytes()).hexdigest() if selection_path else None,
            btc_sha256=hashlib.sha256(btc_path.read_bytes()).hexdigest())
        (out/'scan_meta.json').write_text(json.dumps(meta,indent=2))
    try:
        for n,path in enumerate(paths,1):
            sym=path.name[:-7]
            if sym in seen: raise ValueError('duplicate frozen input '+sym)
            seen.add(sym)
            raw,q,buy=load(path,end)
            # The authoritative loader validates a nonempty original file;
            # truncation can legitimately leave a post-2023 listing empty.
            if not len(raw[0]): continue
            if raw[0][0]>=base.DEV_END-30*DAY: continue
            f=features(raw,q,buy);btc=base.align_btc(raw[0],br[0],bf)
            eligible=f['eligible']&((raw[0]+BAR)>=start)&((raw[0]+BAR)<end)
            if eligible.any():
                data_funding,valid_months=funding.load_symbol(sym,raw[0],start,end,funding_cache,out/'funding_evidence')
            else:
                data_funding=(np.array([],np.int64),np.array([],float),np.array([],float));valid_months=set()
            f.update(funding.asof(raw[0],data_funding,valid_months,end))
            decision_year=pd.to_datetime(raw[0]+BAR,unit='ms',utc=True).tz_convert('Asia/Seoul').year
            year_coverage={str(year):dict(eligible=int((eligible&(decision_year==year)).sum()),
                known=int((eligible&f['funding_available']&(decision_year==year)).sum()))
                for year in sorted(set(decision_year[eligible]))}
            r,c,b=policy_rows(sym,chosen,raw,f,btc,start,end)
            for row in r: row['split']=stage
            rows.extend(r);counts.update({stage+'/'+k:v for k,v in c.items()});bad.extend(b)
            coverage.append(dict(symbol=sym,bars=len(raw[0]),outcomes=len(r),
                eligible_bars=int(eligible.sum()),
                known_funding_bars=int((eligible&f['funding_available']).sum()),
                unavailable_funding_bars=int((eligible&~f['funding_available']).sum()),
                funding_year_coverage=year_coverage,valid_funding_months=sorted(valid_months),
                unmatched_btc_returns=int((~np.isfinite(btc['r1'])).sum()),
                source_url=f'https://data.binance.vision/?prefix=data/futures/um/monthly/klines/{sym}/15m/',
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            chronology.chronology.CACHE.clear()
            if n%8==0 or n==len(paths):
                checkpoint(False);print('FUNDING_SCAN',stage,n,'/',len(paths),'outcomes',len(rows),flush=True)
        if not coverage: raise ValueError('no eligible frozen-market coverage')
    except BaseException:
        checkpoint(False);raise
    checkpoint(True)
    print('FUNDING_SCAN_DONE',stage,len(rows),'excluded',len(bad),flush=True)

def funding_coverage(originals):
    totals={str(y):dict(eligible=0,known=0) for y in (2022,2023)};seen=set()
    for meta in originals:
        for coin in meta['coverage']:
            if coin['symbol'] in seen: raise ValueError('duplicate funding coverage symbol')
            seen.add(coin['symbol'])
            for year,v in coin['funding_year_coverage'].items():
                if not (0<=v['known']<=v['eligible']): raise ValueError('invalid funding coverage counts')
                if year in totals:
                    for key in ('eligible','known'):totals[year][key]+=v[key]
    for v in totals.values():
        v['unavailable']=v['eligible']-v['known']
        v['fraction']=v['known']/v['eligible'] if v['eligible'] else None
    passed=all(v['eligible']>0 and v['known']*100>=95*v['eligible'] for v in totals.values())
    return dict(passed=passed,minimum_fraction=.95,years=totals,symbols=len(seen))

def select(parts,out):
    out.mkdir(parents=True,exist_ok=True)
    paths=sorted(parts.rglob('independent_candidates.csv.gz'))
    if len(paths)!=8: raise ValueError('incomplete development shards')
    originals=[]
    for path in paths:
        m=json.loads((path.parent/'scan_meta.json').read_text())
        if not m['complete'] or m['stage']!='DEV': raise ValueError('incomplete development scan')
        originals.append(m)
    coverage_gate=funding_coverage(originals)
    frames=[pd.read_csv(p) for p in paths]
    nonempty=[g for g in frames if len(g)]
    x=pd.concat(nonempty,ignore_index=True) if nonempty else frames[0].iloc[:0].copy()
    if x.duplicated(['symbol','variant','entry_time']).any(): raise ValueError('duplicate development intents')
    info={p['policy']:p for p in policies()}
    grouped={k:g for k,g in x.groupby('variant')}
    table=[]
    for name,p in info.items():
        g=grouped.get(name,x.iloc[:0])
        years=pd.to_datetime(g.entry_time,unit='ms',utc=True).dt.tz_convert('Asia/Seoul').dt.year
        pn=g.net40_fraction;rr=g.net40_R
        gain,loss=pn[pn>0].sum(),-pn[pn<0].sum()
        sym=g.groupby('symbol').net40_fraction.sum().clip(lower=0)
        r=dict(**p,n=len(g),symbols=g.symbol.nunique(),
            net40_mean_bp=float(pn.mean()*10000) if len(g) else None,
            net40_R_mean=float(rr.mean()) if len(g) else None,
            net40_pf=float(gain/loss) if loss else None,
            win_pct=float((pn>0).mean()*100) if len(g) else None,
            top_symbol_share_pct=float(sym.max()/sym.sum()*100) if sym.sum()>0 else 100.)
        for year,z in g.groupby(years):
            days=(z.entry_time.to_numpy(np.int64)+account.KOREA_OFFSET)//DAY
            dayR=z.groupby(days).net40_R.mean()
            r[f'year_{year}_net40_R']=float(z.net40_R.mean())
            r[f'year_{year}_net40_mean_bp']=float(z.net40_fraction.mean()*10000)
            r[f'year_{year}_day_R']=float(dayR.mean())
            r[f'year_{year}_days']=len(dayR)
            r[f'year_{year}_n']=len(z)
        table.append(r)
    table.sort(key=lambda r:(-min(r.get('year_2022_day_R',-999),r.get('year_2023_day_R',-999)),r['policy']))
    chosen=[];used=set();side_count=Counter()
    for r in table:
        if not coverage_gate['passed']: continue
        if (r['n']<300 or r['symbols']<10 or r['net40_mean_bp'] is None
            or r['net40_mean_bp']<=0 or r['net40_R_mean']<=0 or r['top_symbol_share_pct']>30
            or min(r.get('year_2022_n',0),r.get('year_2023_n',0))<80
            or min(r.get('year_2022_days',0),r.get('year_2023_days',0))<60
            or min(r.get('year_2022_net40_R',-999),r.get('year_2023_net40_R',-999))<=0
            or min(r.get('year_2022_net40_mean_bp',-999),r.get('year_2023_net40_mean_bp',-999))<=0):
            continue
        if r['key'] in used or side_count[r['side']]>=3: continue
        chosen.append({**info[r['policy']],'development_net40_R':r['net40_R_mean'],
                       'development_worst_year_day_R':min(r['year_2022_day_R'],r['year_2023_day_R'])})
        used.add(r['key']);side_count[r['side']]+=1
        if len(chosen)==6: break
    decision=dict(status=('SOURCE_COVERAGE_INSUFFICIENT' if not coverage_gate['passed'] else
        'ACCOUNT_REPLAY_REQUIRED' if chosen else 'NO_DEVELOPMENT_POLICY_SURVIVOR'),
        cells_examined=96,selected=chosen,policies=chosen,union_name='FUNDING_UNION',
        funding_coverage=coverage_gate,
        note='Independent overlapping policies and active-entry-date means are diagnostics, not account profits.')
    pd.DataFrame(table).to_csv(out/'development_policy_cells.csv',index=False)
    (out/'selection.json').write_text(json.dumps(decision,indent=2))
    selection_hash=hashlib.sha256((out/'selection.json').read_bytes()).hexdigest()
    selected_names={p['policy'] for p in chosen}
    for i,(path,original) in enumerate(zip(paths,originals)):
        d=pd.read_csv(path);d=d[d.variant.isin(selected_names)]
        kept={k:v for k,v in original['counts'].items() if any('/'+p+'/' in k for p in selected_names)}
        target=out/f'dev-{i}';target.mkdir()
        d.to_csv(target/'independent_candidates.csv.gz',index=False,compression='gzip')
        (target/'scan_meta.json').write_text(json.dumps(dict(complete=True,stage='DEV',
            counts=kept,selection_sha256=selection_hash,
            source_development_shard=str(path),
            source_development_ledger_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            coverage=original.get('coverage',[]))))
    print('FUNDING_SELECT',json.dumps(decision),flush=True)

def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('scan')
    for n in ('data','btc','out','minute-cache','funding-cache'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--stage',choices=('DEV','GATE'),required=True)
    p.add_argument('--selection',type=Path)
    p=sub.add_parser('select')
    p.add_argument('--parts',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=ap.parse_args()
    if a.command=='scan':scan(a.data,a.btc,a.out,a.minute_cache,a.funding_cache,a.stage,a.selection)
    else:select(a.parts,a.out)

if __name__=='__main__':main()
