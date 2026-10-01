"""Preregistered compression-to-expansion trend research; no trading orders."""
from __future__ import annotations
import argparse, hashlib, json, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from scripts import day_edge_lab as base
from scripts import day_edge_canonical as canonical
from scripts import relative_pullback_v1 as chronology
from scripts import relative_pullback_portfolio as account
from scripts.shock_confirmation_v3 import load

BAR,DAY=base.BAR,base.DAY
HOLDS=(24,48)
EXITS=('TP2','TP3','TRAIL')
MINUTE_INPUTS={}
COLUMNS=['symbol','key','signal_time','decision_time','entry_time','entry','sl','side',
    'risk_pct','score','atr_mult','buy_share','compression_ratio','volume_multiple',
    'breakout_window','breakout_level','btc_r16','tp','max_hold_bars','status',
    'exit_time','exit','reason','gross_return','variant','policy','exit_type',
    'hold_min','net40_fraction','net40_R','split']

def configurations():
    return [dict(key=f'EXPAND_S{side:+d}_W{window}_C{int(cut*100)}_{btc}',
                 side=side,window=window,compression=cut,btc=btc)
            for side in (1,-1) for window in (16,48)
            for cut in (.50,.75) for btc in ('ANY','ALIGN4H')]

def policies():
    return [dict(**cfg,hold=h,exit_type=e,policy=f'{cfg["key"]}__H{h}__{e}')
            for cfg in configurations() for h in HOLDS for e in EXITS]

def features(raw,q,buy):
    f=base.features(raw,q)
    t,o,h,l,c=raw
    for key in ('prior_atr','compression','volume_multiple','prior_ema50',
                'prior_ema_slope','low5','high5','prevh48','prevl48'):
        f[key]=np.full(len(t),np.nan)
    f['buy_share']=np.divide(buy,q,out=np.full(len(q),np.nan),where=q>0)
    for a,b in base.segments(t):
        cc,hh,ll,qq=c[a:b],h[a:b],l[a:b],q[a:b]
        prev=pd.Series(cc).shift(1).to_numpy()
        tr=pd.Series(np.maximum(hh-ll,np.maximum(abs(hh-prev),abs(ll-prev))))
        slow=tr.rolling(96,min_periods=96).mean().shift(1).to_numpy()
        fast=tr.rolling(4,min_periods=4).mean().shift(1).to_numpy()
        f['compression'][a:b]=np.divide(fast,slow,out=np.full(len(cc),np.nan),where=slow>0)
        f['prior_atr'][a:b]=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean().shift(1).to_numpy()
        oldq=pd.Series(qq).rolling(96,min_periods=96).mean().shift(1).to_numpy()
        f['volume_multiple'][a:b]=np.divide(qq,oldq,out=np.full(len(cc),np.nan),where=oldq>0)
        ep=pd.Series(cc).ewm(span=50,adjust=False,min_periods=50).mean().shift(1)
        f['prior_ema50'][a:b]=ep.to_numpy()
        f['prior_ema_slope'][a:b]=(ep-ep.shift(8)).to_numpy()
        f['low5'][a:b]=pd.Series(ll).rolling(5,min_periods=5).min().to_numpy()
        f['high5'][a:b]=pd.Series(hh).rolling(5,min_periods=5).max().to_numpy()
        f['prevh48'][a:b]=pd.Series(hh).rolling(48,min_periods=48).max().shift(1).to_numpy()
        f['prevl48'][a:b]=pd.Series(ll).rolling(48,min_periods=48).min().shift(1).to_numpy()
    return f

def mask(cfg,raw,f,btc):
    t,o,h,l,c=raw
    side,w=cfg['side'],cfg['window']
    level=f[f'prevh{w}'] if side==1 else f[f'prevl{w}']
    result=(f['eligible'] & (f['compression']<=cfg['compression'])
            & (f['volume_multiple']>=2) & (side*(c-level)>0)
            & (side*(c-o)>0) & (side*f['r1']>0) & (abs(f['r1'])<=.03)
            & (side*(c-f['prior_ema50'])>0) & (side*f['prior_ema_slope']>0))
    result &= (f['buy_share']>=.55)&(f['clv']>=.8) if side==1 else (f['buy_share']<=.45)&(f['clv']<=.2)
    if cfg['btc']=='ALIGN4H': result &= side*btc['r16']>0
    prior=np.r_[False,result[:-1]]
    contiguous=np.r_[False,np.diff(t)==BAR]
    return result&~(prior&contiguous)

