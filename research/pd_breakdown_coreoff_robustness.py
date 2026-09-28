#!/usr/bin/env python3
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

START=pd.Timestamp('2023-01-01',tz='UTC')
END=pd.Timestamp('2026-09-01',tz='UTC')

def pf(v):
    v=np.asarray(v,float); gp=v[v>0].sum(); gl=-v[v<0].sum()
    return gp/gl if gl>0 else np.inf

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

def short_admit(S,cmap,rf=0.02):
    cash=1.;peak=1.;mdd=0.;active=[];acc=[]
    for et,g in S.sort_values(['entry_time','symbol']).groupby('entry_time',sort=True):
        done=[p for p in active if p['exit15']<=et]
        for xt in sorted({p['exit15'] for p in done}):
            batch=[p for p in done if p['exit15']==xt]
            cash+=sum(p['stake']*p['r'] for p in batch)
            peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
        active=[p for p in active if p['exit15']>et]
        if int(cmap.get(et.floor('D'),0)): continue
        free=5-len(active)
        if free<=0: continue
        base=cash
        for r in g.head(free).itertuples():
            p=dict(symbol=r.symbol,entry_time=r.entry_time,exit15=r.exit15,r=float(r.r24),stake=base*rf)
            active.append(p);acc.append(p)
    for xt in sorted({p['exit15'] for p in active}):
        batch=[p for p in active if p['exit15']==xt]
        cash+=sum(p['stake']*p['r'] for p in batch)
        peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
    return pd.DataFrame(acc),cash,mdd

def combo(core,S,cmap,rf):
    cnet=core.set_index('dt').base_net.to_dict()
    cash=1.;peak=1.;mdd=0.;active=[];acc=[]
    for day in pd.date_range(START,END,freq='D',inclusive='left'):
        for xt in sorted({p['exit15'] for p in active if p['exit15']<=day}):
            batch=[p for p in active if p['exit15']==xt]
            cash+=sum(p['stake']*p['r'] for p in batch)
            active=[p for p in active if p['exit15']!=xt]
            peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
        core_stake=cash if int(cmap.get(day,0)) else 0.
        gday=S[S.day==day]
        times=sorted(set(gday.entry_time.tolist()+[p['exit15'] for p in active if day<p['exit15']<day+pd.Timedelta(days=1)]))
        for t in times:
            for xt in sorted({p['exit15'] for p in active if p['exit15']<=t}):
                batch=[p for p in active if p['exit15']==xt]
                cash+=sum(p['stake']*p['r'] for p in batch)
                active=[p for p in active if p['exit15']!=xt]
                peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
            eg=gday[gday.entry_time==t]
            if len(eg) and int(cmap.get(day,0))==0:
                free=5-len(active)
                if free>0:
                    base=cash
                    for r in eg.sort_values('symbol').head(free).itertuples():
                        p=dict(symbol=r.symbol,entry_time=t,exit15=r.exit15,r=float(r.r24),stake=base*rf)
                        active.append(p);acc.append(p)
        if core_stake:
            cash+=core_stake*(float(cnet[day])/100)
            peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
    for xt in sorted({p['exit15'] for p in active}):
        batch=[p for p in active if p['exit15']==xt]
        cash+=sum(p['stake']*p['r'] for p in batch)
        peak=max(peak,cash);mdd=max(mdd,(peak-cash)/peak)
    return cash,mdd,pd.DataFrame(acc)

def self_test():
    core=pd.DataFrame({'dt':[START,START+pd.Timedelta(days=1)],'base':[1,0],'base_net':[1.,0.]})
    cmap=core.set_index('dt').base.to_dict()
    S=pd.DataFrame([
      dict(symbol='A',entry_time=START+pd.Timedelta(hours=4),exit15=START+pd.Timedelta(hours=8),day=START,r24=3.),
      dict(symbol='B',entry_time=START+pd.Timedelta(days=1,hours=4),exit15=START+pd.Timedelta(days=1,hours=8),day=START+pd.Timedelta(days=1),r24=1.)])
    a,e,m=short_admit(S,cmap,.02)
    assert len(a)==1 and a.iloc[0].symbol=='B' and e>1 and m>=0
    print('SELF_TEST_PASS')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--self-test',action='store_true');ap.add_argument('--core');ap.add_argument('--short');ap.add_argument('--out',default='out');a=ap.parse_args()
    if a.self_test:self_test();return
    core,S,cmap=prep(a.core,a.short); O=Path(a.out); O.mkdir(parents=True,exist_ok=True)
    A,_,_=short_admit(S,cmap,.02)
    assert len(A)==179 and A.entry_time.nunique()==36
    yr=[]
    for y,g in A.groupby(A.entry_time.dt.year):
        ev=g.groupby('entry_time').r.mean()
        yr.append(dict(year=int(y),events=len(ev),trades=len(g),trade_wr=(g.r>0).mean(),trade_pf=pf(g.r),avg_trade_r=g.r.mean(),event_wr=(ev>0).mean(),event_pf=pf(ev),avg_event_r=ev.mean(),sum_r=g.r.sum()))
    Y=pd.DataFrame(yr);Y.to_csv(O/'yearly_coreoff_short_24bp.csv',index=False)
    E=A.groupby('entry_time').agg(trades=('symbol','size'),mean_r=('r','mean'),sum_r=('r','sum')).reset_index().sort_values('sum_r',ascending=False)
    E.to_csv(O/'events_ranked_24bp.csv',index=False)
    rows=[]
    for k in [0,1,3,5]:
        drop=set(E.head(k).entry_time) if k else set()
        Q=S[~S.entry_time.isin(drop)].copy()
        for rf in [.02,.025,.03]:
            aa,se,sm=short_admit(Q,cmap,rf)
            ce,cm,ca=combo(core,Q,cmap,rf)
            rows.append(dict(drop_top=k,risk=rf,short_equity=se,short_return=se-1,short_booked_mdd=sm,short_trades=len(aa),short_events=aa.entry_time.nunique(),combo_equity=ce,combo_return=ce-1,combo_booked_mdd=cm,combo_short_trades=len(ca)))
    R=pd.DataFrame(rows);R.to_csv(O/'top_event_removal_24bp.csv',index=False)
    print('YEARLY');print(Y.to_string(index=False));print('TOP_EVENT_STRESS');print(R.to_string(index=False))

if __name__=='__main__':main()
