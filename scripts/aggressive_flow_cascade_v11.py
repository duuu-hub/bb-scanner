"""Preregistered aggressive taker-flow cascade continuation; research only."""
from __future__ import annotations
import argparse, json, shutil, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from scripts import day_edge_lab as base
from scripts import day_edge_canonical as canonical
from scripts import relative_pullback_v1 as chronology
from scripts import relative_pullback_portfolio as account
from scripts import official_minute_provenance as official
from scripts import premium_absorption_v8 as minute_audit
from scripts import btc_factor_lag_v6 as history
from scripts import cross_sectional_ranks_v9 as source_helpers
from scripts.cross_sectional_leader_v9 import development_rejections, strict_accounts
from scripts.shock_confirmation_v3 import load

BAR, DAY = base.BAR, base.DAY
HOLDS, EXITS = (8, 24), ('TP15', 'TP25', 'TRAIL')
ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / 'research/aggressive-flow-cascade-v11/FROZEN_CONTEXT.json'
digest = source_helpers.digest
COLUMNS = ['symbol','key','signal_time','decision_time','entry_time','entry','sl',
    'side','risk_pct','score','atr_mult','prior_atr','buy_share','volume_multiple',
    'shock_threshold','breakout_bars','breakout_level','coin_r1','btc_r1',
    'idiosyncratic_r1','clv','prior_buy_share3','prior_flow_count3',
    'flow_confirmation','baseline_last_bar_open_time','known_entry_gap',
    'tp','max_hold_bars','status','exit_time','exit','reason','gross_return',
    'variant','policy','exit_type','hold_min','net40_fraction','net40_R','split']

def configurations():
    return [dict(key=f'CASCADE_S{side:+d}_T{int(threshold*1000):02d}_B{lookback}_{flow}',
                 side=side,threshold=threshold,lookback=lookback,flow=flow)
            for side in (1,-1) for threshold in (.015,.03) for lookback in (16,48)
            for flow in ('CURRENT65','PERSIST55')]

def policies():
    return [dict(**cfg,hold=h,exit_type=e,policy=f'{cfg["key"]}__H{h}__{e}')
            for cfg in configurations() for h in HOLDS for e in EXITS]

def features(raw,q,buy):
    f = history.features(raw,q,buy)
    t=raw[0];n=len(t)
    for key in ('prevh16','prevl16','prevh48','prevl48','prior_buy_share3','prior_long_count3','prior_short_count3'):
        f[key]=np.full(n,np.nan)
    for a,b in base.segments(t):
        h,l=raw[2][a:b],raw[3][a:b];share=pd.Series(f['buy_share'][a:b])
        for w in (16,48):
            f[f'prevh{w}'][a:b]=pd.Series(h).rolling(w,min_periods=w).max().shift(1).to_numpy()
            f[f'prevl{w}'][a:b]=pd.Series(l).rolling(w,min_periods=w).min().shift(1).to_numpy()
        f['prior_buy_share3'][a:b]=share.rolling(3,min_periods=3).mean().shift(1).to_numpy()
        f['prior_long_count3'][a:b]=(share>=.55).rolling(3,min_periods=3).sum().shift(1).to_numpy()
        f['prior_short_count3'][a:b]=(share<=.45).rolling(3,min_periods=3).sum().shift(1).to_numpy()
    f['eligible'] &= source_helpers.observed_days(t) >= 30
    return f

def align_context(t,br,_unused=None):
    """Exact BTC close and exact prior-close return; no interpolation/fill."""
    close=np.full(len(t),np.nan);previous=np.full(len(t),np.nan)
    k=np.searchsorted(br[0],t);ok=k<len(br[0]);ok[ok]&=br[0][k[ok]]==t[ok]
    close[ok]=br[4][k[ok]]
    pk=np.searchsorted(br[0],t-BAR);pok=pk<len(br[0]);pok[pok]&=br[0][pk[pok]]==(t-BAR)[pok]
    previous[pok]=br[4][pk[pok]]
    r1=np.divide(close,previous,out=np.full(len(t),np.nan),where=(close>0)&(previous>0))-1
    return dict(close=close,r1=r1)

def mask(cfg,raw,f,btc):
    t,o,_,_,c=raw;side,w=cfg['side'],cfg['lookback']
    level=f[f'prevh{w}'] if side==1 else f[f'prevl{w}']
    relative=side*(f['r1']-btc['r1'])
    good=(f['eligible']&(f['volume_multiple']>=3)&(side*(c-o)>0)
          &(side*f['r1']>=cfg['threshold'])&(side*f['r1']<=.08)
          &(relative>=.01)&(abs(btc['r1'])<=.015)&(side*(c-level)>0))
    good &= f['clv']>=.8 if side==1 else f['clv']<=.2
    if cfg['flow']=='CURRENT65':good &= f['buy_share']>=.65 if side==1 else f['buy_share']<=.35
    elif cfg['flow']=='PERSIST55':
        if side==1:good &= (f['buy_share']>=.60)&(f['prior_buy_share3']>=.55)&(f['prior_long_count3']>=2)
        else:good &= (f['buy_share']<=.40)&(f['prior_buy_share3']<=.45)&(f['prior_short_count3']>=2)
    else:raise ValueError('unknown flow confirmation')
    previous=np.r_[False,good[:-1]];contiguous=np.r_[False,np.diff(t)==BAR]
    return good&~(previous&contiguous)

