#!/usr/bin/env python3
import argparse, io, zipfile, math, glob
from pathlib import Path
import numpy as np
import pandas as pd
import requests

CORE_HALF_FEE = 0.00125  # 0.25% round trip, charged half on state transition
START = pd.Timestamp('2023-01-01', tz='UTC')
END = pd.Timestamp('2026-09-01', tz='UTC')


def normalize_ms(s):
    x = pd.to_numeric(s, errors='coerce').to_numpy(dtype='float64')
    x = np.where(x > 1e14, x / 1000.0, x)
    return pd.to_datetime(x, unit='ms', utc=True)


def download_spot_15m(symbol, start, end, cache_dir):
    cache_dir = Path(cache_dir); cache_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for month in pd.date_range(start.floor('D').replace(day=1), end - pd.Timedelta(seconds=1), freq='MS', tz='UTC'):
        ym = month.strftime('%Y-%m')
        cache = cache_dir / f'{symbol}-15m-{ym}.csv.gz'
        if cache.exists():
            q = pd.read_csv(cache, compression='gzip')
        else:
            url = f'https://data.binance.vision/data/spot/monthly/klines/{symbol}/15m/{symbol}-15m-{ym}.zip'
            r = requests.get(url, timeout=90); r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                member = next(n for n in z.namelist() if n.endswith('.csv'))
                raw = pd.read_csv(z.open(member), header=None)
            q = raw.iloc[:, [0,1,4]].copy(); q.columns = ['open_time','open','close']
            q.to_csv(cache, index=False, compression='gzip')
        q['dt'] = normalize_ms(q.open_time)
        q['open'] = pd.to_numeric(q.open, errors='coerce'); q['close'] = pd.to_numeric(q.close, errors='coerce')
        frames.append(q[['dt','open','close']].dropna())
    d = pd.concat(frames, ignore_index=True).drop_duplicates('dt').sort_values('dt')
    return d[(d.dt >= start) & (d.dt < end)].reset_index(drop=True)


def build_core_marks(core, btc, eth, initial_prev_base=0):
    b = btc.rename(columns={'open':'b_open','close':'b_close'})
    e = eth.rename(columns={'open':'e_open','close':'e_close'})
    x = b.merge(e, on='dt', how='inner').sort_values('dt')
    x['day'] = x.dt.dt.floor('D')
    rows=[]; parity=[]
    c = core.set_index('dt').sort_index()
    prev = int(initial_prev_base)
    for day, r in c.iterrows():
        state = int(r.base)
        fee = abs(state-prev) * CORE_HALF_FEE
        if state == 0:
            expected = float(r.base_net)/100.0
            if abs(expected + fee) > 2e-8:
                raise AssertionError(f'inactive core fee parity {day}: base_net={expected} fee={fee}')
            parity.append((day, 0.0, expected, 0))
            prev = state
            continue
        d = x[x.day == day].copy()
        if len(d) != 96:
            raise AssertionError(f'core spot 15m bars {day}: {len(d)} != 96')
        expected_times = pd.date_range(day, day+pd.Timedelta(days=1)-pd.Timedelta(minutes=15), freq='15min', tz='UTC')
        if not d.dt.reset_index(drop=True).equals(pd.Series(expected_times)):
            raise AssertionError(f'core spot cadence gap on {day}')
        bo=float(d.iloc[0].b_open); eo=float(d.iloc[0].e_open)
        d['mark_time']=d.dt+pd.Timedelta(minutes=15)
        d['gross']=0.5*((d.b_close/bo-1)+(d.e_close/eo-1))
        expected_gross=float(r.base_net)/100.0 + fee
        got=float(d.iloc[-1].gross)
        if abs(got-expected_gross) > 2e-6:
            raise AssertionError(f'core daily/15m parity {day}: raw={got} expected={expected_gross}')
        if abs(got) > 1e-15:
            d['gross'] *= expected_gross/got
        elif abs(expected_gross) > 2e-6:
            raise AssertionError(f'zero 15m gross but nonzero daily gross {day}')
        for z in d.itertuples(): rows.append((day,z.mark_time,float(z.gross)))
        parity.append((day, got, expected_gross, len(d)))
        prev = state
    marks=pd.DataFrame(rows,columns=['day','mark_time','gross'])
    return marks,pd.DataFrame(parity,columns=['day','raw_final_gross','expected_gross','bars'])


