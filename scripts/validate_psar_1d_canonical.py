"""Frozen 1D SHORT protocol; canonical data checks, replay and annual rolling validation."""
import argparse
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

from audit_psar_1d import digest, features, fit_threshold, run_audit, unique, verify_raw_daily
from reconcile_psar_1d import BURN, CUT, DAY, candidates, daily, filtered, load, metrics, segments, symbol

HORIZONS = [4,5,6,7,8]
PROTOCOL = {
    'strategy':'1D SHORT, flip age=3, D0 LOW exclusion, BULL reflip OPEN exit or max horizon',
    'age_definition':'flip OPEN age=0; entry at flip OPEN+3 calendar days (fourth OPEN including flip)',
    'horizons':HORIZONS, 'cost_pct':[.2,.4,.8], 'selection_cost_pct':.4,
    'selection_rule':'Highest TRAIN per-signal PF only, tie -> shortest horizon; diagnostic candidate, not portfolio optimum',
    'burn_in_days':BURN, 'ATR':'14 closed-bar simple mean true range, lagged one OPEN; preserves prior implementation',
    'threshold_rule':'D0 > TRAIN tercile-1; no TRAIN minimum/maximum bounding of future D0',
    'common_cohort':'all horizons use eight future daily OPENs; not legacy 12D/24D support',
    'train_end_utc':'2025-01-01', 'purge':'TRAIN selection entries plus 8D must precede split OPEN',
    'gap_policy':'split at missing 15m; retain complete UTC days only; reset PSAR and 100D burn-in after gaps',
    'walk_forward':'annual test, preceding two calendar years fit, eight-day maturity purge; fit D0 and choose horizon on past only',
    'BTC_regime':'prior closed BTC close vs close 90 days earlier; >+10% UP, <-10% DOWN, otherwise RANGE; no optimization',
    'OOS_status':'2025-2026 repeatedly observed before this protocol; historical validation, not pristine OOS',
    'funding':'not included; 20/40/80bp are round-trip fee/slippage assumptions, not measured funding',
}


def measure(frame,k,mode,cost=.4):
    if not len(frame):
        return {'n':0,'pf':None,'win':None,'avg':None,'avg_hold_days':None,'early_exit_pct':None}
    ret = frame[f'ret{k}'].to_numpy(float).copy()
    days = np.full(len(frame),k,dtype=int)
    if mode=='REFLIP_OR_MAX':
        exited = np.zeros(len(frame),bool)
        for j in range(1,k+1):
            first = ~exited & frame[f'bull{j}'].to_numpy(bool)
            ret[first] = frame.loc[first,f'ret{j}'].to_numpy(float)
            days[first] = j
            exited |= first
    values = ret-cost
    loss = -values[values<0].sum()
    return {'n':len(values),'pf':float(values[values>0].sum()/loss) if loss else None,
            'win':float((values>0).mean()*100),'avg':float(values.mean()),
            'avg_hold_days':float(days.mean()),'early_exit_pct':float((days<k).mean()*100)}


def resolved(frame,k):
    ret = frame[f'ret{k}'].to_numpy(float).copy()
    days = np.full(len(frame),k,dtype=int)
    exited = np.zeros(len(frame),bool)
    for j in range(1,k+1):
        first = ~exited & frame[f'bull{j}'].to_numpy(bool)
        ret[first] = frame.loc[first,f'ret{j}'].to_numpy(float)
        days[first] = j; exited |= first
    z = frame.copy()
    z['gross_return_pct'], z['hold_days'] = ret, days
    z['net_return_40bp_pct'] = ret-.4
    z['exit_ts'] = z.ts+days*DAY
    z['early_exit'] = days<k
    return z


def choose(train):
    scores = {str(k):measure(train,k,'REFLIP_OR_MAX',.4) for k in HORIZONS}
    valid = [k for k in HORIZONS if scores[str(k)]['pf'] is not None]
    assert valid
    return max(valid,key=lambda k:(scores[str(k)]['pf'],-k)), scores


