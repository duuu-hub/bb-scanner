"""Preregistered relative log-price residual/AR1 reversion; research only."""
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
FITS, HOLDS, EXITS = (672, 1344), (24, 96), ('MEAN', 'TP2', 'TRAIL')
ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / 'research/residual-reversion-v10/FROZEN_CONTEXT.json'
digest = source_helpers.digest
COLUMNS = ['symbol','key','signal_time','decision_time','entry_time','entry','sl',
    'side','risk_pct','score','atr_mult','prior_atr','buy_share','volume_multiple',
    'fit_bars','fit_cutoff_time','fit_last_bar_open_time','fit_observations',
    'price_beta','price_intercept','price_r2','residual_sigma','residual_rho',
    'residual_half_life_bars','residual_log_gap','residual_z','inward_residual_change',
    'equilibrium_price','btc_r16','flow_confirmation','known_entry_gap',
    'tp','max_hold_bars','status','exit_time','exit','reason','gross_return',
    'variant','policy','exit_type','hold_min','net40_fraction','net40_R','split']

def configurations():
    return [dict(key=f'RESIDUAL_S{side:+d}_W{w}_Z{z}_{flow}', side=side, fit=w, z=z, flow=flow)
            for side in (1,-1) for w in FITS for z in (2,3) for flow in ('PRICE_ONLY','FLOW55')]

def policies():
    return [dict(**cfg,hold=h,exit_type=e,policy=f'{cfg["key"]}__H{h}__{e}')
            for cfg in configurations() for h in HOLDS for e in EXITS]

def features(raw,q,buy):
    f = history.features(raw,q,buy)
    f['eligible'] &= source_helpers.observed_days(raw[0]) >= 30
    return f

def align_context(t, br, bf):
    btc = base.align_btc(t,br[0],bf)
    k = np.searchsorted(br[0],t); ok = k < len(br[0])
    ok[ok] &= br[0][k[ok]] == t[ok]
    btc['close'] = np.full(len(t),np.nan)
    btc['close'][ok] = br[4][k[ok]]
    return btc

def residual_features(raw, f, btc):
    """All regression/AR1 moments end at i-1; no current-price fitting."""
    t,_,_,_,close = raw; n=len(t);out={**f}
    names=('beta','intercept','r2','sigma','rho','half_life','residual','z','inward','equilibrium')
    for w in FITS:
        for name in names: out[f'{name}{w}']=np.full(n,np.nan)
    for a,b in base.segments(t):
        with np.errstate(invalid='ignore',divide='ignore'):
            xx=np.log(btc['close'][a:b]); yy=np.log(close[a:b])
        valid=np.isfinite(xx)&np.isfinite(yy)
        if not valid.any():continue
        # A past constant origin improves numerical accuracy without future input.
        first=np.flatnonzero(valid)[0]; xo,yo=xx[first],yy[first]
        x=pd.Series(np.where(valid,xx-xo,np.nan));y=pd.Series(np.where(valid,yy-yo,np.nan))
        xp,yp=x.shift(1),y.shift(1)
        for w in FITS:
            def mean(v,count=w):return v.rolling(count,min_periods=count).mean().shift(1).to_numpy()
            mx,my=mean(x),mean(y)
            vx,vy=mean(x*x)-mx*mx,mean(y*y)-my*my
            cv=mean(x*y)-mx*my
            beta=np.divide(cv,vx,out=np.full(b-a,np.nan),where=(vx>1e-12)&(vy>1e-12))
            alpha=my-beta*mx;variance=vy-2*beta*cv+beta*beta*vx
            sigma=np.sqrt(np.where(variance>=1e-12,variance,np.nan))
            r2=np.divide(cv*cv,vx*vy,out=np.full(b-a,np.nan),where=(vx>1e-12)&(vy>1e-12))
            r2=np.clip(r2,0.,1.)
            def cov(u,v):return mean(u*v,w-1)-mean(u,w-1)*mean(v,w-1)
            lagvar=cov(yp,yp)-2*beta*cov(xp,yp)+beta*beta*cov(xp,xp)
            adjacent=cov(y,yp)-beta*cov(y,xp)-beta*cov(x,yp)+beta*beta*cov(x,xp)
            rho=np.divide(adjacent,lagvar,out=np.full(b-a,np.nan),where=lagvar>1e-12)
            half=np.full(b-a,np.nan);valid_rho=(rho>0)&(rho<1)&np.isfinite(rho)
            half[valid_rho]=-np.log(2.)/np.log(rho[valid_rho])
            residual=y.to_numpy()-alpha-beta*x.to_numpy()
            old_residual=yp.to_numpy()-alpha-beta*xp.to_numpy()
            z=np.divide(residual,sigma,out=np.full(b-a,np.nan),where=sigma>=1e-6)
            full_alpha=alpha+yo-beta*xo
            with np.errstate(over='ignore',invalid='ignore'):
                equilibrium=np.exp(full_alpha+beta*xx)
            values=(beta,full_alpha,r2,sigma,rho,half,residual,z,residual-old_residual,equilibrium)
            for name,value in zip(names,values):out[f'{name}{w}'][a:b]=value
    return out