def load_um_needed(root, symbols):
    paths={}
    for fn in glob.glob(str(Path(root)/'**/*.csv.gz'), recursive=True):
        sym=Path(fn).name.replace('.csv.gz','')
        if sym in symbols: paths[sym]=fn
    missing=sorted(set(symbols)-set(paths))
    if missing: raise AssertionError(f'missing UM symbols {len(missing)}: {missing[:10]}')
    out={}
    for sym,fn in paths.items():
        d=pd.read_csv(fn,compression='gzip')
        tc='open_time' if 'open_time' in d else 'timestamp_ms'
        d['dt']=normalize_ms(d[tc]); d['close']=pd.to_numeric(d.close,errors='coerce')
        d=d[['dt','close']].dropna().sort_values('dt').drop_duplicates('dt').set_index('dt')
        out[sym]=d
    return out


def build_short_paths(S, px):
    rows=[]
    for r in S.itertuples():
        d=px[r.symbol]
        rows.append((r.symbol,r.entry_time,r.entry_time,0.0))
        w=d[(d.index>=r.entry_time)&((d.index+pd.Timedelta(minutes=15))<r.exit15)]
        for ot,b in w.iterrows():
            mt=ot+pd.Timedelta(minutes=15)
            gross_r=(float(r.entry)-float(b.close))/float(r.risk_dist)
            rows.append((r.symbol,r.entry_time,mt,gross_r))
    return pd.DataFrame(rows,columns=['symbol','entry_time','mark_time','gross_r'])


def prepare_short_candidates(S, core):
    S=S.copy(); S['entry_time']=pd.to_datetime(S.entry_time,utc=True); S['exit15']=pd.to_datetime(S.exit15,utc=True)
    S=S[(S.entry_time>=START)&(S.entry_time<END)].sort_values(['entry_time','symbol']).copy()
    cmap=core.set_index('dt').base.to_dict(); S['day']=S.entry_time.dt.floor('D');S['core_active']=S.day.map(cmap).fillna(0).astype(int)
    return S


