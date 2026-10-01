"""Canonical account replay of frozen Day Edge Lab V2 hypotheses."""
from __future__ import annotations
import argparse, hashlib, json, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from scripts import day_edge_lab as scout
from scripts import relative_pullback_v1 as chrono
from scripts import relative_pullback_portfolio as account

BAR,DAY=scout.BAR,scout.DAY
STOPS=(2.,3.,4.)
EXITS=('TIME','TP2','TRAIL')
SPLITS={'DEV':(scout.START,scout.DEV_END),'GATE':(scout.DEV_END,scout.GATE_END),
        'COMPARISON':(scout.GATE_END,scout.COMPARISON_END)}

def variant(key,mult,kind):
    return f'{key}__ATR{int(mult)}__{kind}'

def intents(symbol,cfg,hold,mult,raw,f,btc,start,end,delay=0):
    t,o,h,l,c=raw
    out,excluded=[],Counter()
    for i in np.flatnonzero(scout.signal_mask(cfg,raw,f,btc)):
        j=i+1+delay
        if j>=len(t) or not start<=t[j]<end: continue
        if t[j]-t[i]!=(delay+1)*BAR:
            excluded['ENTRY_PATH_GAP']+=1;continue
        atr_abs=f['atr'][i]*c[i]
        entry=float(o[j])
        if not np.isfinite(atr_abs) or atr_abs<=0:
            excluded['INVALID_ATR']+=1;continue
        dist=max(mult*atr_abs,.01*entry)
        if dist/entry>.08:
            excluded['STOP_ABOVE_8PCT']+=1;continue
        side=cfg['side'];sl=entry-side*dist;tp=entry+side*2*dist
        if min(sl,tp)<=0:
            excluded['NONPOSITIVE_LEVEL']+=1;continue
        out.append(dict(symbol=symbol,key=cfg['key'],entry_index=int(j),
            signal_time=int(t[i]),decision_time=int(t[i]+BAR),entry_time=int(t[j]),
            entry=entry,sl=float(sl),tp=float(tp),side=int(side),
            score=float(abs(f[f'r{cfg["lookback"]}'][i])/f['atr'][i]),
            risk_pct=float(dist/entry),max_hold_bars=int(hold),
            atr_mult=float(mult),delay_bars=int(delay)))
    return out,excluded

def resolve(tr,raw,f,kind,end):
    t,o,h,l,c=raw
    j,side,entry=tr['entry_index'],tr['side'],tr['entry']
    stop=tr['sl'];tp=tr['tp'] if kind=='TP2' else (np.inf if side==1 else 0.)
    deadline=min(tr['entry_time']+tr['max_hold_bars']*BAR,end)
    for k in range(j,min(j+tr['max_hold_bars'],len(t))):
        if t[k]>=deadline: break
        if k>j and t[k]!=t[k-1]+BAR: return dict(status='DATA_GAP')
        hs=l[k]<=stop if side==1 else h[k]>=stop
        ht=h[k]>=tp if side==1 else l[k]<=tp
        if hs or ht:
            if k==j or (hs and ht):
                reason,xt,price=chrono.resolve_minutes(tr['symbol'],int(t[k]),entry,tp,stop,side,k==j)
                if reason in {'DATA_GAP','ENTRY_MISMATCH','EXIT_MISMATCH'}:
                    return dict(status=reason)
            elif hs:
                reason='SL';price=float(min(stop,o[k]) if side==1 else max(stop,o[k]))
                xt=int(t[k] if side*(o[k]-stop)<=0 else t[k]+BAR)
            else:
                reason='TP';price=float(tp);xt=int(t[k] if side*(o[k]-tp)>=0 else t[k]+BAR)
            return dict(status='RESOLVED',exit_time=xt,exit=float(price),reason=reason,
                        gross_return=float(side*(price-entry)/entry))
        if kind=='TRAIL' and k-j+1>=4:
            atr=f['atr'][k]*c[k]
            if not np.isfinite(atr) or atr<=0: return dict(status='DATA_GAP')
            proposed=c[k]-side*tr['atr_mult']*atr
            stop=max(stop,proposed) if side==1 else min(stop,proposed)
    z=int(np.searchsorted(t,deadline))
    if z<len(t) and t[z]==deadline:
        if np.any(np.diff(t[j:z+1])!=BAR): return dict(status='DATA_GAP')
        price=float(o[z])
    elif z>j and t[z-1]+BAR==deadline:
        if np.any(np.diff(t[j:z])!=BAR): return dict(status='DATA_GAP')
        price=float(c[z-1])
    else: return dict(status='DATA_GAP')
    return dict(status='RESOLVED',exit_time=int(deadline),exit=price,
                reason='TIME' if deadline<end else 'SPLIT_END',
                gross_return=float(side*(price-entry)/entry))