def mask(cfg,raw,f,btc):
    t,o,_,_,c=raw;side,w=cfg['side'],cfg['fit']
    good=(f['eligible']&(f['volume_multiple']>=1.)&(side*(c-o)>0)&(abs(f['r1'])<=.03)
          &(abs(btc['r16'])<=.03)&(f[f'beta{w}']>=.5)&(f[f'beta{w}']<=3.)
          &(f[f'r2{w}']>=.5)&(f[f'sigma{w}']>=1e-6)&(f[f'half_life{w}']>=1.)
          &(f[f'half_life{w}']<=96.)&(-side*f[f'z{w}']>=cfg['z'])
          &(-side*f[f'residual{w}']>=.015)&(side*f[f'inward{w}']>0))
    if cfg['flow']=='FLOW55':good &= f['buy_share']>=.55 if side==1 else f['buy_share']<=.45
    elif cfg['flow']!='PRICE_ONLY':raise ValueError('unknown flow confirmation')
    previous=np.r_[False,good[:-1]];contiguous=np.r_[False,np.diff(t)==BAR]
    return good&~(previous&contiguous)

def intents(symbol,cfg,raw,f,btc,start,end):
    t,o,_,_,c=raw; rows,excluded,last=[],Counter(),-10000
    for i in np.flatnonzero(mask(cfg,raw,f,btc)):
        j=i+1;side,w=cfg['side'],cfg['fit']
        if j>=len(t) or not start<=t[j]<end or i-last<32:continue
        if t[j]!=t[i]+BAR:
            excluded['ENTRY_PATH_GAP']+=1;continue
        entry=float(o[j]);atr=float(f['prior_atr'][i]);gap=side*(entry/c[i]-1)
        if not np.isfinite(entry) or entry<=0:
            excluded['INVALID_ENTRY']+=1;continue
        if gap>.005:
            excluded['ENTRY_CATCHUP_GAP']+=1;continue
        if not np.isfinite(atr) or atr<=0:
            excluded['INVALID_ATR']+=1;continue
        distance=max(2*atr,.0075*entry);stop=entry-side*distance
        if distance/entry>.08:
            excluded['STOP_ABOVE_8PCT']+=1;continue
        if stop<=0:
            excluded['NONPOSITIVE_LEVEL']+=1;continue
        equilibrium=float(f[f'equilibrium{w}'][i])
        if not np.isfinite(equilibrium) or equilibrium<=0 or side*(equilibrium-entry)<=0:
            excluded['EQUILIBRIUM_TARGET_PASSED']+=1;continue
        last=i;log_gap=-side*f[f'residual{w}'][i]
        rows.append(dict(symbol=symbol,key=cfg['key'],signal_time=int(t[i]),decision_time=int(t[i]+BAR),
            entry_time=int(t[j]),entry_index=int(j),entry=entry,sl=float(stop),side=int(side),
            risk_pct=float(distance/entry),score=float(log_gap*np.sqrt(f[f'r2{w}'][i])/(atr/c[i])),
            atr_mult=3.,prior_atr=atr,buy_share=float(f['buy_share'][i]),
            volume_multiple=float(f['volume_multiple'][i]),fit_bars=w,fit_cutoff_time=int(t[i-1]+BAR),
            fit_last_bar_open_time=int(t[i-1]),fit_observations=w,price_beta=float(f[f'beta{w}'][i]),
            price_intercept=float(f[f'intercept{w}'][i]),price_r2=float(f[f'r2{w}'][i]),
            residual_sigma=float(f[f'sigma{w}'][i]),residual_rho=float(f[f'rho{w}'][i]),
            residual_half_life_bars=float(f[f'half_life{w}'][i]),residual_log_gap=float(log_gap),
            residual_z=float(f[f'z{w}'][i]),inward_residual_change=float(side*f[f'inward{w}'][i]),
            equilibrium_price=equilibrium,btc_r16=float(btc['r16'][i]),flow_confirmation=cfg['flow'],known_entry_gap=float(gap)))
    return rows,excluded