def simulate_combo(core, S, core_marks, short_paths, rf, costbp, use_shorts=True, initial_prev_base=0):
    core=core.sort_values('dt').reset_index(drop=True)
    cdict={r.dt:r for r in core.itertuples()}
    cm={d:g.set_index('mark_time').gross.to_dict() for d,g in core_marks.groupby('day')}
    sp={(sym,et):g.set_index('mark_time').gross_r.to_dict() for (sym,et),g in short_paths.groupby(['symbol','entry_time'])}
    event_groups={et:g for et,g in S.groupby('entry_time',sort=True)}
    cash=1.0; peak=1.0; mtm_mdd=0.0; booked_peak=1.0; booked_mdd=0.0
    openp=[]; accepted=[]; max_open=0; max_nominal=0.0
    core_stake=0.0; current_core_day=None; current_core_gross=0.0
    prev_base=int(initial_prev_base)

    def realize_short_exits(t):
        nonlocal cash,openp,booked_peak,booked_mdd
        done=[p for p in openp if p['exit15']<=t]
        if done:
            for p in done: cash += p['stake']*p['r_real']
            openp=[p for p in openp if p['exit15']>t]
            booked_peak=max(booked_peak,cash); booked_mdd=max(booked_mdd,(booked_peak-cash)/booked_peak)

    def mark_equity(t):
        nonlocal peak,mtm_mdd,max_nominal
        eq=cash
        if current_core_day is not None and int(cdict[current_core_day].base):
            eq += core_stake*current_core_gross
        short_risk_capital=0.0
        for p in openp:
            path=sp[(p['symbol'],p['entry_time'])]
            ks=[k for k in path.keys() if k<=t]
            gr=path[max(ks)] if ks else 0.0
            eq += p['stake']*(gr-p['cost_r'])
            short_risk_capital += p['stake']
        peak=max(peak,eq); mtm_mdd=max(mtm_mdd,(peak-eq)/peak)
        max_nominal=max(max_nominal,(core_stake if (current_core_day is not None and int(cdict[current_core_day].base)) else 0.0)+short_risk_capital)
        return eq

    grid=pd.date_range(START,END,freq='15min',inclusive='both')
    for t in grid:
        if t.minute==0 and t.hour==0:
            if current_core_day is not None and int(cdict[current_core_day].base):
                current_core_gross=cm[current_core_day][t]
                mark_equity(t)
                cash += core_stake*current_core_gross
                booked_peak=max(booked_peak,cash); booked_mdd=max(booked_mdd,(booked_peak-cash)/booked_peak)
            realize_short_exits(t)
            if t>=END: break
            day=t; row=cdict[day]; state=int(row.base)
            base_before=cash
            fee=abs(state-prev_base)*CORE_HALF_FEE
            if fee: cash -= base_before*fee
            core_stake=base_before if state else 0.0
            current_core_day=day; current_core_gross=0.0; prev_base=state
            booked_peak=max(booked_peak,cash); booked_mdd=max(booked_mdd,(booked_peak-cash)/booked_peak)
            if use_shorts and t in event_groups and state==0:
                g=event_groups[t];free=5-len(openp);base=cash
                for r in g.head(max(0,free)).itertuples():
                    riskpx=float(r.risk_dist)/float(r.entry);cost_r=(costbp/10000.0)/riskpx
                    rr=float(r.r_net)-((costbp-8)/10000.0)/riskpx
                    p=dict(symbol=r.symbol,entry_time=r.entry_time,exit15=r.exit15,stake=base*rf,r_real=rr,cost_r=cost_r)
                    openp.append(p);accepted.append(p)
            max_open=max(max_open,len(openp));mark_equity(t);continue

        realize_short_exits(t)
        day=t.floor('D'); row=cdict[day]
        if int(row.base):
            current_core_gross=cm[day][t]
        else:
            current_core_gross=0.0
        if use_shorts and t in event_groups and int(row.base)==0:
            g=event_groups[t];free=5-len(openp);base=cash
            for r in g.head(max(0,free)).itertuples():
                riskpx=float(r.risk_dist)/float(r.entry);cost_r=(costbp/10000.0)/riskpx
                rr=float(r.r_net)-((costbp-8)/10000.0)/riskpx
                p=dict(symbol=r.symbol,entry_time=r.entry_time,exit15=r.exit15,stake=base*rf,r_real=rr,cost_r=cost_r)
                openp.append(p);accepted.append(p)
        max_open=max(max_open,len(openp));mark_equity(t)

    if openp: raise AssertionError(f'open shorts remain at END: {len(openp)}')
    return dict(final_equity=cash,booked_mdd=booked_mdd,mtm_mdd=mtm_mdd,accepted=len(accepted),max_open=max_open,max_nominal=max_nominal)


def self_test():
    global START,END
    old=(START,END);START=pd.Timestamp('2024-01-01',tz='UTC');END=pd.Timestamp('2024-01-04',tz='UTC')
    core=pd.DataFrame({
        'dt':pd.date_range(START,END-pd.Timedelta(days=1),freq='D',tz='UTC'),
        'base':[1,0,1],
        'base_net':[1.875,-0.125,-1.125],
    })
    rows=[]
    for day,final in [(START,.02),(START+pd.Timedelta(days=2),-.01)]:
        for k in range(1,97): rows.append((day,day+pd.Timedelta(minutes=15*k),final*k/96.0))
    cm=pd.DataFrame(rows,columns=['day','mark_time','gross'])
    S=pd.DataFrame([
        dict(symbol='A',entry_time=START+pd.Timedelta(hours=6),exit15=START+pd.Timedelta(hours=12),entry=100.,risk_dist=1.,r_net=3-.08,core_active=1),
        dict(symbol='B',entry_time=START+pd.Timedelta(days=1,hours=6),exit15=START+pd.Timedelta(days=2,hours=12),entry=100.,risk_dist=1.,r_net=1.0-.08,core_active=0),
    ])
    sp=pd.DataFrame([
        ('A',S.iloc[0].entry_time,S.iloc[0].entry_time,0.),
        ('B',S.iloc[1].entry_time,S.iloc[1].entry_time,0.),
        ('B',S.iloc[1].entry_time,START+pd.Timedelta(days=1,hours=12),.2),
        ('B',S.iloc[1].entry_time,START+pd.Timedelta(days=2),.4),
        ('B',S.iloc[1].entry_time,START+pd.Timedelta(days=2,hours=6),.6),
    ],columns=['symbol','entry_time','mark_time','gross_r'])
    res0=simulate_combo(core,S,cm,sp,.02,8,use_shorts=False)
    expected=float(np.prod(1+core.base_net.to_numpy()/100))
    assert abs(res0['final_equity']-expected)<1e-12,(res0,expected)
    res=simulate_combo(core,S,cm,sp,.02,8,use_shorts=True)
    assert res['accepted']==1,res
    assert res['max_open']==1,res
    assert res['mtm_mdd']>=0 and res['booked_mdd']>=0
    print('SELF_TEST_PASS',{'core_expected':expected,'core_result':res0,'combo':res})
    START,END=old