def intents(symbol,cfg,raw,f,btc,start,end):
    t,o,_,_,c=raw;rows,excluded,last=[],Counter(),-10000
    for i in np.flatnonzero(mask(cfg,raw,f,btc)):
        j=i+1;side,w=cfg['side'],cfg['lookback']
        if j>=len(t) or not start<=t[j]<end or i-last<16:continue
        if t[j]!=t[i]+BAR:
            excluded['ENTRY_PATH_GAP']+=1;continue
        entry=float(o[j]);atr=float(f['prior_atr'][i]);gap=side*(entry/c[i]-1)
        if not np.isfinite(entry) or entry<=0:
            excluded['INVALID_ENTRY']+=1;continue
        if gap>.005:
            excluded['ENTRY_CATCHUP_GAP']+=1;continue
        if not np.isfinite(atr) or atr<=0:
            excluded['INVALID_ATR']+=1;continue
        distance=max(1.5*atr,.0075*entry);stop=entry-side*distance
        if distance/entry>.06:
            excluded['STOP_ABOVE_6PCT']+=1;continue
        if stop<=0:
            excluded['NONPOSITIVE_LEVEL']+=1;continue
        last=i;relative=side*(f['r1'][i]-btc['r1'][i]);imbalance=abs(2*f['buy_share'][i]-1)
        level=float(f[f'prevh{w}'][i] if side==1 else f[f'prevl{w}'][i])
        rows.append(dict(symbol=symbol,key=cfg['key'],signal_time=int(t[i]),decision_time=int(t[i]+BAR),
            entry_time=int(t[j]),entry_index=int(j),entry=entry,sl=float(stop),side=int(side),
            risk_pct=float(distance/entry),score=float(relative*np.sqrt(f['volume_multiple'][i])*imbalance/(atr/c[i])),
            atr_mult=3.,prior_atr=atr,buy_share=float(f['buy_share'][i]),
            volume_multiple=float(f['volume_multiple'][i]),shock_threshold=cfg['threshold'],breakout_bars=w,
            breakout_level=level,coin_r1=float(f['r1'][i]),btc_r1=float(btc['r1'][i]),
            idiosyncratic_r1=float(relative),clv=float(f['clv'][i]),
            prior_buy_share3=float(f['prior_buy_share3'][i]),
            prior_flow_count3=float(f['prior_long_count3'][i] if side==1 else f['prior_short_count3'][i]),
            flow_confirmation=cfg['flow'],baseline_last_bar_open_time=int(t[i-1]),known_entry_gap=float(gap)))
    return rows,excluded

def policy_rows(symbol,chosen,raw,f,btc,start,end):
    rows,counts,bad,grouped=[],Counter(),[],{}
    for p in chosen:grouped.setdefault(p['key'],[]).append(p)
    for cfgs in grouped.values():
        seeds,excluded=intents(symbol,cfgs[0],raw,f,btc,start,end)
        counts.update({cfgs[0]['key']+'/'+k:n for k,n in excluded.items()})
        for p in cfgs:
            for seed in seeds:
                multiple=1.5 if p['exit_type']=='TP15' else 2.5
                tp=seed['entry']+seed['side']*multiple*abs(seed['entry']-seed['sl'])
                tr=dict(seed,tp=float(tp),max_hold_bars=p['hold'])
                result=canonical.resolve(tr,raw,f,'TRAIL' if p['exit_type']=='TRAIL' else 'TP2',end)
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
    chronology.MINUTE_CACHE_DIR=cache;chronology.chronology.one_min=minute_audit.audited_minutes
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
                unavailable_breakout_bars={str(w):int((eligible&~np.isfinite(f[f'prevh{w}'])).sum()) for w in (16,48)},
                unavailable_prior_flow3_bars=int((eligible&~np.isfinite(f['prior_buy_share3'])).sum()),market_sha256=sources[symbol]))
            chronology.chronology.CACHE.clear()
            if n%8==0 or n==len(paths):
                checkpoint(False);print('V11_CASCADE_SCAN',stage,n,'/',len(paths),'parameterized_outcomes',len(rows),flush=True)
    except BaseException:
        checkpoint(False);raise
    checkpoint(True);print('V11_CASCADE_SCAN_DONE',stage,len(rows),'chronology_exclusions',len(bad),flush=True)

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
        selected=chosen,policies=chosen,union_name='AGGRESSIVE_FLOW_CASCADE_UNION',source_context_sha256=source_context_sha,
        source_symbols=len(sources),economic_surviving_cells=sum(not r['rejections'] for r in table),
        rejection_counts=dict(Counter(reason for r in table for reason in r['rejections'].split('|') if reason)),
        note='Short-horizon aggressive-flow continuation experiment. Overlapping diagnostics are not executable account growth.')
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
    print('V11_CASCADE_SELECT',json.dumps(decision),flush=True)

def accounts(data,parts,out,selection_path):
    canonical.accounts(data,parts,out,selection_path,('DEV','GATE'),expected_shards=16,all_kst_days=True)
    shutil.copyfile(out/'survivors.json',out/'shared_engine_decision.json')
    old=json.loads((out/'survivors.json').read_text())
    result=strict_accounts(pd.read_csv(out/'summary.csv'),pd.read_csv(out/'year_quarter.csv'),old['integrity'])
    (out/'survivors.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    print('V11_STRICT_ACCOUNT_DECISION',json.dumps(result),flush=True)

def smoke(out):
    """Synthetic source-file -> 8 scan shards -> 96-cell selection contract."""
    out.mkdir(parents=True,exist_ok=True);n=32*96;t=base.START+np.arange(n,dtype=np.int64)*BAR
    rng=np.random.default_rng(61003);x=np.cumsum(rng.normal(0,.001,n));btc_close=np.exp(10+x)
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
    (out/'smoke.json').write_text(json.dumps(report,indent=2));print('V11_SYNTHETIC_PIPELINE_PASS',json.dumps(report),flush=True)

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
