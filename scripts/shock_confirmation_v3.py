"""Confirmed liquidation/pump reversion; development-only canonical policy scout."""
from __future__ import annotations
import argparse,hashlib,json,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from scripts import day_edge_lab as base
from scripts import day_edge_canonical as canonical
from scripts import relative_pullback_v1 as chronology
from scripts import relative_pullback_portfolio as account

BAR,DAY=base.BAR,base.DAY
HOLDS=(16,48)
EXITS=('TP2','TP3','TRAIL')

def configurations():
    out=[]
    for threshold in (.03,.06):
        for side in (1,-1):
            for confirmation in ('RECLAIM','FLOW'):
                for btc in ('ANY','RECOVERY'):
                    key=f'CONFIRM_T{int(threshold*100)}_S{side:+d}_{confirmation}_{btc}'
                    out.append(dict(key=key,threshold=threshold,side=side,
                                    confirmation=confirmation,btc=btc))
    assert len(out)==16
    return out

def policies():
    return [dict(**c,hold=h,exit_type=e,policy=f'{c["key"]}__H{h}__{e}')
            for c in configurations() for h in HOLDS for e in EXITS]

def load(path,end=None):
    raw,q=base.load(path)
    d=pd.read_csv(path,usecols=['taker_buy_quote'])
    buy=d.taker_buy_quote.to_numpy(float)
    if len(buy)!=len(q) or np.any(~np.isfinite(buy)) or np.any(buy<0) or np.any(buy>q*(1+1e-7)+1e-5):
        raise ValueError(f'bad taker flow {path}')
    if end is not None:
        m=raw[0]<end;raw=tuple(a[m] for a in raw);q=q[m];buy=buy[m]
    return raw,q,buy

def features(raw,q,buy):
    f=base.features(raw,q)
    t,o,h,l,c=raw
    for name in ('recent_down','recent_up','low5','high5'):
        f[name]=np.full(len(t),np.nan)
    f['buy_share']=np.divide(buy,q,out=np.full(len(q),np.nan),where=q>0)
    for a,b in base.segments(t):
        r=pd.Series(f['r1'][a:b])
        f['recent_down'][a:b]=-r.shift(1).rolling(4,min_periods=4).min().to_numpy()
        f['recent_up'][a:b]=r.shift(1).rolling(4,min_periods=4).max().to_numpy()
        f['low5'][a:b]=pd.Series(l[a:b]).rolling(5,min_periods=5).min().to_numpy()
        f['high5'][a:b]=pd.Series(h[a:b]).rolling(5,min_periods=5).max().to_numpy()
    return f

def mask(cfg,raw,f,btc):
    t,o,h,l,c=raw
    pc=np.r_[np.nan,c[:-1]];ph=np.r_[np.nan,h[:-1]];pl=np.r_[np.nan,l[:-1]]
    side=cfg['side']
    if side==1:
        shock=f['recent_down']>=cfg['threshold']
        confirmation=(c>o)&(c>pc)&(l>=pl)
        if cfg['confirmation']=='RECLAIM': confirmation&=c>ph
        else: confirmation&=f['buy_share']>=.55
    else:
        shock=f['recent_up']>=cfg['threshold']
        confirmation=(c<o)&(c<pc)&(h<=ph)
        if cfg['confirmation']=='RECLAIM': confirmation&=c<pl
        else: confirmation&=f['buy_share']<=.45
    result=shock&confirmation&f['eligible']
    if cfg['btc']=='RECOVERY': result&=side*btc['r1']>0
    prior=np.r_[False,result[:-1]]
    contiguous=np.r_[False,np.diff(t)==BAR]
    return result&~(prior&contiguous)