def data_smoke(core_path,cache_dir):
    core=pd.read_csv(core_path,compression='gzip',parse_dates=['dt']);core['dt']=pd.to_datetime(core.dt,utc=True)
    s=pd.Timestamp('2023-05-01',tz='UTC');e=pd.Timestamp('2023-06-01',tz='UTC')
    prev=int(core.loc[core.dt<s,'base'].iloc[-1]) if (core.dt<s).any() else 0
    c=core[(core.dt>=s)&(core.dt<e)].copy()
    btc=download_spot_15m('BTCUSDT',s,e,cache_dir);eth=download_spot_15m('ETHUSDT',s,e,cache_dir)
    marks,par=build_core_marks(c,btc,eth,prev)
    active=int(c.base.sum()); assert len(marks)==active*96
    print('DATA_SMOKE_PASS',{'days':len(c),'active_days':active,'mark_rows':len(marks),'max_parity_err':float((par.raw_final_gross-par.expected_gross).abs().max())})


def full_run(core_path,short_path,um_dir,out_dir,spot_cache):
    allcore=pd.read_csv(core_path,compression='gzip',parse_dates=['dt']);allcore['dt']=pd.to_datetime(allcore.dt,utc=True)
    prev=int(allcore.loc[allcore.dt<START,'base'].iloc[-1]) if (allcore.dt<START).any() else 0
    core=allcore[(allcore.dt>=START)&(allcore.dt<END)].copy().reset_index(drop=True)
    S=pd.read_csv(short_path,parse_dates=['entry_time','exit15']);S=prepare_short_candidates(S,core)
    btc=download_spot_15m('BTCUSDT',START,END,spot_cache);eth=download_spot_15m('ETHUSDT',START,END,spot_cache)
    core_marks,par=build_core_marks(core,btc,eth,prev)
    px=load_um_needed(um_dir,set(S.symbol));short_paths=build_short_paths(S,px)
    O=Path(out_dir);O.mkdir(parents=True,exist_ok=True)
    par.to_csv(O/'core_spot15m_parity.csv',index=False)
    S.to_csv(O/'short_candidates_used.csv',index=False)
    core_only=simulate_combo(core,S,core_marks,short_paths,.02,8,use_shorts=False,initial_prev_base=prev)
    expected=float(np.prod(1+core.base_net.to_numpy()/100))
    if abs(core_only['final_equity']-expected)>1e-10: raise AssertionError((core_only,expected))
    rows=[]
    for cost in [8,24]:
        for rf in [.02,.025,.03]:
            z=simulate_combo(core,S,core_marks,short_paths,rf,cost,use_shorts=True,initial_prev_base=prev)
            rows.append(dict(cost_bp=cost,risk=rf,core_only_equity=core_only['final_equity'],core_only_booked_mdd=core_only['booked_mdd'],core_only_mtm_mdd=core_only['mtm_mdd'],**z))
    R=pd.DataFrame(rows);R.to_csv(O/'combo_15m_mtm.csv',index=False)
    print('CORE_ONLY',core_only,'expected',expected)
    print(R.to_string(index=False))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--self-test',action='store_true');ap.add_argument('--data-smoke',action='store_true')
    ap.add_argument('--core');ap.add_argument('--short');ap.add_argument('--um');ap.add_argument('--out',default='out');ap.add_argument('--spot-cache',default='spot15m')
    a=ap.parse_args()
    if a.self_test:self_test();return
    if a.data_smoke:
        if not a.core: raise SystemExit('--core required')
        data_smoke(a.core,a.spot_cache);return
    if not all([a.core,a.short,a.um]): raise SystemExit('--core --short --um required')
    full_run(a.core,a.short,a.um,a.out,a.spot_cache)
if __name__=='__main__': main()