def replay(legacy_path, expected_path):
    d = pd.read_csv(legacy_path)
    unique(d)
    f,q = filtered(d)
    expected = json.loads(Path(expected_path).read_text())
    np.testing.assert_allclose(q,expected['d0_edges'],rtol=1e-12,atol=1e-12)
    results = {}
    for k in HORIZONS:
        results[str(k)]={}
        for per,mask in [('TRAIN',f.ts<CUT),('HOLDOUT',f.ts>=CUT)]:
            z=f[mask]
            r = {mode:measure(z,k,mode) for mode in ['FIXED','REFLIP_OR_MAX']}
            for mode in r:
                for key in ['n','pf','win','avg']:
                    assert np.isclose(r[mode][key],expected['results'][str(k)][per][mode][key],rtol=1e-11,atol=1e-11),(k,per,mode,key)
            results[str(k)][per]=r
    return {'all_prior_12D_reflip_results_reproduced':True,'d0_edges':q.tolist(),'results':results}


def cluster_interval(z):
    if not len(z):
        return None
    grouped = z.groupby('ts').net_return_40bp_pct.agg(['sum','count'])
    s,n = grouped['sum'].to_numpy(),grouped['count'].to_numpy()
    rng=np.random.default_rng(20261001)
    values=[]
    for _ in range(2000):
        idx=rng.integers(0,len(grouped),len(grouped))
        values.append(float(s[idx].sum()/n[idx].sum()))
    return {'entry_date_clusters':len(grouped),'replications':2000,
            'trade_weighted_mean_ci95_pct':np.quantile(values,[.025,.975]).tolist(),
            'limitation':'date-clustered diagnostic; not full overlapping-position/account uncertainty'}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--data',required=True)
    ap.add_argument('--reconciliation',required=True)
    ap.add_argument('--reference-reflip',required=True)
    ap.add_argument('--out',required=True)
    args=ap.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    (out/'protocol.json').write_text(json.dumps(PROTOCOL,indent=2))
    audit=run_audit()
    (out/'audit_30x10.json').write_text(json.dumps(audit,indent=2))
    print('AUDIT distinct=30 consecutive_full_clean_passes=10 PASS',flush=True)
    rec=Path(args.reconciliation)
    # Reproduce previous numbers before applying any canonical corrections.
    prior=replay(rec/'candidates_raw12.csv.gz',args.reference_reflip)
    (out/'prior_reflip_exact_replay.json').write_text(json.dumps(prior,indent=2))
    files=sorted(glob.glob(args.data+'/**/*.csv.gz',recursive=True))
    by_symbol={}
    for path in files:
        by_symbol.setdefault(symbol(path),[]).append(path)
    assert files
    all_rows=[];data_checks=[];daily_by_symbol={};invariant_counts={k:0 for k in ['raw_files','raw_segments','daily_segments','mutation_points','age_D0_points']}
    for si,(s,paths) in enumerate(sorted(by_symbol.items())):
        chunks=[]
        for path in paths:
            raw,ordered=load(path)
            assert ordered, path
            chunks.append(raw);invariant_counts['raw_files']+=1
        raw=tuple(np.concatenate([x[j] for x in chunks]) for j in range(5))
        order=np.argsort(raw[0]);raw=tuple(v[order] for v in raw)
        assert (np.diff(raw[0])>0).all(),f'duplicate symbol/time {s}'
        dparts=[]
        for segid,(a,b) in enumerate(segments(raw[0])):
            r=tuple(v[a:b] for v in raw)
            d=daily(r)
            proof=verify_raw_daily(r,d)
            data_checks.append({'symbol':s,'segment':segid,**proof})
            invariant_counts['raw_segments']+=1
            if not len(d[0]):
                continue
            dparts.append(d);invariant_counts['daily_segments']+=1
            if len(d[0])<=BURN+9:
                continue
            sar,bull,atr,flip,age,d0=features(d)
            idx=np.flatnonzero(age>=0)
            assert not (age[:BURN]>=0).any()
            assert (age[flip & (age>=0)]==0).all()
            j=np.flatnonzero((age[1:]>=0)&(age[:-1]>=0)&~flip[1:])+1
            assert (age[j]==age[j-1]+1).all()
            assert np.allclose(d0[j],d0[j-1])
            assert np.isfinite(d0[idx]).all() and (d0[idx]>=0).all()
            invariant_counts['age_D0_points']+=len(idx)
            points=sorted(set([BURN,min(len(d[0])-1,BURN+3),len(d[0])//2,len(d[0])-9]))
            for p in points:
                if p<2:
                    continue
                altered=[v.copy() for v in d]
                altered[2][p:]*=3;altered[3][p:]*=.1;altered[4][p:]*=2
                ss,bb,aa,*_=features(tuple(altered))
                np.testing.assert_allclose(sar[:p+1],ss[:p+1],equal_nan=True,rtol=0,atol=0)
                np.testing.assert_array_equal(bull[:p+1],bb[:p+1])
                np.testing.assert_allclose(atr[:p+1],aa[:p+1],equal_nan=True,rtol=0,atol=0)
                invariant_counts['mutation_points']+=1
            rows=candidates(d,s,'canonical_joined_symbol',segid,8)
            for row in rows:
                i=int(np.searchsorted(d[0],row['ts']))
                assert age[i]==3 and not bull[i] and row['flip_ts']==row['ts']-3*DAY
                assert row['days_remaining']>=8 and i+8<len(d[0])
                for k in HORIZONS:
                    mfe=(1-d[3][i:i+k].min()/d[1][i])*100
                    mae=(d[2][i:i+k].max()/d[1][i]-1)*100
                    assert mfe>=-1e-10 and mae>=-1e-10
                    row[f'mfe{k}']=float(mfe);row[f'mae{k}']=float(mae)
            all_rows.extend(rows)
        if dparts:
            merged=tuple(np.concatenate([x[j] for x in dparts]) for j in range(5))
            assert (np.diff(merged[0])>0).all()
            daily_by_symbol[s]=merged
        if si%50==0:
            print(f'CANONICAL progress={si+1}/{len(by_symbol)} candidates={len(all_rows)}',flush=True)
    df=pd.DataFrame(all_rows).sort_values(['ts','symbol']).reset_index(drop=True)
    unique(df)
    old8=pd.read_csv(rec/'candidates_raw8.csv.gz')
    parity=df.merge(old8,on=['symbol','ts'],how='outer',suffixes=('_canonical','_raw8'),indicator=True)
    assert (parity._merge=='both').all(), 'canonical/raw8 candidate key mismatch'
    for key in ['d0']+[f'ret{k}' for k in range(1,9)]:
        np.testing.assert_allclose(parity[key+'_canonical'],parity[key+'_raw8'],rtol=1e-11,atol=1e-11)
    # Shard contract is checked on the actual observed file universe.
    shards=[set(files[j::8]) for j in range(8)]
    assert set.union(*shards)==set(files) and sum(len(x) for x in shards)==len(files)
    assert all(shards[a].isdisjoint(shards[b]) for a in range(8) for b in range(a))
    records=df.to_dict('records');hash1=digest(records)
    assert hash1==digest(json.loads(json.dumps(records,sort_keys=True)))
    df.to_csv(out/'canonical_all_candidates.csv.gz',index=False)
    train_raw=df[df.ts+8*DAY<CUT].copy()
    assert len(train_raw)>100 and (train_raw.ts+8*DAY<CUT).all()
    threshold=fit_threshold(train_raw)
    train=train_raw[train_raw.d0>threshold].copy()
    selected,scores=choose(train)
    frozen={'selection':selected,'D0_threshold':threshold,'TRAIN_scores':scores,
            'threshold_fit_n':len(train_raw),'selected_TRAIN_n':len(train),
            'threshold_fit_latest_entry_utc':pd.to_datetime(train_raw.ts.max(),unit='ms',utc=True).isoformat(),
            'train_input_hash':digest(train_raw.to_dict('records')),
            'protocol':PROTOCOL,'data_hash':hash1}
    # This artifact is written before HOLDOUT metrics are evaluated.
    (out/'frozen_train_selection.json').write_text(json.dumps(frozen,indent=2))
    assert fit_threshold(train_raw)==threshold
    print('TRAIN_FROZEN '+json.dumps({'horizon':selected,'threshold':threshold,'n':len(train)}),flush=True)
    holdout=df[(df.ts>=CUT)&(df.d0>threshold)].copy()
    comparisons={}
    for k in HORIZONS:
        comparisons[str(k)]={}
        for per,z in [('TRAIN',train),('HOLDOUT',holdout)]:
            comparisons[str(k)][per]={str(cost):{mode:measure(z,k,mode,cost) for mode in ['FIXED','REFLIP_OR_MAX']}
                                                for cost in [.2,.4,.8]}
    z=pd.concat([resolved(train,selected).assign(period='TRAIN'),resolved(holdout,selected).assign(period='HOLDOUT')],ignore_index=True)
    z['year']=pd.to_datetime(z.ts,unit='ms',utc=True).dt.year
    # All BTC regime inputs are closed before each candidate OPEN.
    regimes={}
    if 'BTCUSDT' in daily_by_symbol:
        bt,bo,bh,bl,bc=daily_by_symbol['BTCUSDT']
        for i in range(91,len(bt)):
            if bt[i]-bt[i-91]!=91*DAY:
                continue
            ret90=(bc[i-1]/bc[i-91]-1)*100
            regimes[int(bt[i])]='UP' if ret90>10 else 'DOWN' if ret90<-10 else 'RANGE'
    z['BTC_regime']=z.ts.map(regimes).fillna('UNKNOWN')
    z.to_csv(out/'selected_trades.csv.gz',index=False)
    annual=[];regime_stats=[]
    for (per,year),group in z.groupby(['period','year']):
        annual.append({'period':per,'year':int(year),**measure(group,selected,'REFLIP_OR_MAX')})
    for (per,regime),group in z.groupby(['period','BTC_regime']):
        regime_stats.append({'period':per,'regime':regime,**measure(group,selected,'REFLIP_OR_MAX')})
    wf=[]
    for year in [2023,2024,2025,2026]:
        start=int(pd.Timestamp(f'{year}-01-01',tz='UTC').timestamp()*1000)
        end=int(pd.Timestamp(f'{year+1}-01-01',tz='UTC').timestamp()*1000)
        fit_start=int(pd.Timestamp(f'{year-2}-01-01',tz='UTC').timestamp()*1000)
        fit=df[(df.ts>=fit_start)&(df.ts+8*DAY<start)].copy()
        if len(fit)<100:
            wf.append({'test_year':year,'status':'INSUFFICIENT_TRAIN','fit_n':len(fit)})
            continue
        q=fit_threshold(fit);fit=fit[fit.d0>q]
        h,ss=choose(fit)
        test=df[(df.ts>=start)&(df.ts+8*DAY<end)&(df.d0>q)]
        assert not len(fit) or fit.ts.max()+8*DAY<start
        wf.append({'test_year':year,'status':'COMPLETE','selected_horizon':h,'D0_threshold':q,
                   'TRAIN':ss[str(h)],'TEST_40bp':measure(test,h,'REFLIP_OR_MAX'),
                   'TEST_80bp':measure(test,h,'REFLIP_OR_MAX',.8),
                   'fit_first_utc':pd.to_datetime(fit.ts.min(),unit='ms',utc=True).isoformat(),
                   'fit_last_utc':pd.to_datetime(fit.ts.max(),unit='ms',utc=True).isoformat()})
    result={'protocol':PROTOCOL,'selected_horizon':selected,'D0_threshold':threshold,
            'files':len(files),'symbols':len(by_symbol),'all_eligible_candidates':len(df),
            'canonical_vs_previous_12D_all_candidate_counts':{'canonical':len(df),'previous_12D':len(pd.read_csv(rec/'candidates_raw12.csv.gz'))},
            'range_utc':[pd.to_datetime(df.ts.min(),unit='ms',utc=True).isoformat(),pd.to_datetime(df.ts.max(),unit='ms',utc=True).isoformat()],
            'boundary_entries_excluded_from_train':int(((df.ts<CUT)&(df.ts+8*DAY>=CUT)).sum()),
            'comparison':comparisons,'annual_selected':annual,'BTC_regime_selected':regime_stats,
            'annual_walk_forward':wf,'date_clustered_holdout_CI':cluster_interval(z[z.period=='HOLDOUT']),
            'audit':{'distinct_invariants':30,'consecutive_clean_passes':10,'data_evidence_counts':invariant_counts},
            'previous_reflip_exact_replay':True,'canonical_raw8_exact_parity':True,'candidate_hash':hash1,
            'limits':['raw signals, not constrained account trades','no funding or liquidation model',
                      'annual rolling tests reuse historical data already explored; need prospective frozen validation']}
    (out/'data_audit.json').write_text(json.dumps({'counts':invariant_counts,'segments':data_checks},indent=2))
    (out/'result.json').write_text(json.dumps(result,indent=2))
    pd.DataFrame(annual).to_csv(out/'annual_selected.csv',index=False)
    pd.DataFrame(regime_stats).to_csv(out/'regime_selected.csv',index=False)
    pd.DataFrame(wf).to_json(out/'annual_walk_forward.json',orient='records',indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ['protocol','comparison']},indent=2),flush=True)


if __name__=='__main__':
    main()