def intents(symbol,cfg,raw,f,btc,start,end):
    t,o,h,l,c=raw
    out,excluded=[],Counter();last=-100
    for i in np.flatnonzero(mask(cfg,raw,f,btc)):
        j=i+1
        if j>=len(t) or not start<=t[j]<end: continue
        if i-last<4: continue
        if t[j]!=t[i]+BAR:
            excluded['ENTRY_PATH_GAP']+=1;continue
        last=i
        side=cfg['side'];entry=float(o[j]);atr=f['atr'][i]*c[i]
        stop=f['low5'][i]-.25*atr if side==1 else f['high5'][i]+.25*atr
        distance=side*(entry-stop)
        if not np.isfinite(distance) or distance<=0:
            excluded['INVALID_STRUCTURAL_STOP']+=1;continue
        distance=max(distance,.005*entry)
        if distance/entry>.08:
            excluded['STOP_ABOVE_8PCT']+=1;continue
        stop=entry-side*distance
        shock=f['recent_down'][i] if side==1 else f['recent_up'][i]
        out.append(dict(symbol=symbol,key=cfg['key'],signal_time=int(t[i]),
            decision_time=int(t[i]+BAR),entry_time=int(t[j]),entry_index=int(j),
            entry=entry,sl=float(stop),side=int(side),risk_pct=float(distance/entry),
            score=float(shock/f['atr'][i]),atr_mult=3.,buy_share=float(f['buy_share'][i]),
            shock_pct=float(shock*100),btc_r1=float(btc['r1'][i])))
    return out,excluded

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
    out.mkdir(parents=True,exist_ok=True)
    if stage=='DEV':
        chosen=policies();start,end=base.START,base.DEV_END
    else:
        chosen=json.loads(selection_path.read_text())['policies'];start,end=base.DEV_END,base.GATE_END
    br,bq,bb=load(btc_path,end);bf=features(br,bq,bb)
    chronology.MINUTE_CACHE_DIR=cache;chronology.chronology.one_min=canonical.bounded_minutes
    rows,counts,bad,coverage=[],Counter(),[],[]
    columns=['symbol','key','signal_time','decision_time','entry_time','entry','sl','side','risk_pct',
        'score','atr_mult','buy_share','shock_pct','btc_r1','tp','max_hold_bars','status','exit_time',
        'exit','reason','gross_return','variant','policy','exit_type','hold_min','net40_fraction','net40_R','split']
    def checkpoint(complete):
        pd.DataFrame(rows,columns=columns).to_csv(out/'independent_candidates.csv.gz',index=False,compression='gzip')
        pd.DataFrame(bad).to_csv(out/'exclusions.csv',index=False)
        meta=dict(complete=complete,stage=stage,counts=dict(counts),coverage=coverage,
            policies=chosen,selection_sha256=hashlib.sha256(selection_path.read_bytes()).hexdigest() if selection_path else None)
        (out/'scan_meta.json').write_text(json.dumps(meta,indent=2))
    paths=sorted(data.rglob('*.csv.gz'))
    try:
        for n,path in enumerate(paths,1):
            sym=path.name[:-7]
            raw,q,buy=load(path,end)
            if not len(raw[0]) or raw[0][0]>=base.DEV_END-30*DAY: continue
            f=features(raw,q,buy);btc=base.align_btc(raw[0],br[0],bf)
            r,c,b=policy_rows(sym,chosen,raw,f,btc,start,end)
            for row in r: row['split']=stage
            rows.extend(r);counts.update({stage+'/'+k:v for k,v in c.items()});bad.extend(b)
            coverage.append(dict(symbol=sym,bars=len(raw[0]),outcomes=len(r),
                                 sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            chronology.chronology.CACHE.clear()
            if n%8==0 or n==len(paths):
                checkpoint(False);print('CONFIRM_SCAN',stage,n,'/',len(paths),'outcomes',len(rows),flush=True)
    except BaseException:
        checkpoint(False);raise
    checkpoint(True)
    print('CONFIRM_SCAN_DONE',stage,len(rows),'excluded',len(bad),flush=True)

def select(parts,out):
    out.mkdir(parents=True,exist_ok=True)
    paths=sorted(parts.rglob('independent_candidates.csv.gz'))
    if len(paths)!=8: raise ValueError('incomplete development shards')
    x=pd.concat([pd.read_csv(p) for p in paths],ignore_index=True)
    if x.duplicated(['symbol','variant','entry_time']).any(): raise ValueError('duplicate development intents')
    info={p['policy']:p for p in policies()}
    table=[]
    for name,g in x.groupby('variant'):
        years=pd.to_datetime(g.entry_time,unit='ms',utc=True).dt.year
        pn=g.net40_fraction;rr=g.net40_R
        gain,loss=pn[pn>0].sum(),-pn[pn<0].sum()
        sym=g.groupby('symbol').net40_fraction.sum().clip(lower=0)
        r=dict(**info[name],n=len(g),symbols=g.symbol.nunique(),net40_mean_bp=float(pn.mean()*10000),
            net40_R_mean=float(rr.mean()),net40_pf=float(gain/loss) if loss else None,
            win_pct=float((pn>0).mean()*100),top_symbol_share_pct=float(sym.max()/sym.sum()*100) if sym.sum()>0 else 100.)
        for year,z in g.groupby(years):
            r[f'year_{year}_net40_R']=float(z.net40_R.mean())
            r[f'year_{year}_n']=len(z)
        table.append(r)
    table.sort(key=lambda r:(-min(r.get('year_2022_net40_R',-999),r.get('year_2023_net40_R',-999)),r['policy']))
    eligible=[]
    for r in table:
        if (r['n']<300 or r['symbols']<10 or r['net40_mean_bp']<=0 or r['top_symbol_share_pct']>30
            or min(r.get('year_2022_n',0),r.get('year_2023_n',0))<80
            or min(r.get('year_2022_net40_R',-999),r.get('year_2023_net40_R',-999))<=0): continue
        eligible.append(r)
    chosen=[];used=set();groups=Counter()
    for r in eligible:
        group=(r['side'],r['confirmation'])
        if r['key'] in used or groups[group]>=2: continue
        chosen.append({**info[r['policy']],'development_net40_R':r['net40_R_mean']})
        used.add(r['key']);groups[group]+=1
        if len(chosen)==6: break
    decision=dict(status='ACCOUNT_REPLAY_REQUIRED' if chosen else 'NO_DEVELOPMENT_POLICY_SURVIVOR',
        cells_examined=96,selected=chosen,policies=chosen,
        note='Independent overlapping policy outcomes are diagnostic, never account returns.')
    pd.DataFrame(table).to_csv(out/'development_policy_cells.csv',index=False)
    (out/'selection.json').write_text(json.dumps(decision,indent=2))
    # Save only selected development ledgers for later identical account replay.
    for i,path in enumerate(paths):
        original=json.loads((path.parent/'scan_meta.json').read_text())
        if not original['complete'] or original['stage']!='DEV': raise ValueError('incomplete development scan')
        d=pd.read_csv(path);d=d[d.variant.isin([p['policy'] for p in chosen])]
        selected_names={p['policy'] for p in chosen}
        kept_counts={k:v for k,v in original['counts'].items()
                     if any('/'+p+'/' in k for p in selected_names)}
        target=out/f'dev-{i}';target.mkdir()
        d.to_csv(target/'independent_candidates.csv.gz',index=False,compression='gzip')
        (target/'scan_meta.json').write_text(json.dumps(dict(complete=True,counts=kept_counts,
            selection_sha256=hashlib.sha256((out/'selection.json').read_bytes()).hexdigest(),
            source_development_shard=str(path))))
    print('CONFIRM_SELECT',json.dumps(decision),flush=True)

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