def intents(symbol,cfg,raw,f,btc,start,end):
    t,o,h,l,c=raw
    out,excluded=[],Counter();last=-100
    for i in np.flatnonzero(mask(cfg,raw,f,btc)):
        j=i+1
        if j>=len(t) or not start<=t[j]<end or i-last<16: continue
        if t[j]!=t[i]+BAR:
            excluded['ENTRY_PATH_GAP']+=1;continue
        side=cfg['side'];entry=float(o[j]);atr=f['prior_atr'][i]
        if not np.isfinite(atr) or atr<=0:
            excluded['INVALID_ATR']+=1;continue
        stop=f['low5'][i]-.25*atr if side==1 else f['high5'][i]+.25*atr
        distance=side*(entry-stop)
        if not np.isfinite(distance) or distance<=0:
            excluded['INVALID_STRUCTURAL_STOP']+=1;continue
        distance=max(distance,.005*entry)
        if distance/entry>.08:
            excluded['STOP_ABOVE_8PCT']+=1;continue
        stop=entry-side*distance
        if stop<=0:
            excluded['NONPOSITIVE_LEVEL']+=1;continue
        level=f[f'prevh{cfg["window"]}'][i] if side==1 else f[f'prevl{cfg["window"]}'][i]
        last=i
        out.append(dict(symbol=symbol,key=cfg['key'],signal_time=int(t[i]),
            decision_time=int(t[i]+BAR),entry_time=int(t[j]),entry_index=int(j),
            entry=entry,sl=float(stop),side=int(side),risk_pct=float(distance/entry),
            score=float(side*(c[i]-level)/atr*np.sqrt(f['volume_multiple'][i])),
            atr_mult=3.,buy_share=float(f['buy_share'][i]),
            compression_ratio=float(f['compression'][i]),
            volume_multiple=float(f['volume_multiple'][i]),breakout_window=cfg['window'],
            breakout_level=float(level),btc_r16=float(btc['r16'][i])))
    return out,excluded

def audited_minutes(symbol,ts):
    ym=pd.Timestamp(ts,unit='ms',tz='UTC').strftime('%Y-%m')
    if len(chronology.chronology.CACHE)>=6: chronology.chronology.CACHE.clear()
    data=chronology._checked_minutes(symbol,ts)
    key=symbol+'/'+ym
    if key not in MINUTE_INPUTS:
        path=chronology.MINUTE_CACHE_DIR/f'{symbol}-{ym}.npz'
        rec=dict(symbol=symbol,month=ym,
            source_url=f'https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/{symbol}-1m-{ym}.zip',
            original_zip_sha256=None,original_zip_checksum_retained=False,
            provenance_note='Legacy official loader: record processed inputs, not an absent original ZIP hash',
            npz_sha256=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None)
        if len(data)==2 and isinstance(data[0],str):
            rec.update(status='DATA_GAP',message=str(data[1]),bars=0)
        else:
            hasher=hashlib.sha256()
            for arr in data:
                hasher.update(str(arr.dtype).encode());hasher.update(np.ascontiguousarray(arr).tobytes())
            rec.update(status='VALIDATED_PROCESSED_INPUT',bars=len(data[0]),array_sha256=hasher.hexdigest())
        MINUTE_INPUTS[key]=rec
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

