#!/usr/bin/env python3
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

START=pd.Timestamp('2023-01-01',tz='UTC')
END=pd.Timestamp('2026-09-01',tz='UTC')

def prep(core_path,short_path):
    core=pd.read_csv(core_path,compression='gzip',parse_dates=['dt'])
    core['dt']=pd.to_datetime(core.dt,utc=True)
    core=core[(core.dt>=START)&(core.dt<END)].copy()
    S=pd.read_csv(short_path,parse_dates=['entry_time','exit15'])
    S=S[(S.entry_time>=START)&(S.entry_time<END)].copy()
    cmap=core.set_index('dt').base.to_dict()
    S['day']=S.entry_time.dt.floor('D')
    S['core_active']=S.day.map(cmap).fillna(0).astype(int)
    S['riskpx']=S.risk_dist/S.entry
    S['r24']=S.r_net-0.0016/S.riskpx
    return core,S,cmap

def admit(S,cmap):
    active=[];acc=[]
    for et,g in S.sort_values(['entry_time','symbol']).groupby('entry_time',sort=True):
        active=[p for p in active if p['exit15']>et]
        if int(cmap.get(et.floor('D'),0)): continue
        free=5-len(active)
        if free<=0: continue
        for r in g.head(free).itertuples():
            p=dict(symbol=r.symbol,entry_time=r.entry_time,exit15=r.exit15,r=float(r.r24))
            active.append(p);acc.append(p)
    A=pd.DataFrame(acc)
    return A

def event_blocks(A):
    rows=[]
    for et,g in A.groupby('entry_time',sort=True):
        rows.append(dict(entry_time=et,trades=len(g),sum_r=float(g.r.sum()),mean_r=float(g.r.mean())))
    E=pd.DataFrame(rows)
    assert len(E)==36 and len(A)==179
    return E

def curve_from_events(sum_r,rf):
    mult=1.0+rf*np.asarray(sum_r,float)
    if (mult<=0).any(): return 0.0,1.0
    eq=np.cumprod(mult)
    curve=np.r_[1.0,eq]
    peak=np.maximum.accumulate(curve)
    mdd=float(np.max((peak-curve)/peak))
    return float(eq[-1]),mdd

def self_test():
    v=np.array([1.,-1.,2.])
    e,m=curve_from_events(v,.1)
    assert abs(e-(1.1*.9*1.2))<1e-12
    assert 0<=m<=1
    rng=np.random.default_rng(7)
    p=rng.permutation(v)
    e2,m2=curve_from_events(p,.1)
    assert abs(e2-e)<1e-12
    print('SELF_TEST_PASS',{'equity':e,'mdd':m,'perm_equity':e2,'perm_mdd':m2})

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--self-test',action='store_true');ap.add_argument('--core');ap.add_argument('--short');ap.add_argument('--out',default='out');ap.add_argument('--sims',type=int,default=50000);ap.add_argument('--seed',type=int,default=20260928);a=ap.parse_args()
    if a.self_test:self_test();return
    core,S,cmap=prep(a.core,a.short);A=admit(S,cmap);E=event_blocks(A)
    O=Path(a.out);O.mkdir(parents=True,exist_ok=True)
    A.to_csv(O/'accepted_coreoff_24bp.csv',index=False);E.to_csv(O/'events_coreoff_24bp.csv',index=False)
    rng=np.random.default_rng(a.seed)
    rows=[]
    for rf in [.02,.025,.03]:
        hist_eq,hist_mdd=curve_from_events(E.sum_r.to_numpy(),rf)
        n=a.sims
        perm_mdd=np.empty(n)
        boot_eq=np.empty(n);boot_mdd=np.empty(n)
        vals=E.sum_r.to_numpy(float)
        for i in range(n):
            _,perm_mdd[i]=curve_from_events(rng.permutation(vals),rf)
            b=rng.choice(vals,size=len(vals),replace=True)
            boot_eq[i],boot_mdd[i]=curve_from_events(b,rf)
        rows.append(dict(
            risk=rf,events=len(E),trades=len(A),
            historical_eventblock_equity=hist_eq,historical_eventblock_mdd=hist_mdd,
            perm_mdd_p05=np.quantile(perm_mdd,.05),perm_mdd_p50=np.quantile(perm_mdd,.50),perm_mdd_p95=np.quantile(perm_mdd,.95),perm_mdd_p99=np.quantile(perm_mdd,.99),
            hist_mdd_percentile=float((perm_mdd<=hist_mdd).mean()),
            boot_equity_p01=np.quantile(boot_eq,.01),boot_equity_p05=np.quantile(boot_eq,.05),boot_equity_p50=np.quantile(boot_eq,.50),boot_equity_p95=np.quantile(boot_eq,.95),
            boot_mdd_p50=np.quantile(boot_mdd,.50),boot_mdd_p95=np.quantile(boot_mdd,.95),boot_mdd_p99=np.quantile(boot_mdd,.99),
            prob_boot_loss=float((boot_eq<1).mean()),prob_boot_mdd_ge50=float((boot_mdd>=.50).mean()),prob_boot_mdd_ge70=float((boot_mdd>=.70).mean())
        ))
    R=pd.DataFrame(rows);R.to_csv(O/'event_mc_24bp.csv',index=False)
    print(R.to_string(index=False))

if __name__=='__main__': main()