def bounded_minutes(symbol,ts):
    if len(chrono.chronology.CACHE)>=6: chrono.chronology.CACHE.clear()
    return chrono._checked_minutes(symbol,ts)

def scan(data,btc_path,selection_path,out,cache,splits,delay=0):
    out.mkdir(parents=True,exist_ok=True)
    selected=json.loads(selection_path.read_text())['selected']
    cfgs={c['key']:c for c in scout.configurations()}
    br,bq=scout.load(btc_path);bf=scout.features(br,bq)
    chrono.MINUTE_CACHE_DIR=cache;chrono.chronology.one_min=bounded_minutes
    ledgers,counts,coverage=[],Counter(),[]
    columns=['symbol','key','signal_time','decision_time','entry_time','entry','sl','tp',
        'side','score','risk_pct','max_hold_bars','atr_mult','delay_bars','status',
        'exit_time','exit','reason','gross_return','split','variant','exit_type','hold_min']
    def checkpoint(complete):
        pd.DataFrame(ledgers,columns=columns).to_csv(out/'independent_candidates.csv.gz',
                                                    index=False,compression='gzip')
        meta=dict(complete=complete,counts=dict(counts),coverage=coverage,splits=splits,
            delay_bars=delay,selection_sha256=hashlib.sha256(selection_path.read_bytes()).hexdigest())
        (out/'scan_meta.json').write_text(json.dumps(meta,indent=2))
    files=sorted(data.rglob('*.csv.gz'));seen=set()
    try:
        for n,path in enumerate(files,1):
            symbol=path.name[:-7]
            if symbol in seen: raise ValueError(f'duplicate input {symbol}')
            seen.add(symbol);raw,q=scout.load(path)
            if not len(raw[0]) or raw[0][0]>=scout.DEV_END-30*DAY: continue
            f=scout.features(raw,q);btc=scout.align_btc(raw[0],br[0],bf);kept=0
            for seed in selected:
                cfg=cfgs[seed['key']]
                for split in splits:
                    start,end=SPLITS[split]
                    for mult in STOPS:
                        entries,exc=intents(symbol,cfg,seed['hold'],mult,raw,f,btc,start,end,delay)
                        for key,value in exc.items(): counts[f'{split}/{seed["key"]}/ATR{int(mult)}/{key}']+=value
                        for kind in EXITS:
                            name=variant(seed['key'],mult,kind)
                            for tr in entries:
                                r=resolve(tr,raw,f,kind,end)
                                counts[f'{split}/{name}/{r["status"]}']+=1
                                if r['status']!='RESOLVED': continue
                                row={k:v for k,v in tr.items() if k!='entry_index'}
                                row.update(r,split=split,variant=name,exit_type=kind)
                                row['hold_min']=(row['exit_time']-row['entry_time'])/60000
                                ledgers.append(row);kept+=1
            coverage.append(dict(symbol=symbol,bars=len(raw[0]),outcomes=kept,
                                 sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            chrono.chronology.CACHE.clear()
            if n%8==0 or n==len(files):
                checkpoint(False)
                print('CANONICAL_SCAN',n,'/',len(files),symbol,'outcomes',len(ledgers),flush=True)
    except BaseException:
        checkpoint(False);raise
    checkpoint(True)
    print('CANONICAL_SCAN_DONE',len(ledgers),flush=True)

def union_ledger(x,mult,kind):
    z=x[(x.atr_mult==mult)&(x.exit_type==kind)].copy()
    z['priority']=np.where(z['key'].str.contains('_L1_'),0,1)
    return z.sort_values(['symbol','entry_time','priority','key']).drop_duplicates(
        ['symbol','entry_time']).drop(columns='priority')

def choose(summary,yearly,out,integrity):
    found=[]
    for name,g in summary.groupby('variant'):
        a=g[g.guarded]
        required={(s,c) for s in ('DEV','GATE') for c in (20,40)}
        if set(zip(a.split,a.cost_bps))!=required: continue
        if (a.halt_time.notna().any() or a.trades.min()<80 or a.mdd_15m_pct.max()>=15
                or a.net_return_pct.min()<=0): continue
        b=a[a.cost_bps==20];stress=a[a.cost_bps==40]
        if b.cagr_pct.min()<20 or b.pf.min()<1.15 or stress.pf.min()<1.05: continue
        yy=yearly[(yearly.variant==name)&yearly.guarded&(yearly.cost_bps==20)
                   &yearly.period.isin(['2022','2023','2024'])]
        if len(yy)!=3 or yy.net_return_pct.min()<=0: continue
        found.append(dict(variant=name,rank_score=float(b.cagr_pct.min()),
            worst_40bp_mdd_pct=float(stress.mdd_15m_pct.max()),
            min_40bp_pf=float(stress.pf.min()),
            goal_daily_mean_met=bool(b.daily_mean_pct.min()>=.7)))
    found.sort(key=lambda x:(-x['rank_score'],x['worst_40bp_mdd_pct'],x['variant']))
    status='PROVISIONAL_RESEARCH_SURVIVOR' if found else 'NO_CANONICAL_ACCOUNT_SURVIVOR'
    if integrity['chronology_exclusions']:
        status+='__DATA_COVERAGE_REVIEW_REQUIRED'
    d=dict(status=status,survivors=found,selected=found[:1],integrity=integrity,
           note='Historical selection, not independent proof. Daily goal is separately measured.')
    (out/'survivors.json').write_text(json.dumps(d,indent=2))
    print('REPLAY_DECISION',json.dumps(d),flush=True)

def accounts(data,parts,out,selection_path,splits,expected_shards=8):
    out.mkdir(parents=True,exist_ok=True)
    paths=sorted(parts.rglob('independent_candidates.csv.gz'))
    metas=sorted(parts.rglob('scan_meta.json'))
    if len(paths)!=expected_shards or len(metas)!=expected_shards:
        raise ValueError('incomplete canonical shards')
    counts=Counter()
    selection_hash=hashlib.sha256(selection_path.read_bytes()).hexdigest()
    for p in metas:
        m=json.loads(p.read_text())
        if not m['complete'] or m['selection_sha256']!=selection_hash:
            raise ValueError('incomplete or inconsistent canonical scan')
        counts.update(m['counts'])
    x=pd.concat([pd.read_csv(p) for p in paths],ignore_index=True)
    if x.duplicated(['symbol','variant','entry_time','split']).any(): raise ValueError('duplicate intents')
    files={}
    for p in data.rglob('*.csv.gz'):
        sym=p.name[:-7]
        if sym in files: raise ValueError('duplicate market file')
        files[sym]=p
    raw={sym:scout.load(files[sym])[0] for sym in sorted(x.symbol.unique())}
    market=account.Market(raw)
    seeds=json.loads(selection_path.read_text())['selected']
    names=[variant(s['key'],m,e) for s in seeds for m in STOPS for e in EXITS]
    names += [variant('UNION',m,e) for m in STOPS for e in EXITS]
    results,periods,audits=[],[],[]
    for split in splits:
        start,end=SPLITS[split];part=x[x.split==split]
        for name in names:
            if name.startswith('UNION__'):
                _,m,e=name.split('__');sel=union_ledger(part,float(m[3:]),e)
            else: sel=part[part.variant==name]
            for cost in (20,40):
                for guarded in (True,False):
                    key=f'{split}__{name}__{cost}bp__{"guarded" if guarded else "diagnostic"}'
                    r,tr,day,curve=account.simulate(sel,market,start,end,cost,guarded)
                    r.update(split=split,variant=name,independent_n=len(sel));results.append(r)
                    folder=out/'details'/key;folder.mkdir(parents=True,exist_ok=True)
                    tr.to_csv(folder/'trades.csv.gz',index=False,compression='gzip')
                    day.to_csv(folder/'daily.csv',index=False)
                    curve.to_csv(folder/'curve.csv.gz',index=False,compression='gzip')
                    pn=tr.net_pnl.to_numpy() if len(tr) else np.array([])
                    assert np.isclose(pn.sum(),r['net_return_pct']/100,atol=1e-10)
                    observed=np.r_[1.,np.column_stack([curve.equity_pre_entry,curve.equity]).ravel()]
                    peak=np.maximum.accumulate(observed)
                    assert np.isclose(((peak-observed)/peak).max()*100,r['mdd_15m_pct'])
                    if len(tr):
                        assert (tr.hold_min<=1440).all()
                        assert (tr.notional/tr.entry_equity<=.300000001).all()
                        assert (tr.reserved_risk/tr.entry_equity<=.005000001).all()
                        pf=pn[pn>0].sum()/-pn[pn<0].sum() if (pn<0).any() else None
                        assert (pf is None and r['pf'] is None) or np.isclose(pf,r['pf'])
                    if len(day):
                        assert np.isclose((day.return_pct>=.7).mean()*100,r['day_ge_0_7_pct'])
                        assert np.isclose((day.return_pct>=2).mean()*100,r['day_ge_2_pct'])
                        dates=pd.to_datetime(day.day)
                        for freq in ('Y','Q'):
                            for period,g in day.groupby(dates.dt.to_period(freq)):
                                periods.append(dict(scenario=key,split=split,variant=name,
                                    cost_bps=cost,guarded=guarded,period=str(period),days=len(g),
                                    net_return_pct=float(100*(np.prod(1+g.return_pct/100)-1))))
                    audits.append(dict(scenario=key,all_checks_passed=True,trades=len(tr),
                        trades_sha256=hashlib.sha256((folder/'trades.csv.gz').read_bytes()).hexdigest(),
                        curve_sha256=hashlib.sha256((folder/'curve.csv.gz').read_bytes()).hexdigest()))
                    print('ACCOUNT',key,json.dumps({k:r.get(k) for k in
                        ('trades','net_return_pct','cagr_pct','mdd_15m_pct','pf','daily_mean_pct','day_ge_0_7_pct')}),flush=True)
    summary=pd.DataFrame([{k:v for k,v in r.items() if not isinstance(v,(dict,list))} for r in results])
    summary.to_csv(out/'summary.csv',index=False)
    (out/'summary.json').write_text(json.dumps(results,indent=2,allow_nan=False))
    yearly=pd.DataFrame(periods);yearly.to_csv(out/'year_quarter.csv',index=False)
    (out/'audit.json').write_text(json.dumps(audits,indent=2))
    x.to_csv(out/'independent_candidates.csv.gz',index=False,compression='gzip')
    bad={k:v for k,v in counts.items() if k.rsplit('/',1)[-1] in {'DATA_GAP','ENTRY_MISMATCH','EXIT_MISMATCH'}}
    integrity=dict(counts=dict(counts),chronology_exclusions=sum(bad.values()),exclusion_detail=bad)
    choose(summary,yearly,out,integrity)

def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest='command',required=True)
    s=sub.add_parser('scan')
    for name in ('data','btc','selection','out','minute-cache'): s.add_argument('--'+name,type=Path,required=True)
    s.add_argument('--splits',nargs='+',choices=SPLITS,default=['DEV','GATE'])
    s.add_argument('--delay-bars',type=int,choices=(0,1),default=0)
    a=sub.add_parser('accounts')
    for name in ('data','parts','out','selection'): a.add_argument('--'+name,type=Path,required=True)
    a.add_argument('--splits',nargs='+',choices=SPLITS,default=['DEV','GATE'])
    a.add_argument('--expected-shards',type=int,default=8)
    args=ap.parse_args()
    if args.command=='scan':
        scan(args.data,args.btc,args.selection,args.out,args.minute_cache,tuple(args.splits),args.delay_bars)
    else:
        accounts(args.data,args.parts,args.out,args.selection,tuple(args.splits),args.expected_shards)

if __name__=='__main__': main()