def policy_rows(symbol,chosen,raw,f,btc,start,end):
    rows,counts,bad,grouped=[],Counter(),[],{}
    for p in chosen:grouped.setdefault(p['key'],[]).append(p)
    for cfgs in grouped.values():
        seeds,excluded=intents(symbol,cfgs[0],raw,f,btc,start,end)
        counts.update({cfgs[0]['key']+'/'+k:n for k,n in excluded.items()})
        for p in cfgs:
            for seed in seeds:
                tp=seed['equilibrium_price'] if p['exit_type']=='MEAN' else seed['entry']+seed['side']*2*abs(seed['entry']-seed['sl'])
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
            f=features(raw,q,buy);btc=align_context(raw[0],br,bf);f=residual_features(raw,f,btc)
            eligible=f['eligible']&(raw[0]+BAR>=start)&(raw[0]+BAR<end)
            r,c,b=policy_rows(symbol,chosen,raw,f,btc,start,end)
            for row in r:row['split']=stage
            rows.extend(r);bad.extend(b);counts.update({stage+'/'+k:v for k,v in c.items()})
            coverage.append(dict(symbol=symbol,status='SCANNED',bars=len(raw[0]),outcomes=len(r),eligible_bars=int(eligible.sum()),
                missing_btc_level_bars=int((eligible&~np.isfinite(btc['close'])).sum()),
                unavailable_fit_bars={str(w):int((eligible&~np.isfinite(f[f'beta{w}'])).sum()) for w in FITS},
                available_fit_bars={str(w):int((eligible&np.isfinite(f[f'beta{w}'])).sum()) for w in FITS},market_sha256=sources[symbol]))
            chronology.chronology.CACHE.clear()
            if n%8==0 or n==len(paths):
                checkpoint(False);print('V10_RESIDUAL_SCAN',stage,n,'/',len(paths),'parameterized_outcomes',len(rows),flush=True)
    except BaseException:
        checkpoint(False);raise
    checkpoint(True);print('V10_RESIDUAL_SCAN_DONE',stage,len(rows),'chronology_exclusions',len(bad),flush=True)

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
        selected=chosen,policies=chosen,union_name='RESIDUAL_REVERSION_UNION',source_context_sha256=source_context_sha,
        source_symbols=len(sources),economic_surviving_cells=sum(not r['rejections'] for r in table),
        rejection_counts=dict(Counter(reason for r in table for reason in r['rejections'].split('|') if reason)),
        note='Prior-fitted single-coin residual experiment, no market neutrality. Overlapping diagnostics are not executable account growth.')
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
    print('V10_RESIDUAL_SELECT',json.dumps(decision),flush=True)

def accounts(data,parts,out,selection_path):
    canonical.accounts(data,parts,out,selection_path,('DEV','GATE'),expected_shards=16,all_kst_days=True)
    shutil.copyfile(out/'survivors.json',out/'shared_engine_decision.json')
    old=json.loads((out/'survivors.json').read_text())
    result=strict_accounts(pd.read_csv(out/'summary.csv'),pd.read_csv(out/'year_quarter.csv'),old['integrity'])
    (out/'survivors.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    print('V10_STRICT_ACCOUNT_DECISION',json.dumps(result),flush=True)

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
    (out/'smoke.json').write_text(json.dumps(report,indent=2));print('V10_SYNTHETIC_PIPELINE_PASS',json.dumps(report),flush=True)

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