def scan(data,btc_path,out,cache,stage,selection_path=None):
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
    MINUTE_INPUTS.clear()
    rows,counts,bad,coverage=[],Counter(),[],[]
    seen=set()
    def checkpoint(complete):
        pd.DataFrame(rows,columns=COLUMNS).to_csv(out/'independent_candidates.csv.gz',index=False,compression='gzip')
        pd.DataFrame(bad,columns=['symbol','policy','entry_time','status']).to_csv(out/'exclusions.csv',index=False)
        (out/'minute_inputs.json').write_text(json.dumps(list(MINUTE_INPUTS.values()),indent=2))
        meta=dict(complete=complete,stage=stage,counts=dict(counts),coverage=coverage,
            source_files=len(paths),policies=chosen,minute_months=len(MINUTE_INPUTS),
            minute_original_zip_hashes_retained=0,
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
            r,c,b=policy_rows(sym,chosen,raw,f,btc,start,end)
            for row in r: row['split']=stage
            rows.extend(r);counts.update({stage+'/'+k:v for k,v in c.items()});bad.extend(b)
            coverage.append(dict(symbol=sym,bars=len(raw[0]),outcomes=len(r),
                source_url=f'https://data.binance.vision/?prefix=data/futures/um/monthly/klines/{sym}/15m/',
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            chronology.chronology.CACHE.clear()
            if n%8==0 or n==len(paths):
                checkpoint(False);print('EXPANSION_SCAN',stage,n,'/',len(paths),'outcomes',len(rows),flush=True)
        if not coverage: raise ValueError('no eligible frozen-market coverage')
    except BaseException:
        checkpoint(False);raise
    checkpoint(True)
    print('EXPANSION_SCAN_DONE',stage,len(rows),'excluded',len(bad),flush=True)

def select(parts,out):
    out.mkdir(parents=True,exist_ok=True)
    paths=sorted(parts.rglob('independent_candidates.csv.gz'))
    if len(paths)!=8: raise ValueError('incomplete development shards')
    originals=[]
    for path in paths:
        m=json.loads((path.parent/'scan_meta.json').read_text())
        if not m['complete'] or m['stage']!='DEV': raise ValueError('incomplete development scan')
        originals.append(m)
    frames=[pd.read_csv(p) for p in paths]
    nonempty=[g for g in frames if len(g)]
    x=pd.concat(nonempty,ignore_index=True) if nonempty else frames[0].iloc[:0].copy()
    if x.duplicated(['symbol','variant','entry_time']).any(): raise ValueError('duplicate development intents')
    info={p['policy']:p for p in policies()}
    grouped={k:g for k,g in x.groupby('variant')}
    table=[]
    for name,p in info.items():
        g=grouped.get(name,x.iloc[:0])
        years=pd.to_datetime(g.entry_time,unit='ms',utc=True).dt.year
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
            r[f'year_{year}_day_R']=float(dayR.mean())
            r[f'year_{year}_days']=len(dayR)
            r[f'year_{year}_n']=len(z)
        table.append(r)
    table.sort(key=lambda r:(-min(r.get('year_2022_day_R',-999),r.get('year_2023_day_R',-999)),r['policy']))
    chosen=[];used=set();side_count=Counter()
    for r in table:
        if (r['n']<300 or r['symbols']<10 or r['net40_mean_bp'] is None
            or r['net40_mean_bp']<=0 or r['top_symbol_share_pct']>30
            or min(r.get('year_2022_n',0),r.get('year_2023_n',0))<80
            or min(r.get('year_2022_days',0),r.get('year_2023_days',0))<60
            or min(r.get('year_2022_net40_R',-999),r.get('year_2023_net40_R',-999))<=0):
            continue
        if r['key'] in used or side_count[r['side']]>=3: continue
        chosen.append({**info[r['policy']],'development_net40_R':r['net40_R_mean'],
                       'development_worst_year_day_R':min(r['year_2022_day_R'],r['year_2023_day_R'])})
        used.add(r['key']);side_count[r['side']]+=1
        if len(chosen)==6: break
    decision=dict(status='ACCOUNT_REPLAY_REQUIRED' if chosen else 'NO_DEVELOPMENT_POLICY_SURVIVOR',
        cells_examined=96,selected=chosen,policies=chosen,union_name='EXPANSION_UNION',
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
    print('EXPANSION_SELECT',json.dumps(decision),flush=True)

def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('scan')
    for n in ('data','btc','out','minute-cache'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--stage',choices=('DEV','GATE'),required=True)
    p.add_argument('--selection',type=Path)
    p=sub.add_parser('select')
    p.add_argument('--parts',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=ap.parse_args()
    if a.command=='scan':scan(a.data,a.btc,a.out,a.minute_cache,a.stage,a.selection)
    else:select(a.parts,a.out)

if __name__=='__main__':main()
